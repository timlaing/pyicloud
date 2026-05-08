"""Tests for the HSA2 trusted-device bridge helpers."""

from __future__ import annotations

import base64
import json
import socket
from binascii import unhexlify
from typing import Callable
from unittest.mock import MagicMock, call

import pytest

import pyicloud.hsa2_bridge as bridge_module
from pyicloud.exceptions import (
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)
from pyicloud.hsa2_bridge import (
    BRIDGE_DONE_DATA_B64,
    BridgePushPayload,
    Hsa2BootContext,
    TrustedDeviceBridgeBootstrapper,
    _encode_ack_message,
    _encode_bytes_field,
    _encode_string_field,
    _encode_uint32_field,
    _encode_web_filter_message,
    _extract_json_payload,
    _hex_to_b64,
    _topic_hash,
    parse_boot_args_html,
)
from pyicloud.hsa2_bridge_prover import (
    TrustedDeviceBridgeProver,
    _TrustedDeviceBridgeServerProver,
)


class _FakeWebSocket:
    def __init__(
        self,
        messages: list[bytes | Exception],
        *,
        on_read: Callable[[int], None] | None = None,
    ) -> None:
        self._messages = list(messages)
        self._on_read = on_read
        self.sent_messages: list[bytes] = []
        self.closed = False
        self.read_count = 0

    def send_binary(self, payload: bytes) -> None:
        self.sent_messages.append(payload)

    def read_message(self) -> bytes:
        self.read_count += 1
        if self._on_read is not None:
            self._on_read(self.read_count)
        message = self._messages.pop(0)
        if isinstance(message, Exception):
            raise message
        return message

    def close(self) -> None:
        self.closed = True


class _FakePrivateKey:
    def sign(self, nonce: bytes, _algorithm: object) -> bytes:
        return b"signature-for-" + nonce[:4]


def _encode_connection_response(push_token: bytes) -> bytes:
    payload = b"".join(
        [
            _encode_string_field(1, base64.b64encode(push_token).decode("ascii")),
            _encode_uint32_field(2, 0),
        ]
    )
    return _encode_bytes_field(1, payload)


def _encode_connection_response_with_token_b64(push_token_b64: str) -> bytes:
    payload = b"".join(
        [
            _encode_string_field(1, push_token_b64),
            _encode_uint32_field(2, 0),
        ]
    )
    return _encode_bytes_field(1, payload)


def _encode_push_message(
    topic: str, payload: dict[str, object], message_id: int
) -> bytes:
    topic_bytes = bytes.fromhex(_topic_hash(topic))
    body = b"".join(
        [
            _encode_bytes_field(1, topic_bytes),
            _encode_uint32_field(2, message_id),
            _encode_bytes_field(4, json.dumps(payload).encode("utf-8")),
        ]
    )
    return _encode_bytes_field(2, body)


def _encode_channel_subscription_response(topic: str, message_id: int = 1) -> bytes:
    channel_response = b"".join(
        [
            _encode_string_field(1, topic),
            _encode_bytes_field(2, _encode_bytes_field(1, b"channel-id")),
        ]
    )
    payload = _encode_bytes_field(1, channel_response)
    body = b"".join(
        [
            _encode_bytes_field(1, payload),
            _encode_uint32_field(2, message_id),
            _encode_uint32_field(3, 0),
        ]
    )
    return _encode_bytes_field(3, body)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
        shift += 7


def _decode_fields(data: bytes) -> dict[int, list[int | bytes]]:
    offset = 0
    fields: dict[int, list[int | bytes]] = {}
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
        else:
            raise AssertionError(f"Unexpected wire type {wire_type}")

        fields.setdefault(field_number, []).append(value)
    return fields


