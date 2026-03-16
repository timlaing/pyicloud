"""Normalization helpers for CLI payloads."""

from __future__ import annotations

from typing import Any


def normalize_account_summary(api, account) -> dict[str, Any]:
    """Normalize account summary data."""

    storage = account.storage
    return {
        "account_name": api.account_name,
        "devices_count": len(account.devices),
        "family_count": len(account.family),
        "used_storage_bytes": storage.usage.used_storage_in_bytes,
        "available_storage_bytes": storage.usage.available_storage_in_bytes,
        "total_storage_bytes": storage.usage.total_storage_in_bytes,
        "used_storage_percent": storage.usage.used_storage_in_percent,
        "summary_plan": account.summary_plan,
    }


def normalize_account_device(device: dict[str, Any]) -> dict[str, Any]:
    """Normalize account device data."""

    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "model_display_name": device.get("modelDisplayName"),
        "device_class": device.get("deviceClass"),
    }


def normalize_family_member(member: Any) -> dict[str, Any]:
    """Normalize family member data."""

    return {
        "full_name": member.full_name,
        "apple_id": member.apple_id,
        "dsid": member.dsid,
        "age_classification": member.age_classification,
        "has_parental_privileges": member.has_parental_privileges,
    }


def normalize_storage(storage: Any) -> dict[str, Any]:
    """Normalize storage usage payloads."""

    return {
        "usage": {
            "used_storage_in_bytes": storage.usage.used_storage_in_bytes,
            "available_storage_in_bytes": storage.usage.available_storage_in_bytes,
            "total_storage_in_bytes": storage.usage.total_storage_in_bytes,
            "used_storage_in_percent": storage.usage.used_storage_in_percent,
        },
        "usages_by_media": {
            key: {
                "label": usage.label,
                "color": usage.color,
                "usage_in_bytes": usage.usage_in_bytes,
            }
            for key, usage in storage.usages_by_media.items()
        },
    }


def normalize_device_summary(device: Any, *, locate: bool) -> dict[str, Any]:
    """Normalize a Find My device for summary views."""

    return {
        "id": getattr(device, "id", None),
        "name": getattr(device, "name", None),
        "display_name": getattr(device, "deviceDisplayName", None),
        "device_class": getattr(device, "deviceClass", None),
        "device_model": getattr(device, "deviceModel", None),
        "battery_level": getattr(device, "batteryLevel", None),
        "battery_status": getattr(device, "batteryStatus", None),
        "location": getattr(device, "location", None) if locate else None,
    }


def normalize_device_details(device: Any, *, locate: bool) -> dict[str, Any]:
    """Normalize a Find My device for detailed views."""

    payload = normalize_device_summary(device, locate=locate)
    payload["raw_data"] = getattr(device, "data", None)
    return payload


def normalize_calendar(calendar: dict[str, Any]) -> dict[str, Any]:
    """Normalize a calendar entry."""

    return {
        "guid": calendar.get("guid"),
        "title": calendar.get("title"),
        "color": calendar.get("color"),
        "share_type": calendar.get("shareType"),
    }


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a calendar event."""

    return {
        "guid": event.get("guid"),
        "calendar_guid": event.get("pGuid"),
        "title": event.get("title"),
        "start": event.get("startDate"),
        "end": event.get("endDate"),
    }


def normalize_contact(contact: dict[str, Any]) -> dict[str, Any]:
    """Normalize a contact entry."""

    return {
        "first_name": contact.get("firstName"),
        "last_name": contact.get("lastName"),
        "phones": [phone.get("field", "") for phone in contact.get("phones", [])],
        "emails": [email.get("field", "") for email in contact.get("emails", [])],
    }


def normalize_me(me: Any) -> dict[str, Any]:
    """Normalize the 'me' contact payload."""

    return {
        "first_name": me.first_name,
        "last_name": me.last_name,
        "photo": me.photo,
        "raw_data": me.raw_data,
    }


def normalize_drive_node(node: Any) -> dict[str, Any]:
    """Normalize an iCloud Drive node."""

    return {
        "name": node.name,
        "type": node.type,
        "size": node.size,
        "modified": node.date_modified,
    }


def normalize_album(album: Any) -> dict[str, Any]:
    """Normalize a photo album."""

    return {
        "name": album.name,
        "full_name": album.fullname,
        "count": len(album),
    }


def normalize_photo(item: Any) -> dict[str, Any]:
    """Normalize a photo asset."""

    return {
        "id": item.id,
        "filename": item.filename,
        "item_type": item.item_type,
        "created": item.created,
        "size": item.size,
    }


def normalize_alias(alias: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Hide My Email alias."""

    return {
        "email": alias.get("hme"),
        "label": alias.get("label"),
        "anonymous_id": alias.get("anonymousId"),
    }


def select_recent_notes(api: Any, *, limit: int, include_deleted: bool) -> list[Any]:
    """Return recent notes, excluding deleted notes by default."""

    if include_deleted:
        return list(api.notes.recents(limit=limit))

    probe_limit = limit
    max_probe = min(max(limit, 10) * 8, 500)
    while True:
        rows = list(api.notes.recents(limit=probe_limit))
        filtered = [row for row in rows if not getattr(row, "is_deleted", False)]
        if (
            len(filtered) >= limit
            or len(rows) < probe_limit
            or probe_limit >= max_probe
        ):
            return filtered[:limit]
        probe_limit = min(probe_limit * 2, max_probe)
