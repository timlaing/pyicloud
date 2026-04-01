"""Mapping helpers for Photos CloudKit records."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable

from pyicloud.common.cloudkit import CKRecord
from pyicloud.common.cloudkit.models import CKAssetToken

from .models import PhotoResource

LOGGER = logging.getLogger(__name__)


def decode_encrypted_text(record: CKRecord, field_name: str) -> str | None:
    """Decode a base64-wrapped text field from STRING or ENCRYPTED_BYTES."""

    value = record_field_value(record, field_name)
    if value is None:
        return None
    raw: bytes
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("ascii")
    else:
        return None

    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        try:
            return raw.decode("utf-8")
        except Exception:
            LOGGER.debug("Failed to decode %s on %s", field_name, record_name(record))
            return None


def record_field_value(record: CKRecord | dict[str, Any], field_name: str):
    """Return a field value from a typed record or a legacy raw-dict record."""

    if isinstance(record, CKRecord):
        value = record.fields.get_value(field_name)
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value
    field = record.get("fields", {}).get(field_name)
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return None


def record_change_tag(record: CKRecord | dict[str, Any]) -> str | None:
    """Return ``recordChangeTag`` from a typed or raw record."""

    if isinstance(record, CKRecord):
        return record.recordChangeTag
    return record.get("recordChangeTag")


def record_name(record: CKRecord | dict[str, Any]) -> str:
    """Return ``recordName`` from a typed or raw record."""

    if isinstance(record, CKRecord):
        return record.recordName
    return record["recordName"]


def record_record_type(record: CKRecord | dict[str, Any]) -> str:
    """Return ``recordType`` from a typed or raw record."""

    if isinstance(record, CKRecord):
        return record.recordType
    return record["recordType"]


def record_zone(record: CKRecord | dict[str, Any]) -> dict[str, Any] | None:
    """Return ``zoneID`` as a mapping from a typed or raw record."""

    if isinstance(record, CKRecord):
        if record.zoneID is None:
            return None
        return record.zoneID.model_dump(exclude_none=True)
    return record.get("zoneID")


def master_asset_pairs(
    records: Iterable[CKRecord],
) -> tuple[dict[str, CKRecord], list[CKRecord]]:
    """Return ``master_id -> asset`` mapping plus ordered master records."""

    assets_by_master: dict[str, CKRecord] = {}
    masters: list[CKRecord] = []

    for record in records:
        if record.recordType == "CPLAsset":
            ref = record.fields.get_value("masterRef")
            master_name = getattr(ref, "recordName", None) or record.recordName
            assets_by_master[master_name] = record
        elif record.recordType == "CPLMaster":
            masters.append(record)

    return assets_by_master, masters


def timestamp_or_epoch(value) -> datetime:
    """Normalize optional CloudKit timestamps to a stable datetime."""

    if isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(0, timezone.utc)


def build_photo_resource(
    *,
    key: str,
    prefix: str,
    master_record: CKRecord | dict[str, Any],
    filename: str,
    item_type_extensions: dict[str, str],
    is_live_photo: bool,
    item_type_lookup: dict[str, str],
) -> PhotoResource | None:
    """Build a ``PhotoResource`` from a ``CPLMaster`` resource prefix."""

    token = record_field_value(master_record, f"{prefix}Res")
    if token is None:
        return None

    if isinstance(token, CKAssetToken):
        url = token.downloadURL
        size = token.size
    elif isinstance(token, dict):
        url = token.get("downloadURL")
        size = token.get("size")
    else:
        url = getattr(token, "downloadURL", None)
        size = getattr(token, "size", None)

    resource_type = record_field_value(master_record, f"{prefix}FileType")
    checksum = record_field_value(master_record, f"{prefix}Fingerprint")
    width = record_field_value(master_record, f"{prefix}Width")
    height = record_field_value(master_record, f"{prefix}Height")

    resource_filename = filename
    if (
        is_live_photo
        and resource_type
        and item_type_lookup.get(resource_type) == "movie"
    ):
        name_base, _ = os.path.splitext(filename)
        resource_filename = (
            f"{name_base}{item_type_extensions.get(resource_type, '.MOV')}"
        )

    return PhotoResource(
        key=key,
        filename=resource_filename,
        url=url,
        size=size,
        type=resource_type,
        checksum=checksum,
        width=width,
        height=height,
    )
