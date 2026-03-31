"""Pure-Python bridge prover for Apple's trusted-device HSA2 flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SCRYPT_PARAMS = {
    "n": 16384,
    "r": 8,
    "p": 1,
    "dklen": 64,
}
_CLIENT_IDENTITY = b"com.apple.security.webprover"
_SERVER_IDENTITY = b"com.apple.security.webverifier"
_SPAKE2_CONTEXT = b"SPAKE2Web"
_KEY_LENGTH = 32
_VERIFIER_KEY_INFO = b"webVerifier"
_PROVER_KEY_INFO = b"webProver"

_P256_P = int("FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF", 16)
_P256_A = (_P256_P - 3) % _P256_P
_P256_B = int("5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B", 16)
_P256_ORDER = int(
    "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16
)
_P256_GX = int("6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296", 16)
_P256_GY = int("4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5", 16)
_SPAKE2_M = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"
_SPAKE2_N = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"
_AES_GCM_LAYOUTS = {0: (12, 16)}


@dataclass(frozen=True)
class _Point:
    x: Optional[int]
    y: Optional[int]

    @property
    def is_infinity(self) -> bool:
        return self.x is None or self.y is None


_INFINITY = _Point(None, None)
_GENERATOR = _Point(_P256_GX, _P256_GY)


def _int_to_bytes(value: int, length: Optional[int] = None) -> bytes:
    if length is None:
        length = max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(length, "big")


def _b64_to_bytes(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _bytes_to_b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _encode_point(point: _Point) -> str:
    if point.is_infinity:
        raise ValueError("Cannot encode the point at infinity.")
    return "04" + _int_to_bytes(point.x, 32).hex() + _int_to_bytes(point.y, 32).hex()


def _decode_point(value: str) -> _Point:
    raw = bytes.fromhex(value)
    if len(raw) == 65 and raw[0] == 0x04:
        point = _Point(
            int.from_bytes(raw[1:33], "big"),
            int.from_bytes(raw[33:65], "big"),
        )
    elif len(raw) == 33 and raw[0] in (0x02, 0x03):
        x_coord = int.from_bytes(raw[1:], "big")
        rhs = (pow(x_coord, 3, _P256_P) + _P256_A * x_coord + _P256_B) % _P256_P
        y_coord = pow(rhs, (_P256_P + 1) // 4, _P256_P)
        if y_coord & 1 != raw[0] & 1:
            y_coord = (-y_coord) % _P256_P
        point = _Point(x_coord, y_coord)
    else:
        raise ValueError("Unsupported P-256 point encoding.")

    if not _is_on_curve(point):
        raise ValueError("Invalid P-256 point.")
    return point


def _is_on_curve(point: _Point) -> bool:
    if point.is_infinity:
        return False
    assert point.x is not None and point.y is not None
    return (
        pow(point.y, 2, _P256_P)
        - (pow(point.x, 3, _P256_P) + _P256_A * point.x + _P256_B)
    ) % _P256_P == 0


def _negate(point: _Point) -> _Point:
    if point.is_infinity:
        return point
    assert point.x is not None and point.y is not None
    return _Point(point.x, (-point.y) % _P256_P)


def _add_points(left: _Point, right: _Point) -> _Point:
    if left.is_infinity:
        return right
    if right.is_infinity:
        return left

    assert left.x is not None and left.y is not None
    assert right.x is not None and right.y is not None

    if left.x == right.x and (left.y + right.y) % _P256_P == 0:
        return _INFINITY

    if left.x == right.x and left.y == right.y:
        if left.y == 0:
            return _INFINITY
        slope = (
            (3 * left.x * left.x + _P256_A) * pow(2 * left.y, -1, _P256_P)
        ) % _P256_P
    else:
        slope = ((right.y - left.y) * pow(right.x - left.x, -1, _P256_P)) % _P256_P

    x_coord = (slope * slope - left.x - right.x) % _P256_P
    y_coord = (slope * (left.x - x_coord) - left.y) % _P256_P
    return _Point(x_coord, y_coord)


def _multiply_point(point: _Point, scalar: int) -> _Point:
    scalar %= _P256_ORDER
    result = _INFINITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _add_points(result, addend)
        addend = _add_points(addend, addend)
        scalar >>= 1
    return result


def _concat_length_prefixed(*parts: bytes) -> bytes:
    output = bytearray()
    for part in parts:
        output.extend(len(part).to_bytes(8, "little"))
        output.extend(part)
    return bytes(output)


def _hkdf_like(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    hash_len = hashlib.sha256().digest_size
    if not salt:
        salt = b"\x00" * hash_len
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    blocks = bytearray()
    previous = b""
    counter = 1
    while len(blocks) < length:
        previous = hmac.new(
            prk,
            previous + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        blocks.extend(previous)
        counter += 1
    return bytes(blocks[:length])


def _confirmation_key_length(info: bytes, requested_length: int) -> int:
    if b"ConfirmationKeys" in info:
        return 64
    return requested_length


def _derive_key(ikm: bytes, info: bytes, length: int = 64) -> bytes:
    return _hkdf_like(
        ikm=ikm,
        salt=b"",
        info=info,
        length=_confirmation_key_length(info, length),
    )


def _derive_prover_and_verifier_keys(raw_key_hex: str) -> tuple[str, str]:
    raw_key = bytes.fromhex(raw_key_hex)
    verifier_key = _derive_key(raw_key, _VERIFIER_KEY_INFO, _KEY_LENGTH)
    prover_key = _derive_key(raw_key, _PROVER_KEY_INFO, _KEY_LENGTH)
    return verifier_key.hex(), prover_key.hex()


@dataclass(frozen=True)
class _ClientSharedSecret:
    transcript: bytes
    share_p: str
    share_v: str

    def __post_init__(self) -> None:
        digest = hashlib.sha256(self.transcript).digest()
        object.__setattr__(self, "_hash_transcript", digest)
        confirmations = _derive_key(digest, b"ConfirmationKeys", 64)
        object.__setattr__(self, "_confirm_client", confirmations[:32])
        object.__setattr__(self, "_confirm_server", confirmations[32:])
        shared_key = _derive_key(digest, b"SharedKey", _KEY_LENGTH)
        object.__setattr__(self, "_shared_key", shared_key)

    def get_confirmation(self) -> str:
        return hmac.new(
            self._confirm_client,
            bytes.fromhex(self.share_v),
            hashlib.sha256,
        ).hexdigest()

    def verify(self, message_hex: str) -> bytes:
        expected = hmac.new(
            self._confirm_server,
            bytes.fromhex(self.share_p),
            hashlib.sha256,
        ).hexdigest()
        if expected != message_hex:
            raise ValueError("invalid confirmation from server")
        return self._shared_key


@dataclass(frozen=True)
class _ServerSharedSecret:
    transcript: bytes
    share_p: str
    share_v: str

    def __post_init__(self) -> None:
        digest = hashlib.sha256(self.transcript).digest()
        confirmations = _derive_key(digest, b"ConfirmationKeys", 64)
        object.__setattr__(self, "_confirm_client", confirmations[:32])
        object.__setattr__(self, "_confirm_server", confirmations[32:])
        object.__setattr__(
            self,
            "_shared_key",
            _derive_key(digest, b"SharedKey", _KEY_LENGTH),
        )

    def get_confirmation(self) -> str:
        return hmac.new(
            self._confirm_server,
            bytes.fromhex(self.share_p),
            hashlib.sha256,
        ).hexdigest()

    def verify(self, message_hex: str) -> bytes:
        expected = hmac.new(
            self._confirm_client,
            bytes.fromhex(self.share_v),
            hashlib.sha256,
        ).hexdigest()
        if expected != message_hex:
            raise ValueError("invalid confirmation from client")
        return self._shared_key


class _ClientHandshake:
    def __init__(
        self,
        *,
        x_scalar: int,
        w0: int,
        w1: int,
    ) -> None:
        self._x = x_scalar
        self._w0 = w0
        self._w1 = w1
        self._message1_point: Optional[_Point] = None
        self.share_p: Optional[str] = None

    def get_message(self) -> str:
        point = _add_points(
            _multiply_point(_GENERATOR, self._x),
            _multiply_point(_decode_point(_SPAKE2_M), self._w0),
        )
        self._message1_point = point
        self.share_p = _encode_point(point)
        return self.share_p

    def finish(self, server_message_hex: str) -> _ClientSharedSecret:
        if self._message1_point is None or self.share_p is None:
            raise ValueError("get_message must be called before finish")

        server_point = _decode_point(server_message_hex)
        if server_point.is_infinity:
            raise ValueError("invalid curve point")

        adjusted = _add_points(
            server_point,
            _negate(_multiply_point(_decode_point(_SPAKE2_N), self._w0)),
        )
        y_point = _multiply_point(adjusted, self._x)
        v_point = _multiply_point(adjusted, self._w1)
        transcript = _concat_length_prefixed(
            _SPAKE2_CONTEXT,
            _CLIENT_IDENTITY,
            _SERVER_IDENTITY,
            bytes.fromhex(_encode_point(_decode_point(_SPAKE2_M))),
            bytes.fromhex(_encode_point(_decode_point(_SPAKE2_N))),
            bytes.fromhex(_encode_point(self._message1_point)),
            bytes.fromhex(_encode_point(server_point)),
            bytes.fromhex(_encode_point(y_point)),
            bytes.fromhex(_encode_point(v_point)),
            _int_to_bytes(self._w0),
        )
        return _ClientSharedSecret(
            transcript=transcript,
            share_p=self.share_p,
            share_v=server_message_hex,
        )


class _ServerHandshake:
    def __init__(
        self,
        *,
        y_scalar: int,
        w0: int,
        verifier_point: _Point,
    ) -> None:
        self._y = y_scalar
        self._w0 = w0
        self._verifier_point = verifier_point
        self._message1_point: Optional[_Point] = None
        self.share_v: Optional[str] = None

    def get_message(self) -> str:
        point = _add_points(
            _multiply_point(_GENERATOR, self._y),
            _multiply_point(_decode_point(_SPAKE2_N), self._w0),
        )
        self._message1_point = point
        self.share_v = _encode_point(point)
        return self.share_v

    def finish(self, client_message_hex: str) -> _ServerSharedSecret:
        if self._message1_point is None or self.share_v is None:
            raise ValueError("get_message must be called before finish")

        client_point = _decode_point(client_message_hex)
        if client_point.is_infinity:
            raise ValueError("invalid curve point")

        adjusted = _add_points(
            client_point,
            _negate(_multiply_point(_decode_point(_SPAKE2_M), self._w0)),
        )
        y_point = _multiply_point(adjusted, self._y)
        verifier_share = _multiply_point(self._verifier_point, self._y)
        transcript = _concat_length_prefixed(
            _SPAKE2_CONTEXT,
            _CLIENT_IDENTITY,
            _SERVER_IDENTITY,
            bytes.fromhex(_encode_point(_decode_point(_SPAKE2_M))),
            bytes.fromhex(_encode_point(_decode_point(_SPAKE2_N))),
            bytes.fromhex(_encode_point(client_point)),
            bytes.fromhex(_encode_point(self._message1_point)),
            bytes.fromhex(_encode_point(y_point)),
            bytes.fromhex(_encode_point(verifier_share)),
            _int_to_bytes(self._w0),
        )
        return _ServerSharedSecret(
            transcript=transcript,
            share_p=client_message_hex,
            share_v=self.share_v,
        )


def _compute_w0_w1(password: str, salt_b64: str) -> tuple[int, int]:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=_b64_to_bytes(salt_b64),
        **_SCRYPT_PARAMS,
    )
    midpoint = len(derived) // 2
    return (
        int.from_bytes(derived[:midpoint], "big"),
        int.from_bytes(derived[midpoint:], "big"),
    )


class TrustedDeviceBridgeProver:
    """Client-side prover mirroring Apple's prover worker."""

    def __init__(self) -> None:
        self._client: Optional[_ClientHandshake] = None
        self._shared_secret: Optional[_ClientSharedSecret] = None
        self._raw_key: Optional[str] = None
        self._verified = False
        self._verifier_key: Optional[str] = None
        self._prover_key: Optional[str] = None

    def init_with_salt(self, salt_b64: str, code: str) -> None:
        w0, w1 = _compute_w0_w1(code, salt_b64)
        self._client = _ClientHandshake(
            x_scalar=secrets.randbelow(_P256_ORDER),
            w0=w0,
            w1=w1,
        )
        self._shared_secret = None
        self._raw_key = None
        self._verified = False
        self._verifier_key = None
        self._prover_key = None

    def get_message1(self) -> str:
        if self._client is None:
            raise ValueError("init_with_salt must be called before get_message1")
        return self._client.get_message()

    def process_message1(self, message_hex: str) -> str:
        if self._client is None:
            raise ValueError("init_with_salt must be called before process_message1")
        self._shared_secret = self._client.finish(message_hex)
        return self.get_message2()

    def get_message2(self) -> str:
        if self._shared_secret is None:
            raise ValueError("process_message1 must be called before get_message2")
        return self._shared_secret.get_confirmation()

    def process_message2(self, message_hex: str) -> dict[str, object]:
        if self._shared_secret is None:
            raise ValueError("process_message1 must be called before process_message2")
        raw_key = self._shared_secret.verify(message_hex).hex()
        self._raw_key = raw_key
        self._verifier_key, self._prover_key = _derive_prover_and_verifier_keys(raw_key)
        self._verified = True
        return {"isVerified": True, "key": raw_key}

    def is_verified(self) -> bool:
        return self._verified

    def get_key(self) -> str:
        if self._raw_key is None:
            raise ValueError("No bridge key is available yet.")
        return self._raw_key

    def decrypt_message(self, ciphertext_b64: str) -> str:
        if self._verifier_key is None:
            raise ValueError("Bridge verifier key is not available.")
        payload = _b64_to_bytes(ciphertext_b64)
        version = payload[0]
        iv_length, tag_length = _AES_GCM_LAYOUTS[version]
        iv = payload[1 : 1 + iv_length]
        tag = payload[1 + iv_length : 1 + iv_length + tag_length]
        ciphertext = payload[1 + iv_length + tag_length :]
        plaintext = AESGCM(bytes.fromhex(self._verifier_key)).decrypt(
            iv,
            ciphertext + tag,
            bytes([version]),
        )
        return plaintext.decode("utf-8")


