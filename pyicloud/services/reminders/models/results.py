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
    alarm: Alarm
    trigger: Optional[LocationTrigger] = None


class ListRemindersResult(FrozenServiceModel):
    reminders: list[Reminder]
    alarms: dict[str, Alarm]
    triggers: dict[str, LocationTrigger]
    attachments: dict[str, URLAttachment | ImageAttachment]
    hashtags: dict[str, Hashtag]
    recurrence_rules: dict[str, RecurrenceRule]
