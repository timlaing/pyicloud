"""Validated, capture-derived fields for a new unpublished event and its share."""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codecs import encode_json_bytes
from .models.dto import EventPlace, EventTime


def creation_fields(
    title: str,
    time: EventTime,
    *,
    place: EventPlace | None,
    notes: str,
    max_attendees: int | None,
    max_additional_guests_per_rsvp: int,
    is_private: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate before network I/O and build encrypted/private + plain previews."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not isinstance(time, EventTime):
        raise ValueError("time must be an EventTime")
    for date in (time.start, time.end):
        if date is not None and date.utcoffset() is None:
            raise ValueError("Event times must be timezone-aware")
    if time.end is not None and time.end.timestamp() < time.start.timestamp():
        raise ValueError("Event end must not precede start")
    if time.is_open_ended and time.end is not None:
        raise ValueError("An open-ended event cannot have an end time")
    if max_additional_guests_per_rsvp is None:
        raise ValueError("max_additional_guests_per_rsvp must be an integer >= 0")
    for name, count, minimum in (
        ("max_attendees", max_attendees, 1),
        ("max_additional_guests_per_rsvp", max_additional_guests_per_rsvp, 0),
    ):
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < minimum
        ):
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if not isinstance(notes, str) or not isinstance(is_private, bool):
        raise ValueError("notes must be a string and is_private must be a boolean")
    if place is not None and not isinstance(place, EventPlace):
        raise ValueError("place must be an EventPlace")
    if place is not None and place.time_zone_identifier:
        try:
            ZoneInfo(place.time_zone_identifier)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("place must use a valid IANA time zone") from exc

    timing: dict[str, Any] = {
        "startSince1970": int(time.start.timestamp() * 1000),
        "isAllDay": time.is_all_day,
        "isOpenEnded": time.is_open_ended,
    }
    if time.end is not None:
        timing["endSince1970"] = int(time.end.timestamp() * 1000)
    location = (
        place.model_dump(mode="json", by_alias=True, exclude_none=True)
        if place is not None
        else {}
    )
    if "time_zone_identifier" in location:
        location["timeZoneIdentifier"] = location.pop("time_zone_identifier")

    preview: dict[str, Any] = {
        "cloudkit.title": {"type": "STRING", "value": title},
        "isPrivate": {"type": "INT64", "value": int(is_private)},
        "time": {"type": "BYTES", "value": encode_json_bytes(timing)},
        "place": {"type": "BYTES", "value": encode_json_bytes(location)},
        "style": {"type": "BYTES", "value": encode_json_bytes({"titleFont": 0})},
        "startDate": {"type": "TIMESTAMP", "value": timing["startSince1970"]},
    }
    if time.end is not None:
        preview["endDate"] = {"type": "TIMESTAMP", "value": timing["endSince1970"]}
    fields = {
        key: {**value, "isEncrypted": True}
        for key, value in preview.items()
        if key not in ("cloudkit.title", "startDate", "endDate")
    }
    fields.update({
        "title": {"type": "STRING", "value": title, "isEncrypted": True},
        "notes": {"type": "STRING", "value": notes, "isEncrypted": True},
        "maxAdditionalGuestsPerRSVP": {
            "type": "INT64",
            "value": max_additional_guests_per_rsvp,
            "isEncrypted": True,
        },
        "background": {
            "type": "BYTES",
            "value": encode_json_bytes({"kind": "image", "visibility": 1}),
            "isEncrypted": True,
        },
        "integrations": {
            "type": "BYTES",
            "value": encode_json_bytes({"version": "1", "data": []}),
            "isEncrypted": True,
        },
    })
    if max_attendees is not None:
        fields["maxAttendees"] = {
            "type": "INT64",
            "value": max_attendees,
            "isEncrypted": True,
        }
    return fields, preview
