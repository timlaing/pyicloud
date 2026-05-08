"""Internal helpers for Apple's HSA2 trusted-device bridge flow."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import ssl
import struct
import time
import uuid
from binascii import Error as BinasciiError
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from pyicloud.exceptions import (
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)
from pyicloud.hsa2_bridge_prover import TrustedDeviceBridgeProver

LOGGER = logging.getLogger(__name__)

BRIDGE_STEP_PATH = "/bridge/step/0"
BRIDGE_STEP_PATH_TEMPLATE = "/bridge/step/{step}"
BRIDGE_CODE_VALIDATE_PATH = "/bridge/code/validate"
NEW_CONNECTION_EXPIRATION_SECONDS = 86400
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA
SERVER_MESSAGE_CONNECTION_RESPONSE = 1
SERVER_MESSAGE_PUSH = 2
SERVER_MESSAGE_CHANNEL_SUBSCRIPTION_RESPONSE = 3
SERVER_MESSAGE_PUSH_ACK = 7
STATUS_OK = 0
STATUS_INVALID_NONCE = 2
BRIDGE_SIGNATURE_PREFIX = b"\x01\x03"
BRIDGE_DONE_DATA_B64 = base64.b64encode(b"done").decode("ascii")
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WEBSOCKET_TIMEOUT_SECONDS = 30.0
WEBSOCKET_ENVIRONMENT_HOSTS: dict[str, str] = {
    "prod": "websocket.push.apple.com",
    "sandbox": "websocket.sandbox.push.apple.com",
}
HTTP_STATUS_OK = 200
HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_CONFLICT = 409
HTTP_STATUS_PRECONDITION_FAILED = 412


@dataclass(frozen=True)
class Hsa2BootContext:
    """Bridge-related HSA2 boot data parsed from Apple's HTML bootstrap."""

    auth_initial_route: str = ""
    has_trusted_devices: bool = False
    auth_factors: tuple[str, ...] = ()
    bridge_initiate_data: dict[str, Any] = field(default_factory=dict)
    phone_number_verification: dict[str, Any] = field(default_factory=dict)
    source_app_id: Optional[str] = None

    @classmethod
    def from_auth_options(cls, auth_options: Mapping[str, Any]) -> "Hsa2BootContext":
        """Build a normalized boot context from Apple's auth-options payload."""
        bridge_initiate_data = auth_options.get("bridgeInitiateData")
        if not isinstance(bridge_initiate_data, dict):
            bridge_initiate_data = {}

        phone_number_verification = auth_options.get("phoneNumberVerification")
        if not isinstance(phone_number_verification, dict):
            phone_number_verification = bridge_initiate_data.get(
                "phoneNumberVerification"
            )
        if not isinstance(phone_number_verification, dict):
            phone_number_verification = {}

        auth_factors = auth_options.get("authFactors")
        if not isinstance(auth_factors, list):
            auth_factors = []

        source_app_id = auth_options.get("sourceAppId")
        if source_app_id is not None:
            source_app_id = str(source_app_id)

        return cls(
            auth_initial_route=str(auth_options.get("authInitialRoute") or ""),
            has_trusted_devices=bool(auth_options.get("hasTrustedDevices")),
            auth_factors=tuple(
                factor for factor in auth_factors if isinstance(factor, str)
            ),
            bridge_initiate_data=dict(bridge_initiate_data),
            phone_number_verification=dict(phone_number_verification),
            source_app_id=source_app_id,
        )

    def as_auth_data(self) -> dict[str, Any]:
        """Return parsed boot data in the shape expected by the auth flow."""

        auth_data: dict[str, Any] = {
            "authInitialRoute": self.auth_initial_route,
            "hasTrustedDevices": self.has_trusted_devices,
            "authFactors": list(self.auth_factors),
        }
        if self.bridge_initiate_data:
            auth_data["bridgeInitiateData"] = dict(self.bridge_initiate_data)
        if self.phone_number_verification:
            auth_data["phoneNumberVerification"] = dict(self.phone_number_verification)
            trusted_phone_number = self.phone_number_verification.get(
                "trustedPhoneNumber"
            )
            if isinstance(trusted_phone_number, dict):
                auth_data["trustedPhoneNumber"] = dict(trusted_phone_number)
        if self.source_app_id is not None:
            auth_data["sourceAppId"] = self.source_app_id
        return auth_data


