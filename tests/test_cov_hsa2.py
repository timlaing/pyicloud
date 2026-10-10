"""Branch-coverage tests for the HSA2 trusted-device bridge helpers (issue #394).

Targets the branches in ``pyicloud/hsa2_bridge.py`` and
``pyicloud/hsa2_bridge_prover.py`` that the existing ``test_hsa2_bridge.py``
does not exercise: parser/decoder error paths, the raw websocket client, the
bootstrapper's defensive guards, and the prover error branches.
"""

# pylint: disable=protected-access

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.exceptions import (
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)
import pyicloud.hsa2_bridge as bridge
from pyicloud.hsa2_bridge import (
    Hsa2BootContext,
    TrustedDeviceBridgeBootstrapper,
    TrustedDeviceBridgeState,
    parse_boot_args_html,
)
import pyicloud.hsa2_bridge_prover as prover_mod
from pyicloud.hsa2_bridge_prover import (
    TrustedDeviceBridgeProver,
    _TrustedDeviceBridgeServerProver,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _connection_frame(
    token: bytes | None, status: int, *, timestamp: int | None = None
) -> bytes:
    payload = b""
    if token is not None:
        payload += bridge._encode_string_field(
            1, base64.b64encode(token).decode("ascii")
        )
    payload += bridge._encode_uint32_field(2, status)
    if timestamp is not None:
        payload += bridge._encode_uint32_field(3, timestamp)
    return bridge._encode_bytes_field(1, payload)


def _raw_connection_frame(payload: bytes) -> bytes:
    return bridge._encode_bytes_field(1, payload)


def _push_frame(topic: str, payload: dict[str, Any], message_id: int) -> bytes:
    body = b"".join([
        bridge._encode_bytes_field(1, bytes.fromhex(bridge._topic_hash(topic))),
        bridge._encode_uint32_field(2, message_id),
        bridge._encode_bytes_field(4, json.dumps(payload).encode("utf-8")),
    ])
    return bridge._encode_bytes_field(2, body)


def _channel_frame(topic: str, *, status: int = 0) -> bytes:
    channel_response = b"".join([
        bridge._encode_string_field(1, topic),
        bridge._encode_bytes_field(2, bridge._encode_bytes_field(1, b"channel-id")),
    ])
    payload = bridge._encode_bytes_field(1, channel_response)
    body = b"".join([
        bridge._encode_bytes_field(1, payload),
        bridge._encode_uint32_field(2, 1),
        bridge._encode_uint32_field(3, status),
    ])
    return bridge._encode_bytes_field(3, body)


def _ack_frame(topic: str, message_id: int) -> bytes:
    body = b"".join([
        bridge._encode_bytes_field(1, bytes.fromhex(bridge._topic_hash(topic))),
        bridge._encode_uint32_field(2, message_id),
        bridge._encode_uint32_field(3, 0),
    ])
    return bridge._encode_bytes_field(7, body)


class _WS:
    """Minimal fake websocket recording sends and replaying messages."""

    def __init__(self, messages: list[bytes | Exception]) -> None:
        self._messages = list(messages)
        self.sent: list[bytes] = []
        self.closed = False

    def send_binary(self, payload: bytes) -> None:
        """Record a binary payload."""
        self.sent.append(payload)

    def read_message(self) -> bytes:
        """Return the next queued message or raise it."""
        message = self._messages.pop(0)
        if isinstance(message, Exception):
            raise message
        return message

    def close(self) -> None:
        """Mark the fake websocket as closed."""
        self.closed = True


class _FakeKey:
    """Private-key stand-in producing deterministic byte signatures."""

    def sign(self, nonce: bytes, _algorithm: object) -> bytes:
        """Return a deterministic signature for the nonce."""
        return b"signature-for-" + nonce[:4]


def _boot_context(
    *, topic: str = "com.apple.idmsauthwidget", source_app_id: str | None = "1159"
) -> Hsa2BootContext:
    return Hsa2BootContext(
        auth_initial_route="auth/bridge/step",
        has_trusted_devices=True,
        auth_factors=("web_piggybacking",),
        bridge_initiate_data={
            "apnsTopic": topic,
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
        source_app_id=source_app_id,
    )


def _state(ws: object, **kwargs: Any) -> TrustedDeviceBridgeState:
    defaults: dict[str, Any] = {
        "connection_path": "path",
        "push_token": "tok",
        "session_uuid": "bridge-session",
        "websocket": ws,
        "topic": "com.apple.idmsauthwidget",
        "topics_by_hash": {},
    }
    defaults.update(kwargs)
    return TrustedDeviceBridgeState(**defaults)


def _bootstrapper(
    websocket: Any,
    *,
    prover: Any | None = None,
    timeout: float = 1.0,
) -> TrustedDeviceBridgeBootstrapper:
    return TrustedDeviceBridgeBootstrapper(
        timeout=timeout,
        websocket_factory=lambda *_args: websocket,
        prover_factory=(lambda: prover) if prover is not None else None,
    )


# ---------------------------------------------------------------------------
# Hsa2BootContext
# ---------------------------------------------------------------------------


def test_from_auth_options_with_non_mapping_direct() -> None:
    """A non-dict ``direct`` falls back to the top-level auth options."""

    ctx = Hsa2BootContext.from_auth_options({
        "direct": "not-a-dict",
        "authInitialRoute": "route",
        "hasTrustedDevices": True,
        "authFactors": ["sms", 123],
        "bridgeInitiateData": "bad",
        "phoneNumberVerification": "bad",
        "sourceAppId": None,
    })
    assert ctx.auth_initial_route == "route"
    assert ctx.auth_factors == ("sms",)
    assert not ctx.bridge_initiate_data
    assert not ctx.phone_number_verification
    assert ctx.source_app_id is None


def test_from_auth_options_phone_verification_fallback() -> None:
    """Phone verification is read from bridge initiate data when absent at the top."""

    ctx = Hsa2BootContext.from_auth_options({
        "direct": {
            "twoSV": {
                "authFactors": ["sms"],
                "bridgeInitiateData": {
                    "phoneNumberVerification": {"trustedPhoneNumber": {"id": 1}}
                },
                "sourceAppId": 1234,
            }
        }
    })
    assert ctx.phone_number_verification["trustedPhoneNumber"]["id"] == 1
    assert ctx.source_app_id == "1234"


def test_as_auth_data_round_trip() -> None:
    """as_auth_data serialises populated context back into Apple's shape."""

    ctx = Hsa2BootContext(
        auth_initial_route="route",
        has_trusted_devices=True,
        auth_factors=("sms",),
        bridge_initiate_data={"apnsTopic": "t"},
        phone_number_verification={"trustedPhoneNumber": {"id": 1}},
        source_app_id="42",
    )
    data = ctx.as_auth_data()
    assert data["authInitialRoute"] == "route"
    assert data["bridgeInitiateData"] == {"apnsTopic": "t"}
    assert data["phoneNumberVerification"] == {"trustedPhoneNumber": {"id": 1}}
    assert data["sourceAppId"] == "42"


def test_as_auth_data_defaults() -> None:
    """An empty context serialises without optional keys."""

    data = Hsa2BootContext().as_auth_data()
    assert data == {
        "authInitialRoute": "",
        "hasTrustedDevices": False,
        "authFactors": [],
    }


# ---------------------------------------------------------------------------
# BridgeStepRequest / BridgePushPayload
# ---------------------------------------------------------------------------


def test_bridge_step_request_optional_fields() -> None:
    """idmsdata/akdata are only included when present."""

    payload = bridge.BridgeStepRequest(
        session_uuid="s",
        data="d",
        push_token="p",
        next_step=2,
    ).as_json()
    assert "idmsdata" not in payload
    assert "akdata" not in payload


def test_bridge_step_request_serialises_akdata_shapes() -> None:
    """Dict akdata is compact JSON; non-dict akdata passes through."""

    as_dict = bridge.BridgeStepRequest(
        session_uuid="s",
        data="d",
        push_token="p",
        next_step=2,
        idmsdata="id",
        akdata={"a": 1},
    ).as_json()
    assert as_dict["akdata"] == '{"a":1}'
    assert as_dict["idmsdata"] == "id"

    as_raw = bridge.BridgeStepRequest(
        session_uuid="s",
        data="d",
        push_token="p",
        next_step=2,
        akdata="raw",
    ).as_json()
    assert as_raw["akdata"] == "raw"


def test_bridge_push_payload_next_step_int_and_fields() -> None:
    """Integer nextStep and optional fields are normalised."""

    payload = bridge.BridgePushPayload.from_payload({
        "sessionUUID": "s",
        "nextStep": 2,
        "txnid": "t",
        "salt": "salt",
        "mid": "m",
        "idmsdata": "i",
        "akdata": {"x": 1},
        "data": "d",
        "encryptedCode": "e",
        "ec": 0,
    })
    assert payload.next_step == "2"
    assert payload.txnid == "t"
    assert payload.encrypted_code == "e"
    assert payload.error_code == 0


def test_bridge_state_apply_push_and_legacy_flag() -> None:
    """State merges push payloads and flags the legacy verifier."""

    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=None,
        topic="t",
        topics_by_hash={},
    )
    state.apply_push_payload(
        bridge.BridgePushPayload.from_payload({
            "sessionUUID": "s",
            "nextStep": "2",
            "txnid": "abc_W",
            "ec": 2,
        })
    )
    assert state.next_step == "2"
    assert state.error_code == 2
    assert state.uses_legacy_trusted_device_verifier is True

    state.apply_push_payload(
        bridge.BridgePushPayload.from_payload({"sessionUUID": "s", "nextStep": "2"})
    )
    assert state.uses_legacy_trusted_device_verifier is False


# ---------------------------------------------------------------------------
# parse_boot_args_html
# ---------------------------------------------------------------------------


def test_parse_boot_args_missing_payload() -> None:
    """HTML without a boot_args script raises."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Missing HSA2"):
        parse_boot_args_html("<html><body>no boot args</body></html>")


def test_parse_boot_args_malformed_json() -> None:
    """Malformed JSON inside the script tag raises."""

    html = '<script class="boot_args">{not json}</script>'
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Malformed HSA2"):
        parse_boot_args_html(html)


def test_parse_boot_args_missing_direct() -> None:
    """A payload without a direct mapping raises."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="direct boot data"):
        parse_boot_args_html('<script class="boot_args">{"other": 1}</script>')


def test_parse_boot_args_defaults_and_ignores_other_tags() -> None:
    """Non-dict fields default to empty and non-boot_args script tags are skipped."""

    html = (
        "<script>var x = 1;</script>"
        "<div>ignored</div>"
        '<script class="boot_args">'
        '{"direct": {"twoSV": "bad", "sourceAppId": null}}'
        "</script>"
    )
    ctx = parse_boot_args_html(html)
    assert not ctx.auth_factors
    assert not ctx.bridge_initiate_data
    assert not ctx.phone_number_verification
    assert ctx.source_app_id is None


# ---------------------------------------------------------------------------
# Protobuf helpers
# ---------------------------------------------------------------------------


def test_encode_varint_rejects_negative() -> None:
    """Negative varints are rejected."""

    with pytest.raises(ValueError, match="Negative varints"):
        bridge._encode_varint(-1)


def test_read_varint_truncated() -> None:
    """A truncated varint raises."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Truncated"):
        bridge._read_varint(b"\x80", 0)


def test_decode_fields_unsupported_wire_type() -> None:
    """Unsupported wire types raise a clear error."""

    data = bridge._encode_varint((1 << 3) | 3)
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unsupported"):
        bridge._decode_fields(data)


def test_decode_connection_response_variants() -> None:
    """Connection responses handle missing tokens and timestamps."""

    empty = bridge._decode_connection_response(b"")
    assert empty.push_token_b64 == ""
    assert empty.server_timestamp_seconds is None

    with_ts = bridge._encode_uint32_field(2, 0) + bridge._encode_uint32_field(3, 99)
    decoded = bridge._decode_connection_response(with_ts)
    assert decoded.server_timestamp_seconds == 99

    bad = bridge._encode_bytes_field(1, b"\xff\xfe")
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="push token"):
        bridge._decode_connection_response(bad)


def test_decode_channel_subscription_variants() -> None:
    """Channel responses without payloads or with non-bytes topics are skipped."""

    no_payload = bridge._encode_uint32_field(2, 1)
    response = bridge._decode_channel_subscription_response(no_payload)
    assert not response.topics

    app_response = bridge._encode_uint32_field(1, 5)
    payload = bridge._encode_bytes_field(1, app_response)
    body = bridge._encode_bytes_field(1, payload) + bridge._encode_uint32_field(2, 1)
    response = bridge._decode_channel_subscription_response(body)
    assert not response.topics


def test_decode_server_message_includes_push_ack() -> None:
    """Push acknowledgments are decoded alongside other frame types."""

    frame = _ack_frame("com.apple.idmsauthwidget", 7)
    decoded = bridge._decode_server_message(frame)
    assert decoded.push_acknowledgment is not None
    assert decoded.push_acknowledgment.message_id == 7


def test_encode_bridge_signature_already_prefixed() -> None:
    """A signature that already carries Apple's prefix is returned unchanged."""

    prefixed = bridge.BRIDGE_SIGNATURE_PREFIX + b"sig"
    assert bridge._encode_bridge_signature(prefixed) == prefixed
    assert bridge._encode_bridge_signature(b"der") == (
        bridge.BRIDGE_SIGNATURE_PREFIX + b"raw"
    ).replace(b"raw", b"der")


def test_extract_json_payload_scans_nested_and_escaped() -> None:
    """The fallback JSON scanner walks nested objects and escaped strings."""

    payload = b'junk{"a": {"b": 1}, "c": "x\\"y"}tail'
    assert bridge._extract_json_payload(payload) == {"a": {"b": 1}, "c": 'x"y'}


def test_extract_json_payload_cannot_decode() -> None:
    """A payload without decodable JSON raises."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Could not decode"):
        bridge._extract_json_payload(b"no json here {broken} still broken")


def test_b64_to_hex_rejects_invalid_base64() -> None:
    """Invalid base64 raises ValueError."""

    with pytest.raises(ValueError, match="Malformed base64"):
        bridge._b64_to_hex("%%%not-base64%%%")


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def test_resolve_websocket_host_variants() -> None:
    """The host is parsed from URLs or environment fallbacks."""

    url_ctx = Hsa2BootContext(
        bridge_initiate_data={"webSocketUrl": "wss://example.test/path"}
    )
    assert bridge._resolve_websocket_host(url_ctx) == "example.test"

    bare_ctx = Hsa2BootContext(
        bridge_initiate_data={"webSocketUrl": "example.test/path"}
    )
    assert bridge._resolve_websocket_host(bare_ctx) == "example.test"

    env_ctx = Hsa2BootContext(bridge_initiate_data={"apnsEnvironment": "prod"})
    assert bridge._resolve_websocket_host(env_ctx) == "websocket.push.apple.com"

    empty_ctx = Hsa2BootContext()
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="websocket host"):
        bridge._resolve_websocket_host(empty_ctx)


def test_resolve_apns_topic_variants() -> None:
    """A missing APNS topic raises."""

    ok = Hsa2BootContext(bridge_initiate_data={"apnsTopic": "t"})
    assert bridge._resolve_apns_topic(ok) == "t"
    empty_ctx = Hsa2BootContext()
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="APNS topic"):
        bridge._resolve_apns_topic(empty_ctx)


def test_derive_origin_variants() -> None:
    """An endpoint without scheme or host raises."""

    assert bridge._derive_origin("https://apple.test/auth") == "https://apple.test"
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Invalid auth"):
        bridge._derive_origin("not-a-url")


def test_summarize_identifier() -> None:
    """Identifiers are shortened or defaulted before logging."""

    assert bridge._summarize_identifier(None) == "<none>"
    assert bridge._summarize_identifier("abc") == "abc"
    assert bridge._summarize_identifier("abcdefghijk", prefix=4) == "abcd..."
    assert bridge._summarize_identifier("", empty="<empty>") == "<empty>"


# ---------------------------------------------------------------------------
# _RawWebSocketClient
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Socket stand-in returning queued chunks from recv."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False

    def recv(self, _size: int) -> bytes:
        """Return the next queued chunk, or empty bytes when drained."""
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def sendall(self, data: bytes) -> None:
        """Record the sent frame."""
        self.sent.append(data)

    def settimeout(self, value: float) -> None:
        """Record the configured timeout."""
        self.timeout = value

    def close(self) -> None:
        """Mark the socket as closed."""
        self.closed = True


class _SendFailSocket(_FakeSocket):
    """Socket whose sendall always fails, to cover swallowed close errors."""

    def sendall(self, data: bytes) -> None:
        """Always fail so close() must swallow the transport error."""
        raise OSError("boom")


def _make_raw_client(sock: _FakeSocket) -> bridge._RawWebSocketClient:
    client = bridge._RawWebSocketClient.__new__(bridge._RawWebSocketClient)
    client._url = "wss://host/ws"
    client._timeout = 1.0
    client._origin = "https://origin"
    client._user_agent = "agent"
    client._buffer = bytearray()
    client._socket = sock  # type: ignore[assignment]
    return client


def _accept_header(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + bridge.WEBSOCKET_GUID).encode("ascii")).digest()
    ).decode("ascii")


def test_raw_websocket_open_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid 101 handshake yields a TLS socket."""

    monkeypatch.setattr(os, "urandom", lambda size: b"\x02" * size)
    key = base64.b64encode(b"\x02" * 16).decode("ascii")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        f"Sec-WebSocket-Accept: {_accept_header(key)}\r\n\r\n"
    ).encode("iso-8859-1")
    raw = _FakeSocket([])
    secure = _FakeSocket([response])
    context = MagicMock()
    context.wrap_socket.return_value = secure
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: raw)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)

    client = bridge._RawWebSocketClient("wss://host/path?q=1", 2.0, "o", "ua")
    assert client._socket is secure  # type: ignore[comparison-overlap]
    assert secure.timeout == 2.0
    assert any(b"GET /path?q=1 HTTP/1.1" in chunk for chunk in secure.sent)


