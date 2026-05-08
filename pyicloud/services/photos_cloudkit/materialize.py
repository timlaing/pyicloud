"""Local materialization helpers for the Photos sync engine."""

from __future__ import annotations

import base64
import json
import logging
import plistlib
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .mappers import decode_encrypted_text, record_field_value

LOGGER = logging.getLogger(__name__)
RAW_EXTENSIONS = frozenset(
    {
        ".arw",
        ".cr2",
        ".cr3",
        ".crw",
        ".dng",
        ".nef",
        ".nrf",
        ".nrw",
        ".orf",
        ".pef",
        ".raf",
        ".rw2",
    }
)
PYICLOUD_XMP_TOOLKIT = "pyicloud photos-cloudkit"


@dataclass(slots=True)
class PhotoXmpMetadata:
    """Metadata exported into XMP sidecars."""

    toolkit: str
    title: str | None = None
    description: str | None = None
    orientation: int | None = None
    make: str | None = None
    digital_source_type: str | None = None
    keywords: list[str] | None = None
    gps_altitude: float | None = None
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    gps_speed: float | None = None
    gps_timestamp: datetime | None = None
    create_date: datetime | None = None
    rating: int | None = None


def resource_is_raw(resource: Any) -> bool:
    """Return ``True`` when a resource looks like a RAW image."""

    resource_type = (getattr(resource, "type", None) or "").lower()
    if "raw" in resource_type:
        return True
    suffix = Path(getattr(resource, "filename", "")).suffix.lower()
    return suffix in RAW_EXTENSIONS


def apply_align_raw_policy(resources: dict[str, Any], policy: str) -> dict[str, Any]:
    """Return a resource mapping with RAW+JPEG original/alternative aligned."""

    aligned = dict(resources)
    if policy == "as-is":
        return aligned

    original = aligned.get("original")
    alternative = aligned.get("alternative")
    if original is None or alternative is None:
        return aligned

    original_is_raw = resource_is_raw(original)
    alternative_is_raw = resource_is_raw(alternative)
    if policy == "original" and alternative_is_raw and not original_is_raw:
        aligned["original"], aligned["alternative"] = alternative, original
    elif policy == "alternative" and original_is_raw and not alternative_is_raw:
        aligned["original"], aligned["alternative"] = alternative, original
    return aligned


def set_exif_datetime_if_missing(path: Path, taken_at: datetime) -> None:
    """Write EXIF created timestamps for JPEGs that do not have them yet."""

    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        return
    try:
        data = path.read_bytes()
    except OSError:
        LOGGER.debug("Failed to read %s for EXIF update", path)
        return

    if _jpeg_has_exif_datetime(data):
        return

    updated = _insert_exif_datetime_segment(
        jpeg_bytes=data,
        timestamp=taken_at.astimezone().strftime("%Y:%m:%d %H:%M:%S"),
    )
    if updated is None:
        LOGGER.debug("Failed to update EXIF datetime on %s", path)
        return

    try:
        path.write_bytes(updated)
    except OSError:
        LOGGER.debug("Failed to write %s after EXIF update", path)


def write_xmp_sidecar(
    *,
    path: Path,
    asset_record: Any,
    dry_run: bool,
) -> None:
    """Write or refresh a generated XMP sidecar for the given asset file."""

    metadata = build_xmp_metadata(asset_record)
    if metadata is None:
        return

    sidecar_path = path.with_name(f"{path.name}.xmp")
    if sidecar_path.exists() and not _can_overwrite_xmp_sidecar(sidecar_path):
        return
    if dry_run:
        return

    sidecar_path.write_bytes(
        ElementTree.tostring(
            _render_xmp_xml(metadata),
            encoding="utf-8",
            xml_declaration=True,
        )
    )


def build_xmp_metadata(asset_record: Any) -> PhotoXmpMetadata | None:
    """Build an XMP metadata payload from a CloudKit asset record."""

    if asset_record is None:
        return None

    title = decode_encrypted_text(asset_record, "captionEnc")
    description = decode_encrypted_text(asset_record, "extendedDescEnc")
    orientation = _extract_orientation(asset_record)
    keywords = _extract_keywords(asset_record)
    location = _extract_location(asset_record)
    create_date = _extract_create_date(asset_record)
    rating = _extract_rating(asset_record)
    asset_subtype = record_field_value(asset_record, "assetSubtypeV2")
    make = "Screenshot" if asset_subtype == 3 else None
    digital_source_type = "screenCapture" if asset_subtype == 3 else None

    return PhotoXmpMetadata(
        toolkit=PYICLOUD_XMP_TOOLKIT,
        title=title,
        description=description,
        orientation=orientation,
        make=make,
        digital_source_type=digital_source_type,
        keywords=keywords,
        gps_altitude=location.get("altitude"),
        gps_latitude=location.get("latitude"),
        gps_longitude=location.get("longitude"),
        gps_speed=location.get("speed"),
        gps_timestamp=location.get("timestamp"),
        create_date=create_date,
        rating=rating,
    )


