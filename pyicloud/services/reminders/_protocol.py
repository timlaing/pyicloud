"""Pure protocol helpers for the Reminders service."""

from __future__ import annotations

import base64
import gzip
import json as _json
import logging
import plistlib
import time
from typing import Any, cast
from urllib.parse import urlparse
import uuid
from xml.parsers.expat import ExpatError
import zlib

from pydantic import ValidationError

from pyicloud.common.cloudkit.models import CKFields

from .models import DateComponents
from .protobuf import reminders_pb2, versioned_document_pb2

LOGGER = logging.getLogger(__name__)


class CRDTDecodeError(ValueError):
    """Raised when a Reminders CRDT payload cannot be decoded."""


def _ref_name(fields: CKFields, key: str) -> str:
    """Extract recordName from a REFERENCE field, or return ''."""
    field = fields.get_field(key)
    if field and field.value and hasattr(field.value, "recordName"):
        return cast(str, field.value.recordName)
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
    except ValueError:
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


# NSKeyedArchiver keys used to encode an NSDateComponents instance.
_NSDATE_COMPONENT_FIELDS: dict[str, str] = {
    "NS.era": "era",
    "NS.year": "year",
    "NS.month": "month",
    "NS.day": "day",
    "NS.hour": "hour",
    "NS.minute": "minute",
    "NS.second": "second",
    "NS.nanosecond": "nanosecond",
    "NS.weekday": "weekday",
    "NS.weekdayOrdinal": "weekday_ordinal",
    "NS.weekOfMonth": "week_of_month",
    "NS.weekOfYear": "week_of_year",
    "NS.yearForWeekOfYear": "year_for_week_of_year",
    "NS.quarter": "quarter",
    "NS.leapMonth": "leap_month",
    "NS.repeatedDay": "repeated_day",
}


def _archive_object(value: object, objects: list[object]) -> object:
    """Resolve an ``NSKeyedArchiver`` object reference (a ``plistlib.UID``)."""
    if isinstance(value, plistlib.UID):
        index = value.data
        if 0 <= index < len(objects):
            return objects[index]
        return None
    return value


def _decode_nsdatecomponents(value: object) -> DateComponents | None:
    """Decode the archived ``NSDateComponents`` in a ``DateComponentsData`` field.

    Reminders stores a date alarm trigger as an ``NSKeyedArchiver`` binary
    plist wrapping an ``NSDateComponents`` instance. The archive carries the
    date/time components and the rule's time zone, so callers can reconstruct
    when the alarm fires. Returns ``None`` when the payload is missing or is
    not a recognisable ``NSKeyedArchiver`` archive.
    """
    decoded_bytes = _extract_archive_bytes(value)
    if decoded_bytes is None:
        return None

    root, objects = _extract_nskeyed_archiver_root(decoded_bytes)
    if root is None or objects is None or not isinstance(root, dict):
        return None

    decoded = _extract_nsdate_components(root)
    time_zone = _archive_object(root.get("NS.timezone"), objects)
    if isinstance(time_zone, dict):
        tz_name = _archive_object(time_zone.get("NS.name"), objects)
        if isinstance(tz_name, str):
            decoded["time_zone"] = tz_name

    if not decoded:
        return None
    try:
        return DateComponents.model_validate(decoded)
    except ValidationError:
        return None


