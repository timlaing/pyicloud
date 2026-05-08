"""Query builders for Photos CloudKit indexes observed in the HAR capture."""

from __future__ import annotations

from typing import Iterable, Optional

from pyicloud.common.cloudkit import (
    CKFVInt64,
    CKFVString,
    CKQueryFilterBy,
    CKQueryObject,
)

from .constants import DirectionEnum, ListTypeEnum


def _string_filter(field_name: str, value: str) -> CKQueryFilterBy:
    return CKQueryFilterBy(
        comparator="EQUALS",
        fieldName=field_name,
        fieldValue=CKFVString(type="STRING", value=value),
    )


def _int_filter(field_name: str, value: int) -> CKQueryFilterBy:
    return CKQueryFilterBy(
        comparator="EQUALS",
        fieldName=field_name,
        fieldValue=CKFVInt64(type="INT64", value=value),
    )


def check_indexing_state_query() -> CKQueryObject:
    """Return the Photos indexing-state query."""

    return CKQueryObject(recordType="CheckIndexingState")


def album_query(parent_id: str | None = None) -> CKQueryObject:
    """Return the album/folder listing query."""

    filter_by: list[CKQueryFilterBy] | None = None
    if parent_id:
        filter_by = [_string_filter("parentId", parent_id)]
    return CKQueryObject(recordType="CPLAlbumByPositionLive", filterBy=filter_by)


def list_query(
    *,
    list_type: ListTypeEnum,
    direction: DirectionEnum,
    offset: int,
    extra_filters: Optional[Iterable[CKQueryFilterBy]] = None,
) -> CKQueryObject:
    """Return an asset listing query."""

    filters: list[CKQueryFilterBy] = [
        _string_filter("direction", direction.value),
        _int_filter("startRank", offset),
    ]
    if extra_filters:
        filters.extend(list(extra_filters))
    return CKQueryObject(recordType=list_type.value, filterBy=filters)


def photo_lookup_query(
    *,
    list_type: ListTypeEnum,
    photo_id: str,
    direction: DirectionEnum = DirectionEnum.ASCENDING,
) -> CKQueryObject:
    """Return a single-photo lookup query within a list index."""

    return list_query(
        list_type=list_type,
        direction=direction,
        offset=0,
        extra_filters=[_string_filter("recordName", photo_id)],
    )


def smart_album_filter(value: str) -> CKQueryFilterBy:
    """Return the smart-album selector filter."""

    return _string_filter("smartAlbum", value)


def parent_filter(parent_id: str) -> CKQueryFilterBy:
    """Return a parent-id selector filter."""

    return _string_filter("parentId", parent_id)
