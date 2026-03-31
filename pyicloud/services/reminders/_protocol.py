"""Pure protocol helpers for the Reminders service."""

from __future__ import annotations

import base64
import binascii
import json as _json
import logging
import time
import uuid
import zlib
from urllib.parse import urlparse

from .protobuf import reminders_pb2, versioned_document_pb2

LOGGER = logging.getLogger(__name__)


class CRDTDecodeError(ValueError):
    """Raised when a Reminders CRDT payload cannot be decoded."""


def _ref_name(fields, key: str) -> str:
    """Extract recordName from a REFERENCE field, or return ''."""
    field = fields.get_field(key)
    if field and field.value and hasattr(field.value, "recordName"):
        return field.value.recordName
    return ""


def _as_record_name(value: str, prefix: str) -> str:
    """Return a record name with the expected prefix (e.g. ``Alarm/UUID``)."""
    if not value:
        return value
    value = str(value)
    token = f"{prefix}/"
    if value.startswith(token):
        return value
    return f"{token}{value}"


def _as_raw_id(value: str, prefix: str) -> str:
    """Return a raw UUID/id token without the record prefix."""
    if not value:
        return value
    value = str(value)
    token = f"{prefix}/"
    if value.startswith(token):
        return value[len(token) :]
    return value


def _looks_like_url(value: str) -> bool:
    """Return True for values that already look like a URL."""
    if not value:
        return False

    parsed = urlparse(value)
    if not parsed.scheme:
        return False
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    return bool(parsed.netloc or parsed.path)


def _decode_attachment_url(value: str) -> str:
    """Decode a URL attachment value, falling back to the raw value."""
    if not value:
        return ""
    if _looks_like_url(value):
        return value

    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(
            f"{value}{padding}",
            validate=True,
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value

    if _looks_like_url(decoded):
        return decoded
    return value


def _encode_cloudkit_text_field(value: str) -> dict[str, str]:
    """Encode text for CloudKit fields that store UTF-8 payload bytes."""
    encoded = base64.b64encode((value or "").encode("utf-8")).decode("ascii")
    return {"type": "ENCRYPTED_BYTES", "value": encoded}


def _decode_cloudkit_text_value(value: object) -> str:
    """Decode a CloudKit text field value into plain ``str``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _decode_crdt_document(encrypted_value: str | bytes) -> str:
    """Decode a CRDT document (TitleDocument or NotesDocument)."""
    data = encrypted_value
    if isinstance(data, str):
        padding = 4 - (len(data) % 4)
        if padding != 4:
            data += "=" * padding
        try:
            data = base64.b64decode(data)
        except (binascii.Error, ValueError) as exc:
            raise CRDTDecodeError("Invalid base64-encoded CRDT document") from exc

    try:
        data = zlib.decompress(data)
    except zlib.error:
        try:
            import gzip as _gzip

            data = _gzip.decompress(data)
        except OSError as exc:
            LOGGER.debug("CRDT decompress skipped: %s (%s)", exc, data[:10])

    try:
        document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
        document.ParseFromString(data)
        if document.version:
            string_bytes = document.version[0].data
            value = reminders_pb2.String()  # type: ignore[attr-defined]
            value.ParseFromString(string_bytes)
            return value.string or ""
    except Exception as exc:  # pragma: no cover - fallback path
        LOGGER.debug("versioned_document.Document parse failed: %s", exc)

    try:
        version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
        version.ParseFromString(data)
        if version.data:
            value = reminders_pb2.String()  # type: ignore[attr-defined]
            value.ParseFromString(version.data)
            return value.string or ""
    except Exception as exc:  # pragma: no cover - fallback path
        LOGGER.debug("versioned_document.Version parse failed: %s", exc)

    try:
        value = reminders_pb2.String()  # type: ignore[attr-defined]
        value.ParseFromString(data)
        if value.string:
            return value.string
    except Exception as exc:  # pragma: no cover - legacy fallback path
        LOGGER.debug("bare String parse failed: %s", exc)

    raise CRDTDecodeError("Unable to decode CRDT document")


def _encode_crdt_document(text: str) -> str:
    """Encode a string into an Apple versioned topotext CRDT document."""
    text_length = len(text) if text else 0
    replica_uuid = bytes.fromhex("d46bcae41b8766c18d75efe35c9145c3")
    clock_max = 0xFFFF_FFFF

    value = reminders_pb2.String()  # type: ignore[attr-defined]
    value.string = text

    sentinel = value.substring.add()
    sentinel.charID.replicaID = 0
    sentinel.charID.clock = 0
    sentinel.length = 0
    sentinel.timestamp.replicaID = 0
    sentinel.timestamp.clock = 0
    sentinel.child.append(1)

    if text_length > 0:
        content = value.substring.add()
        content.charID.replicaID = 1
        content.charID.clock = 0
        content.length = text_length
        content.timestamp.replicaID = 1
        content.timestamp.clock = 0
        content.child.append(2)

    terminal = value.substring.add()
    terminal.charID.replicaID = 0
    terminal.charID.clock = clock_max
    terminal.length = 0
    terminal.timestamp.replicaID = 0
    terminal.timestamp.clock = clock_max

    timestamp_clock = value.timestamp.clock.add()
    timestamp_clock.replicaUUID = replica_uuid
    content_clock = timestamp_clock.replicaClock.add()
    content_clock.clock = text_length
    sentinel_clock = timestamp_clock.replicaClock.add()
    sentinel_clock.clock = 1

    if text_length > 0:
        attribute_run = value.attributeRun.add()
        attribute_run.length = text_length

    string_bytes = value.SerializeToString()

    version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
    version.serializationVersion = 0
    version.minimumSupportedVersion = 0
    version.data = string_bytes

    document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
    document.serializationVersion = 0
    document.version.append(version)
    doc_bytes = document.SerializeToString()

    compressed = zlib.compress(doc_bytes)
    return base64.b64encode(compressed).decode("utf-8")


def _generate_resolution_token_map(fields_modified: list[str]) -> str:
    """Generate a ResolutionTokenMap for a set of modified fields."""
    apple_epoch = time.time() - 978307200.0
    tokens = {}
    for field_name in fields_modified:
        tokens[field_name] = {
            "counter": 1,
            "modificationTime": apple_epoch,
            "replicaID": str(uuid.uuid4()).upper(),
        }
    return _json.dumps({"map": tokens}, separators=(",", ":"))