def test_raw_websocket_open_rejects_bad_scheme() -> None:
    """Non-wss URLs are rejected before any connection."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unsupported"):
        bridge._RawWebSocketClient("ws://host/path", 1.0, "o", "ua")


def test_raw_websocket_open_rejects_bad_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-101 response raises."""

    monkeypatch.setattr(os, "urandom", lambda size: b"\x03" * size)
    raw = _FakeSocket([])
    secure = _FakeSocket([b"HTTP/1.1 403 Forbidden\r\n\r\n"])
    context = MagicMock()
    context.wrap_socket.return_value = secure
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: raw)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="upgrade failed"):
        bridge._RawWebSocketClient("wss://host/", 1.0, "o", "ua")


def test_raw_websocket_open_bad_accept_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mismatched accept header aborts the handshake."""

    monkeypatch.setattr(os, "urandom", lambda size: b"\x03" * size)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\nsec-websocket-accept: nope\r\n\r\n"
    ).encode("iso-8859-1")
    raw = _FakeSocket([])
    secure = _FakeSocket([response])
    context = MagicMock()
    context.wrap_socket.return_value = secure
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: raw)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="accept header"):
        bridge._RawWebSocketClient("wss://host/", 1.0, "o", "ua")


def test_raw_websocket_open_unsupported_url() -> None:
    """A non-wss URL is rejected before any socket work."""

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unsupported"):
        bridge._RawWebSocketClient("ws://host/", 1.0, "o", "ua")


def test_read_http_response_eof() -> None:
    """An EOF during the handshake raises."""

    sock = _FakeSocket([])
    client = _make_raw_client(sock)
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unexpected EOF"):
        client._read_http_response(sock)  # type: ignore[arg-type]


def test_read_exact_eof() -> None:
    """An EOF while reading a frame raises."""

    sock = _FakeSocket([])
    client = _make_raw_client(sock)
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unexpected EOF"):
        client._read_exact(4)


def test_send_frame_length_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three websocket length encodings are exercised."""

    monkeypatch.setattr(os, "urandom", lambda size: b"\x00" * size)
    sock = _FakeSocket([])
    client = _make_raw_client(sock)

    client._send_frame(bridge.OPCODE_BINARY, b"a" * 10)
    assert sock.sent[-1][1] & 0x7F == 10

    client._send_frame(bridge.OPCODE_BINARY, b"a" * 200)
    assert sock.sent[-1][1] & 0x7F == 126

    client._send_frame(bridge.OPCODE_BINARY, b"a" * 70000)
    assert sock.sent[-1][1] & 0x7F == 127


