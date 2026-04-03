"""
High-level Reminders service built on top of the iCloud Reminders CloudKit API.

Public API:
  - RemindersService.lists() -> Iterable[RemindersList]
  - RemindersService.reminders(list_id=None) -> Iterable[Reminder]
  - RemindersService.list_reminders(list_id, include_completed=False, results_limit=200)
  - RemindersService.get(reminder_id) -> Reminder
  - RemindersService.sync_cursor() -> str
  - RemindersService.iter_changes(since=None) -> Iterable[ReminderChangeEvent]
  - RemindersService.create(...)
  - RemindersService.update(reminder) -> None
  - RemindersService.delete(reminder) -> None
  - RemindersService.add_location_trigger(reminder, ...) -> tuple[Alarm, LocationTrigger]
  - RemindersService.create_hashtag(...) / update_hashtag(...) / delete_hashtag(...)
  - RemindersService.create_url_attachment(...) / update_attachment(...) / delete_attachment(...)
  - RemindersService.create_recurrence_rule(...) / update_recurrence_rule(...) / delete_recurrence_rule(...)
  - RemindersService.alarms_for(reminder) -> list[AlarmWithTrigger]
  - RemindersService.tags_for(reminder) -> list[Hashtag]
  - RemindersService.attachments_for(reminder) -> list[Attachment]
  - RemindersService.recurrence_rules_for(reminder) -> list[RecurrenceRule]

The service returns typed reminder, list, alarm, attachment, hashtag, and
recurrence models so normal callers do not need to work with CloudKit records
directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union

from pyicloud.common.cloudkit import CKRecord
from pyicloud.common.cloudkit.base import CloudKitExtraMode
from pyicloud.services.base import BaseService

from ._mappers import RemindersRecordMapper
from ._protocol import (
    _decode_crdt_document,
    _encode_crdt_document,
    _generate_resolution_token_map,
)
from ._reads import RemindersReadAPI
from ._writes import RemindersWriteAPI
from .client import CloudKitRemindersClient
from .models import (
    Alarm,
    AlarmWithTrigger,
    Hashtag,
    ImageAttachment,
    ListRemindersResult,
    LocationTrigger,
    Proximity,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    ReminderChangeEvent,
    RemindersList,
    URLAttachment,
)

LOGGER = logging.getLogger(__name__)

Attachment = Union[URLAttachment, ImageAttachment]


class RemindersService(BaseService):
    """
    Typed Reminders API for snapshot reads, incremental sync, and mutations.

    Use this service for list discovery, reminder CRUD, and supported reminder
    metadata such as alarms, hashtags, attachments, and recurrence rules.
    """

    _CONTAINER = "com.apple.reminders"
    _ENV = "production"
    _SCOPE = "private"

    def __init__(
        self,
        service_root: str,
        session: Any,
        params: Dict[str, Any],
        *,
        cloudkit_validation_extra: CloudKitExtraMode | None = None,
    ):
        super().__init__(service_root, session, params)
        endpoint = (
            f"{self.service_root}/database/1/"
            f"{self._CONTAINER}/{self._ENV}/{self._SCOPE}"
        )
        base_params = {
            "remapEnums": True,
            "getCurrentSyncToken": True,
            **(params or {}),
        }
        self._raw = CloudKitRemindersClient(
            endpoint,
            session,
            base_params,
            validation_extra=cloudkit_validation_extra,
        )

        def get_raw() -> CloudKitRemindersClient:
            return self._raw

        self._mapper = RemindersRecordMapper(get_raw, LOGGER)
        self._reads = RemindersReadAPI(get_raw, self._mapper, LOGGER)
        self._writes = RemindersWriteAPI(get_raw, self._mapper, LOGGER)

    def lists(self) -> Iterable[RemindersList]:
        """
        Yield reminder lists as ``RemindersList`` models.

        Use this to discover available lists and obtain the ``list_id`` values
        needed by creation and query helpers.
        """
        return self._reads.lists()

    def reminders(
        self,
        list_id: Optional[str] = None,
    ) -> Iterable[Reminder]:
        """
        Yield reminders across all lists or for a specific list.

        Args:
            list_id: Optional list identifier. When provided, only reminders in
                that list are returned.
        """
        reminder_map: Dict[str, Reminder] = {}

        list_ids: List[str]
        if list_id:
            list_ids = [list_id]
        else:
            list_ids = [lst.id for lst in self.lists()]

        for lid in list_ids:
            batch = self.list_reminders(
                list_id=lid,
                include_completed=True,
                results_limit=200,
            )
            for reminder in batch.reminders:
                reminder_map[reminder.id] = reminder

        for reminder in reminder_map.values():
            yield reminder

    def sync_cursor(self) -> str:
        """
        Return the current sync token for the Reminders zone.

        Persist this token and pass it to ``iter_changes(since=...)`` later to
        enumerate only newer changes.
        """
        return self._reads.sync_cursor()

    def iter_changes(
        self,
        *,
        since: Optional[str] = None,
    ) -> Iterable[ReminderChangeEvent]:
        """
        Yield reminder change events since an optional sync token.

        Updated reminders are returned with ``type="updated"`` and a hydrated
        ``reminder`` payload. Deletions are returned with ``type="deleted"``
        and only the ``reminder_id`` populated.
        """
        return self._reads.iter_changes(since=since)

    def get(self, reminder_id: str) -> Reminder:
        """
        Return a single reminder by ID.

        Args:
            reminder_id: The full reminder record identifier.
        """
        return self._reads.get(reminder_id)

    def create(
        self,
        list_id: str,
        title: str,
        desc: str = "",
        completed: bool = False,
        due_date: Optional[datetime] = None,
        priority: int = 0,
        flagged: bool = False,
        all_day: bool = False,
        time_zone: Optional[str] = None,
        parent_reminder_id: Optional[str] = None,
    ) -> Reminder:
        """
        Create a reminder and return the hydrated ``Reminder`` model.

        Args:
            list_id: Target reminder list ID.
            title: Reminder title.
            desc: Reminder notes/body text.
            completed: Whether the reminder should be created as completed.
            due_date: Optional due date. Naive datetimes are treated as UTC.
            priority: Apple Reminders priority value. Common values are
                ``0`` (none), ``1`` (high), ``5`` (medium), and ``9`` (low).
            flagged: Whether the reminder is flagged.
            all_day: Whether the reminder should be treated as all-day.
            time_zone: Optional time zone name for the due date.
            parent_reminder_id: Optional parent reminder ID for subtasks.
        """
        return self._writes.create(
            list_id=list_id,
            title=title,
            desc=desc,
            completed=completed,
            due_date=due_date,
            priority=priority,
            flagged=flagged,
            all_day=all_day,
            time_zone=time_zone,
            parent_reminder_id=parent_reminder_id,
        )

    def update(self, reminder: Reminder) -> None:
        """
        Persist changes made to a ``Reminder`` model back to iCloud.

        Fetch the reminder, mutate its fields locally, then pass it to
        ``update()``.
        """
        self._writes.update(reminder)

    def delete(self, reminder: Reminder) -> None:
        """
        Soft-delete a reminder in iCloud.

        The provided ``Reminder`` model is marked deleted and the remote record
        is updated accordingly.
        """
        self._writes.delete(reminder)

    def add_location_trigger(
        self,
        reminder: Reminder,
        title: str = "",
        address: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        radius: float = 100.0,
        proximity: Proximity = Proximity.ARRIVING,
    ) -> tuple[Alarm, LocationTrigger]:
        """
        Add a location-based alarm trigger to a reminder.

        Returns the created ``Alarm`` and ``LocationTrigger`` records.
        """
        return self._writes.add_location_trigger(
            reminder=reminder,
            title=title,
            address=address,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            proximity=proximity,
        )

    def create_hashtag(self, reminder: Reminder, name: str) -> Hashtag:
        """Create and attach a hashtag to ``reminder``."""
        return self._writes.create_hashtag(reminder, name)

    def update_hashtag(self, hashtag: Hashtag, name: str) -> None:
        """
        Update a hashtag name.

        Note: the iCloud Reminders web app currently treats hashtag names as
        effectively read-only in some live flows, so rename behavior may not be
        reflected consistently outside the API.
        """
        self._writes.update_hashtag(hashtag, name)

    def delete_hashtag(self, reminder: Reminder, hashtag: Hashtag) -> None:
        """Detach and delete a hashtag from ``reminder``."""
        self._writes.delete_hashtag(reminder, hashtag)

    def create_url_attachment(
        self,
        reminder: Reminder,
        url: str,
        uti: str = "public.url",
    ) -> URLAttachment:
        """Create a URL attachment on ``reminder``."""
        return self._writes.create_url_attachment(reminder, url, uti)

    def update_attachment(
        self,
        attachment: Attachment,
        *,
        url: Optional[str] = None,
        uti: Optional[str] = None,
        filename: Optional[str] = None,
        file_size: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        """
        Update a reminder attachment in place.

        URL attachments can update ``url`` and ``uti``. Image attachments can
        update metadata such as filename, size, and dimensions.
        """
        self._writes.update_attachment(
            attachment,
            url=url,
            uti=uti,
            filename=filename,
            file_size=file_size,
            width=width,
            height=height,
        )

    def delete_attachment(self, reminder: Reminder, attachment: Attachment) -> None:
        """Detach and delete an attachment from ``reminder``."""
        self._writes.delete_attachment(reminder, attachment)

    def create_recurrence_rule(
        self,
        reminder: Reminder,
        *,
        frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY,
        interval: int = 1,
        occurrence_count: int = 0,
        first_day_of_week: int = 0,
    ) -> RecurrenceRule:
        """
        Create and attach a recurrence rule to ``reminder``.

        ``occurrence_count=0`` means the recurrence is open-ended.
        """
        return self._writes.create_recurrence_rule(
            reminder,
            frequency=frequency,
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        )

    def update_recurrence_rule(
        self,
        recurrence_rule: RecurrenceRule,
        *,
        frequency: Optional[RecurrenceFrequency] = None,
        interval: Optional[int] = None,
        occurrence_count: Optional[int] = None,
        first_day_of_week: Optional[int] = None,
    ) -> None:
        """Update fields on an existing recurrence rule."""
        self._writes.update_recurrence_rule(
            recurrence_rule,
            frequency=frequency,
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        )

    def delete_recurrence_rule(
        self,
        reminder: Reminder,
        recurrence_rule: RecurrenceRule,
    ) -> None:
        """Detach and delete a recurrence rule from ``reminder``."""
        self._writes.delete_recurrence_rule(reminder, recurrence_rule)

    def list_reminders(
        self,
        list_id: str,
        include_completed: bool = False,
        results_limit: int = 200,
    ) -> ListRemindersResult:
        """
        Return a compound reminder snapshot for one list.

        The result includes the list's reminders plus related alarms,
        triggers, attachments, hashtags, and recurrence rules keyed by ID.
        """
        return self._reads.list_reminders(
            list_id=list_id,
            include_completed=include_completed,
            results_limit=results_limit,
        )

    def alarms_for(self, reminder: Reminder) -> List[AlarmWithTrigger]:
        """Return alarm rows, including attached location triggers, for ``reminder``."""
        return self._reads.alarms_for(reminder)

    def tags_for(self, reminder: Reminder) -> List[Hashtag]:
        """Return hashtags currently attached to ``reminder``."""
        return self._reads.tags_for(reminder)

    def attachments_for(self, reminder: Reminder) -> List[Attachment]:
        """Return attachments currently attached to ``reminder``."""
        return self._reads.attachments_for(reminder)

    def recurrence_rules_for(self, reminder: Reminder) -> List[RecurrenceRule]:
        """Return recurrence rules currently attached to ``reminder``."""
        return self._reads.recurrence_rules_for(reminder)

    # Compatibility wrappers for the service's tested helper surface.
    def _decode_crdt_document(self, encrypted_value: str | bytes) -> str:
        return _decode_crdt_document(encrypted_value)

    def _encode_crdt_document(self, text: str) -> str:
        return _encode_crdt_document(text)

    def _generate_resolution_token_map(self, fields_modified: list[str]) -> str:
        return _generate_resolution_token_map(fields_modified)

    def _record_to_list(self, rec: CKRecord) -> RemindersList:
        return self._mapper.record_to_list(rec)

    def _record_to_reminder(self, rec: CKRecord) -> Reminder:
        return self._mapper.record_to_reminder(rec)

    def _record_to_alarm(self, rec: CKRecord) -> Alarm:
        return self._mapper.record_to_alarm(rec)

    def _record_to_alarm_trigger(self, rec: CKRecord) -> Optional[LocationTrigger]:
        return self._mapper.record_to_alarm_trigger(rec)

    def _record_to_attachment(self, rec: CKRecord) -> Optional[Attachment]:
        return self._mapper.record_to_attachment(rec)

    def _record_to_hashtag(self, rec: CKRecord) -> Hashtag:
        return self._mapper.record_to_hashtag(rec)

    def _record_to_recurrence_rule(self, rec: CKRecord) -> RecurrenceRule:
        return self._mapper.record_to_recurrence_rule(rec)
