"""Query builders for Photos CloudKit indexes observed in the HAR capture."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pyicloud.common.cloudkit import (
    CKFVInt64,
    CKFVString,
    CKQueryFilterBy,
    CKQueryObject,
    CKQueryRequest,
    CKZoneIDReq,
)

from .constants import DirectionEnum, ListTypeEnum


def record_name_filter(record_name_value: str) -> CKQueryFilterBy:
    """Return a typed record-name equality filter."""
    return _string_filter("recordName", record_name_value)


def query_request_payload(
    *,
    query: CKQueryObject,
    zone_id: dict[str, str],
    desired_keys: list[str] | None = None,
    results_limit: int | None = None,
    continuation: str | None = None,
) -> dict[str, Any]:
    """Serialize a typed request for raw callers without mutating query inputs."""
    return CKQueryRequest(
        query=query,
        zoneID=CKZoneIDReq(**zone_id),
        desiredKeys=desired_keys,
        resultsLimit=results_limit,
        continuationMarker=continuation,
    ).model_dump(mode="json", exclude_none=True)


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
    extra_filters: Iterable[CKQueryFilterBy] | None = None,
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
    extra_filters: Iterable[CKQueryFilterBy] | None = None,
) -> CKQueryObject:
    """Return a single-photo lookup query within a list index."""

    filters = [_string_filter("recordName", photo_id)]
    if extra_filters:
        filters.extend(list(extra_filters))
    return list_query(
        list_type=list_type,
        direction=direction,
        offset=0,
        extra_filters=filters,
    )


def smart_album_filter(value: str) -> CKQueryFilterBy:
    """Return the smart-album selector filter."""

    return _string_filter("smartAlbum", value)


def parent_filter(parent_id: str) -> CKQueryFilterBy:
    """Return a parent-id selector filter."""

    return _string_filter("parentId", parent_id)
