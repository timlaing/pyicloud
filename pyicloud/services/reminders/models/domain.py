from datetime import datetime
from enum import IntEnum
from typing import Literal, Optional

from pydantic import Field

from pyicloud.common.models import FrozenServiceModel, MutableServiceModel


class Reminder(MutableServiceModel):
    id: str
    list_id: str
    title: str
    desc: str = ""
    completed: bool = False
    completed_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    priority: int = 0
    flagged: bool = False
    all_day: bool = False
    deleted: bool = False
    time_zone: Optional[str] = None
    alarm_ids: list[str] = Field(default_factory=list)
    hashtag_ids: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    recurrence_rule_ids: list[str] = Field(default_factory=list)
    parent_reminder_id: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    record_change_tag: Optional[str] = None


class ReminderChangeEvent(FrozenServiceModel):
    """Incremental reminder change event emitted by ``iter_changes()``."""

    type: Literal["updated", "deleted"]
    reminder_id: str
    reminder: Optional[Reminder] = None


class RemindersList(MutableServiceModel):
    id: str
    title: str
    color: Optional[str] = None
    count: int = 0
    badge_emblem: Optional[str] = None
    sorting_style: Optional[str] = None
    is_group: bool = False
    reminder_ids: list[str] = Field(default_factory=list)
    guid: Optional[str] = None
    record_change_tag: Optional[str] = None


# --- Alarm records ---


class Alarm(MutableServiceModel):
    """Container for alarm triggers, referenced by Reminder.alarm_ids."""

    id: str
    alarm_uid: str
    reminder_id: str
    trigger_id: str
    record_change_tag: Optional[str] = None


class Proximity(IntEnum):
    """Geofence proximity direction."""

    ARRIVING = 1
    LEAVING = 2


class LocationTrigger(MutableServiceModel):
    """Location-based alarm trigger (geofence)."""

    id: str
    alarm_id: str
    title: str = ""
    address: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    radius: float = Field(default=0.0, ge=0.0)
    proximity: Proximity = Proximity.ARRIVING
    location_uid: str = ""
    record_change_tag: Optional[str] = None


# --- Attachment records ---


class URLAttachment(MutableServiceModel):
    """URL attachment on a reminder."""

    id: str
    reminder_id: str
    url: str = ""
    uti: str = "public.url"
    record_change_tag: Optional[str] = None


class ImageAttachment(MutableServiceModel):
    """Image attachment on a reminder."""

    id: str
    reminder_id: str
    file_asset_url: str = ""
    filename: str = ""
    file_size: int = Field(default=0, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    uti: str = "public.jpeg"
    record_change_tag: Optional[str] = None


# --- Hashtag records ---


class Hashtag(MutableServiceModel):
    """Tag associated with a reminder."""

    id: str
    name: str
    reminder_id: str
    created: Optional[datetime] = None
    record_change_tag: Optional[str] = None


# --- Recurrence rules ---


class RecurrenceFrequency(IntEnum):
    """Recurrence frequency type."""

    DAILY = 1
    WEEKLY = 2
    MONTHLY = 3
    YEARLY = 4


class RecurrenceRule(MutableServiceModel):
    """Recurrence rule for a repeating reminder."""

    id: str
    reminder_id: str
    frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY
    interval: int = Field(default=1, ge=1)
    occurrence_count: int = Field(default=0, ge=0)  # 0 = infinite
    first_day_of_week: int = Field(default=0, ge=0, le=6)
    record_change_tag: Optional[str] = None