def test_send_binary_uses_binary_opcode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_binary frames the payload with the binary opcode."""

    monkeypatch.setattr(os, "urandom", lambda size: b"\x00" * size)
    sock = _FakeSocket([])
    client = _make_raw_client(sock)
    client.send_binary(b"hi")
    assert sock.sent[0][0] == 0x80 | bridge.OPCODE_BINARY


def test_read_message_binary_single_frame() -> None:
    """A single finished binary frame is returned."""

    sock = _FakeSocket([b"\x82\x03abc"])
    client = _make_raw_client(sock)
    assert client.read_message() == b"abc"


def test_read_message_ping_pong_and_close() -> None:
    """Ping frames trigger pongs; close frames raise."""

    sock = _FakeSocket([b"\x89\x01p", b"\x8a\x00", b"\x82\x01x"])
    client = _make_raw_client(sock)
    assert client.read_message() == b"x"
    assert sock.sent[0][0] == 0x80 | bridge.OPCODE_PONG

    closing = _make_raw_client(_FakeSocket([b"\x88\x00"]))
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="closed before"):
        closing.read_message()


def test_read_message_fragmented_and_masked() -> None:
    """Fragmented and masked frames are reassembled."""

    mask = b"\x01\x02\x03\x04"
    payload = b"hello"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = b"\x82\x85" + mask + masked
    client = _make_raw_client(_FakeSocket([frame]))
    assert client.read_message() == payload

    fragments = b"\x02\x02ab" + b"\x80\x03cde"
    frag_client = _make_raw_client(_FakeSocket([fragments]))
    assert frag_client.read_message() == b"abcde"


def test_read_message_extended_lengths() -> None:
    """16-bit and 64-bit payload lengths are honoured."""

    payload16 = b"a" * 200
    frame16 = b"\x82\x7e" + len(payload16).to_bytes(2, "big") + payload16
    assert _make_raw_client(_FakeSocket([frame16])).read_message() == payload16

    payload64 = b"b" * 70000
    frame64 = b"\x82\x7f" + len(payload64).to_bytes(8, "big") + payload64
    assert _make_raw_client(_FakeSocket([frame64])).read_message() == payload64


def test_read_message_unsupported_opcode() -> None:
    """Control/unknown opcodes on a finished frame raise."""

    frame = b"\x83\x01x"
    client = _make_raw_client(_FakeSocket([frame]))
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Unsupported"):
        client.read_message()


def test_raw_websocket_close_paths() -> None:
    """close() is a no-op without a socket and swallows send failures."""

    client = bridge._RawWebSocketClient.__new__(bridge._RawWebSocketClient)
    client._socket = None  # type: ignore[assignment]
    client.close()

    sock = _SendFailSocket([])
    client = _make_raw_client(sock)
    client.close()
    assert sock.closed is True


# ---------------------------------------------------------------------------
# Bootstrapper helpers
# ---------------------------------------------------------------------------


def test_generate_keypair_and_session_uuid() -> None:
    """The keypair is an uncompressed P-256 point and the UUID is a string."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    public_key, private_key = bootstrapper._generate_keypair()
    assert public_key[0] == 0x04
    assert private_key.key_size == 256
    assert isinstance(bootstrapper._generate_session_uuid(), str)