class _BridgePushPayloadModel(BaseModel):
    """Strict validator for Apple's bridge push JSON envelope."""

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    session_uuid: StrictStr = Field(alias="sessionUUID")
    next_step: Optional[StrictStr | StrictInt] = Field(default=None, alias="nextStep")
    rui_url_key: Optional[str] = Field(default=None, alias="ruiURLKey")
    txnid: Optional[StrictStr] = None
    salt: Optional[StrictStr] = None
    mid: Optional[StrictStr] = None
    idmsdata: Optional[StrictStr] = None
    akdata: Any = None
    data: Optional[StrictStr] = None
    encrypted_code: Optional[StrictStr] = Field(default=None, alias="encryptedCode")
    error_code: Optional[StrictInt] = Field(default=None, alias="ec")

    @field_validator("session_uuid")
    @classmethod
    def _validate_session_uuid(cls, value: str) -> str:
        """Reject blank bridge session identifiers."""
        if not value.strip():
            raise ValueError("sessionUUID must not be blank")
        return value

    @field_validator(
        "txnid",
        "salt",
        "mid",
        "idmsdata",
        "data",
        "encrypted_code",
    )
    @classmethod
    def _validate_optional_non_empty_strings(
        cls, value: Optional[str]
    ) -> Optional[str]:
        """Reject present-but-blank optional bridge string fields."""
        if value is not None and not value.strip():
            raise ValueError("Bridge payload strings must not be blank")
        return value

    @field_validator("next_step")
    @classmethod
    def _validate_next_step(cls, value: Optional[str | int]) -> Optional[str | int]:
        """Reject blank next-step markers while allowing ints or strings."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("nextStep must not be blank")
        return value


@dataclass(frozen=True)
class BridgePushPayload:
    """Decoded bridge push metadata needed to bootstrap trusted-device prompts."""

    payload: dict[str, Any]
    session_uuid: str
    next_step: Optional[str] = None
    rui_url_key: Optional[str] = None
    txnid: Optional[str] = None
    salt: Optional[str] = None
    mid: Optional[str] = None
    idmsdata: Optional[str] = None
    akdata: Any = None
    data: Optional[str] = None
    encrypted_code: Optional[str] = None
    error_code: Optional[int] = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BridgePushPayload":
        """Validate and normalize one decoded bridge push payload."""
        try:
            validated = _BridgePushPayloadModel.model_validate(payload)
        except ValidationError as exc:
            raise PyiCloudTrustedDevicePromptException(
                "Malformed trusted-device bridge push payload."
            ) from exc

        if not validated.session_uuid:
            raise PyiCloudTrustedDevicePromptException(
                "Trusted-device bridge push payload is missing sessionUUID."
            )

        return cls(
            payload=payload,
            session_uuid=validated.session_uuid,
            next_step=(
                str(validated.next_step) if validated.next_step is not None else None
            ),
            rui_url_key=validated.rui_url_key,
            txnid=validated.txnid,
            salt=validated.salt,
            mid=validated.mid,
            idmsdata=validated.idmsdata,
            akdata=validated.akdata,
            data=validated.data,
            encrypted_code=validated.encrypted_code,
            error_code=validated.error_code,
        )


@dataclass
class TrustedDeviceBridgeState:
    """Ephemeral trusted-device bridge state."""

    connection_path: str
    push_token: str
    session_uuid: str
    websocket: Optional[_WebSocketLike]
    topic: str
    topics_by_hash: dict[str, str]
    source_app_id: Optional[str] = None
    next_step: Optional[str] = None
    rui_url_key: Optional[str] = None
    push_payload: dict[str, Any] = field(default_factory=dict)
    txnid: Optional[str] = None
    salt: Optional[str] = None
    mid: Optional[str] = None
    idmsdata: Optional[str] = None
    akdata: Any = None
    data: Optional[str] = None
    encrypted_code: Optional[str] = None
    error_code: Optional[int] = None

    def apply_push_payload(self, push_payload: BridgePushPayload) -> None:
        """Persist the latest bridge push metadata in the live bridge session."""

        self.push_payload = dict(push_payload.payload)
        self.session_uuid = push_payload.session_uuid
        self.next_step = push_payload.next_step
        self.rui_url_key = push_payload.rui_url_key
        self.txnid = push_payload.txnid
        self.salt = push_payload.salt
        self.mid = push_payload.mid
        self.idmsdata = push_payload.idmsdata
        self.akdata = push_payload.akdata
        self.data = push_payload.data
        self.encrypted_code = push_payload.encrypted_code
        self.error_code = push_payload.error_code

    @property
    def uses_legacy_trusted_device_verifier(self) -> bool:
        """Return whether Apple routed this bridge challenge to the legacy verifier."""

        return bool(self.txnid and self.txnid.endswith("_W"))


@dataclass(frozen=True)
class BridgeStepRequest:
    """Typed request body for Apple's bridge step endpoints."""

    session_uuid: str
    data: str
    push_token: str
    next_step: int
    idmsdata: Optional[str] = None
    akdata: Any = None

    def as_json(self) -> dict[str, Any]:
        """Serialize the step request into Apple's JSON envelope."""
        payload: dict[str, Any] = {
            "sessionUUID": self.session_uuid,
            "data": self.data,
            "ptkn": self.push_token,
            "nextStep": self.next_step,
        }
        if self.idmsdata is not None:
            payload["idmsdata"] = self.idmsdata
        if self.akdata is not None:
            payload["akdata"] = (
                json.dumps(self.akdata, separators=(",", ":"))
                if isinstance(self.akdata, dict)
                else self.akdata
            )
        return payload


@dataclass(frozen=True)
class BridgeCodeValidateRequest:
    """Typed request body for Apple's final bridge code validation endpoint."""

    session_uuid: str
    code: str

    def as_json(self) -> dict[str, str]:
        """Serialize the final bridge code-validation request body."""
        return {
            "sessionUUID": self.session_uuid,
            "code": self.code,
        }


@dataclass(frozen=True)
class _ConnectionResponse:
    """Decoded server response for the initial websocket bootstrap."""

    push_token_b64: str = ""
    status: int = 0
    server_timestamp_seconds: Optional[int] = None


@dataclass(frozen=True)
class _PushMessage:
    """Decoded APNS-style push frame from the bridge websocket."""

    topic: bytes
    message_id: int
    payload: bytes


@dataclass(frozen=True)
class _ChannelSubscriptionResponse:
    """Decoded response to the bridge topic subscription request."""

    message_id: int = 0
    status: int = 0
    retry_interval_seconds: int = 0
    topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AcknowledgementMessage:
    """Decoded acknowledgment frame emitted by Apple's bridge service."""

    topic: bytes
    message_id: int
    delivery_status: int = 0


@dataclass(frozen=True)
class _ServerMessage:
    """One websocket frame decoded into its known top-level message variants."""

    connection_response: Optional[_ConnectionResponse] = None
    push_message: Optional[_PushMessage] = None
    channel_subscription_response: Optional[_ChannelSubscriptionResponse] = None
    push_acknowledgment: Optional[_AcknowledgementMessage] = None
    field_numbers: tuple[int, ...] = ()


class _WebSocketLike(Protocol):
    """Protocol for the minimal websocket operations used by the bridge flow."""

    def send_binary(self, payload: bytes) -> None:
        """Send one binary websocket message."""

    def read_message(self) -> bytes:
        """Read one complete websocket message payload."""

    def close(self) -> None:
        """Close the websocket transport."""


class _InvalidNonceError(Exception):
    """Signal Apple's INVALID_NONCE response along with the server timestamp."""

    def __init__(self, server_timestamp_ms: int) -> None:
        """Capture the server timestamp returned with INVALID_NONCE."""
        super().__init__("Invalid nonce from bridge server.")
        self.server_timestamp_ms = server_timestamp_ms


class _BootArgsHTMLParser(HTMLParser):
    """Extract the JSON body from Apple's boot_args script tag."""

    def __init__(self) -> None:
        """Initialize parser state for the first matching boot_args script tag."""
        super().__init__()
        self._collecting = False
        self._found = False
        self._chunks: list[str] = []

    @property
    def payload(self) -> str:
        """Return the collected boot_args JSON text."""
        return "".join(self._chunks).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        """Start collecting data when the boot_args script tag is found."""
        if tag != "script" or self._found:
            return
        attr_map = {key: value for key, value in attrs}
        classes = (attr_map.get("class") or "").split()
        if "boot_args" in classes:
            self._collecting = True
            self._found = True

    def handle_endtag(self, tag: str) -> None:
        """Stop collecting when the current script tag closes."""
        if tag == "script" and self._collecting:
            self._collecting = False

    def handle_data(self, data: str) -> None:
        """Append script contents while the boot_args tag is active."""
        if self._collecting:
            self._chunks.append(data)