def _extract_archive_bytes(value: object) -> bytes | None:
    """Convert the possible encoded value to bytes."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=False)
        except (ValueError, TypeError):
            return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return None


def _extract_nskeyed_archiver_root(
    value: bytes,
) -> tuple[dict[str, object], list[object]] | tuple[None, None]:
    """Extract the root object and objects list from an NSKeyedArchiver plist."""
    try:
        plist = plistlib.loads(value)
    except (ValueError, TypeError, ExpatError):
        return None, None
    if not isinstance(plist, dict) or plist.get("$archiver") != "NSKeyedArchiver":
        return None, None
    objects = plist.get("$objects")
    top = plist.get("$top")
    if not isinstance(objects, list) or not isinstance(top, dict):
        return None, None
    root = _archive_object(top.get("root"), objects)
    if not isinstance(root, dict):
        return None, None
    return root, objects


def _extract_nsdate_components(root: dict[str, object]) -> dict[str, object]:
    """Extract DateComponents fields from the root."""
    decoded: dict[str, object] = {}
    for archive_key, field_name in _NSDATE_COMPONENT_FIELDS.items():
        if archive_key in root:
            decoded[field_name] = root[archive_key]
    return decoded


def _decode_crdt_document(  # noqa: S3776
    encrypted_value: str | bytes,
) -> str:
    """Decode a CRDT document (TitleDocument or NotesDocument)."""
    data = encrypted_value
    if isinstance(data, str):
        padding = 4 - (len(data) % 4)
        if padding != 4:
            data += "=" * padding
        try:
            data = base64.b64decode(data)
        except ValueError as exc:
            raise CRDTDecodeError("Invalid base64-encoded CRDT document") from exc

    try:
        data = zlib.decompress(data)
    except zlib.error:
        try:
            data = gzip.decompress(data)
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
    except Exception as exc:
        LOGGER.debug("versioned_document.Document parse failed: %s", exc)

    try:
        version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
        version.ParseFromString(data)
        if version.data:
            value = reminders_pb2.String()  # type: ignore[attr-defined]
            value.ParseFromString(version.data)
            return value.string or ""
    except Exception as exc:
        LOGGER.debug("versioned_document.Version parse failed: %s", exc)

    try:
        value = reminders_pb2.String()  # type: ignore[attr-defined]
        value.ParseFromString(data)
        if value.string:
            return cast(str, value.string)
    except Exception as exc:
        LOGGER.debug("bare String parse failed: %s", exc)

    raise CRDTDecodeError("Unable to decode CRDT document")


def _encode_crdt_document(text: str) -> str:
    """Encode a string into an Apple versioned topotext CRDT document."""
    # Apple measures topotext offsets in UTF-16 code units, not codepoints.
    # An astral character (emoji, rarer CJK) is one codepoint but two units,
    # so len() under-declares every length this document carries.
    text_length = len(text.encode("utf-16-le")) // 2 if text else 0
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


def _utf16_length(text: str) -> int:
    """Return the length of ``text`` in UTF-16 code units, as topotext counts it."""
    return len(text.encode("utf-16-le")) // 2 if text else 0


def _parse_crdt_string(encoded: str | bytes) -> Any:
    """Parse a TitleDocument/NotesDocument payload into a topotext ``String``.

    Unlike :func:`_decode_crdt_document`, this keeps the full CRDT structure
    (substrings, vector clock, attribute runs) so it can be edited in place.
    It accepts the same wrappers, in the same order: a versioned ``Document``,
    a bare ``Version`` and a bare ``String``.
    """
    data = encoded
    if isinstance(data, str):
        data += "=" * (-len(data) % 4)
        try:
            data = base64.b64decode(data)
        except ValueError as exc:
            raise CRDTDecodeError("Invalid base64-encoded CRDT document") from exc

    for decompress in (zlib.decompress, gzip.decompress):
        try:
            data = decompress(data)
            break
        except (zlib.error, OSError, EOFError):
            continue

    try:
        document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
        document.ParseFromString(data)
        if document.version:
            value = reminders_pb2.String()  # type: ignore[attr-defined]
            value.ParseFromString(document.version[0].data)
            if value.substring:
                return value
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("versioned_document.Document parse failed: %s", exc)

    # A bare String and a bare Version share field numbers, so either parses
    # as the other; only accept a candidate that carries both substrings and
    # a vector clock.
    for unwrap in (_bare_string_bytes, _version_string_bytes):
        try:
            value = reminders_pb2.String()  # type: ignore[attr-defined]
            value.ParseFromString(unwrap(data))
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("%s parse failed: %s", unwrap.__name__, exc)
            continue
        if value.substring and value.timestamp.clock:
            return value
    raise CRDTDecodeError("CRDT document has no editable structure")


def _bare_string_bytes(data: bytes) -> bytes:
    """Return ``data`` unchanged: the payload is a bare topotext ``String``."""
    return data


def _version_string_bytes(data: bytes) -> bytes:
    """Return the ``String`` bytes held by a bare ``versioned_document.Version``."""
    version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
    version.ParseFromString(data)
    return cast(bytes, version.data)


def _serialize_crdt_string(value: Any) -> str:
    """Wrap a topotext ``String`` in a versioned document, compressed and base64."""
    version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
    version.serializationVersion = 0
    version.minimumSupportedVersion = 0
    version.data = value.SerializeToString()

    document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
    document.serializationVersion = 0
    document.version.append(version)
    return base64.b64encode(zlib.compress(document.SerializeToString())).decode("utf-8")


def _reorder_substrings(value: Any) -> None:
    """Store substrings in document order, so children follow their parent.

    The Reminders app writes substrings in the order a depth-first walk from
    the start sentinel visits them; edits keep that layout.
    """
    original = list(value.substring)
    order: list[int] = []
    seen: set[int] = set()
    stack = [0]
    while stack:
        index = stack.pop()
        if index in seen or index >= len(original):
            continue
        seen.add(index)
        order.append(index)
        stack.extend(reversed(list(original[index].child)))
    order.extend(index for index in range(len(original)) if index not in seen)

    position = {old: new for new, old in enumerate(order)}
    reordered = []
    for old in order:
        substring = type(original[old])()
        substring.CopyFrom(original[old])
        children = [position[child] for child in substring.child if child in position]
        del substring.child[:]
        substring.child.extend(children)
        reordered.append(substring)
    del value.substring[:]
    value.substring.extend(reordered)


def _edit_crdt_document(  # noqa: S3776
    existing: str | bytes,
    text: str,
    *,
    replica_uuid: bytes | None = None,
) -> str:
    """Replace the visible text of an existing topotext document as a CRDT edit.

    Apple devices merge TitleDocument/NotesDocument as CRDTs: characters are
    identified by ``(replica, clock)`` and a device ignores characters it has
    already seen. Re-encoding a fresh document therefore never reaches a
    device that knows the old one. This helper instead records the edit the
    way the Reminders app does:

    * a new replica is added to the vector clock (kept sorted by UUID, with
      character and style references remapped accordingly);
    * every visible substring is tombstoned;
    * the new text is inserted right after the start sentinel, with character
      clocks continuing after the highest clock any replica has used.
    """
    value = _parse_crdt_string(existing)
    clocks = list(value.timestamp.clock)
    if not clocks:
        raise CRDTDecodeError("CRDT document has no vector clock")

    new_uuid = replica_uuid or uuid.uuid4().bytes
    old_uuids = [bytes(clock.replicaUUID) for clock in clocks]
    if new_uuid in old_uuids:
        raise ValueError("replica_uuid is already present in the document")

    ordered = sorted([*old_uuids, new_uuid])
    remap = {index + 1: ordered.index(uid) + 1 for index, uid in enumerate(old_uuids)}
    replica_id = ordered.index(new_uuid) + 1

    def _remap(char_id: Any) -> None:
        if char_id.replicaID:
            char_id.replicaID = remap.get(char_id.replicaID, char_id.replicaID)

    for substring in value.substring:
        _remap(substring.charID)
        _remap(substring.timestamp)

    next_char_clock = max(
        (clock.replicaClock[0].clock for clock in clocks if clock.replicaClock),
        default=0,
    )
    text_length = _utf16_length(text)
    style_clock = 0

    deleted_any = False
    for substring in value.substring:
        is_marker = substring.charID.replicaID == 0
        if is_marker or substring.tombstone or substring.length == 0:
            continue
        substring.tombstone = True
        substring.timestamp.replicaID = replica_id
        substring.timestamp.clock = 1
        deleted_any = True

    if text_length:
        sentinel = next(
            (
                substring
                for substring in value.substring
                if substring.charID.replicaID == 0 and substring.charID.clock == 0
            ),
            None,
        )
        if sentinel is None:
            raise CRDTDecodeError("CRDT document has no start sentinel")
        inserted = value.substring.add()
        inserted.charID.replicaID = replica_id
        inserted.charID.clock = next_char_clock
        inserted.length = text_length
        inserted.timestamp.replicaID = replica_id
        inserted.timestamp.clock = 0
        inserted.child.extend(sentinel.child)
        del sentinel.child[:]
        sentinel.child.append(len(value.substring) - 1)
        style_clock = 1
    if deleted_any:
        style_clock = 2

    by_uuid = {bytes(clock.replicaUUID): clock for clock in clocks}
    del value.timestamp.clock[:]
    for uid in ordered:
        clock = value.timestamp.clock.add()
        if uid == new_uuid:
            clock.replicaUUID = new_uuid
            clock.replicaClock.add().clock = next_char_clock + text_length
            clock.replicaClock.add().clock = style_clock
        else:
            clock.CopyFrom(by_uuid[uid])

    _reorder_substrings(value)
    value.string = text
    del value.attributeRun[:]
    if text_length:
        value.attributeRun.add().length = text_length

    return _serialize_crdt_string(value)


def _merge_resolution_token_map(
    existing: str | None,
    fields_modified: list[str],
    *,
    replica_id: str | None = None,
) -> str:
    """Bump the ResolutionTokenMap counters of ``fields_modified``.

    Devices resolve field conflicts per field by comparing these counters, so
    a write must carry a counter higher than the one already stored. Tokens
    for fields that are not being written are kept as they are.
    """
    data: dict[str, object] = {}
    if existing:
        try:
            parsed = _json.loads(existing)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            LOGGER.debug("Ignoring unparsable ResolutionTokenMap")

    current = data.get("map")
    tokens: dict[str, object] = dict(current) if isinstance(current, dict) else {}
    apple_epoch = time.time() - 978307200.0
    replica = replica_id or str(uuid.uuid4()).upper()
    for field_name in fields_modified:
        previous = tokens.get(field_name)
        counter = 0
        if isinstance(previous, dict):
            try:
                counter = int(previous.get("counter", 0))
            except (TypeError, ValueError):
                counter = 0
        tokens[field_name] = {
            "counter": counter + 1,
            "modificationTime": apple_epoch,
            "replicaID": replica,
        }
    data["map"] = tokens
    return _json.dumps(data, separators=(",", ":"))


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