def test_bridge_headers_adds_source_app_id() -> None:
    """source_app_id is reflected into the bridge headers."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=None,
        topic="t",
        topics_by_hash={},
        source_app_id="42",
    )
    headers = bootstrapper._bridge_headers({"a": "b"}, state)
    assert headers == {"a": "b", "X-Apple-App-Id": "42"}


def test_post_bridge_step_status_validation() -> None:
    """Only 200/204/409 are accepted for bridge steps."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=MagicMock(),
        topic="t",
        topics_by_hash={},
    )
    session = MagicMock()
    response = MagicMock()
    response.status_code = 500
    session.request_raw.return_value = response
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="step 2"):
        bootstrapper._post_bridge_step(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            next_step=2,
            data="d",
            idmsdata=None,
            akdata=None,
        )


def test_post_bridge_step0_and_code_validate_status() -> None:
    """Step 0 and code-validate failures raise their own errors."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    session = MagicMock()
    response = MagicMock()
    response.status_code = 500
    session.request_raw.return_value = response

    with pytest.raises(PyiCloudTrustedDevicePromptException, match="step 0"):
        bootstrapper._post_bridge_step0(
            session=session,
            auth_endpoint="https://e",
            headers={},
            session_uuid="s",
            push_token="p",
        )

    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=MagicMock(),
        topic="t",
        topics_by_hash={},
    )
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="code valid"):
        bootstrapper._post_bridge_code_validate(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


def test_wait_for_push_token_skips_other_frames() -> None:
    """Frames without a connection response are skipped."""

    ws = _WS([
        _push_frame("topic", {"sessionUUID": "s"}, 1),
        _connection_frame(b"token", 0),
    ])
    bootstrapper = _bootstrapper(ws)
    assert bootstrapper._wait_for_push_token(ws) == b"token"


def test_wait_for_push_token_statuses() -> None:
    """Invalid nonce and unexpected statuses raise."""

    bootstrapper = _bootstrapper(_WS([]))
    nonce_ws = _WS([_connection_frame(None, 2, timestamp=5)])
    with pytest.raises(bridge._InvalidNonceError):
        bootstrapper._wait_for_push_token(nonce_ws)

    bad_status = _WS([_connection_frame(None, 7)])
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="status 7"):
        bootstrapper._wait_for_push_token(bad_status)


def test_wait_for_push_token_timeout() -> None:
    """An expired deadline raises before reading."""

    bootstrapper = TrustedDeviceBridgeBootstrapper(timeout=-1.0)
    empty_ws = _WS([])
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Timed out"):
        bootstrapper._wait_for_push_token(empty_ws)


def test_wait_for_bridge_push_handles_control_frames() -> None:
    """Channel responses, acks and unrelated frames are tolerated."""

    topic = "com.apple.idmsauthwidget"
    ws = _WS([
        _channel_frame(topic),
        _ack_frame(topic, 5),
        bridge._encode_bytes_field(99, b"junk"),
        _push_frame(topic, {"sessionUUID": "s", "nextStep": "2"}, 6),
    ])
    bootstrapper = TrustedDeviceBridgeBootstrapper()
    payload = bootstrapper._wait_for_bridge_push(
        ws, topic, {bridge._topic_hash(topic): topic}
    )
    assert payload.session_uuid == "s"


def test_wait_for_bridge_push_channel_failure() -> None:
    """A failed channel subscription raises."""

    ws = _WS([_channel_frame("t", status=5)])
    bootstrapper = TrustedDeviceBridgeBootstrapper()
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="subscription"):
        bootstrapper._wait_for_bridge_push(ws, "t", {})


def test_wait_for_bridge_push_skips_other_topics() -> None:
    """Pushes for other topics are acknowledged and skipped."""

    topic = "com.apple.idmsauthwidget"
    ws = _WS([
        _push_frame("other", {"sessionUUID": "s"}, 1),
        _push_frame(topic, {"sessionUUID": "s", "nextStep": "2"}, 2),
    ])
    payload = TrustedDeviceBridgeBootstrapper()._wait_for_bridge_push(
        ws, topic, {bridge._topic_hash(topic): topic}
    )
    assert payload.session_uuid == "s"


def test_wait_for_bridge_push_timeout() -> None:
    """An empty queue with an expired deadline raises."""

    bootstrapper = TrustedDeviceBridgeBootstrapper(timeout=-1.0)
    empty_ws = _WS([])
    with pytest.raises(PyiCloudTrustedDevicePromptException, match="Timed out"):
        bootstrapper._wait_for_bridge_push(empty_ws, "expected", {})


def test_apply_bridge_push_rejects_mismatch_and_errors() -> None:
    """Mismatched sessions and error pushes raise."""

    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=MagicMock(),
        topic="t",
        topics_by_hash={},
    )
    bootstrapper = TrustedDeviceBridgeBootstrapper()
    mismatched = bridge.BridgePushPayload.from_payload({"sessionUUID": "other"})
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="mismatched"):
        bootstrapper._apply_bridge_push(state, mismatched)

    error_push = bridge.BridgePushPayload.from_payload({"sessionUUID": "s", "ec": 3})
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="error push"):
        bootstrapper._apply_bridge_push(state, error_push)


def test_apply_expected_step4_push_variants() -> None:
    """The step-4 push must carry step-4 data."""

    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=MagicMock(),
        topic="t",
        topics_by_hash={},
    )
    bootstrapper = TrustedDeviceBridgeBootstrapper()
    step3 = bridge.BridgePushPayload.from_payload({
        "sessionUUID": "s",
        "nextStep": "3",
        "data": "d",
    })
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="post-step-2"):
        bootstrapper._apply_expected_step4_push(state, step3)

    bootstrapper._apply_expected_step4_push(
        state,
        bridge.BridgePushPayload.from_payload({
            "sessionUUID": "s",
            "nextStep": "4",
            "data": "d",
        }),
    )


def test_apply_final_bridge_push_variants() -> None:
    """The final push must carry encryptedCode and a final step."""

    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=MagicMock(),
        topic="t",
        topics_by_hash={},
    )
    bootstrapper = TrustedDeviceBridgeBootstrapper()
    missing_code = bridge.BridgePushPayload.from_payload({
        "sessionUUID": "s",
        "nextStep": "5",
    })
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="final payload"
    ):
        bootstrapper._apply_final_bridge_push(state, missing_code)

    bootstrapper._apply_final_bridge_push(
        state,
        bridge.BridgePushPayload.from_payload({
            "sessionUUID": "s",
            "nextStep": "6",
            "encryptedCode": "e",
        }),
    )


# ---------------------------------------------------------------------------
# Bootstrapper.start / close / validate_code edge cases
# ---------------------------------------------------------------------------


def _patch_keypair(monkeypatch: pytest.MonkeyPatch, bootstrapper: Any) -> None:
    monkeypatch.setattr(
        bootstrapper,
        "_generate_keypair",
        MagicMock(return_value=(b"\x04public-key", _FakeKey())),
    )
    monkeypatch.setattr(
        bootstrapper,
        "_generate_session_uuid",
        MagicMock(return_value="session"),
    )


def test_close_handles_missing_websocket() -> None:
    """close() tolerates an already-closed session."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=None,
        topic="t",
        topics_by_hash={},
    )
    bootstrapper.close(state)

    ws = _WS([])
    active = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="t",
        topics_by_hash={},
    )
    bootstrapper.close(active)
    assert ws.closed is True