def _boot_context(topic: str = "com.apple.idmsauthwidget") -> Hsa2BootContext:
    return Hsa2BootContext(
        auth_initial_route="auth/bridge/step",
        has_trusted_devices=True,
        auth_factors=("web_piggybacking", "sms"),
        bridge_initiate_data={
            "apnsTopic": topic,
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
        source_app_id="1159",
    )


def _response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    return response


def test_parse_boot_args_html_extracts_bridge_context() -> None:
    """Request-5 style boot args should yield the bridge routing metadata."""

    html = """
    <html>
      <script type="application/json" class="boot_args">
        {
          "direct": {
            "authInitialRoute": "auth/bridge/step",
            "hasTrustedDevices": true,
            "twoSV": {
              "authFactors": ["web_piggybacking", "robocall", "sms", "generatedcode"],
              "sourceAppId": 1159,
              "bridgeInitiateData": {
                "apnsTopic": "com.apple.idmsauthwidget",
                "apnsEnvironment": "prod",
                "webSocketUrl": "websocket.push.apple.com",
                "phoneNumberVerification": {
                  "trustedPhoneNumber": {
                    "id": 3,
                    "nonFTEU": false,
                    "pushMode": "sms"
                  }
                }
              }
            }
          }
        }
      </script>
    </html>
    """

    boot_context = parse_boot_args_html(html)

    assert boot_context.auth_initial_route == "auth/bridge/step"
    assert boot_context.has_trusted_devices is True
    assert boot_context.auth_factors == (
        "web_piggybacking",
        "robocall",
        "sms",
        "generatedcode",
    )
    assert boot_context.bridge_initiate_data["webSocketUrl"] == (
        "websocket.push.apple.com"
    )
    assert boot_context.phone_number_verification["trustedPhoneNumber"]["id"] == 3
    assert boot_context.source_app_id == "1159"


def test_parse_boot_args_html_accepts_reordered_script_attributes() -> None:
    """boot_args extraction should not depend on one exact script tag string."""

    html = """
    <html>
      <script nonce="abc123" class="boot_args extra" type="application/json" data-test="1">
        {
          "direct": {
            "authInitialRoute": "auth/bridge/step",
            "hasTrustedDevices": true,
            "twoSV": {
              "authFactors": ["web_piggybacking", "sms"],
              "sourceAppId": 1159,
              "bridgeInitiateData": {
                "apnsTopic": "com.apple.idmsauthwidget",
                "apnsEnvironment": "prod",
                "webSocketUrl": "websocket.push.apple.com"
              }
            }
          }
        }
      </script>
    </html>
    """

    boot_context = parse_boot_args_html(html)

    assert boot_context.auth_initial_route == "auth/bridge/step"
    assert boot_context.has_trusted_devices is True
    assert boot_context.bridge_initiate_data["webSocketUrl"] == (
        "websocket.push.apple.com"
    )


def test_read_varint_rejects_malformed_overlong_varint() -> None:
    """Malformed bridge varints should fail immediately instead of reading forever."""

    with pytest.raises(
        PyiCloudTrustedDevicePromptException,
        match="Malformed protobuf varint",
    ):
        bridge_module._read_varint(b"\x80" * 10, 0)


def test_decode_fields_rejects_truncated_length_delimited_field() -> None:
    """Length-delimited bridge fields must fit inside the current frame."""

    with pytest.raises(
        PyiCloudTrustedDevicePromptException,
        match="Truncated protobuf field",
    ):
        bridge_module._decode_fields(b"\x0a\x05abc")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"sessionUUID": 123}, "Malformed trusted-device bridge push payload"),
        ({"sessionUUID": "   "}, "Malformed trusted-device bridge push payload"),
        (
            {"sessionUUID": "bridge-session", "nextStep": "   "},
            "Malformed trusted-device bridge push payload",
        ),
        (
            {"sessionUUID": "bridge-session", "encryptedCode": "   "},
            "Malformed trusted-device bridge push payload",
        ),
        (
            {"sessionUUID": "bridge-session", "ec": "oops"},
            "Malformed trusted-device bridge push payload",
        ),
    ],
)
def test_bridge_push_payload_rejects_malformed_fields(
    payload: dict[str, object], message: str
) -> None:
    """Bridge push validation should reject coerced or blank protocol fields."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match=message):
        BridgePushPayload.from_payload(payload)


def test_bridge_push_payload_preserves_unknown_extra_fields() -> None:
    """Unknown Apple bridge fields should survive strict validation unchanged."""

    payload = BridgePushPayload.from_payload(
        {
            "sessionUUID": "bridge-session",
            "nextStep": "2",
            "extraField": {"foo": "bar"},
        }
    )

    assert payload.session_uuid == "bridge-session"
    assert payload.payload["extraField"] == {"foo": "bar"}


def test_extract_json_payload_finds_embedded_json() -> None:
    """Request-8 style binary payloads should yield the embedded JSON envelope."""

    expected_payload = {
        "sessionUUID": "bridge-session",
        "nextStep": "2",
        "ruiURLKey": "hsa2TwoFactorAuthApprovalFlowUrl",
    }
    noisy_payload = (
        b"\x12\xa8\x07\x00"
        + json.dumps(expected_payload).encode("utf-8")
        + b"\x18\x00\x01"
    )

    assert _extract_json_payload(noisy_payload) == expected_payload


def test_trusted_device_bridge_prover_roundtrip() -> None:
    """The Python prover port should match the worker's SPAKE2+/AES-GCM flow."""

    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    prover = TrustedDeviceBridgeProver()
    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64=salt_b64)

    prover.init_with_salt(salt_b64, "050044")
    client_message1 = prover.get_message1()
    server_message1 = server.get_message1()
    server_message2 = server.process_message1(client_message1)
    client_message2 = prover.process_message1(server_message1)
    server_key = server.verify_message2(client_message2)
    client_key = prover.process_message2(server_message2)["key"]

    assert prover.is_verified() is True
    assert client_key == server_key
    encrypted_code = server.encrypt_message("derived-device-code")
    assert prover.decrypt_message(encrypted_code) == "derived-device-code"