class _TrustedDeviceBridgeServerProver:
    """Internal test helper mirroring Apple's server-side bridge flow."""

    def __init__(self, *, password: str, salt_b64: str) -> None:
        w0, w1 = _compute_w0_w1(password, salt_b64)
        verifier_point = _multiply_point(_GENERATOR, w1)
        self._server = _ServerHandshake(
            y_scalar=secrets.randbelow(_P256_ORDER),
            w0=w0,
            verifier_point=verifier_point,
        )
        self._shared_secret: Optional[_ServerSharedSecret] = None
        self._raw_key: Optional[str] = None
        self._verifier_key: Optional[str] = None
        self._prover_key: Optional[str] = None

    def get_message1(self) -> str:
        return self._server.get_message()

    def process_message1(self, client_message_hex: str) -> str:
        self._shared_secret = self._server.finish(client_message_hex)
        return self.get_message2()

    def get_message2(self) -> str:
        if self._shared_secret is None:
            raise ValueError("process_message1 must be called before get_message2")
        return self._shared_secret.get_confirmation()

    def verify_message2(self, message_hex: str) -> str:
        if self._shared_secret is None:
            raise ValueError("process_message1 must be called before verify_message2")
        raw_key = self._shared_secret.verify(message_hex).hex()
        self._raw_key = raw_key
        self._verifier_key, self._prover_key = _derive_prover_and_verifier_keys(raw_key)
        return raw_key

    def encrypt_message(self, plaintext: str) -> str:
        if self._verifier_key is None:
            raise ValueError("Bridge verifier key is not available.")
        version = 0
        iv_length, tag_length = _AES_GCM_LAYOUTS[version]
        iv = secrets.token_bytes(iv_length)
        encrypted = AESGCM(bytes.fromhex(self._verifier_key)).encrypt(
            iv,
            plaintext.encode("utf-8"),
            bytes([version]),
        )
        ciphertext = encrypted[:-tag_length]
        tag = encrypted[-tag_length:]
        payload = bytes([version]) + iv + tag + ciphertext
        return _bytes_to_b64(payload)