def test_close_swallows_send_errors() -> None:
    """A failing close frame is swallowed."""

    class _BadWS(_WS):
        def send_binary(self, payload: bytes) -> None:
            raise OSError("boom")

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=_BadWS([]),
        topic="t",
        topics_by_hash={},
    )
    bootstrapper.close(state)


def test_start_source_app_id_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A context without source_app_id omits the app-id header."""

    topic = "com.apple.idmsauthwidget"
    ws = _WS([
        _connection_frame(b"token", 0),
        _channel_frame(topic),
        _push_frame(topic, {"sessionUUID": "session", "nextStep": "2"}, 1),
    ])
    bootstrapper = _bootstrapper(ws)
    _patch_keypair(monkeypatch, bootstrapper)
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://e/auth",
        headers={"scnt": "x"},
        boot_context=_boot_context(source_app_id=None),
        user_agent="ua",
    )
    assert state.source_app_id is None
    assert session.request_raw.call_args.kwargs["headers"] == {"scnt": "x"}


def test_start_retries_invalid_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    """An INVALID_NONCE response triggers a retry with the server timestamp."""

    topic = "com.apple.idmsauthwidget"
    first = _WS([_connection_frame(None, 2, timestamp=1234)])
    second = _WS([
        _connection_frame(b"token", 0),
        _channel_frame(topic),
        _push_frame(topic, {"sessionUUID": "session", "nextStep": "2"}, 1),
    ])
    sockets = iter([first, second])
    bootstrapper = TrustedDeviceBridgeBootstrapper(
        timeout=1.0,
        websocket_factory=lambda *_args: next(sockets),
    )
    _patch_keypair(monkeypatch, bootstrapper)
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)

    state = bootstrapper.start(
        session=session,
        auth_endpoint="https://e/auth",
        headers={},
        boot_context=_boot_context(),
        user_agent="ua",
    )
    assert state.session_uuid == "session"
    assert first.closed is True


def test_start_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OSError during bootstrap aborts with a prompt failure."""

    ws = _WS([OSError("boom")])
    bootstrapper = _bootstrapper(ws)
    _patch_keypair(monkeypatch, bootstrapper)

    session = MagicMock()
    boot_context = _boot_context()
    with pytest.raises(
        PyiCloudTrustedDevicePromptException, match="Failed to bootstrap"
    ):
        bootstrapper.start(
            session=session,
            auth_endpoint="https://e/auth",
            headers={},
            boot_context=boot_context,
            user_agent="ua",
        )
    assert ws.closed is True