def _decode_field_bytes(record: Any, field_name: str) -> bytes | None:
    value = record_field_value(record, field_name)
    if value is None:
        return None
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("ascii")
    else:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        return raw


def _extract_orientation(asset_record: Any) -> int | None:
    raw = _decode_field_bytes(asset_record, "adjustmentSimpleDataEnc")
    if not raw or raw.startswith((b"crdt", b"bplist00")):
        return None
    try:
        adjustments = json.loads(zlib.decompress(raw, -zlib.MAX_WBITS))
    except Exception:
        return None
    metadata = adjustments.get("metadata")
    if not isinstance(metadata, dict):
        return None
    orientation = metadata.get("orientation")
    return orientation if isinstance(orientation, int) else None


def _extract_keywords(asset_record: Any) -> list[str] | None:
    raw = _decode_field_bytes(asset_record, "keywordsEnc")
    if not raw:
        return None
    try:
        value = plistlib.loads(raw)
    except Exception:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _extract_location(asset_record: Any) -> dict[str, Any]:
    raw = _decode_field_bytes(asset_record, "locationEnc")
    if not raw:
        return {}
    try:
        location = plistlib.loads(raw)
    except Exception:
        return {}
    if not isinstance(location, dict):
        return {}
    timestamp = location.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, datetime):
        timestamp = None
    return {
        "altitude": _maybe_float(location.get("alt")),
        "latitude": _maybe_float(location.get("lat")),
        "longitude": _maybe_float(location.get("lon")),
        "speed": _maybe_float(location.get("speed")),
        "timestamp": timestamp,
    }


def _extract_create_date(asset_record: Any) -> datetime | None:
    asset_date = record_field_value(asset_record, "assetDate")
    if isinstance(asset_date, datetime):
        return asset_date
    if not isinstance(asset_date, (int, float)):
        return None
    offset = record_field_value(asset_record, "timeZoneOffset")
    offset_seconds = int(offset) if isinstance(offset, (int, float)) else 0
    return datetime.fromtimestamp(
        asset_date / 1000.0,
        tz=timezone(timedelta(seconds=offset_seconds)),
    )


def _extract_rating(asset_record: Any) -> int | None:
    if record_field_value(asset_record, "isHidden") == 1:
        return -1
    if record_field_value(asset_record, "isDeleted") == 1:
        return -1
    if record_field_value(asset_record, "isFavorite") == 1:
        return 5
    return None


def _maybe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _can_overwrite_xmp_sidecar(path: Path) -> bool:
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:
        return False
    toolkit = root.attrib.get("{adobe:ns:meta/}xmptk") or root.attrib.get("x:xmptk")
    return isinstance(toolkit, str) and toolkit.startswith(PYICLOUD_XMP_TOOLKIT)