def parse_boot_args_html(html_text: str) -> Hsa2BootContext:
    """Extract HSA2 boot args from the HTML returned by GET /appleauth/auth."""

    parser = _BootArgsHTMLParser()
    parser.feed(html_text)
    parser.close()

    payload_text = parser.payload
    if not payload_text:
        raise PyiCloudTrustedDevicePromptException("Missing HSA2 boot args payload.")

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise PyiCloudTrustedDevicePromptException(
            "Malformed HSA2 boot args payload."
        ) from exc
    direct = payload.get("direct")
    if not isinstance(direct, dict):
        raise PyiCloudTrustedDevicePromptException("Missing HSA2 direct boot data.")

    two_sv = direct.get("twoSV")
    if not isinstance(two_sv, dict):
        two_sv = {}

    bridge_initiate_data = two_sv.get("bridgeInitiateData")
    if not isinstance(bridge_initiate_data, dict):
        bridge_initiate_data = {}

    phone_number_verification = bridge_initiate_data.get("phoneNumberVerification")
    if not isinstance(phone_number_verification, dict):
        phone_number_verification = {}

    auth_factors = two_sv.get("authFactors")
    if not isinstance(auth_factors, list):
        auth_factors = []

    source_app_id = two_sv.get("sourceAppId")
    if source_app_id is not None:
        source_app_id = str(source_app_id)

    return Hsa2BootContext(
        auth_initial_route=str(direct.get("authInitialRoute") or ""),
        has_trusted_devices=bool(direct.get("hasTrustedDevices")),
        auth_factors=tuple(
            factor for factor in auth_factors if isinstance(factor, str)
        ),
        bridge_initiate_data=dict(bridge_initiate_data),
        phone_number_verification=dict(phone_number_verification),
        source_app_id=source_app_id,
    )


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned protobuf varint."""
    if value < 0:
        raise ValueError("Negative varints are not supported.")
    parts = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            parts.append(to_write | 0x80)
        else:
            parts.append(to_write)
            return bytes(parts)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode one protobuf varint from a byte string and return the new offset."""
    value = 0
    shift = 0
    start_offset = offset
    while True:
        if offset >= len(data):
            raise PyiCloudTrustedDevicePromptException("Truncated protobuf varint.")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
        shift += 7
        # Guard against malformed wire data rather than silently accepting an
        # overlong varint from Apple's private bridge protocol.
        if shift > 63 or offset - start_offset >= 10:
            raise PyiCloudTrustedDevicePromptException("Malformed protobuf varint.")


def _encode_field(field_number: int, wire_type: int, value: bytes) -> bytes:
    """Encode one protobuf field header and payload."""
    return _encode_varint((field_number << 3) | wire_type) + value