def _topics(topic: str) -> dict[str, str]:
    return {bridge._topic_hash(topic): topic}


def _message1_prover() -> MagicMock:
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    return prover


def test_validate_code_guards() -> None:
    """validate_code enforces the active/session/step/salt preconditions."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    inactive = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=None,
        topic="t",
        topics_by_hash={},
    )
    session = MagicMock()
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="not active"):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=inactive,
            code="c",
        )


def test_validate_code_legacy_and_not_ready() -> None:
    """Legacy and premature states raise before any network work."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    base: dict[str, Any] = {
        "connection_path": "c",
        "push_token": "p",
        "session_uuid": "s",
        "websocket": _WS([]),
        "topic": "t",
        "topics_by_hash": {},
    }
    session = MagicMock()
    legacy = TrustedDeviceBridgeState(**{**base, "txnid": "x_W"})
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="Legacy"):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=legacy,
            code="c",
        )

    not_ready = TrustedDeviceBridgeState(**{**base, "next_step": "9"})
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="not ready"):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=not_ready,
            code="c",
        )

    no_salt = TrustedDeviceBridgeState(**{**base, "next_step": "2"})
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="salt"):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=no_salt,
            code="c",
        )


def test_validate_code_inactive_and_prover_rejection() -> None:
    """An inactive session and a prover rejection are handled."""

    bootstrapper = TrustedDeviceBridgeBootstrapper()
    inactive = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=None,
        topic="t",
        topics_by_hash={},
    )
    session = MagicMock()
    with pytest.raises(PyiCloudTrustedDeviceVerificationException, match="not active"):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=inactive,
            code="c",
        )

    step4_data = base64.b64encode(
        (bridge._hex_to_b64("aa01") + "_" + bridge._hex_to_b64("bb02")).encode()
    ).decode("ascii")
    ws = _WS([
        _push_frame(
            "topic",
            {"sessionUUID": "s", "nextStep": "4", "data": step4_data},
            2,
        )
    ])
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.side_effect = ValueError("bad")
    bootstrapper = _bootstrapper(ws, prover=prover)
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    assert (
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )
        is False
    )