def _render_xmp_xml(metadata: PhotoXmpMetadata) -> ElementTree.Element:
    xml_doc = ElementTree.Element(
        "x:xmpmeta",
        {"xmlns:x": "adobe:ns:meta/", "x:xmptk": metadata.toolkit},
    )
    rdf = ElementTree.SubElement(
        xml_doc,
        "rdf:RDF",
        {"xmlns:rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
    )

    description_dc = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
        },
    )
    description_exif = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:exif": "http://ns.adobe.com/exif/1.0/",
        },
    )
    description_iptc = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/",
        },
    )
    description_photoshop = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:photoshop": "http://ns.adobe.com/photoshop/1.0/",
        },
    )
    description_tiff = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:tiff": "http://ns.adobe.com/tiff/1.0/",
        },
    )
    description_xmp = ElementTree.Element(
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:xmp": "http://ns.adobe.com/xap/1.0/",
        },
    )

    if metadata.title:
        ElementTree.SubElement(description_dc, "dc:title").text = metadata.title
    if metadata.description:
        ElementTree.SubElement(
            description_dc, "dc:description"
        ).text = metadata.description
    if metadata.keywords:
        subject = ElementTree.SubElement(description_dc, "dc:subject")
        seq = ElementTree.SubElement(subject, "rdf:Seq")
        for keyword in metadata.keywords:
            ElementTree.SubElement(seq, "rdf:li").text = keyword

    if metadata.orientation is not None:
        ElementTree.SubElement(description_tiff, "tiff:Orientation").text = str(
            metadata.orientation
        )
    if metadata.make:
        ElementTree.SubElement(description_tiff, "tiff:Make").text = metadata.make
    if metadata.digital_source_type:
        ElementTree.SubElement(
            description_iptc,
            "Iptc4xmpExt:DigitalSourceType",
        ).text = metadata.digital_source_type

    if metadata.gps_altitude is not None:
        ElementTree.SubElement(description_exif, "exif:GPSAltitude").text = str(
            metadata.gps_altitude
        )
    if metadata.gps_latitude is not None:
        ElementTree.SubElement(description_exif, "exif:GPSLatitude").text = str(
            metadata.gps_latitude
        )
    if metadata.gps_longitude is not None:
        ElementTree.SubElement(description_exif, "exif:GPSLongitude").text = str(
            metadata.gps_longitude
        )
    if metadata.gps_speed is not None:
        ElementTree.SubElement(description_exif, "exif:GPSSpeed").text = str(
            metadata.gps_speed
        )
    if metadata.gps_timestamp is not None:
        ElementTree.SubElement(
            description_exif, "exif:GPSTimeStamp"
        ).text = metadata.gps_timestamp.strftime("%Y-%m-%dT%H:%M:%S%z")

    if metadata.create_date is not None:
        timestamp = metadata.create_date.strftime("%Y-%m-%dT%H:%M:%S%z")
        ElementTree.SubElement(description_xmp, "xmp:CreateDate").text = timestamp
        ElementTree.SubElement(
            description_photoshop,
            "photoshop:DateCreated",
        ).text = timestamp

    if metadata.rating is not None:
        ElementTree.SubElement(description_xmp, "xmp:Rating").text = str(
            metadata.rating
        )

    for description in (
        description_dc,
        description_exif,
        description_iptc,
        description_photoshop,
        description_tiff,
        description_xmp,
    ):
        if len(list(description)) > 0:
            rdf.append(description)

    return xml_doc


def _jpeg_has_exif_datetime(jpeg_bytes: bytes) -> bool:
    exif_payload = _extract_exif_payload(jpeg_bytes)
    if exif_payload is None:
        return False

    parsed = _parse_tiff_ifd(exif_payload, _read_uint32(exif_payload, 4, b"<"))
    if parsed is None:
        return False
    _, ifd0 = parsed
    for tag in (0x0132,):
        value = _read_ascii_tag(exif_payload, ifd0, tag)
        if value:
            return True

    exif_ifd_offset = _read_long_tag(exif_payload, ifd0, 0x8769)
    if exif_ifd_offset is None:
        return False
    parsed = _parse_tiff_ifd(exif_payload, exif_ifd_offset)
    if parsed is None:
        return False
    _, exif_ifd = parsed
    return any(_read_ascii_tag(exif_payload, exif_ifd, tag) for tag in (0x9003, 0x9004))


def _extract_exif_payload(jpeg_bytes: bytes) -> bytes | None:
    if len(jpeg_bytes) < 4 or jpeg_bytes[:2] != b"\xff\xd8":
        return None

    index = 2
    while index + 4 <= len(jpeg_bytes):
        if jpeg_bytes[index] != 0xFF:
            return None
        marker = jpeg_bytes[index + 1]
        if marker in {0xD9, 0xDA}:
            return None
        segment_length = struct.unpack(">H", jpeg_bytes[index + 2 : index + 4])[0]
        if segment_length < 2 or index + 2 + segment_length > len(jpeg_bytes):
            return None
        payload_start = index + 4
        payload_end = index + 2 + segment_length
        if (
            marker == 0xE1
            and jpeg_bytes[payload_start : payload_start + 6] == b"Exif\x00\x00"
        ):
            return jpeg_bytes[payload_start + 6 : payload_end]
        index = payload_end
    return None


def _insert_exif_datetime_segment(*, jpeg_bytes: bytes, timestamp: str) -> bytes | None:
    if len(jpeg_bytes) < 2 or jpeg_bytes[:2] != b"\xff\xd8":
        return None

    ascii_timestamp = timestamp.encode("ascii") + b"\x00"
    tiff = _build_exif_tiff(ascii_timestamp)
    payload = b"Exif\x00\x00" + tiff
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpeg_bytes[:2] + segment + jpeg_bytes[2:]


