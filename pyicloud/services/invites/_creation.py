"""Validated, capture-derived fields for a new unpublished event and its share."""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codecs import encode_json_bytes
from .models.constants import EventDetailsField
from .models.dto import EventPlace, EventTime

# Share-preview keys with no EventDetails counterpart: the preview mirrors what
# the invite card shows, while EventDetails stores its title and dates alone.
_SHARE_PREVIEW_ONLY = frozenset({"cloudkit.title", "startDate", "endDate"})


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
    _validate_title(title)
    _validate_time(time)
    _validate_notes(notes, is_private)
    _validate_counts(max_attendees, max_additional_guests_per_rsvp)
    _validate_place(place)

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
        EventDetailsField.IS_PRIVATE.value: {
            "type": "INT64",
            "value": int(is_private),
        },
        EventDetailsField.TIME.value: {
            "type": "BYTES",
            "value": encode_json_bytes(timing),
        },
        EventDetailsField.PLACE.value: {
            "type": "BYTES",
            "value": encode_json_bytes(location),
        },
        EventDetailsField.STYLE.value: {
            "type": "BYTES",
            "value": encode_json_bytes({"titleFont": 0}),
        },
        "startDate": {"type": "TIMESTAMP", "value": timing["startSince1970"]},
    }
    if time.end is not None:
        preview["endDate"] = {"type": "TIMESTAMP", "value": timing["endSince1970"]}
    fields = {
        key: {**value, "isEncrypted": True}
        for key, value in preview.items()
        if key not in _SHARE_PREVIEW_ONLY
    }
    fields.update({
        EventDetailsField.TITLE.value: {
            "type": "STRING",
            "value": title,
            "isEncrypted": True,
        },
        EventDetailsField.NOTES.value: {
            "type": "STRING",
            "value": notes,
            "isEncrypted": True,
        },
        EventDetailsField.MAX_ADDITIONAL_GUESTS_PER_RSVP.value: {
            "type": "INT64",
            "value": max_additional_guests_per_rsvp,
            "isEncrypted": True,
        },
        EventDetailsField.BACKGROUND.value: {
            "type": "BYTES",
            "value": encode_json_bytes({"kind": "image", "visibility": 1}),
            "isEncrypted": True,
        },
        EventDetailsField.INTEGRATIONS.value: {
            "type": "BYTES",
            "value": encode_json_bytes({"version": "1", "data": []}),
            "isEncrypted": True,
        },
    })
    if max_attendees is not None:
        fields[EventDetailsField.MAX_ATTENDEES.value] = {
            "type": "INT64",
            "value": max_attendees,
            "isEncrypted": True,
        }
    return fields, preview


def _validate_title(title: str) -> None:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")


def _validate_time(time: EventTime) -> None:
    if not isinstance(time, EventTime):
        raise ValueError("time must be an EventTime")
    for date in (time.start, time.end):
        if date is not None and date.utcoffset() is None:
            raise ValueError("Event times must be timezone-aware")
    if time.end is not None and time.end.timestamp() < time.start.timestamp():
        raise ValueError("Event end must not precede start")
    if time.is_open_ended and time.end is not None:
        raise ValueError("An open-ended event cannot have an end time")


def _validate_notes(notes: str, is_private: bool) -> None:
    if not isinstance(notes, str) or not isinstance(is_private, bool):
        raise ValueError("notes must be a string and is_private must be a boolean")


def _validate_counts(
    max_attendees: int | None, max_additional_guests_per_rsvp: int
) -> None:
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


def _validate_place(place: EventPlace | None) -> None:
    if place is not None and not isinstance(place, EventPlace):
        raise ValueError("place must be an EventPlace")
    if place is not None and place.time_zone_identifier is not None:
        try:
            ZoneInfo(place.time_zone_identifier)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("place must use a valid IANA time zone") from exc
