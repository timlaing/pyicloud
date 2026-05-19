"""Result models for reminders queries."""

from __future__ import annotations

from typing import Optional

from pyicloud.common.models import FrozenServiceModel

from .domain import (
    Alarm,
    Hashtag,
    ImageAttachment,
    LocationTrigger,
    RecurrenceRule,
    Reminder,
    URLAttachment,
)


class AlarmWithTrigger(FrozenServiceModel):
    """Alarm paired with its optional location trigger."""

    alarm: Alarm
    trigger: Optional[LocationTrigger] = None


class ListRemindersResult(FrozenServiceModel):
    """Complete result of querying reminders including related alarms, attachments, and metadata."""

    reminders: list[Reminder]
    alarms: dict[str, Alarm]
    triggers: dict[str, LocationTrigger]
    attachments: dict[str, URLAttachment | ImageAttachment]
    hashtags: dict[str, Hashtag]
    recurrence_rules: dict[str, RecurrenceRule]