def _encode_bytes_field(field_number: int, value: bytes) -> bytes:
    """Encode a length-delimited protobuf field."""
    return _encode_field(field_number, 2, _encode_varint(len(value)) + value)


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a UTF-8 string protobuf field."""
    return _encode_bytes_field(field_number, value.encode("utf-8"))


def _encode_uint32_field(field_number: int, value: int) -> bytes:
    """Encode an unsigned integer protobuf field."""
    return _encode_field(field_number, 0, _encode_varint(value))


def _decode_fields(data: bytes) -> dict[int, list[Any]]:
    """Decode a minimal subset of protobuf wire types into field lists."""
    offset = 0
    fields: dict[int, list[Any]] = {}
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            # Length-delimited fields must stay within the current message
            # bounds; otherwise the bridge frame is truncated or malformed.
            end_offset = offset + length
            if end_offset > len(data):
                raise PyiCloudTrustedDevicePromptException("Truncated protobuf field.")
            value = data[offset:end_offset]
            offset = end_offset
        else:
            raise PyiCloudTrustedDevicePromptException(
                f"Unsupported protobuf wire type: {wire_type}"
            )

        fields.setdefault(field_number, []).append(value)
    return fields


def _decode_connection_response(message: bytes) -> _ConnectionResponse:
    """Decode the server's websocket bootstrap response."""
    fields = _decode_fields(message)
    push_token_b64 = ""
    if fields.get(1):
        try:
            push_token_b64 = fields[1][0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise PyiCloudTrustedDevicePromptException(
                "Malformed bridge connection response push token."
            ) from exc
    status = int(fields.get(2, [0])[0])
    server_timestamp_seconds = None
    if fields.get(3):
        server_timestamp_seconds = int(fields[3][0])
    return _ConnectionResponse(
        push_token_b64=push_token_b64,
        status=status,
        server_timestamp_seconds=server_timestamp_seconds,
    )


def _decode_push_message(message: bytes) -> _PushMessage:
    """Decode one push-delivery frame from the bridge websocket."""
    fields = _decode_fields(message)
    topic = bytes(fields.get(1, [b""])[0])
    message_id = int(fields.get(2, [0])[0])
    payload = bytes(fields.get(4, [b""])[0])
    return _PushMessage(topic=topic, message_id=message_id, payload=payload)


def _decode_channel_subscription_response(
    message: bytes,
) -> _ChannelSubscriptionResponse:
    """Decode the server's response to the topic subscription message."""
    fields = _decode_fields(message)
    topics: list[str] = []

    payload_values = fields.get(1)
    if payload_values:
        payload_fields = _decode_fields(bytes(payload_values[0]))
        for app_response_value in payload_fields.get(1, []):
            app_response_fields = _decode_fields(bytes(app_response_value))
            topic_value = app_response_fields.get(1, [b""])[0]
            if isinstance(topic_value, bytes):
                topics.append(topic_value.decode("utf-8", "ignore"))

    return _ChannelSubscriptionResponse(
        message_id=int(fields.get(2, [0])[0]),
        status=int(fields.get(3, [0])[0]),
        retry_interval_seconds=int(fields.get(4, [0])[0]),
        topics=tuple(topic for topic in topics if topic),
    )


def _decode_acknowledgement_message(message: bytes) -> _AcknowledgementMessage:
    """Decode a push acknowledgment frame from the bridge websocket."""
    fields = _decode_fields(message)
    topic = bytes(fields.get(1, [b""])[0])
    message_id = int(fields.get(2, [0])[0])
    delivery_status = int(fields.get(3, [0])[0])
    return _AcknowledgementMessage(
        topic=topic,
        message_id=message_id,
        delivery_status=delivery_status,
    )


def _decode_server_message(message: bytes) -> _ServerMessage:
    """Decode all known top-level messages embedded in one websocket frame."""
    fields = _decode_fields(message)

    connection_response = None
    if fields.get(SERVER_MESSAGE_CONNECTION_RESPONSE):
        connection_response = _decode_connection_response(
            bytes(fields[SERVER_MESSAGE_CONNECTION_RESPONSE][0])
        )

    push_message = None
    if fields.get(SERVER_MESSAGE_PUSH):
        push_message = _decode_push_message(bytes(fields[SERVER_MESSAGE_PUSH][0]))

    channel_subscription_response = None
    if fields.get(SERVER_MESSAGE_CHANNEL_SUBSCRIPTION_RESPONSE):
        channel_subscription_response = _decode_channel_subscription_response(
            bytes(fields[SERVER_MESSAGE_CHANNEL_SUBSCRIPTION_RESPONSE][0])
        )

    push_acknowledgment = None
    if fields.get(SERVER_MESSAGE_PUSH_ACK):
        push_acknowledgment = _decode_acknowledgement_message(
            bytes(fields[SERVER_MESSAGE_PUSH_ACK][0])
        )

    return _ServerMessage(
        connection_response=connection_response,
        push_message=push_message,
        channel_subscription_response=channel_subscription_response,
        push_acknowledgment=push_acknowledgment,
        field_numbers=tuple(sorted(fields)),
    )


def _encode_connection_message(
    public_key: bytes, nonce: bytes, signature: bytes
) -> bytes:
    """Encode the initial bridge websocket bootstrap message."""
    connection_message = b"".join(
        [
            _encode_bytes_field(1, public_key),
            _encode_bytes_field(2, nonce),
            _encode_bytes_field(3, _encode_bridge_signature(signature)),
            _encode_bytes_field(
                5, _encode_uint32_field(1, NEW_CONNECTION_EXPIRATION_SECONDS)
            ),
        ]
    )
    return _encode_bytes_field(1, connection_message)


def _encode_bridge_signature(signature: bytes) -> bytes:
    """Wrap the DER ECDSA signature using Apple's bridge signature envelope."""

    if signature.startswith(BRIDGE_SIGNATURE_PREFIX):
        return signature
    return BRIDGE_SIGNATURE_PREFIX + signature


def _encode_web_filter_message(allowed_topics: list[str]) -> bytes:
    """Encode the topic subscription message sent after bridge connect."""
    filter_payload = b"".join(
        _encode_string_field(1, topic) for topic in allowed_topics
    )
    return _encode_bytes_field(3, filter_payload)


def _encode_ack_message(topic: bytes, message_id: int) -> bytes:
    """Encode the acknowledgment frame for one delivered push message."""
    ack_payload = b"".join(
        [
            _encode_bytes_field(1, topic),
            _encode_uint32_field(2, message_id),
        ]
    )
    return _encode_bytes_field(2, ack_payload)


def _topic_hash(topic: str) -> str:
    """Return Apple's websocket topic hash for a named APNS topic."""
    return hashlib.sha1(topic.encode("utf-8")).hexdigest()


def _topic_name(topic_bytes: bytes, topics_by_hash: Mapping[str, str]) -> str:
    """Resolve a hashed topic payload back to a readable topic name."""
    return topics_by_hash.get(topic_bytes.hex(), topic_bytes.decode("utf-8", "ignore"))


def _extract_json_payload(payload: bytes) -> dict[str, Any]:
    """Extract the JSON object embedded in one bridge push payload."""
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = payload.decode("utf-8", "ignore")

    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index, character in enumerate(text[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    raise PyiCloudTrustedDevicePromptException(
        "Could not decode the trusted-device bridge push payload."
    )


def _b64_to_hex(value: str) -> str:
    """Decode base64 bridge data and return it as lowercase hex."""
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).hex()
    except (ValueError, BinasciiError) as exc:
        raise ValueError("Malformed base64-encoded bridge payload.") from exc


def _hex_to_b64(value: str) -> str:
    """Encode hex bridge data as standard base64 text."""
    return base64.b64encode(bytes.fromhex(value)).decode("ascii")


def _build_nonce(timestamp_ms: int) -> bytes:
    """Build the nonce format expected by Apple's bridge bootstrap."""
    return b"\x00" + timestamp_ms.to_bytes(8, "big", signed=False) + os.urandom(8)


def _summarize_identifier(
    value: Optional[str], *, prefix: int = 8, empty: str = "<none>"
) -> str:
    """Shorten sensitive identifiers before logging them at debug level."""
    if not value:
        return empty
    if len(value) <= prefix:
        return value
    return f"{value[:prefix]}..."


def _resolve_websocket_host(boot_context: Hsa2BootContext) -> str:
    """Resolve the websocket host Apple expects for the bridge session."""
    bridge_data = boot_context.bridge_initiate_data
    web_socket_url = bridge_data.get("webSocketUrl")
    if isinstance(web_socket_url, str) and web_socket_url:
        if "://" in web_socket_url:
            parsed = urlparse(web_socket_url)
            if parsed.hostname:
                return parsed.hostname
        return web_socket_url.split("/", 1)[0]

    environment = bridge_data.get("apnsEnvironment")
    if isinstance(environment, str) and environment in WEBSOCKET_ENVIRONMENT_HOSTS:
        return WEBSOCKET_ENVIRONMENT_HOSTS[environment]

    raise PyiCloudTrustedDevicePromptException(
        "Missing HSA2 websocket host for the trusted-device bridge."
    )


def _resolve_apns_topic(boot_context: Hsa2BootContext) -> str:
    """Resolve the APNS topic Apple uses for trusted-device pushes."""
    topic = boot_context.bridge_initiate_data.get("apnsTopic")
    if isinstance(topic, str) and topic:
        return topic

    raise PyiCloudTrustedDevicePromptException(
        "Missing HSA2 APNS topic for the trusted-device bridge."
    )


def _derive_origin(auth_endpoint: str) -> str:
    """Derive the websocket Origin header from the auth endpoint URL."""
    parsed = urlparse(auth_endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise PyiCloudTrustedDevicePromptException(
            "Invalid auth endpoint for trusted-device bridge."
        )
    return f"{parsed.scheme}://{parsed.hostname}"


class _RawWebSocketClient:
    """Minimal websocket client for Apple's webcourier bridge."""

    def __init__(
        self,
        url: str,
        timeout: float,
        origin: str,
        user_agent: str,
    ) -> None:
        """Open a websocket connection and prepare buffered frame reads."""
        self._url = url
        self._timeout = timeout
        self._origin = origin
        self._user_agent = user_agent
        self._buffer = bytearray()
        self._socket = self._open()

    def _open(self) -> ssl.SSLSocket:
        """Perform the websocket HTTP upgrade and return the TLS socket."""
        parsed = urlparse(self._url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise PyiCloudTrustedDevicePromptException(
                f"Unsupported websocket URL: {self._url}"
            )

        port = parsed.port or 443
        resource = parsed.path or "/"
        if parsed.query:
            resource = f"{resource}?{parsed.query}"

        raw_socket = socket.create_connection((parsed.hostname, port), self._timeout)
        context = ssl.create_default_context()
        secure_socket = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
        secure_socket.settimeout(self._timeout)

        websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_headers = [
            f"GET {resource} HTTP/1.1",
            f"Host: {parsed.hostname}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Origin: {self._origin}",
            f"User-Agent: {self._user_agent}",
            "Sec-WebSocket-Version: 13",
            f"Sec-WebSocket-Key: {websocket_key}",
            "\r\n",
        ]
        secure_socket.sendall("\r\n".join(request_headers).encode("ascii"))

        response = self._read_http_response(secure_socket)
        status_line, _, headers_text = response.partition("\r\n")
        if " 101 " not in status_line:
            raise PyiCloudTrustedDevicePromptException(
                f"Websocket upgrade failed: {status_line}"
            )

        headers: dict[str, str] = {}
        for line in headers_text.split("\r\n"):
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        expected_accept = base64.b64encode(
            hashlib.sha1((websocket_key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise PyiCloudTrustedDevicePromptException(
                "Invalid websocket accept header from bridge server."
            )

        return secure_socket

    def _read_http_response(self, sock: ssl.SSLSocket) -> str:
        """Read the HTTP upgrade response headers from the websocket socket."""
        while b"\r\n\r\n" not in self._buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise PyiCloudTrustedDevicePromptException(
                    "Unexpected EOF during websocket handshake."
                )
            self._buffer.extend(chunk)

        marker = self._buffer.find(b"\r\n\r\n") + 4
        data = bytes(self._buffer[:marker]).decode("iso-8859-1")
        del self._buffer[:marker]
        return data

    def _read_exact(self, size: int) -> bytes:
        """Read exactly ``size`` buffered bytes from the websocket socket."""
        while len(self._buffer) < size:
            chunk = self._socket.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise PyiCloudTrustedDevicePromptException(
                    "Unexpected EOF while reading websocket frame."
                )
            self._buffer.extend(chunk)

        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        """Send one masked websocket frame to Apple's bridge server."""
        first_byte = 0x80 | opcode
        mask_key = os.urandom(4)
        length = len(payload)

        header = bytearray([first_byte])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        masked_payload = bytes(
            byte ^ mask_key[index % 4] for index, byte in enumerate(payload)
        )
        self._socket.sendall(bytes(header) + mask_key + masked_payload)

    def send_binary(self, payload: bytes) -> None:
        """Send one binary websocket message payload."""
        self._send_frame(OPCODE_BINARY, payload)

    def read_message(self) -> bytes:
        """Read one complete websocket message, handling control frames inline."""
        fragments: list[bytes] = []
        opcode: Optional[int] = None

        while True:
            first_byte, second_byte = self._read_exact(2)
            frame_opcode = first_byte & 0x0F
            finished = bool(first_byte & 0x80)
            masked = bool(second_byte & 0x80)
            payload_length = second_byte & 0x7F

            if payload_length == 126:
                payload_length = struct.unpack("!H", self._read_exact(2))[0]
            elif payload_length == 127:
                payload_length = struct.unpack("!Q", self._read_exact(8))[0]

            mask_key = self._read_exact(4) if masked else b""
            payload = self._read_exact(payload_length)
            if masked:
                payload = bytes(
                    byte ^ mask_key[index % 4] for index, byte in enumerate(payload)
                )

            if frame_opcode == OPCODE_CLOSE:
                raise PyiCloudTrustedDevicePromptException(
                    "Bridge websocket closed before delivering a prompt."
                )
            if frame_opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if frame_opcode == OPCODE_PONG:
                continue

            if frame_opcode != 0:
                opcode = frame_opcode
            fragments.append(payload)
            if finished:
                if opcode not in (0x1, OPCODE_BINARY):
                    raise PyiCloudTrustedDevicePromptException(
                        f"Unsupported websocket opcode: {opcode}"
                    )
                return b"".join(fragments)

    def close(self) -> None:
        """Attempt a clean websocket close and always close the socket object."""
        if getattr(self, "_socket", None) is None:
            return
        try:
            self._send_frame(OPCODE_CLOSE, b"")
        except OSError:
            pass
        finally:
            try:
                self._socket.close()
            except OSError:
                pass


class TrustedDeviceBridgeBootstrapper:
    """Bootstrap the trusted-device bridge flow captured in Apple's browser client."""

    def __init__(
        self,
        *,
        timeout: float = WEBSOCKET_TIMEOUT_SECONDS,
        websocket_factory: Optional[
            Callable[[str, float, str, str], _WebSocketLike]
        ] = None,
        prover_factory: Optional[Callable[[], TrustedDeviceBridgeProver]] = None,
    ) -> None:
        """Configure websocket and prover factories for bridge operations."""
        self.timeout = timeout
        self._websocket_factory = websocket_factory or _RawWebSocketClient
        self._prover_factory = prover_factory or TrustedDeviceBridgeProver

    def start(
        self,
        *,
        session: Any,
        auth_endpoint: str,
        headers: Mapping[str, str],
        boot_context: Hsa2BootContext,
        user_agent: str,
    ) -> TrustedDeviceBridgeState:
        """Bootstrap Apple's trusted-device bridge until the first prompt payload arrives."""
        topic = _resolve_apns_topic(boot_context)
        websocket_host = _resolve_websocket_host(boot_context)
        origin = _derive_origin(auth_endpoint)
        topics_by_hash = {_topic_hash(topic): topic}
        source_app_id = boot_context.source_app_id
        public_key, private_key = self._generate_keypair()

        LOGGER.debug(
            "Bootstrapping trusted-device bridge: auth_endpoint=%s websocket_host=%s topic=%s source_app_id=%s",
            auth_endpoint,
            websocket_host,
            topic,
            source_app_id,
        )

        timestamp_ms: Optional[int] = None
        last_error: Optional[Exception] = None
        for _ in range(2):
            nonce = _build_nonce(timestamp_ms or int(time.time() * 1000))
            signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
            connection_message = _encode_connection_message(
                public_key, nonce, signature
            )
            connection_path = connection_message.hex()
            websocket_url = f"wss://{websocket_host}/v2/{connection_path}"
            LOGGER.debug(
                "Opening trusted-device websocket: host=%s bootstrapPayloadLen=%d",
                websocket_host,
                len(connection_path),
            )
            websocket = self._websocket_factory(
                websocket_url,
                self.timeout,
                origin,
                user_agent,
            )
            keep_websocket_open = False

            try:
                push_token = self._wait_for_push_token(websocket)
                push_token_hex = push_token.hex()
                LOGGER.debug(
                    "Trusted-device bridge connected; received push token (%d bytes)",
                    len(push_token),
                )
                websocket.send_binary(_encode_web_filter_message([topic]))
                LOGGER.debug("Sent trusted-device webFilterMessage for topic=%s", topic)

                session_uuid = self._generate_session_uuid()
                bridge_headers = dict(headers)
                if source_app_id:
                    bridge_headers["X-Apple-App-Id"] = source_app_id

                LOGGER.debug(
                    "Posting trusted-device bridge step 0 with sessionUUID=%s ptknLen=%d",
                    _summarize_identifier(session_uuid),
                    len(push_token_hex),
                )
                # Apple's browser posts step 0 immediately after obtaining the push
                # token. Waiting for the first push before posting step 0 causes the
                # bridge flow to stall.
                self._post_bridge_step0(
                    session=session,
                    auth_endpoint=auth_endpoint,
                    headers=bridge_headers,
                    session_uuid=session_uuid,
                    push_token=push_token_hex,
                )

                push_payload = self._wait_for_bridge_push(
                    websocket, topic, topics_by_hash
                )
                LOGGER.debug(
                    "Received trusted-device bridge payload: sessionUUID=%s nextStep=%s ruiURLKey=%s",
                    _summarize_identifier(push_payload.session_uuid),
                    push_payload.next_step,
                    push_payload.rui_url_key,
                )
                if push_payload.session_uuid != session_uuid:
                    raise PyiCloudTrustedDevicePromptException(
                        "Trusted-device bridge returned a mismatched session UUID."
                    )

                bridge_state = TrustedDeviceBridgeState(
                    connection_path=connection_path,
                    push_token=push_token_hex,
                    session_uuid=session_uuid,
                    websocket=websocket,
                    topic=topic,
                    topics_by_hash=dict(topics_by_hash),
                    source_app_id=source_app_id,
                )
                bridge_state.apply_push_payload(push_payload)
                keep_websocket_open = True
                return bridge_state
            except _InvalidNonceError as exc:
                timestamp_ms = exc.server_timestamp_ms
                last_error = exc
                LOGGER.debug(
                    "Trusted-device bridge received INVALID_NONCE; retrying with server timestamp %s",
                    timestamp_ms,
                )
            except (OSError, socket.timeout, ssl.SSLError) as exc:
                last_error = exc
                LOGGER.debug(
                    "Trusted-device websocket transport error during bootstrap.",
                    exc_info=True,
                )
                break
            except PyiCloudTrustedDevicePromptException as exc:
                last_error = exc
                LOGGER.debug(
                    "Trusted-device bridge bootstrap failed before completion.",
                    exc_info=True,
                )
                break
            finally:
                if not keep_websocket_open:
                    websocket.close()

        raise PyiCloudTrustedDevicePromptException(
            "Failed to bootstrap the trusted-device bridge prompt."
        ) from last_error

    def _generate_keypair(self) -> tuple[bytes, ec.EllipticCurvePrivateKey]:
        """Generate the ephemeral P-256 keypair used for websocket bootstrap."""
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        return public_key, private_key

    def _generate_session_uuid(self) -> str:
        """Generate the browser-style bridge session UUID string."""
        return f"{uuid.uuid4()}-{int(time.time())}"

    def close(self, bridge_state: Optional[TrustedDeviceBridgeState]) -> None:
        """Close and detach the websocket associated with an active bridge session."""

        if bridge_state is None:
            return
        websocket = bridge_state.websocket
        bridge_state.websocket = None
        if websocket is None:
            return
        try:
            websocket.close()
        except OSError:
            LOGGER.debug(
                "Trusted-device bridge websocket close failed.",
                exc_info=True,
            )

    def validate_code(
        self,
        *,
        session: Any,
        auth_endpoint: str,
        headers: Mapping[str, str],
        bridge_state: TrustedDeviceBridgeState,
        code: str,
    ) -> bool:
        """Run Apple's bridge-specific trusted-device verification flow."""

        websocket = bridge_state.websocket
        if websocket is None:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge session is not active."
            )
        if bridge_state.uses_legacy_trusted_device_verifier:
            raise PyiCloudTrustedDeviceVerificationException(
                "Legacy trusted-device verification should bypass the bridge verifier."
            )
        if bridge_state.next_step not in {"2", 2}:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge is not ready for step 2 verification."
            )
        if not bridge_state.salt:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge payload is missing the step-2 salt."
            )

        prover = self._prover_factory()
        bridge_headers = self._bridge_headers(headers, bridge_state)

        try:
            LOGGER.debug(
                "Starting trusted-device bridge code verification: sessionUUID=%s nextStep=%s txnid=%s",
                _summarize_identifier(bridge_state.session_uuid),
                bridge_state.next_step,
                _summarize_identifier(bridge_state.txnid, prefix=12),
            )

            prover.init_with_salt(bridge_state.salt, code)
            message1 = prover.get_message1()
            LOGGER.debug(
                "Posting trusted-device bridge step 2 with sessionUUID=%s",
                _summarize_identifier(bridge_state.session_uuid),
            )
            self._post_bridge_step(
                session=session,
                auth_endpoint=auth_endpoint,
                headers=bridge_headers,
                bridge_state=bridge_state,
                next_step=2,
                data=_hex_to_b64(message1),
                idmsdata=bridge_state.idmsdata,
                akdata=bridge_state.akdata,
            )

            step4_payload = self._wait_for_bridge_push(
                websocket,
                bridge_state.topic,
                bridge_state.topics_by_hash,
            )
            self._apply_expected_step4_push(bridge_state, step4_payload)

            if not bridge_state.data:
                raise PyiCloudTrustedDeviceVerificationException(
                    "Trusted-device bridge step 4 payload is missing prover data."
                )
            try:
                step4_data = base64.b64decode(
                    bridge_state.data.encode("ascii"), validate=True
                ).decode("utf-8")
                bridge_message1_b64, bridge_message2_b64 = step4_data.split("_", 1)
                bridge_message1_hex = _b64_to_hex(bridge_message1_b64)
                bridge_message2_hex = _b64_to_hex(bridge_message2_b64)
            except (ValueError, UnicodeDecodeError, BinasciiError) as exc:
                raise PyiCloudTrustedDeviceVerificationException(
                    "Trusted-device bridge step 4 payload is malformed."
                ) from exc

            LOGGER.debug(
                "Processing trusted-device bridge step 4 payload for sessionUUID=%s",
                _summarize_identifier(bridge_state.session_uuid),
            )
            try:
                message2 = prover.process_message1(bridge_message1_hex)
            except ValueError as exc:
                raise PyiCloudTrustedDeviceVerificationException(
                    "Trusted-device bridge step 4 payload is malformed."
                ) from exc
            try:
                prover.process_message2(bridge_message2_hex)
            except ValueError:
                LOGGER.debug(
                    "Trusted-device bridge prover rejected the step-4 confirmation for sessionUUID=%s",
                    _summarize_identifier(bridge_state.session_uuid),
                )
                return False

            LOGGER.debug(
                "Posting trusted-device bridge step 4 with sessionUUID=%s",
                _summarize_identifier(bridge_state.session_uuid),
            )
            self._post_bridge_step(
                session=session,
                auth_endpoint=auth_endpoint,
                headers=bridge_headers,
                bridge_state=bridge_state,
                next_step=4,
                data=_hex_to_b64(message2),
                idmsdata=bridge_state.idmsdata,
                akdata=bridge_state.akdata,
            )

            final_payload = self._wait_for_bridge_push(
                websocket,
                bridge_state.topic,
                bridge_state.topics_by_hash,
            )
            self._apply_final_bridge_push(bridge_state, final_payload)

            if not bridge_state.encrypted_code:
                raise PyiCloudTrustedDeviceVerificationException(
                    "Trusted-device bridge final payload is missing encryptedCode."
                )

            LOGGER.debug(
                "Decrypting trusted-device bridge code for sessionUUID=%s",
                _summarize_identifier(bridge_state.session_uuid),
            )
            try:
                derived_code = prover.decrypt_message(bridge_state.encrypted_code)
            except ValueError as exc:
                raise PyiCloudTrustedDeviceVerificationException(
                    "Failed to decrypt the trusted-device bridge validation code."
                ) from exc

            verify_response = self._post_bridge_code_validate(
                session=session,
                auth_endpoint=auth_endpoint,
                headers=bridge_headers,
                bridge_state=bridge_state,
                code=derived_code,
            )
            verification_succeeded = (
                verify_response.status_code != HTTP_STATUS_PRECONDITION_FAILED
            )

            completion_step = 6 if bridge_state.next_step in {"6", 6} else 4
            LOGGER.debug(
                "Posting trusted-device bridge completion step %s with sessionUUID=%s verifyStatus=%s",
                completion_step,
                _summarize_identifier(bridge_state.session_uuid),
                verify_response.status_code,
            )
            self._post_bridge_step(
                session=session,
                auth_endpoint=auth_endpoint,
                headers=bridge_headers,
                bridge_state=bridge_state,
                next_step=completion_step,
                data=BRIDGE_DONE_DATA_B64,
                idmsdata=bridge_state.idmsdata,
                akdata=bridge_state.akdata,
            )
            return verification_succeeded
        except PyiCloudTrustedDevicePromptException as exc:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge verification failed while waiting for the next bridge push."
            ) from exc
        except (OSError, socket.timeout, ssl.SSLError) as exc:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge verification failed due to a websocket transport error."
            ) from exc
        finally:
            self.close(bridge_state)

    def _wait_for_push_token(self, websocket: _WebSocketLike) -> bytes:
        """Wait for the bridge connection response that carries the push token."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = websocket.read_message()
            server_message = _decode_server_message(message)
            connection_response = server_message.connection_response
            if connection_response is None:
                LOGGER.debug(
                    "Ignoring non-connection websocket frame while waiting for push token; fields=%s",
                    server_message.field_numbers,
                )
                continue

            if (
                connection_response.status == STATUS_OK
                and connection_response.push_token_b64
            ):
                try:
                    return base64.b64decode(
                        connection_response.push_token_b64.encode("ascii"),
                        validate=True,
                    )
                except (ValueError, BinasciiError) as exc:
                    raise PyiCloudTrustedDevicePromptException(
                        "Malformed bridge push token."
                    ) from exc

            if (
                connection_response.status == STATUS_INVALID_NONCE
                and connection_response.server_timestamp_seconds is not None
            ):
                raise _InvalidNonceError(
                    connection_response.server_timestamp_seconds * 1000
                )

            LOGGER.debug(
                "Trusted-device bridge connection response returned status=%s",
                connection_response.status,
            )
            raise PyiCloudTrustedDevicePromptException(
                f"Bridge server returned status {connection_response.status}."
            )

        raise PyiCloudTrustedDevicePromptException(
            "Timed out waiting for the bridge push token."
        )

    def _wait_for_bridge_push(
        self,
        websocket: _WebSocketLike,
        topic: str,
        topics_by_hash: Mapping[str, str],
    ) -> BridgePushPayload:
        """Wait for, acknowledge, and decode the next relevant bridge push."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = websocket.read_message()
            server_message = _decode_server_message(message)
            if server_message.channel_subscription_response is not None:
                channel_response = server_message.channel_subscription_response
                LOGGER.debug(
                    "Received channel subscription response during bridge bootstrap: messageId=%s status=%s retryIntervalSeconds=%s topics=%s",
                    channel_response.message_id,
                    channel_response.status,
                    channel_response.retry_interval_seconds,
                    channel_response.topics,
                )
                if channel_response.status != STATUS_OK:
                    raise PyiCloudTrustedDevicePromptException(
                        "Trusted-device bridge topic subscription failed "
                        f"(status {channel_response.status})."
                    )

            if server_message.push_acknowledgment is not None:
                push_ack = server_message.push_acknowledgment
                LOGGER.debug(
                    "Received bridge push acknowledgment during bootstrap: messageId=%s deliveryStatus=%s topic=%s",
                    push_ack.message_id,
                    push_ack.delivery_status,
                    _topic_name(push_ack.topic, topics_by_hash),
                )

            push_message = server_message.push_message
            if push_message is None:
                LOGGER.debug(
                    "Ignoring non-push websocket frame during trusted-device bootstrap; fields=%s",
                    server_message.field_numbers,
                )
                continue

            websocket.send_binary(
                _encode_ack_message(push_message.topic, push_message.message_id)
            )
            LOGGER.debug(
                "Acknowledged trusted-device push message id=%s topic=%s",
                push_message.message_id,
                _topic_name(push_message.topic, topics_by_hash),
            )

            if _topic_name(push_message.topic, topics_by_hash) != topic:
                continue

            payload = _extract_json_payload(push_message.payload)
            return BridgePushPayload.from_payload(payload)

        raise PyiCloudTrustedDevicePromptException(
            "Timed out waiting for the trusted-device bridge payload."
        )

    def _apply_bridge_push(
        self,
        bridge_state: TrustedDeviceBridgeState,
        push_payload: BridgePushPayload,
    ) -> None:
        """Validate a generic bridge push and merge it into the active state."""
        if push_payload.session_uuid != bridge_state.session_uuid:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge returned a mismatched session UUID."
            )
        LOGGER.debug(
            "Decoded trusted-device bridge payload: sessionUUID=%s nextStep=%s txnid=%s ec=%s has_data=%s has_encryptedCode=%s",
            _summarize_identifier(push_payload.session_uuid),
            push_payload.next_step,
            _summarize_identifier(push_payload.txnid, prefix=12),
            push_payload.error_code,
            bool(push_payload.data),
            bool(push_payload.encrypted_code),
        )
        if push_payload.error_code not in (None, 0):
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge returned an error push "
                f"(nextStep={push_payload.next_step!r}, ec={push_payload.error_code})."
            )
        bridge_state.apply_push_payload(push_payload)

    def _apply_expected_step4_push(
        self,
        bridge_state: TrustedDeviceBridgeState,
        push_payload: BridgePushPayload,
    ) -> None:
        """Require the post-step-2 bridge push to contain step-4 prover data."""
        self._apply_bridge_push(bridge_state, push_payload)
        if bridge_state.next_step != "4" or not bridge_state.data:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge returned an unexpected post-step-2 payload."
            )
        LOGGER.debug(
            "Received trusted-device bridge payload: sessionUUID=%s nextStep=%s txnid=%s",
            _summarize_identifier(bridge_state.session_uuid),
            bridge_state.next_step,
            _summarize_identifier(bridge_state.txnid, prefix=12),
        )

    def _apply_final_bridge_push(
        self,
        bridge_state: TrustedDeviceBridgeState,
        push_payload: BridgePushPayload,
    ) -> None:
        """Require the final bridge push to contain the encrypted validation code."""
        self._apply_bridge_push(bridge_state, push_payload)
        # Apple's bridge can finish with either:
        # - nextStep=6 plus encryptedCode
        # - nextStep=4 plus encryptedCode
        # The browser routes both shapes into final code validation.
        if (
            bridge_state.next_step not in {"4", "6", 4, 6}
            or not bridge_state.encrypted_code
        ):
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge returned an unexpected final payload."
            )
        LOGGER.debug(
            "Received trusted-device bridge final payload: sessionUUID=%s nextStep=%s txnid=%s",
            _summarize_identifier(bridge_state.session_uuid),
            bridge_state.next_step,
            _summarize_identifier(bridge_state.txnid, prefix=12),
        )

    def _bridge_headers(
        self,
        headers: Mapping[str, str],
        bridge_state: TrustedDeviceBridgeState,
    ) -> dict[str, str]:
        """Build the auth headers used for bridge-specific HTTP requests."""
        bridge_headers = dict(headers)
        if bridge_state.source_app_id:
            bridge_headers["X-Apple-App-Id"] = bridge_state.source_app_id
        return bridge_headers

    def _bridge_step_json(
        self,
        *,
        bridge_state: TrustedDeviceBridgeState,
        next_step: int,
        data: str,
        idmsdata: Optional[str],
        akdata: Any,
    ) -> dict[str, Any]:
        """Build the JSON payload for one bridge step POST."""
        return BridgeStepRequest(
            session_uuid=bridge_state.session_uuid,
            data=data,
            push_token=bridge_state.push_token,
            next_step=next_step,
            idmsdata=idmsdata,
            akdata=akdata,
        ).as_json()

    def _post_bridge_step(
        self,
        *,
        session: Any,
        auth_endpoint: str,
        headers: Mapping[str, str],
        bridge_state: TrustedDeviceBridgeState,
        next_step: int,
        data: str,
        idmsdata: Optional[str],
        akdata: Any,
    ) -> Any:
        """POST one bridge step and enforce the small set of valid statuses."""
        response = session.request_raw(
            "POST",
            f"{auth_endpoint}{BRIDGE_STEP_PATH_TEMPLATE.format(step=next_step)}",
            json=self._bridge_step_json(
                bridge_state=bridge_state,
                next_step=next_step,
                data=data,
                idmsdata=idmsdata,
                akdata=akdata,
            ),
            headers=headers,
        )
        if response.status_code not in {
            HTTP_STATUS_OK,
            HTTP_STATUS_NO_CONTENT,
            HTTP_STATUS_CONFLICT,
        }:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge step "
                f"{next_step} failed with status {response.status_code}."
            )
        return response

    def _post_bridge_step0(
        self,
        *,
        session: Any,
        auth_endpoint: str,
        headers: Mapping[str, str],
        session_uuid: str,
        push_token: str,
    ) -> Any:
        """POST bridge step 0 immediately after obtaining the push token."""
        response = session.request_raw(
            "POST",
            f"{auth_endpoint}{BRIDGE_STEP_PATH}",
            json={
                "sessionUUID": session_uuid,
                "ptkn": push_token,
            },
            headers=headers,
        )
        if response.status_code not in {
            HTTP_STATUS_OK,
            HTTP_STATUS_NO_CONTENT,
            HTTP_STATUS_CONFLICT,
        }:
            raise PyiCloudTrustedDevicePromptException(
                "Trusted-device bridge step 0 failed with status "
                f"{response.status_code}."
            )
        return response

    def _post_bridge_code_validate(
        self,
        *,
        session: Any,
        auth_endpoint: str,
        headers: Mapping[str, str],
        bridge_state: TrustedDeviceBridgeState,
        code: str,
    ) -> Any:
        """POST the decrypted bridge code to Apple's final validation endpoint."""
        response = session.request_raw(
            "POST",
            f"{auth_endpoint}{BRIDGE_CODE_VALIDATE_PATH}",
            json=BridgeCodeValidateRequest(
                session_uuid=bridge_state.session_uuid,
                code=code,
            ).as_json(),
            headers=headers,
        )
        if response.status_code not in {
            HTTP_STATUS_OK,
            HTTP_STATUS_NO_CONTENT,
            HTTP_STATUS_CONFLICT,
            HTTP_STATUS_PRECONDITION_FAILED,
        }:
            raise PyiCloudTrustedDeviceVerificationException(
                "Trusted-device bridge code validation failed with status "
                f"{response.status_code}."
            )
        return response