def test_trusted_device_bridge_prover_retries_zero_ephemeral_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ephemeral prover scalars must stay in the non-zero subgroup range."""

    draws = iter([0, 7, 0, 9])
    monkeypatch.setattr(
        "pyicloud.hsa2_bridge_prover.secrets.randbelow",
        lambda _limit: next(draws),
    )

    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    prover = TrustedDeviceBridgeProver()
    prover.init_with_salt(salt_b64, "050044")
    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64=salt_b64)

    assert prover._client is not None
    assert prover._client._x == 7
    assert server._server._y == 9


def test_trusted_device_bridge_prover_normalizes_malformed_bridge_payloads() -> None:
    """Malformed encrypted payloads should surface as ValueError."""

    prover = TrustedDeviceBridgeProver()
    prover._verifier_key = "00" * 32

    with pytest.raises(ValueError, match="Malformed bridge payload"):
        prover.decrypt_message(base64.b64encode(b"").decode("ascii"))

    with pytest.raises(ValueError, match="Malformed bridge payload"):
        prover.decrypt_message(base64.b64encode(b"\x01truncated").decode("ascii"))


def test_trusted_device_bridge_bootstrap_keeps_websocket_open_and_persists_step2() -> (
    None
):
    """The bridge bootstrap should keep the websocket alive after step 0 succeeds."""

    topic = "com.apple.idmsauthwidget"
    bridge_payload = {
        "sessionUUID": "bridge-session",
        "nextStep": "2",
        "ruiURLKey": "hsa2TwoFactorAuthApprovalFlowUrl",
        "txnid": "2300_282820214_S",
        "salt": "c2FsdA==",
        "mid": "bridge-mid",
        "idmsdata": "idms-data",
        "akdata": {"lat": 49.52, "lng": 6.1},
    }
    websocket_urls: list[tuple[str, float, str, str]] = []
    session = MagicMock()
    session.request_raw.return_value = _response(200)
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(topic, bridge_payload, 2300),
        ],
        on_read=lambda read_count: (
            read_count == 1 or session.request_raw.call_count == 1
        )
        or (_ for _ in ()).throw(
            AssertionError("Bridge step 0 should be posted before waiting for push")
        ),
    )

    def websocket_factory(
        url: str, timeout: float, origin: str, user_agent: str
    ) -> _FakeWebSocket:
        websocket_urls.append((url, timeout, origin, user_agent))
        return websocket

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=websocket_factory,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    websocket_url = websocket_urls[0][0]
    assert websocket_url.startswith("wss://websocket.push.apple.com/v2/")
    assert state.connection_path == websocket_url.rsplit("/", 1)[1]
    connection_message = unhexlify(state.connection_path)
    outer_fields = _decode_fields(connection_message)
    inner_fields = _decode_fields(outer_fields[1][0])
    assert inner_fields[1][0] == b"\x04public-key"
    assert bytes(inner_fields[3][0]).startswith(b"\x01\x03signature-for-")
    assert state.push_token == b"push-token".hex()
    assert state.session_uuid == "bridge-session"
    assert state.next_step == "2"
    assert state.rui_url_key == "hsa2TwoFactorAuthApprovalFlowUrl"
    assert state.txnid == "2300_282820214_S"
    assert state.salt == "c2FsdA=="
    assert state.mid == "bridge-mid"
    assert state.idmsdata == "idms-data"
    assert state.akdata == {"lat": 49.52, "lng": 6.1}
    assert state.websocket is websocket
    assert websocket.sent_messages[0] == _encode_web_filter_message([topic])
    assert websocket.sent_messages[1] == _encode_ack_message(
        bytes.fromhex(_topic_hash(topic)),
        2300,
    )
    session.request_raw.assert_called_once_with(
        "POST",
        "https://idmsa.apple.com/appleauth/auth/bridge/step/0",
        json={
            "sessionUUID": "bridge-session",
            "ptkn": b"push-token".hex(),
        },
        headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
    )
    assert websocket.closed is False
    bootstrapper.close(state)
    assert websocket.closed is True
    assert state.websocket is None


def test_trusted_device_bridge_rejects_malformed_push_token() -> None:
    """Malformed push tokens should surface as bridge prompt failures."""

    websocket = _FakeWebSocket(
        [_encode_connection_response_with_token_b64("%%%not-base64%%%")]
    )
    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )

    with pytest.raises(
        PyiCloudTrustedDevicePromptException,
        match="Failed to bootstrap the trusted-device bridge prompt.",
    ) as exc_info:
        bootstrapper.start(
            session=MagicMock(),
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            boot_context=_boot_context(),
            user_agent="test-agent",
        )

    assert isinstance(exc_info.value.__cause__, PyiCloudTrustedDevicePromptException)
    assert "Malformed bridge push token" in str(exc_info.value.__cause__)
    assert websocket.closed is True


def test_trusted_device_bridge_rejects_mismatched_session_uuid() -> None:
    """The first bridge push should match the session UUID used for step 0."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "different-session",
                    "nextStep": "2",
                },
                2300,
            ),
        ]
    )

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.return_value = _response(200)

    with pytest.raises(
        PyiCloudTrustedDevicePromptException,
        match="Failed to bootstrap the trusted-device bridge prompt.",
    ) as exc_info:
        bootstrapper.start(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            boot_context=_boot_context(topic),
            user_agent="test-agent",
        )
    assert isinstance(exc_info.value.__cause__, PyiCloudTrustedDevicePromptException)
    assert "mismatched session UUID" in str(exc_info.value.__cause__)
    assert websocket.closed is True