def _build_exif_tiff(ascii_timestamp: bytes) -> bytes:
    byte_order = b"II"
    tiff_header = byte_order + struct.pack("<H", 42) + struct.pack("<I", 8)

    ifd0_offset = 8
    ifd0_size = 2 + (2 * 12) + 4
    ifd0_data_offset = ifd0_offset + ifd0_size
    exif_ifd_offset = ifd0_data_offset + len(ascii_timestamp)
    exif_ifd_size = 2 + (2 * 12) + 4
    exif_data_offset = exif_ifd_offset + exif_ifd_size
    digitized_offset = exif_data_offset + len(ascii_timestamp)

    ifd0 = [
        _pack_ascii_entry(0x0132, len(ascii_timestamp), ifd0_data_offset),
        _pack_long_entry(0x8769, exif_ifd_offset),
    ]
    exif_ifd = [
        _pack_ascii_entry(0x9003, len(ascii_timestamp), exif_data_offset),
        _pack_ascii_entry(0x9004, len(ascii_timestamp), digitized_offset),
    ]

    return (
        tiff_header
        + struct.pack("<H", len(ifd0))
        + b"".join(ifd0)
        + struct.pack("<I", 0)
        + ascii_timestamp
        + struct.pack("<H", len(exif_ifd))
        + b"".join(exif_ifd)
        + struct.pack("<I", 0)
        + ascii_timestamp
        + ascii_timestamp
    )


def _pack_ascii_entry(tag: int, count: int, offset: int) -> bytes:
    return struct.pack("<HHI", tag, 2, count) + struct.pack("<I", offset)


def _pack_long_entry(tag: int, value: int) -> bytes:
    return struct.pack("<HHI", tag, 4, 1) + struct.pack("<I", value)


def _parse_tiff_ifd(
    exif_payload: bytes, offset: int
) -> tuple[bytes, dict[int, tuple[int, int, int]]] | None:
    if len(exif_payload) < 8 or offset + 2 > len(exif_payload):
        return None
    byte_order = exif_payload[:2]
    if byte_order == b"II":
        fmt = b"<"
    elif byte_order == b"MM":
        fmt = b">"
    else:
        return None

    count = _read_uint16(exif_payload, offset, fmt)
    if count is None:
        return None
    entries: dict[int, tuple[int, int, int]] = {}
    entry_offset = offset + 2
    for _ in range(count):
        if entry_offset + 12 > len(exif_payload):
            return None
        tag = _read_uint16(exif_payload, entry_offset, fmt)
        field_type = _read_uint16(exif_payload, entry_offset + 2, fmt)
        item_count = _read_uint32(exif_payload, entry_offset + 4, fmt)
        value_offset = _read_uint32(exif_payload, entry_offset + 8, fmt)
        if None in {tag, field_type, item_count, value_offset}:
            return None
        entries[int(tag)] = (int(field_type), int(item_count), int(value_offset))
        entry_offset += 12
    return fmt, entries


def _read_ascii_tag(
    exif_payload: bytes, ifd: dict[int, tuple[int, int, int]], tag: int
) -> str | None:
    entry = ifd.get(tag)
    if entry is None:
        return None
    field_type, count, value_offset = entry
    if field_type != 2 or count < 1:
        return None
    if count <= 4:
        raw = struct.pack("<I", value_offset)[:count]
    elif value_offset + count <= len(exif_payload):
        raw = exif_payload[value_offset : value_offset + count]
    else:
        return None
    return raw.rstrip(b"\x00").decode("ascii", errors="ignore") or None


def _read_long_tag(
    exif_payload: bytes, ifd: dict[int, tuple[int, int, int]], tag: int
) -> int | None:
    entry = ifd.get(tag)
    if entry is None:
        return None
    field_type, count, value_offset = entry
    if field_type != 4 or count != 1:
        return None
    return value_offset


def _read_uint16(data: bytes, offset: int, fmt: bytes) -> int | None:
    if offset + 2 > len(data):
        return None
    return struct.unpack(f"{fmt.decode()}H", data[offset : offset + 2])[0]


def _read_uint32(data: bytes, offset: int, fmt: bytes) -> int | None:
    if offset + 4 > len(data):
        return None
    return struct.unpack(f"{fmt.decode()}I", data[offset : offset + 4])[0]