def test_validate_code_missing_step4_data() -> None:
    """A step-4 push without prover data raises."""

    ws = _WS([_push_frame("topic", {"sessionUUID": "s", "nextStep": "4"}, 2)])
    bootstrapper = _bootstrapper(ws, prover=_message1_prover())
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="unexpected post-step-2"
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


def test_validate_code_malformed_step4() -> None:
    """A step-4 payload that is not valid base64 raises."""

    ws = _WS([
        _push_frame("topic", {"sessionUUID": "s", "nextStep": "4", "data": "!!!"}, 2)
    ])
    bootstrapper = _bootstrapper(ws, prover=_message1_prover())
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="step 4 payload is malformed"
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


def test_validate_code_final_push_without_encrypted_code() -> None:
    """The real final-push guard rejects a payload lacking encryptedCode."""

    step4_data = base64.b64encode(
        (bridge._hex_to_b64("aa01") + "_" + bridge._hex_to_b64("bb02")).encode()
    ).decode("ascii")
    ws = _WS([
        _push_frame(
            "topic",
            {"sessionUUID": "s", "nextStep": "4", "data": step4_data},
            2,
        ),
        _push_frame("topic", {"sessionUUID": "s", "nextStep": "6"}, 3),
    ])
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True}
    prover.decrypt_message.return_value = "code"
    bootstrapper = _bootstrapper(ws, prover=prover)
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="unexpected final payload"
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


def test_validate_code_decrypt_failure() -> None:
    """A decryption failure raises a verification exception."""

    step4_data = base64.b64encode(
        (bridge._hex_to_b64("aa01") + "_" + bridge._hex_to_b64("bb02")).encode()
    ).decode("ascii")
    ws = _WS([
        _push_frame(
            "topic",
            {"sessionUUID": "s", "nextStep": "4", "data": step4_data},
            2,
        ),
        _push_frame(
            "topic",
            {"sessionUUID": "s", "nextStep": "6", "encryptedCode": "cipher"},
            3,
        ),
    ])
    prover = MagicMock()
    prover.get_message1.return_value = "abcd"
    prover.process_message1.return_value = "ef01"
    prover.process_message2.return_value = {"isVerified": True}
    prover.decrypt_message.side_effect = ValueError("bad")
    bootstrapper = _bootstrapper(ws, prover=prover)
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="Failed to decrypt"
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


def test_validate_code_prompt_exception_wrapped() -> None:
    """A prompt failure while waiting for a push is wrapped."""

    ws = _WS([_channel_frame("topic", status=5)])
    bootstrapper = _bootstrapper(ws, prover=_message1_prover())
    state = TrustedDeviceBridgeState(
        connection_path="c",
        push_token="p",
        session_uuid="s",
        websocket=ws,
        topic="topic",
        topics_by_hash=_topics("topic"),
        next_step="2",
        salt="c2FsdA==",
    )
    session = MagicMock()
    session.request_raw.return_value = MagicMock(status_code=200)
    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException, match="verification failed"
    ):
        bootstrapper.validate_code(
            session=session,
            auth_endpoint="https://e",
            headers={},
            bridge_state=state,
            code="c",
        )


# ---------------------------------------------------------------------------
# Prover error branches
# ---------------------------------------------------------------------------


def test_prover_guards_raise_before_init() -> None:
    """Prover methods require prior initialization."""

    prover = TrustedDeviceBridgeProver()
    with pytest.raises(ValueError, match="init_with_salt"):
        prover.get_message1()
    with pytest.raises(ValueError, match="init_with_salt"):
        prover.process_message1("aa")
    with pytest.raises(ValueError, match="process_message1"):
        prover.get_message2()
    with pytest.raises(ValueError, match="process_message1"):
        prover.process_message2("aa")
    with pytest.raises(ValueError, match="No bridge key is available yet"):
        prover.get_key()


def test_prover_roundtrip_and_decrypt_guards() -> None:
    """A full prover round-trip succeeds and decrypt guards apply."""

    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    prover = TrustedDeviceBridgeProver()
    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64=salt_b64)
    prover.init_with_salt(salt_b64, "050044")
    client_message1 = prover.get_message1()
    server_message1 = server.get_message1()
    server_message2 = server.process_message1(client_message1)
    client_message2 = prover.process_message1(server_message1)
    server.verify_message2(client_message2)
    prover.process_message2(server_message2)
    assert prover.is_verified() is True
    assert prover.get_key()

    unverified_prover = TrustedDeviceBridgeProver()
    with pytest.raises(ValueError, match="verifier key"):
        unverified_prover.decrypt_message("AA==")


