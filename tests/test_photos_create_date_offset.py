"""Capture timestamps retain their instant and track whether the offset is known."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

import pytest

from pyicloud.services.photos_cloudkit.materialize import (
    PhotoXmpMetadata,
    _extract_create_date,
    _render_xmp_xml,
    build_xmp_metadata,
)

TAKEN_MS = 1_757_260_800_000  # 2025-09-07T16:00:00Z
TAKEN = datetime(2025, 9, 7, 16, 0, tzinfo=timezone.utc)


def record(**fields: Any) -> dict[str, Any]:
    """Record."""
    return {"fields": {name: {"value": value} for name, value in fields.items()}}


def rendered(metadata: PhotoXmpMetadata) -> str:
    """Rendered."""
    return ElementTree.tostring(_render_xmp_xml(metadata), encoding="unicode")


# --- unknown offsets ------------------------------------------------------


@pytest.mark.parametrize(
    "offset",
    [None, "-14400", True, 90_000, -86_400, float("nan"), float("inf"), 10**1000],
    ids=[
        "absent",
        "a_string",
        "a_bool",
        "a_day_or_more",
        "minus_a_day",
        "nan",
        "infinity",
        "huge",
    ],
)
def test_an_offset_apple_did_not_give_is_reported_as_unknown(offset: Any) -> None:
    """An offset apple did not give is reported as unknown."""
    fields: dict[str, Any] = {"assetDate": TAKEN_MS}
    if offset is not None:
        fields["timeZoneOffset"] = offset

    metadata = build_xmp_metadata(record(**fields))

    assert metadata is not None
    # The instant is known and kept.
    assert metadata.create_date == TAKEN
    assert metadata.create_date_offset_known is False


def test_an_unknown_offset_is_not_written_to_the_sidecar_as_utc() -> None:
    """An unknown offset is not written to the sidecar as utc."""
    metadata = build_xmp_metadata(record(assetDate=TAKEN_MS))
    assert metadata is not None

    text = rendered(metadata)

    assert "2025-09-07T16:00:00-0000" in text
    assert "+0000" not in text


# --- known offsets --------------------------------------------------------


def test_a_known_offset_dates_the_asset_in_its_own_zone() -> None:
    """A known offset dates the asset in its own zone."""
    metadata = build_xmp_metadata(record(assetDate=TAKEN_MS, timeZoneOffset=-14_400))
    assert metadata is not None

    assert metadata.create_date_offset_known is True
    assert metadata.create_date == TAKEN
    assert metadata.create_date is not None
    assert metadata.create_date.utcoffset() == timedelta(hours=-4)
    assert "2025-09-07T12:00:00-0400" in rendered(metadata)


def test_a_real_zero_offset_is_utc_and_says_so() -> None:
    """A positive control: 0 SENT is a known UTC offset, not an unknown one."""
    metadata = build_xmp_metadata(record(assetDate=TAKEN_MS, timeZoneOffset=0))
    assert metadata is not None

    assert metadata.create_date_offset_known is True
    assert "2025-09-07T16:00:00+0000" in rendered(metadata)


def test_a_typed_timestamp_adopts_the_offset_apple_sent() -> None:
    """`TIMESTAMP` decodes to an aware UTC datetime; the offset still applies."""
    metadata = build_xmp_metadata(record(assetDate=TAKEN, timeZoneOffset=7_200))
    assert metadata is not None

    assert metadata.create_date == TAKEN
    assert metadata.create_date is not None
    assert metadata.create_date.utcoffset() == timedelta(hours=2)
    assert metadata.create_date_offset_known is True


def test_a_typed_timestamp_without_an_offset_is_unknown_too() -> None:
    """A typed timestamp without an offset is unknown too."""
    metadata = build_xmp_metadata(record(assetDate=TAKEN))
    assert metadata is not None

    assert metadata.create_date == TAKEN
    assert metadata.create_date_offset_known is False
    assert "-0000" in rendered(metadata)


def test_the_private_extractor_still_returns_the_instant() -> None:
    """The private extractor still returns the instant."""
    created = _extract_create_date(record(assetDate=TAKEN_MS))

    assert created == TAKEN


# --- constructed metadata -------------------------------------------------


def test_metadata_built_by_hand_is_trusted_as_given() -> None:
    """A caller constructing metadata states its own zone; nothing changes."""
    metadata = PhotoXmpMetadata(toolkit="x", create_date=TAKEN)

    assert metadata.create_date_offset_known is True
    assert "2025-09-07T16:00:00+0000" in rendered(metadata)


@pytest.mark.parametrize("asset_date", [True, "invalid", None])
def test_invalid_capture_timestamp_is_omitted(asset_date: Any) -> None:
    """Unsupported capture timestamps do not invent an instant."""
    metadata = build_xmp_metadata(record(assetDate=asset_date))
    assert metadata is not None
    assert metadata.create_date is None
    assert not metadata.create_date_offset_known


def test_naive_capture_datetime_remains_unknown() -> None:
    """A naive datetime retains its wall clock without assigning an instant."""
    supplied = datetime(2025, 9, 7, 12)
    metadata = build_xmp_metadata(record(assetDate=supplied, timeZoneOffset=7200))
    assert metadata is not None
    assert metadata.create_date == supplied
    assert not metadata.create_date_offset_known
