"""Coverage-focused unit tests for the Photos CloudKit read helpers.

These tests exercise the pure mapping, query-building, and local
materialization helpers in ``pyicloud.services.photos_cloudkit`` directly.
No network or real file-system access is performed: ``session`` objects are
never touched and every ``Path`` touch goes through ``MagicMock`` instances.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import plistlib
import struct
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree
import zlib

from pyicloud.common.cloudkit import CKRecord
from pyicloud.common.cloudkit.models import CKAssetToken
from pyicloud.services.photos_cloudkit import materialize as materialize_module
from pyicloud.services.photos_cloudkit.constants import DirectionEnum, ListTypeEnum
from pyicloud.services.photos_cloudkit.mappers import (
    _master_asset_groups,
    _raw_asset_groups,
    build_photo_resource,
    decode_encrypted_text,
    master_asset_pairs,
    record_change_tag,
    record_field_value,
    record_name,
    record_record_type,
    record_zone,
    timestamp_or_epoch,
)
from pyicloud.services.photos_cloudkit.materialize import (
    PhotoXmpMetadata,
    _build_exif_tiff,
    _can_overwrite_xmp_sidecar,
    _decode_field_bytes,
    _extract_create_date,
    _extract_exif_payload,
    _extract_keywords,
    _extract_location,
    _extract_orientation,
    _extract_rating,
    _insert_exif_datetime_segment,
    _jpeg_has_exif_datetime,
    _parse_tiff_ifd,
    _read_ascii_tag,
    _read_long_tag,
    _read_uint16,
    _read_uint32,
    _render_xmp_xml,
    _tiff_byte_order,
    apply_align_raw_policy,
    build_xmp_metadata,
    resource_is_raw,
    set_exif_datetime_if_missing,
    write_xmp_sidecar,
)
from pyicloud.services.photos_cloudkit.queries import (
    album_query,
    check_indexing_state_query,
    list_query,
    parent_filter,
    photo_lookup_query,
    smart_album_filter,
)

UTC = timezone.utc
TAKEN_AT = datetime(2026, 4, 21, 12, 34, 56, tzinfo=UTC)
EXIF_TIMESTAMP_BYTES = b"2026:04:21 12:34:56\x00"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _raw_record(**fields: Any) -> dict[str, Any]:
    return {
        "recordName": "record-1",
        "recordType": "CPLMaster",
        "fields": {name: {"value": value} for name, value in fields.items()},
    }


def _deflate_b64(value: Any) -> str:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    packed = compressor.compress(json.dumps(value).encode("utf-8"))
    packed += compressor.flush()
    return _b64(packed)


def _typed_asset(name: str, master: str | None) -> CKRecord:
    fields: dict[str, Any] = {}
    if master is not None:
        fields["masterRef"] = {"type": "REFERENCE", "value": {"recordName": master}}
    return CKRecord.model_validate({
        "recordName": name,
        "recordType": "CPLAsset",
        "fields": fields,
    })


def _typed_master(name: str) -> CKRecord:
    return CKRecord.model_validate({"recordName": name, "recordType": "CPLMaster"})


def _exif_segment(tiff_payload: bytes) -> bytes:
    embedded = b"Exif\x00\x00" + tiff_payload
    return b"\xff\xe1" + struct.pack(">H", len(embedded) + 2) + embedded


def _jpeg_with_exif(tiff_payload: bytes) -> bytes:
    return b"\xff\xd8" + _exif_segment(tiff_payload) + b"\xff\xd9"


def _le_entry(tag: int, field_type: int, count: int, value: int) -> bytes:
    return struct.pack("<HHI", tag, field_type, count) + struct.pack("<I", value)


# ---------------------------------------------------------------------------
# queries.py
# ---------------------------------------------------------------------------


def test_check_indexing_state_query() -> None:
    """Check indexing state query."""
    query = check_indexing_state_query()
    assert query.recordType == "CheckIndexingState"


def test_album_query_with_and_without_parent() -> None:
    """Album query with and without parent."""
    assert album_query().filterBy is None
    filtered = album_query("parent-1")
    assert filtered.filterBy is not None
    fields = [f.fieldName for f in filtered.filterBy]  # pylint: disable=not-an-iterable
    assert fields == ["parentId"]


def test_list_query_covers_extra_filters() -> None:
    """List query covers extra filters."""
    plain = list_query(
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        offset=0,
    )
    assert len(plain.filterBy or []) == 2

    extra = smart_album_filter("Favorites")
    with_extra = list_query(
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.DESCENDING,
        offset=5,
        extra_filters=[extra],
    )
    assert with_extra.filterBy is not None
    assert len(with_extra.filterBy) == 3


def test_photo_lookup_query_covers_extra_filters() -> None:
    """Photo lookup query covers extra filters."""
    plain = photo_lookup_query(list_type=ListTypeEnum.DEFAULT, photo_id="p1")
    assert plain.filterBy is not None
    assert len(plain.filterBy) == 3

    with_extra = photo_lookup_query(
        list_type=ListTypeEnum.DEFAULT,
        photo_id="p1",
        direction=DirectionEnum.DESCENDING,
        extra_filters=[parent_filter("album-1")],
    )
    assert with_extra.filterBy is not None
    assert len(with_extra.filterBy) == 4


# ---------------------------------------------------------------------------
# mappers.py
# ---------------------------------------------------------------------------


def test_decode_encrypted_text_paths() -> None:
    """Decode encrypted text paths."""
    assert decode_encrypted_text({"fields": {}}, "captionEnc") is None
    assert (
        decode_encrypted_text(
            {"fields": {"captionEnc": {"value": _b64(b"hello")}}}, "captionEnc"
        )
        == "hello"
    )
    assert (
        decode_encrypted_text(
            {"fields": {"captionEnc": {"value": base64.b64encode(b"world")}}},
            "captionEnc",
        )
        == "world"
    )
    assert (
        decode_encrypted_text(
            {"fields": {"captionEnc": {"value": b"plaintext"}}}, "captionEnc"
        )
        == "plaintext"
    )
    assert (
        decode_encrypted_text(
            {
                "recordName": "r1",
                "fields": {"captionEnc": {"value": b"\xffa"}},
            },
            "captionEnc",
        )
        is None
    )
    assert (
        decode_encrypted_text({"fields": {"captionEnc": {"value": 5}}}, "captionEnc")
        is None
    )
    assert (
        decode_encrypted_text(
            {"fields": {"captionEnc": {"value": "h\u00e9llo"}}}, "captionEnc"
        )
        == "h\u00e9llo"
    )


def test_record_field_value_paths() -> None:
    # Untyped passthrough fields decode to a {"value": ...} envelope.
    """Record field value paths."""
    passthrough = CKRecord.model_validate({
        "recordName": "r1",
        "recordType": "CPLMaster",
        "fields": {"count": {"value": 7}},
    })
    assert record_field_value(passthrough, "count") == 7

    # Typed fields decode straight to the inner value.
    typed = CKRecord.model_validate({
        "recordName": "r1",
        "recordType": "CPLMaster",
        "fields": {"count": {"type": "INT64", "value": 7}},
    })
    assert record_field_value(typed, "count") == 7

    assert record_field_value({"fields": {"x": {"value": 9}}}, "x") == 9
    assert record_field_value({"fields": {"x": 3}}, "x") is None
    assert record_field_value({"fields": {}}, "missing") is None


def test_record_metadata_accessors() -> None:
    """Record metadata accessors."""
    typed = CKRecord.model_validate({
        "recordName": "r1",
        "recordType": "CPLAsset",
        "recordChangeTag": "tag-1",
        "zoneID": {"zoneName": "Z", "ownerRecordName": "owner"},
    })
    assert record_change_tag(typed) == "tag-1"
    assert record_name(typed) == "r1"
    assert record_record_type(typed) == "CPLAsset"
    assert record_zone(typed) == {"zoneName": "Z", "ownerRecordName": "owner"}

    bare = CKRecord.model_validate({"recordName": "r2", "recordType": "CPLMaster"})
    assert record_zone(bare) is None

    raw = {
        "recordName": "r3",
        "recordType": "CPLMaster",
        "recordChangeTag": "tag-3",
        "zoneID": {"zoneName": "Raw"},
    }
    assert record_change_tag(raw) == "tag-3"
    assert record_name(raw) == "r3"
    assert record_record_type(raw) == "CPLMaster"
    assert record_zone(raw) == {"zoneName": "Raw"}


def test_master_asset_pairs_covers_reference_fallback() -> None:
    """Master asset pairs covers reference fallback."""
    other = CKRecord.model_validate({"recordName": "o1", "recordType": "CPLOther"})
    records = [
        _typed_asset("a1", "m1"),
        _typed_asset("a2", None),
        other,
        _typed_master("m1"),
    ]
    assets, masters = master_asset_pairs(records)
    assert assets["m1"].recordName == "a1"
    assert assets["a2"].recordName == "a2"
    assert [master.recordName for master in masters] == ["m1"]


def test_master_asset_groups_skips_unrelated_records() -> None:
    """Master asset groups skips unrelated records."""
    other = CKRecord.model_validate({"recordName": "o1", "recordType": "CPLOther"})
    assets, masters = _master_asset_groups([
        _typed_asset("a1", "m1"),
        other,
        _typed_master("m1"),
    ])
    assert [row.recordName for row in assets["m1"]] == ["a1"]
    assert [row.recordName for row in masters] == ["m1"]


def test_raw_asset_groups_handles_malformed_records() -> None:
    """Raw asset groups handles malformed records."""
    empty, empty_masters = _raw_asset_groups({"records": "not-a-list"})
    assert not empty
    assert not empty_masters

    records: list[Any] = [
        "not-a-dict",
        {"recordName": "m1", "recordType": "CPLMaster"},
        {"recordName": "a-fields", "recordType": "CPLAsset", "fields": "bad"},
        {
            "recordName": "a-ref",
            "recordType": "CPLAsset",
            "fields": {"masterRef": "bad"},
        },
        {
            "recordName": "a-noname",
            "recordType": "CPLAsset",
            "fields": {"masterRef": {"value": {"recordName": 5}}},
        },
        {
            "recordName": "a-ok",
            "recordType": "CPLAsset",
            "fields": {"masterRef": {"value": {"recordName": "m1"}}},
        },
        {"recordName": "unrelated", "recordType": "CPLOther"},
    ]
    assets, masters = _raw_asset_groups({"records": records})
    assert [row["recordName"] for row in masters] == ["m1"]
    assert [row["recordName"] for row in assets["m1"]] == ["a-ok"]


def test_timestamp_or_epoch() -> None:
    """Timestamp or epoch."""
    assert timestamp_or_epoch(TAKEN_AT) is TAKEN_AT
    assert timestamp_or_epoch(None) == datetime.fromtimestamp(0, UTC)


def test_build_photo_resource_token_variants() -> None:
    """Build photo resource token variants."""
    assert (
        build_photo_resource(
            key="original",
            prefix="resOriginal",
            master_record={},
            filename="IMG.JPG",
            item_type_extensions={},
            is_live_photo=False,
            item_type_lookup={},
        )
        is None
    )

    token = CKAssetToken(downloadURL="https://cdn/asset", size=10)
    record = _raw_record(resOriginalRes=token, resOriginalFileType="public.jpeg")
    resource = build_photo_resource(
        key="original",
        prefix="resOriginal",
        master_record=record,
        filename="IMG.JPG",
        item_type_extensions={},
        is_live_photo=False,
        item_type_lookup={},
    )
    assert resource is not None
    assert resource.url == "https://cdn/asset"
    assert resource.size == 10
    assert resource.filename == "IMG.JPG"

    dict_record = _raw_record(
        resOriginalRes={"downloadURL": "https://cdn/dict", "size": 20},
        resOriginalFileType="public.jpeg",
    )
    dict_resource = build_photo_resource(
        key="original",
        prefix="resOriginal",
        master_record=dict_record,
        filename="IMG.JPG",
        item_type_extensions={},
        is_live_photo=False,
        item_type_lookup={},
    )
    assert dict_resource is not None
    assert dict_resource.url == "https://cdn/dict"

    obj_record = _raw_record(
        resOriginalRes=SimpleNamespace(downloadURL="https://cdn/obj", size=30),
        resOriginalFileType="public.jpeg",
    )
    obj_resource = build_photo_resource(
        key="original",
        prefix="resOriginal",
        master_record=obj_record,
        filename="IMG.JPG",
        item_type_extensions={},
        is_live_photo=False,
        item_type_lookup={},
    )
    assert obj_resource is not None
    assert obj_resource.url == "https://cdn/obj"


def test_build_photo_resource_renames_live_and_extension() -> None:
    """Build photo resource renames live and extension."""
    live = _raw_record(
        resVidRes={"downloadURL": "https://cdn/vid", "size": 1},
        resVidFileType="public.movie",
    )
    live_resource = build_photo_resource(
        key="original",
        prefix="resVid",
        master_record=live,
        filename="IMG.JPG",
        item_type_extensions={},
        is_live_photo=True,
        item_type_lookup={"public.movie": "movie"},
    )
    assert live_resource is not None
    assert live_resource.filename == "IMG.MOV"

    ext = _raw_record(
        resOriginalRes={"downloadURL": "https://cdn/ext", "size": 2},
        resOriginalFileType="public.jpeg",
    )
    ext_resource = build_photo_resource(
        key="original",
        prefix="resOriginal",
        master_record=ext,
        filename="IMG.JPG",
        item_type_extensions={"public.jpeg": ".jpeg"},
        is_live_photo=False,
        item_type_lookup={},
    )
    assert ext_resource is not None
    assert ext_resource.filename == "IMG.jpeg"


# ---------------------------------------------------------------------------
# materialize.py — policy helpers
# ---------------------------------------------------------------------------


def test_resource_is_raw_paths() -> None:
    """Resource is raw paths."""
    assert resource_is_raw(SimpleNamespace(type="public.raw-image", filename="x"))
    assert resource_is_raw(SimpleNamespace(type="", filename="x.dng"))
    assert not resource_is_raw(SimpleNamespace(type="public.jpeg", filename="x.jpg"))


def test_apply_align_raw_policy_paths() -> None:
    """Apply align raw policy paths."""
    raw = SimpleNamespace(type="public.raw", filename="x.raw")
    jpeg = SimpleNamespace(type="public.jpeg", filename="x.jpg")

    original = {"original": jpeg, "alternative": raw}
    assert apply_align_raw_policy({"original": jpeg}, "as-is") == {"original": jpeg}
    assert not apply_align_raw_policy({}, "original")
    assert apply_align_raw_policy({"original": jpeg}, "original") == {"original": jpeg}

    swapped = apply_align_raw_policy(dict(original), "original")
    assert swapped["original"] is raw
    assert swapped["alternative"] is jpeg

    reverse = apply_align_raw_policy(
        {"original": raw, "alternative": jpeg}, "alternative"
    )
    assert reverse["original"] is jpeg
    assert reverse["alternative"] is raw

    unchanged = apply_align_raw_policy(
        {"original": raw, "alternative": jpeg}, "original"
    )
    assert unchanged["original"] is raw


def test_set_exif_datetime_if_missing_paths() -> None:
    """Set exif datetime if missing paths."""
    non_jpeg = MagicMock()
    non_jpeg.suffix = ".png"
    set_exif_datetime_if_missing(cast(Path, non_jpeg), TAKEN_AT)
    non_jpeg.read_bytes.assert_not_called()

    unreadable = MagicMock()
    unreadable.suffix = ".jpg"
    unreadable.read_bytes.side_effect = OSError("nope")
    set_exif_datetime_if_missing(cast(Path, unreadable), TAKEN_AT)

    with_exif = MagicMock()
    with_exif.suffix = ".jpg"
    with_exif.read_bytes.return_value = _insert_exif_datetime_segment(
        jpeg_bytes=b"\xff\xd8\xff\xd9", timestamp="2026:01:01 00:00:00"
    )
    set_exif_datetime_if_missing(cast(Path, with_exif), TAKEN_AT)
    with_exif.write_bytes.assert_not_called()

    not_jpeg = MagicMock()
    not_jpeg.suffix = ".jpg"
    not_jpeg.read_bytes.return_value = b"\x00\x00"
    set_exif_datetime_if_missing(cast(Path, not_jpeg), TAKEN_AT)
    not_jpeg.write_bytes.assert_not_called()

    unwritable = MagicMock()
    unwritable.suffix = ".jpg"
    unwritable.read_bytes.return_value = b"\xff\xd8\xff\xd9"
    unwritable.write_bytes.side_effect = OSError("nope")
    set_exif_datetime_if_missing(cast(Path, unwritable), TAKEN_AT)

    writable = MagicMock()
    writable.suffix = ".jpeg"
    writable.read_bytes.return_value = b"\xff\xd8\xff\xd9"
    set_exif_datetime_if_missing(cast(Path, writable), TAKEN_AT)
    writable.write_bytes.assert_called_once()


def _xmp_path(exists: bool) -> tuple[MagicMock, MagicMock]:
    sidecar = MagicMock()
    sidecar.exists.return_value = exists
    path = MagicMock()
    path.name = "photo.jpg"
    path.with_name.return_value = sidecar
    return path, sidecar


def test_write_xmp_sidecar_paths() -> None:
    # metadata None short-circuits before any path access.
    """Write xmp sidecar paths."""
    write_xmp_sidecar(path=cast(Path, MagicMock()), asset_record=None, dry_run=False)

    record = _raw_record(captionEnc=_b64(b"Hello"))

    path, sidecar = _xmp_path(exists=True)
    with patch.object(
        materialize_module, "_can_overwrite_xmp_sidecar", return_value=False
    ):
        write_xmp_sidecar(path=cast(Path, path), asset_record=record, dry_run=False)
    sidecar.write_bytes.assert_not_called()

    path, sidecar = _xmp_path(exists=False)
    write_xmp_sidecar(path=cast(Path, path), asset_record=record, dry_run=True)
    sidecar.write_bytes.assert_not_called()

    path, sidecar = _xmp_path(exists=True)
    with patch.object(
        materialize_module, "_can_overwrite_xmp_sidecar", return_value=True
    ):
        write_xmp_sidecar(path=cast(Path, path), asset_record=record, dry_run=False)
    sidecar.write_bytes.assert_called_once()

    path, sidecar = _xmp_path(exists=False)
    write_xmp_sidecar(path=cast(Path, path), asset_record=record, dry_run=False)
    sidecar.write_bytes.assert_called_once()


def test_can_overwrite_xmp_sidecar_paths() -> None:
    """Can overwrite xmp sidecar paths."""
    path = cast(Path, MagicMock())
    parse_target = "pyicloud.services.photos_cloudkit.materialize.ElementTree.parse"

    with patch(parse_target, side_effect=ElementTree.ParseError):
        assert _can_overwrite_xmp_sidecar(path) is False

    adobe = ElementTree.Element(
        "rdf:RDF", {"{adobe:ns:meta/}xmptk": "pyicloud photos-cloudkit v1"}
    )
    with patch(parse_target) as parse:
        parse.return_value.getroot.return_value = adobe
        assert _can_overwrite_xmp_sidecar(path) is True

    prefixed = ElementTree.Element(
        "rdf:RDF", {"x:xmptk": "pyicloud photos-cloudkit v2"}
    )
    with patch(parse_target) as parse:
        parse.return_value.getroot.return_value = prefixed
        assert _can_overwrite_xmp_sidecar(path) is True

    foreign = ElementTree.Element("rdf:RDF", {"x:xmptk": "OtherTool"})
    with patch(parse_target) as parse:
        parse.return_value.getroot.return_value = foreign
        assert _can_overwrite_xmp_sidecar(path) is False


# ---------------------------------------------------------------------------
# materialize.py — XMP metadata extraction and rendering
# ---------------------------------------------------------------------------


def _full_asset_record() -> dict[str, Any]:
    return _raw_record(
        captionEnc=_b64(b"Caption"),
        extendedDescEnc=_b64(b"Description"),
        adjustmentSimpleDataEnc=_deflate_b64({"metadata": {"orientation": 6}}),
        keywordsEnc=_b64(plistlib.dumps(["alpha", "beta"])),
        locationEnc=_b64(
            plistlib.dumps({
                "alt": 1,
                "lat": 2,
                "lon": 3,
                "speed": 4,
                "timestamp": TAKEN_AT,
            })
        ),
        assetDate=TAKEN_AT,
        isFavorite=1,
        assetSubtypeV2=3,
    )


def test_build_xmp_metadata_paths() -> None:
    """Build xmp metadata paths."""
    assert build_xmp_metadata(None) is None

    metadata = build_xmp_metadata(_full_asset_record())
    assert metadata is not None
    assert metadata.title == "Caption"
    assert metadata.description == "Description"
    assert metadata.orientation == 6
    assert metadata.make == "Screenshot"
    assert metadata.digital_source_type == "screenCapture"
    assert metadata.keywords == ["alpha", "beta"]
    assert metadata.gps_altitude == 1.0
    assert metadata.gps_latitude == 2.0
    assert metadata.gps_longitude == 3.0
    assert metadata.gps_speed == 4.0
    assert metadata.gps_timestamp is not None
    assert metadata.gps_timestamp.year == 2026
    assert metadata.rating == 5

    plain = _raw_record(
        assetDate=1700000000000,
        timeZoneOffset=-3600,
        isHidden=1,
        assetSubtypeV2=0,
    )
    plain_metadata = build_xmp_metadata(plain)
    assert plain_metadata is not None
    assert plain_metadata.make is None
    assert plain_metadata.digital_source_type is None
    assert plain_metadata.rating == -1
    assert plain_metadata.create_date is not None


def test_decode_field_bytes_paths() -> None:
    """Decode field bytes paths."""
    assert _decode_field_bytes(_raw_record(), "missing") is None
    assert _decode_field_bytes(_raw_record(x=_b64(b"raw")), "x") == b"raw"
    assert _decode_field_bytes(_raw_record(x=5), "x") is None
    assert _decode_field_bytes(_raw_record(x=b"\xffa"), "x") == b"\xffa"


def test_extract_orientation_branches() -> None:
    """Extract orientation branches."""
    assert _extract_orientation(_raw_record()) is None
    assert (
        _extract_orientation(_raw_record(adjustmentSimpleDataEnc=_b64(b"crdt123")))
        is None
    )
    assert (
        _extract_orientation(_raw_record(adjustmentSimpleDataEnc=_b64(b"bplist00xyz")))
        is None
    )
    assert (
        _extract_orientation(_raw_record(adjustmentSimpleDataEnc=_b64(b"not-zlib")))
        is None
    )
    assert (
        _extract_orientation(
            _raw_record(
                adjustmentSimpleDataEnc=_deflate_b64({"metadata": ["not-a-dict"]})
            )
        )
        is None
    )
    assert (
        _extract_orientation(
            _raw_record(
                adjustmentSimpleDataEnc=_deflate_b64({
                    "metadata": {"orientation": "sideways"}
                })
            )
        )
        is None
    )
    assert (
        _extract_orientation(
            _raw_record(
                adjustmentSimpleDataEnc=_deflate_b64({"metadata": {"orientation": 8}})
            )
        )
        == 8
    )


def test_extract_keywords_branches() -> None:
    """Extract keywords branches."""
    assert _extract_keywords(_raw_record()) is None
    assert _extract_keywords(_raw_record(keywordsEnc=_b64(b"not-a-plist"))) is None
    assert (
        _extract_keywords(_raw_record(keywordsEnc=_b64(plistlib.dumps({"a": 1}))))
        is None
    )
    assert _extract_keywords(
        _raw_record(keywordsEnc=_b64(plistlib.dumps(["one", "two"])))
    ) == ["one", "two"]


def test_extract_location_branches() -> None:
    """Extract location branches."""
    assert not _extract_location(_raw_record())
    assert not _extract_location(_raw_record(locationEnc=_b64(b"not-a-plist")))
    assert not _extract_location(_raw_record(locationEnc=_b64(plistlib.dumps([1, 2]))))
    non_datetime = _extract_location(
        _raw_record(
            locationEnc=_b64(
                plistlib.dumps({
                    "alt": "high",
                    "lat": 1.5,
                    "lon": 2,
                    "speed": 3,
                    "timestamp": "not-a-datetime",
                })
            )
        )
    )
    assert non_datetime["altitude"] is None
    assert non_datetime["latitude"] == 1.5
    assert non_datetime["longitude"] == 2.0
    assert non_datetime["speed"] == 3.0
    assert non_datetime["timestamp"] is None

    dated = _extract_location(
        _raw_record(locationEnc=_b64(plistlib.dumps({"timestamp": TAKEN_AT, "lat": 9})))
    )
    assert dated["timestamp"] is not None
    assert dated["latitude"] == 9.0


def test_extract_create_date_branches() -> None:
    """Extract create date branches."""
    assert _extract_create_date(_raw_record(assetDate=TAKEN_AT)) == TAKEN_AT
    assert _extract_create_date(_raw_record(assetDate="not-a-date")) is None
    with_offset = _extract_create_date(
        _raw_record(assetDate=1700000000000, timeZoneOffset=-3600)
    )
    assert with_offset is not None
    assert with_offset.utcoffset() == timedelta(hours=-1)
    without_offset = _extract_create_date(_raw_record(assetDate=1700000000000))
    assert without_offset is not None
    assert without_offset.utcoffset() == timedelta(0)


def test_extract_rating_branches() -> None:
    """Extract rating branches."""
    assert _extract_rating(_raw_record(isHidden=1)) == -1
    assert _extract_rating(_raw_record(isDeleted=1)) == -1
    assert _extract_rating(_raw_record(isFavorite=1)) == 5
    assert _extract_rating(_raw_record()) is None


def test_render_xmp_xml_full_and_minimal() -> None:
    """Render xmp xml full and minimal."""
    full = PhotoXmpMetadata(
        toolkit="pyicloud photos-cloudkit",
        title="Title",
        description="Description",
        orientation=6,
        make="Screenshot",
        digital_source_type="screenCapture",
        keywords=["a", "b"],
        gps_altitude=1.0,
        gps_latitude=2.0,
        gps_longitude=3.0,
        gps_speed=4.0,
        gps_timestamp=TAKEN_AT,
        create_date=TAKEN_AT,
        rating=5,
    )
    rendered = ElementTree.tostring(_render_xmp_xml(full))
    assert b"dc:title" in rendered
    assert b"GPSAltitude" in rendered
    assert b"xmp:Rating" in rendered

    minimal = PhotoXmpMetadata(toolkit="pyicloud photos-cloudkit", title="Only title")
    minimal_rendered = ElementTree.tostring(_render_xmp_xml(minimal))
    assert b"dc:title" in minimal_rendered
    assert b"tiff:Orientation" not in minimal_rendered

    description_only = PhotoXmpMetadata(
        toolkit="pyicloud photos-cloudkit", description="Only description"
    )
    description_rendered = ElementTree.tostring(_render_xmp_xml(description_only))
    assert b"dc:description" in description_rendered
    assert b"dc:title" not in description_rendered


# ---------------------------------------------------------------------------
# materialize.py — JPEG EXIF internals
# ---------------------------------------------------------------------------


def test_jpeg_has_exif_datetime_no_exif() -> None:
    """Jpeg has exif datetime no exif."""
    assert _jpeg_has_exif_datetime(b"\xff\xd8\xff\xd9") is False


def test_jpeg_has_exif_datetime_byte_order_none() -> None:
    """Jpeg has exif datetime byte order none."""
    payload = b"XY\x00\x00\x00\x00\x00\x00"
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_jpeg_has_exif_datetime_ifd0_offset_none() -> None:
    """Jpeg has exif datetime ifd0 offset none."""
    payload = b"II" + b"\x2a\x00"
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_jpeg_has_exif_datetime_parse_none() -> None:
    """Jpeg has exif datetime parse none."""
    payload = b"II*\x00" + struct.pack("<I", 8)
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_jpeg_has_exif_datetime_missing_datetime_and_pointer() -> None:
    """Jpeg has exif datetime missing datetime and pointer."""
    ifd0 = struct.pack("<H", 1) + _le_entry(0x0100, 4, 1, 0) + struct.pack("<I", 0)
    payload = b"II*\x00" + struct.pack("<I", 8) + ifd0
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_jpeg_has_exif_datetime_exif_ifd_pointer() -> None:
    """Jpeg has exif datetime exif ifd pointer."""
    ifd0_count = 2
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_ifd_offset = 8 + ifd0_size
    data_offset = exif_ifd_offset + 2 + 12 + 4
    ifd0 = (
        struct.pack("<H", ifd0_count)
        + _le_entry(0x0100, 4, 1, 0)
        + _le_entry(0x8769, 4, 1, exif_ifd_offset)
        + struct.pack("<I", 0)
    )
    exif = (
        struct.pack("<H", 1)
        + _le_entry(0x9003, 2, len(EXIF_TIMESTAMP_BYTES), data_offset)
        + struct.pack("<I", 0)
        + EXIF_TIMESTAMP_BYTES
    )
    payload = b"II*\x00" + struct.pack("<I", 8) + ifd0 + exif
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is True


def test_jpeg_has_exif_datetime_exif_ifd_without_timestamp() -> None:
    """Jpeg has exif datetime exif ifd without timestamp."""
    ifd0_count = 2
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_ifd_offset = 8 + ifd0_size
    ifd0 = (
        struct.pack("<H", ifd0_count)
        + _le_entry(0x0100, 4, 1, 0)
        + _le_entry(0x8769, 4, 1, exif_ifd_offset)
        + struct.pack("<I", 0)
    )
    exif = struct.pack("<H", 1) + _le_entry(0x9999, 4, 1, 0) + struct.pack("<I", 0)
    payload = b"II*\x00" + struct.pack("<I", 8) + ifd0 + exif
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_jpeg_has_exif_datetime_exif_ifd_unparseable() -> None:
    """Jpeg has exif datetime exif ifd unparseable."""
    ifd0_count = 2
    ifd0_size = 2 + ifd0_count * 12 + 4
    invalid_offset = 8 + ifd0_size
    ifd0 = (
        struct.pack("<H", ifd0_count)
        + _le_entry(0x0100, 4, 1, 0)
        + _le_entry(0x8769, 4, 1, invalid_offset)
        + struct.pack("<I", 0)
    )
    payload = b"II*\x00" + struct.pack("<I", 8) + ifd0
    assert _jpeg_has_exif_datetime(_jpeg_with_exif(payload)) is False


def test_extract_exif_payload_paths() -> None:
    """Extract exif payload paths."""
    assert _extract_exif_payload(b"\xff\xd8") is None
    assert _extract_exif_payload(b"\x00\x00\x00\x00") is None
    assert _extract_exif_payload(b"\xff\xd8\x00\x00\x00\x00") is None
    assert _extract_exif_payload(b"\xff\xd8\xff\xd9\x00\x00") is None
    assert _extract_exif_payload(b"\xff\xd8\xff\xda\x00\x00") is None
    assert _extract_exif_payload(b"\xff\xd8\xff\xe0\x00\x01") is None
    assert _extract_exif_payload(b"\xff\xd8\xff\xe0\xff\xff") is None
    assert _extract_exif_payload(b"\xff\xd8\xff\xe0\x00\x04AB\xff\xd9") is None

    embedded = _exif_segment(b"II*\x00")
    payload = _extract_exif_payload(b"\xff\xd8" + embedded + b"\xff\xd9")
    assert payload == b"II*\x00"


def test_tiff_byte_order_paths() -> None:
    """Tiff byte order paths."""
    assert _tiff_byte_order(b"") is None
    assert _tiff_byte_order(b"IIrest") == b"<"
    assert _tiff_byte_order(b"MMrest") == b">"
    assert _tiff_byte_order(b"XYrest") is None


def test_insert_exif_datetime_segment_paths() -> None:
    """Insert exif datetime segment paths."""
    assert _insert_exif_datetime_segment(jpeg_bytes=b"\x00\x00", timestamp="x") is None
    updated = _insert_exif_datetime_segment(
        jpeg_bytes=b"\xff\xd8\xff\xd9", timestamp="2026:01:01 00:00:00"
    )
    assert updated is not None
    assert updated.startswith(b"\xff\xd8\xff\xe1")


def test_build_exif_tiff_is_deterministic() -> None:
    """Build exif tiff is deterministic."""
    assert _build_exif_tiff(b"abc\x00") == _build_exif_tiff(b"abc\x00")


def test_parse_tiff_ifd_paths() -> None:
    """Parse tiff ifd paths."""
    assert _parse_tiff_ifd(b"II", 0) is None
    assert _parse_tiff_ifd(b"XY" + b"\x00" * 6, 0) is None
    assert _parse_tiff_ifd(b"II" + b"\x00" * 6, 0) is None

    payload = (
        b"II*\x00"
        + struct.pack("<I", 8)
        + struct.pack("<H", 1)
        + _le_entry(0x0100, 4, 1, 0)
    )
    assert _parse_tiff_ifd(payload, 8) == (
        b"<",
        {0x0100: (4, 1, 0)},
    )

    big_endian = (
        b"MM"
        + struct.pack(">H", 42)
        + struct.pack(">I", 8)
        + struct.pack(">H", 1)
        + struct.pack(">HHI", 0x0100, 4, 1)
        + struct.pack(">I", 0)
    )
    assert _parse_tiff_ifd(big_endian, 8) == (b">", {0x0100: (4, 1, 0)})


def test_read_ascii_tag_paths() -> None:
    """Read ascii tag paths."""
    assert _read_ascii_tag(b"", {}, 0x0132) is None
    assert _read_ascii_tag(b"", {0x0132: (4, 1, 0)}, 0x0132) is None
    assert _read_ascii_tag(b"", {0x0132: (2, 0, 0)}, 0x0132) is None
    assert _read_ascii_tag(b"", {0x0132: (2, 10, 1000)}, 0x0132) is None
    assert _read_ascii_tag(b"", {0x0132: (2, 4, 0)}, 0x0132) is None


def test_read_ascii_tag_reads_inline_bytes() -> None:
    """Read ascii tag reads inline bytes."""
    value = int.from_bytes(b"abc\x00", "little")
    assert _read_ascii_tag(b"", {0x0132: (2, 3, value)}, 0x0132) == "abc"
    payload = b".............." + b"date\x00"
    entry = (2, 5, 14)
    assert _read_ascii_tag(payload, {0x0132: entry}, 0x0132) == "date"


def test_read_long_tag_paths() -> None:
    """Read long tag paths."""
    assert _read_long_tag(b"", {}, 0x8769) is None
    assert _read_long_tag(b"", {0x8769: (2, 1, 0)}, 0x8769) is None
    assert _read_long_tag(b"", {0x8769: (4, 2, 0)}, 0x8769) is None
    assert _read_long_tag(b"", {0x8769: (4, 1, 123)}, 0x8769) == 123


def test_read_uint_paths() -> None:
    """Read uint paths."""
    assert _read_uint16(b"", 0, b"<") is None
    assert _read_uint16(b"\x01\x00", 0, b"<") == 1
    assert _read_uint32(b"", 0, b"<") is None
    assert _read_uint32(b"\x01\x00\x00\x00", 0, b"<") == 1