def test_trusted_device_bridge_start_propagates_unexpected_exception() -> None:
    """Unexpected bootstrap bugs should surface directly instead of being wrapped."""

    websocket = _FakeWebSocket([TypeError("boom")])
    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )

    with pytest.raises(TypeError, match="boom"):
        bootstrapper.start(
            session=MagicMock(),
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            boot_context=_boot_context(),
            user_agent="test-agent",
        )

    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_runs_step2_step4_step6_sequence() -> None:
    """Bridge-backed trusted-device verification should follow Apple's step 2/4/6 flow."""

    topic = "com.apple.idmsauthwidget"
    initial_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "2",
        "ruiURLKey": "hsa2TwoFactorAuthApprovalFlowUrl",
        "txnid": "2300_282820214_S",
        "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
        "mid": "bridge-mid",
        "idmsdata": "initial-idms",
        "akdata": {"lat": 49.52},
    }
    server_message1_hex = "aa01"
    server_message2_hex = "bb02"
    step4_data = base64.b64encode(
        (
            _hex_to_b64(server_message1_hex) + "_" + _hex_to_b64(server_message2_hex)
        ).encode("utf-8")
    ).decode("ascii")
    step4_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "4",
        "txnid": "2300_282820214_S",
        "data": step4_data,
        "idmsdata": "step4-idms",
        "akdata": {"step": 4},
    }
    step6_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "6",
        "txnid": "2300_282820214_S",
        "encryptedCode": "ciphertext",
        "idmsdata": "step6-idms",
        "akdata": {"step": 6},
        "mid": "bridge-mid",
    }
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(topic, initial_push, 2300),
            _encode_push_message(topic, step4_push, 2301),
            _encode_push_message(topic, step6_push, 2302),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True, "key": "deadbeef"}
    prover.get_key.return_value = "deadbeef"
    prover.decrypt_message.return_value = "derived-device-code"

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )

    session = MagicMock()
    session.request_raw.side_effect = [
        _response(200),
        _response(200),
        _response(200),
        _response(409),
        _response(204),
    ]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    assert (
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
        is True
    )

    prover.init_with_salt.assert_called_once_with(initial_push["salt"], "050044")
    prover.process_message1.assert_called_once_with(server_message1_hex)
    prover.process_message2.assert_called_once_with(server_message2_hex)
    prover.decrypt_message.assert_called_once_with("ciphertext")
    assert session.request_raw.call_args_list == [
        call(
            "POST",
            "https://idmsa.apple.com/appleauth/auth/bridge/step/0",
            json={
                "sessionUUID": "bridge-session",
                "ptkn": b"push-token".hex(),
            },
            headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
        ),
        call(
            "POST",
            "https://idmsa.apple.com/appleauth/auth/bridge/step/2",
            json={
                "sessionUUID": "bridge-session",
                "data": _hex_to_b64("abcd"),
                "ptkn": b"push-token".hex(),
                "nextStep": 2,
                "idmsdata": "initial-idms",
                "akdata": '{"lat":49.52}',
            },
            headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
        ),
        call(
            "POST",
            "https://idmsa.apple.com/appleauth/auth/bridge/step/4",
            json={
                "sessionUUID": "bridge-session",
                "data": _hex_to_b64("ef01"),
                "ptkn": b"push-token".hex(),
                "nextStep": 4,
                "idmsdata": "step4-idms",
                "akdata": '{"step":4}',
            },
            headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
        ),
        call(
            "POST",
            "https://idmsa.apple.com/appleauth/auth/bridge/code/validate",
            json={
                "sessionUUID": "bridge-session",
                "code": "derived-device-code",
            },
            headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
        ),
        call(
            "POST",
            "https://idmsa.apple.com/appleauth/auth/bridge/step/6",
            json={
                "sessionUUID": "bridge-session",
                "data": BRIDGE_DONE_DATA_B64,
                "ptkn": b"push-token".hex(),
                "nextStep": 6,
                "idmsdata": "step6-idms",
                "akdata": '{"step":6}',
            },
            headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
        ),
    ]
    assert websocket.sent_messages[2] == _encode_ack_message(
        bytes.fromhex(_topic_hash(topic)),
        2301,
    )
    assert websocket.sent_messages[3] == _encode_ack_message(
        bytes.fromhex(_topic_hash(topic)),
        2302,
    )
    assert websocket.closed is True
    assert state.websocket is None


