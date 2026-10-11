"""Synthetic duplicated assets survive raw and typed response joins."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any

import pytest

from pyicloud.common.cloudkit import CKRecord
from pyicloud.services.photos_cloudkit.mappers import (
    _master_asset_groups,
    _raw_asset_groups,
)
from pyicloud.services.photos_cloudkit.service import BasePhotoAlbum
from pyicloud.services.photos_legacy import BasePhotoAlbum as LegacyAlbum


def asset(name: str, master_id: str = "master") -> dict[str, Any]:
    """Create a synthetic asset referring to a master."""
    return {
        "recordName": name,
        "recordType": "CPLAsset",
        "fields": {
            "masterRef": {"type": "REFERENCE", "value": {"recordName": master_id}}
        },
    }


def master(name: str = "master") -> dict[str, Any]:
    """Create a synthetic master record without account metadata."""
    return {"recordName": name, "recordType": "CPLMaster", "fields": {}}


def album() -> Any:
    """Supply a minimal album whose factory preserves each asset identity."""

    def factory(_service: Any, _master: Any, item: Any, **_kwargs: Any) -> Any:
        """Represent a constructed photo by its asset record name."""
        return SimpleNamespace(
            id=item.recordName if isinstance(item, CKRecord) else item["recordName"]
        )

    return SimpleNamespace(
        service=object(), _library=SimpleNamespace(asset_type=factory)
    )


@pytest.mark.parametrize("typed", [False, True])
def test_modern_join_keeps_two_assets_on_one_master(typed: bool) -> None:
    """Modern join keeps two assets on one master."""
    records = [asset("first"), master(), asset("second")]
    response: Any = (
        [CKRecord.model_validate(row) for row in records]
        if typed
        else {"records": records}
    )
    assert [
        row.id for row in BasePhotoAlbum._process_photo_list_response(album(), response)
    ] == [
        "first",
        "second",
    ]


def test_legacy_join_keeps_two_assets_on_one_master() -> None:
    """Legacy join keeps two assets on one master."""
    response = {"records": [asset("first"), asset("second"), master()]}
    assert [
        row.id for row in LegacyAlbum._process_photo_list_response(album(), response)
    ] == [
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    "processor",
    [
        BasePhotoAlbum._process_photo_list_response,
        LegacyAlbum._process_photo_list_response,
    ],
)
def test_separate_pages_can_reuse_the_same_master(processor: Any) -> None:
    """Separate pages can reuse the same master."""
    value = album()
    pages = [
        {"records": [asset("first"), master()]},
        {"records": [asset("second"), master()]},
    ]
    assert [item.id for page in pages for item in processor(value, page)] == [
        "first",
        "second",
    ]


def test_groups_keep_master_order_and_all_related_assets() -> None:
    """Groups keep master order and all related assets."""
    records = [
        asset("second", "m2"),
        asset("first", "m1"),
        master("m1"),
        master("m2"),
        asset("third", "m1"),
    ]
    raw, masters = _raw_asset_groups({"records": records})
    typed, typed_masters = _master_asset_groups(
        CKRecord.model_validate(row) for row in records
    )
    assert [row["recordName"] for row in masters] == ["m1", "m2"]
    assert [row.recordName for row in typed_masters] == ["m1", "m2"]
    assert [row["recordName"] for row in raw["m1"]] == ["first", "third"]
    assert [row.recordName for row in typed["m1"]] == ["first", "third"]


def test_unmatched_master_does_not_invent_a_photo() -> None:
    """An unmatched master does not invent a photo."""
    assert not list(
        BasePhotoAlbum._process_photo_list_response(album(), {"records": [master()]})
    )