def test_prover_point_helpers() -> None:
    """Point encoding/decoding helpers raise on invalid inputs."""

    infinity = prover_mod._INFINITY
    with pytest.raises(ValueError, match="infinity"):
        prover_mod._encode_point(infinity)
    with pytest.raises(ValueError, match="Unsupported"):
        prover_mod._decode_point("00")
    with pytest.raises(ValueError, match="Invalid P-256"):
        # Valid encoding length but not on the curve.
        prover_mod._decode_point("04" + "00" * 64)

    assert prover_mod._is_on_curve(infinity) is False
    assert prover_mod._negate(infinity) is infinity


def test_prover_add_points_special_cases() -> None:
    """Special-case point addition branches."""

    g = prover_mod._GENERATOR
    assert prover_mod._add_points(prover_mod._INFINITY, g) is g
    assert prover_mod._add_points(g, prover_mod._INFINITY) is g
    neg_g = prover_mod._negate(g)
    assert prover_mod._add_points(g, neg_g) is prover_mod._INFINITY


def test_prover_confirmation_key_length() -> None:
    """ConfirmationKeys requests expand to 64 bytes."""

    assert prover_mod._confirmation_key_length(b"ConfirmationKeys", 32) == 64
    assert prover_mod._confirmation_key_length(b"other", 32) == 32


def test_prover_hkdf_empty_salt() -> None:
    """An empty salt is replaced with a zero hash-length salt."""

    derived = prover_mod._derive_key(b"ikm", b"info", 16)
    assert len(derived) == 16


def test_prover_finish_requires_message() -> None:
    """finish() requires get_message() to have been called."""

    client = prover_mod._ClientHandshake(x_scalar=1, w0=2, w1=3)
    with pytest.raises(ValueError, match="get_message"):
        client.finish("04" + "00" * 64)
    server = prover_mod._ServerHandshake(
        y_scalar=1, w0=2, verifier_point=prover_mod._GENERATOR
    )
    with pytest.raises(ValueError, match="get_message"):
        server.finish("04" + "00" * 64)


def test_prover_shared_secret_bad_confirmation() -> None:
    """A mismatched confirmation raises ValueError."""

    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    client = TrustedDeviceBridgeProver()
    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64=salt_b64)
    client.init_with_salt(salt_b64, "050044")
    client_msg = client.get_message1()
    server_msg = server.get_message1()
    server.process_message1(client_msg)
    client.process_message1(server_msg)
    with pytest.raises(ValueError, match="confirmation"):
        client.process_message2("00")


def test_prover_get_key_and_decrypt_guards() -> None:
    """Get key and decrypt raise when state is missing."""

    prover = TrustedDeviceBridgeProver()
    with pytest.raises(ValueError, match="available yet"):
        prover.get_key()
    with pytest.raises(ValueError, match="verifier key"):
        prover.decrypt_message("AAAA")


def test_server_prover_guards() -> None:
    """Server prover helpers require initialization."""

    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64="c2FsdA==")
    with pytest.raises(ValueError, match="verifier key"):
        server.encrypt_message("x")
    with pytest.raises(ValueError, match="process_message1"):
        server.get_message2()
    with pytest.raises(ValueError, match="process_message1"):
        server.verify_message2("aa")


def test_server_prover_rejects_bad_confirmation() -> None:
    """A mismatched prover confirmation raises on the server side too."""

    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    client = TrustedDeviceBridgeProver()
    server = _TrustedDeviceBridgeServerProver(password="050044", salt_b64=salt_b64)
    client.init_with_salt(salt_b64, "050044")
    client_msg = client.get_message1()
    server_msg = server.get_message1()
    client.process_message1(server_msg)
    server.process_message1(client_msg)
    with pytest.raises(ValueError, match="invalid confirmation from client"):
        server.verify_message2("00")


def test_prover_hkdf_with_explicit_salt() -> None:
    """A non-empty salt takes the pass-through branch."""

    assert len(prover_mod._hkdf_like(b"ikm", b"salt", b"info", 20)) == 20


def test_prover_decode_point_compressed() -> None:
    """Compressed points decode using the curve equation."""

    gx = prover_mod._P256_GX
    gy = prover_mod._P256_GY
    prefix = "02" if gy % 2 == 0 else "03"
    encoded = prefix + format(gx, "064x")
    point = prover_mod._decode_point(encoded)
    assert point.x == gx
    assert point.y is not None
    assert point.y % 2 == gy % 2


def test_prover_multiply_and_scrypt() -> None:
    """Scalar multiplication and scrypt-derived scalars behave."""

    assert prover_mod._multiply_point(prover_mod._GENERATOR, 0) is prover_mod._INFINITY
    w0, w1 = prover_mod._compute_w0_w1("050044", base64.b64encode(b"salt").decode())
    assert isinstance(w0, int)
    assert isinstance(w1, int)


def test_prover_b64_helpers() -> None:
    """Base64 helper round-trips bytes."""

    assert prover_mod._b64_to_bytes(prover_mod._bytes_to_b64(b"hi")) == b"hi"


def test_prover_decrypt_rejects_bad_layout() -> None:
    """An unknown version byte raises a malformed-payload error."""

    prover = TrustedDeviceBridgeProver()
    prover._verifier_key = "00" * 32
    payload = base64.b64encode(bytes([9]) + b"data").decode("ascii")
    with pytest.raises(ValueError, match="Malformed bridge payload"):
        prover.decrypt_message(payload)
