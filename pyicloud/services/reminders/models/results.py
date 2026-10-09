"""Result models for reminders queries."""

from __future__ import annotations

from pyicloud.common.models import FrozenServiceModel

from .domain import (
    Alarm,
    DateTrigger,
    Hashtag,
    ImageAttachment,
    LocationTrigger,
    RecurrenceRule,
    Reminder,
    URLAttachment,
)


class AlarmWithTrigger(FrozenServiceModel):
    """Alarm paired with its optional location or date trigger."""

    alarm: Alarm
    trigger: LocationTrigger | DateTrigger | None = None


class ListRemindersResult(FrozenServiceModel):
    """Complete result of querying reminders including related alarms,
    attachments, and metadata."""

    reminders: list[Reminder]
    alarms: dict[str, Alarm]
    triggers: dict[str, LocationTrigger | DateTrigger]
    attachments: dict[str, URLAttachment | ImageAttachment]
    hashtags: dict[str, Hashtag]
    recurrence_rules: dict[str, RecurrenceRule]
