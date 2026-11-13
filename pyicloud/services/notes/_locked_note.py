"""Parse and authenticate the observed custom-password Notes envelope."""

from dataclasses import dataclass, field
from hashlib import pbkdf2_hmac
import plistlib
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap

_MAX_ARCHIVE_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 4096
_MAX_ITERATIONS = 1_000_000
_BINARY_PLIST = b"bplist00"
_INVALID = "Unsupported custom-password Notes envelope"
_DECRYPT_FAILED = "Could not authenticate the locked Notes body"
_NONCE_BYTES = 32
_TAG_BYTES = 16
_TRAILER_BYTES = _NONCE_BYTES + _TAG_BYTES


@dataclass(frozen=True)
class LockedNoteEnvelope:
    """Validated cipher-v2 components; keep bytes and identifiers out of repr."""

    salt: bytes = field(repr=False)
    iterations: int
    metadata: bytes = field(repr=False)
    wrapped_key: bytes = field(repr=False)
    encrypted_data: bytes = field(repr=False)


def _read_plist(data: bytes, limit: int) -> dict[str, Any]:
    if len(data) > limit or not data.startswith(_BINARY_PLIST):
        raise ValueError(_INVALID)
    try:
        parsed = plistlib.loads(data)
    except (plistlib.InvalidFileException, OverflowError, RecursionError, ValueError):
        raise ValueError(_INVALID) from None
    if not isinstance(parsed, dict):
        raise ValueError(_INVALID)
    return parsed


def _resolve(objects: list[Any], reference: object) -> Any:
    if not isinstance(reference, plistlib.UID) or not 0 < reference.data < len(objects):
        raise ValueError(_INVALID)
    return objects[reference.data]


def _read_bytes(objects: list[Any], root: dict[str, Any], name: str) -> bytes:
    value = _resolve(objects, root.get(name))
    if not isinstance(value, bytes):
        raise ValueError(_INVALID)
    return value


def parse_locked_note_envelope(data: bytes) -> LockedNoteEnvelope:
    """Recognize the observed archive, not the outer CloudKit crypto fields."""
    archive = _read_plist(data, _MAX_ARCHIVE_BYTES)
    objects = archive.get("$objects")
    top = archive.get("$top")
    if (
        archive.get("$archiver") != "NSKeyedArchiver"
        or archive.get("$version") != 100000
        or not isinstance(objects, list)
        or not 1 <= len(objects) <= 32
        or not isinstance(top, dict)
    ):
        raise ValueError(_INVALID)
    root = _resolve(objects, top.get("root"))
    if not isinstance(root, dict):
        raise ValueError(_INVALID)
    class_info = _resolve(objects, root.get("$class"))
    if (
        not isinstance(class_info, dict)
        or class_info.get("$classname") != "ICCryptoEncryptionObject"
    ):
        raise ValueError(_INVALID)

    metadata = _read_bytes(objects, root, "metadata")
    descriptor = _read_plist(metadata, _MAX_METADATA_BYTES)
    if descriptor.get("cipherVersion") != 2:
        raise ValueError(_INVALID)
    params = _read_plist(
        _read_bytes(objects, root, "unauthenticatedMetadata"), _MAX_METADATA_BYTES
    )
    salt = params.get("passphraseSalt")
    iterations = params.get("passphraseIterationCount")
    wrapped_key = _read_bytes(objects, root, "wrappedEncryptionKey")
    encrypted_data = _read_bytes(objects, root, "encryptedData")
    if (
        not isinstance(salt, bytes)
        or len(salt) != 32
        or not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or not 1 <= iterations <= _MAX_ITERATIONS
        or len(wrapped_key) != 40
        or len(encrypted_data) < _TRAILER_BYTES
    ):
        raise ValueError(_INVALID)
    return LockedNoteEnvelope(salt, iterations, metadata, wrapped_key, encrypted_data)


def decrypt_locked_note_body(data: bytes, password: str) -> bytes:
    """Return authenticated body bytes using one caller-supplied password."""
    envelope = parse_locked_note_envelope(data)
    try:
        password_bytes = password.encode("utf-8")
        wrapping_key = pbkdf2_hmac(
            "sha256", password_bytes, envelope.salt, envelope.iterations, dklen=32
        )
        content_key = aes_key_unwrap(wrapping_key, envelope.wrapped_key)
        framed = envelope.encrypted_data
        nonce = framed[-_TRAILER_BYTES:-_TAG_BYTES]
        ciphertext_and_tag = framed[:-_TRAILER_BYTES] + framed[-_TAG_BYTES:]
        return AESGCM(content_key).decrypt(nonce, ciphertext_and_tag, envelope.metadata)
    except (UnicodeEncodeError, InvalidUnwrap, InvalidTag):
        raise ValueError(_DECRYPT_FAILED) from None