def test_trusted_device_bridge_validate_code_accepts_step4_encrypted_code_final_push() -> (
    None
):
    """Apple can finish the bridge flow with nextStep=4 when encryptedCode is present."""

    topic = "com.apple.idmsauthwidget"
    initial_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "2",
        "txnid": "2300_282820214_S",
        "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
        "idmsdata": "initial-idms",
        "akdata": {"lat": 49.52},
    }
    step4_data = base64.b64encode(
        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode("utf-8")
    ).decode("ascii")
    prover_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "4",
        "txnid": "2300_282820214_S",
        "data": step4_data,
        "idmsdata": "step4-idms",
        "akdata": {"step": 4},
    }
    final_push = {
        "sessionUUID": "bridge-session",
        "nextStep": "4",
        "txnid": "2300_282820214_S",
        "encryptedCode": "ciphertext",
        "idmsdata": "final-idms",
        "akdata": {"step": "final"},
    }
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(topic, initial_push, 2300),
            _encode_push_message(topic, prover_push, 2301),
            _encode_push_message(topic, final_push, 2302),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True, "key": "deadbeef"}
    prover.decrypt_message.return_value = "derived-device-code"

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [
        _response(200),
        _response(200),
        _response(200),
        _response(200),
        _response(204),
    ]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    assert (
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
        is True
    )
    assert session.request_raw.call_args_list[-1] == call(
        "POST",
        "https://idmsa.apple.com/appleauth/auth/bridge/step/4",
        json={
            "sessionUUID": "bridge-session",
            "data": BRIDGE_DONE_DATA_B64,
            "ptkn": b"push-token".hex(),
            "nextStep": 4,
            "idmsdata": "final-idms",
            "akdata": '{"step":"final"}',
        },
        headers={"scnt": "test-scnt", "X-Apple-App-Id": "1159"},
    )
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_returns_false_on_412() -> None:
    """A bridge code-validate 412 should be treated as an invalid code, not a transport failure."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                    "data": base64.b64encode(
                        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                    "idmsdata": "step4-idms",
                    "akdata": {"step": 4},
                },
                2301,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "6",
                    "txnid": "2300_282820214_S",
                    "encryptedCode": "ciphertext",
                    "idmsdata": "step6-idms",
                    "akdata": {"step": 6},
                },
                2302,
            ),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True, "key": "deadbeef"}
    prover.decrypt_message.return_value = "derived-device-code"

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [
        _response(200),
        _response(200),
        _response(200),
        _response(412),
        _response(204),
    ]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    assert (
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
        is False
    )
    assert session.request_raw.call_args_list[-1].args[1].endswith("/bridge/step/6")
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_rejects_error_push() -> None:
    """Bridge error pushes should surface as verification exceptions."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                    "data": base64.b64encode(
                        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                    "idmsdata": "step4-idms",
                    "akdata": {"step": 4},
                },
                2301,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "6",
                    "txnid": "2300_282820214_S",
                    "ec": 7,
                },
                2302,
            ),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True, "key": "deadbeef"}

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [_response(200), _response(200), _response(200)]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="error push",
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_rejects_malformed_final_push() -> None:
    """Final bridge pushes must include encryptedCode once the prover flow is complete."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                    "data": base64.b64encode(
                        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                    "idmsdata": "step4-idms",
                    "akdata": {"step": 4},
                },
                2301,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                },
                2302,
            ),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True, "key": "deadbeef"}

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [_response(200), _response(200), _response(200)]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="unexpected final payload",
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_rejects_mismatched_followup_push() -> None:
    """Follow-up bridge pushes must stay on the same bridge session."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "different-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                    "data": base64.b64encode(
                        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                },
                2301,
            ),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [_response(200), _response(200)]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="mismatched session UUID",
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_closes_on_timeout() -> None:
    """Timeouts after prompt delivery should surface as bridge verification failures."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            socket.timeout("timed out"),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [_response(200), _response(200)]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="websocket transport error",
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
    assert websocket.closed is True


def test_trusted_device_bridge_validate_code_wraps_step4_prover_message1_failure() -> (
    None
):
    """Malformed step-4 prover data should surface as bridge verification failures."""

    topic = "com.apple.idmsauthwidget"
    websocket = _FakeWebSocket(
        [
            _encode_connection_response(b"push-token"),
            _encode_channel_subscription_response(topic),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "2",
                    "txnid": "2300_282820214_S",
                    "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                    "idmsdata": "initial-idms",
                    "akdata": {"lat": 49.52},
                },
                2300,
            ),
            _encode_push_message(
                topic,
                {
                    "sessionUUID": "bridge-session",
                    "nextStep": "4",
                    "txnid": "2300_282820214_S",
                    "data": base64.b64encode(
                        (_hex_to_b64("aa01") + "_" + _hex_to_b64("bb02")).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                    "idmsdata": "step4-idms",
                    "akdata": {"step": 4},
                },
                2301,
            ),
        ]
    )
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.side_effect = ValueError("bad point")

    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: websocket,
        prover_factory=lambda: prover,
    )
    bootstrapper._generate_keypair = MagicMock(  # type: ignore[attr-defined]
        return_value=(b"\x04public-key", _FakePrivateKey())
    )
    bootstrapper._generate_session_uuid = MagicMock(  # type: ignore[attr-defined]
        return_value="bridge-session"
    )
    session = MagicMock()
    session.request_raw.side_effect = [_response(200), _response(200)]

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://idmsa.apple.com/appleauth/auth",
        headers={"scnt": "test-scnt"},
        boot_context=_boot_context(topic),
        user_agent="test-agent",
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="step 4 payload is malformed",
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://idmsa.apple.com/appleauth/auth",
            headers={"scnt": "test-scnt"},
            bridge_state=state,
            code="050044",
        )
    assert websocket.closed is True
