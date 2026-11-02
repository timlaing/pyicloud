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
  - RemindersService.set_early_reminders(reminder, alerts) -> None
  - RemindersService.set_urgent(reminder, enabled, person_id=None) -> None
  - RemindersService.add_location_trigger(reminder, ...) ->
    tuple[Alarm, LocationTrigger]
  - RemindersService.create_hashtag(...) / update_hashtag(...) /
    delete_hashtag(...)
  - RemindersService.create_url_attachment(...) / update_attachment(...) /
    delete_attachment(...)
  - RemindersService.create_recurrence_rule(...) / update_recurrence_rule(...) /
    delete_recurrence_rule(...)
  - RemindersService.alarms_for(reminder) -> list[AlarmWithTrigger]
  - RemindersService.tags_for(reminder) -> list[Hashtag]
  - RemindersService.attachments_for(reminder) -> list[Attachment]
  - RemindersService.recurrence_rules_for(reminder) -> list[RecurrenceRule]

The service returns typed reminder, list, alarm, attachment, hashtag, and
recurrence models so normal callers do not need to work with CloudKit records
directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
import logging
from typing import Any, Union

from pyicloud.common.cloudkit import CKRecord, CKZoneIDReq
from pyicloud.common.cloudkit.base import CloudKitExtraMode
from pyicloud.services.base import BaseService

from ._mappers import RemindersRecordMapper
from ._protocol import (
    _decode_crdt_document,
    _encode_crdt_document,
    _generate_resolution_token_map,
)
from ._reads import ListDiscoveryState, RemindersReadAPI
from ._writes import RemindersWriteAPI
from .client import CloudKitRemindersClient
from .models import (
    Alarm,
    AlarmWithTrigger,
    DateTrigger,
    EarlyReminder,
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
from .models.domain import ZonedRemindersRecord

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
        params: dict[str, Any],
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
        shared_endpoint = (
            f"{self.service_root}/database/1/{self._CONTAINER}/{self._ENV}/shared"
        )
        self._shared_raw = CloudKitRemindersClient(
            shared_endpoint,
            session,
            base_params,
            validation_extra=cloudkit_validation_extra,
        )
        self._list_zones: dict[tuple[str, str, str, str | None], RemindersList] = {}

        def get_raw() -> CloudKitRemindersClient:
            return self._raw

        self._mapper = RemindersRecordMapper(get_raw, LOGGER)
        self._reads = RemindersReadAPI(
            get_raw, self._mapper, LOGGER, lambda: self._shared_raw
        )
        self._writes = RemindersWriteAPI(
            get_raw,
            self._mapper,
            LOGGER,
            account_id=(params or {}).get("dsid"),
        )

    def lists(self) -> Iterable[RemindersList]:
        """
        Yield reminder lists as ``RemindersList`` models.

        Use this to discover available lists and obtain the ``list_id`` values
        needed by creation and query helpers.
        """

        state = ListDiscoveryState()
        return self._cache_lists(self._reads.lists(state=state), state=state)

    def _cache_lists(
        self, items: Iterable[RemindersList], *, state: ListDiscoveryState
    ) -> Iterable[RemindersList]:
        """Replace the routing snapshot only when discovery completes."""
        discovered: dict[tuple[str, str, str, str | None], RemindersList] = {}
        for item in items:
            if not item.deleted:
                discovered[(item.id, item.scope, item.zone_name, item.zone_owner)] = (
                    item
                )
            yield item
        if state.complete:
            self._list_zones = discovered

    def reminders(
        self,
        list_id: str | RemindersList | None = None,
    ) -> Iterable[Reminder]:
        """
        Yield reminders across all lists or for a specific list.

        Args:
            list_id: Optional list identifier. When provided, only reminders in
                that list are returned.
        """
        reminder_map: dict[tuple[str, str, str | None, str], Reminder] = {}

        list_ids: list[str | RemindersList] = (
            [list_id] if list_id else list(self.lists())
        )

        for lid in list_ids:
            batch = self.list_reminders(
                list_id=lid,
                include_completed=True,
                results_limit=200,
            )
            for reminder in batch.reminders:
                reminder_map[
                    (
                        reminder.scope,
                        reminder.zone_name,
                        reminder.zone_owner,
                        reminder.id,
                    )
                ] = reminder

        yield from reminder_map.values()

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
        since: str | None = None,
    ) -> Iterable[ReminderChangeEvent]:
        """
        Yield reminder change events since an optional sync token.

        Updated reminders are returned with ``type="updated"`` and a hydrated
        ``reminder`` payload. Deletions are returned with ``type="deleted"``
        and only the ``reminder_id`` populated.
        """
        return self._reads.iter_changes(since=since)

    def _list_route(
        self, list_id: str, *, discover: bool = False
    ) -> tuple[str, str, str | None]:
        """Resolve a unique cached list route, rejecting ambiguous record IDs."""
        matches = [item for key, item in self._list_zones.items() if key[0] == list_id]
        if not matches and discover:
            state = ListDiscoveryState()
            list(
                self._cache_lists(
                    self._reads.lists(routing_only=True, state=state), state=state
                )
            )
            matches = [
                item for key, item in self._list_zones.items() if key[0] == list_id
            ]
        if len(matches) > 1:
            raise ValueError(
                "Ambiguous list ID; pass a RemindersList with zone metadata"
            )
        if matches:
            item = matches[0]
            return item.scope, item.zone_name, item.zone_owner
        return "private", "Reminders", None

    def _apis_for(
        self, record: ZonedRemindersRecord
    ) -> tuple[RemindersReadAPI, RemindersWriteAPI]:
        """Build immutable per-operation routing without changing private helpers."""
        route = (record.scope, record.zone_name, record.zone_owner)
        if (
            isinstance(record, Reminder)
            and "scope" not in record.model_fields_set
            and not {"zone_name", "zone_owner"} & record.model_fields_set
        ):
            route = self._list_route(record.list_id, discover=True)
        scope, zone_name, zone_owner = route
        if route == ("private", "Reminders", None):
            return self._reads, self._writes
        raw = self._shared_raw if scope == "shared" else self._raw
        mapper = RemindersRecordMapper(
            lambda: raw,
            LOGGER,
            scope=scope,
            zone_name=zone_name,
            zone_owner=zone_owner,
        )
        zone_id = CKZoneIDReq(
            zoneName=zone_name,
            ownerRecordName=zone_owner,
            zoneType="REGULAR_CUSTOM_ZONE",
        )
        return (
            RemindersReadAPI(lambda: raw, mapper, LOGGER, zone_id=zone_id),
            RemindersWriteAPI(
                lambda: raw,
                mapper,
                LOGGER,
                zone_id=zone_id,
                account_id=self.params.get("dsid"),
                scope=scope,
            ),
        )

    def get(
        self, reminder_id: str, *, list_context: RemindersList | Reminder | None = None
    ) -> Reminder:
        """
        Return a single reminder by ID.

        Args:
            reminder_id: The full reminder record identifier.
            list_context: List or reminder carrying the shared zone identity.
        """
        reads = self._reads if list_context is None else self._apis_for(list_context)[0]
        return reads.get(reminder_id)

    def _list_context(self, value: str | RemindersList) -> RemindersList:
        """Retain an explicit route or resolve a unique string list identity."""
        if isinstance(value, RemindersList):
            return value
        scope, zone_name, zone_owner = self._list_route(value, discover=True)
        return RemindersList(
            id=value, title="", scope=scope, zone_name=zone_name, zone_owner=zone_owner
        )

    def create(
        self,
        list_id: str | RemindersList,
        title: str,
        desc: str = "",
        completed: bool = False,
        due_date: datetime | None = None,
        priority: int = 0,
        flagged: bool = False,
        all_day: bool = False,
        time_zone: str | None = None,
        parent_reminder_id: str | None = None,
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
            time_zone: Optional IANA time zone name for the due date. When
                omitted, it is taken from a ``ZoneInfo`` due date, or set to
                ``UTC`` for naive and UTC due dates (not for all-day ones).
            parent_reminder_id: Optional parent reminder ID for subtasks.
        """
        context = self._list_context(list_id)
        return self._apis_for(context)[1].create(
            list_id=context.id,
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
        self._apis_for(reminder)[1].update(reminder)

    def delete(self, reminder: Reminder) -> None:
        """
        Soft-delete a reminder in iCloud.

        The provided ``Reminder`` model is marked deleted and the remote record
        is updated accordingly.
        """
        self._apis_for(reminder)[1].delete(reminder)

    def set_early_reminders(
        self,
        reminder: Reminder,
        alerts: Sequence[EarlyReminder],
    ) -> None:
        """
        Replace a reminder's "Early Reminder" alerts (before the due date).

        Apple stores early reminders in the reminder record's
        ``DueDateDeltaAlertsData`` field rather than as alarm records. The
        payload carries a per-account ``accountIdentifier``: a value the
        Reminders app already wrote is reused, and otherwise the signed-in
        account's directory id is used, so no extra input is needed even for
        an account's first early reminder. Pass an empty ``alerts`` sequence
        to remove them all.
        """
        self._apis_for(reminder)[1].set_early_reminders(reminder, alerts)

    def set_urgent(
        self,
        reminder: Reminder,
        enabled: bool,
        *,
        person_id: str | None = None,
    ) -> None:
        """
        Enable or disable Urgent presentation alarms (iOS 26.2+) on a reminder.

        The state is an uploaded asset plus a checksum string. Apple only
        exposes the per-account ``person_id`` on a reminder the app itself
        marked Urgent, so read one of those first (or pass ``person_id``).
        The checksum Apple writes is not reproducible; the SHA-512 of the
        asset bytes is written in its place, which the server accepted.
        """
        self._apis_for(reminder)[1].set_urgent(reminder, enabled, person_id=person_id)

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
        return self._apis_for(reminder)[1].add_location_trigger(
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
        return self._apis_for(reminder)[1].create_hashtag(reminder, name)

    def update_hashtag(self, hashtag: Hashtag, name: str) -> None:
        """
        Update a hashtag name.

        Note: the iCloud Reminders web app currently treats hashtag names as
        effectively read-only in some live flows, so rename behavior may not be
        reflected consistently outside the API.
        """
        self._apis_for(hashtag)[1].update_hashtag(hashtag, name)

    def delete_hashtag(self, reminder: Reminder, hashtag: Hashtag) -> None:
        """Detach and delete a hashtag from ``reminder``."""
        self._apis_for(reminder)[1].delete_hashtag(reminder, hashtag)

    def create_url_attachment(
        self,
        reminder: Reminder,
        url: str,
        uti: str = "public.url",
    ) -> URLAttachment:
        """Create a URL attachment on ``reminder``."""
        return self._apis_for(reminder)[1].create_url_attachment(reminder, url, uti)

    def update_attachment(
        self,
        attachment: Attachment,
        *,
        url: str | None = None,
        uti: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """
        Update a reminder attachment in place.

        URL attachments can update ``url`` and ``uti``. Image attachments can
        update metadata such as filename, size, and dimensions.
        """
        self._apis_for(attachment)[1].update_attachment(
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
        self._apis_for(reminder)[1].delete_attachment(reminder, attachment)

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
        return self._apis_for(reminder)[1].create_recurrence_rule(
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
        frequency: RecurrenceFrequency | None = None,
        interval: int | None = None,
        occurrence_count: int | None = None,
        first_day_of_week: int | None = None,
    ) -> None:
        """Update fields on an existing recurrence rule."""
        self._apis_for(recurrence_rule)[1].update_recurrence_rule(
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
        self._apis_for(reminder)[1].delete_recurrence_rule(reminder, recurrence_rule)

    def list_reminders(
        self,
        list_id: str | RemindersList,
        include_completed: bool = False,
        results_limit: int = 200,
    ) -> ListRemindersResult:
        """
        Return a compound reminder snapshot for one list.

        The result includes the list's reminders plus related alarms,
        triggers, attachments, hashtags, and recurrence rules keyed by ID.
        """
        if isinstance(list_id, RemindersList):
            scope, zone_name, zone_owner = (
                list_id.scope,
                list_id.zone_name,
                list_id.zone_owner,
            )
            list_id = list_id.id
        else:
            scope, zone_name, zone_owner = self._list_route(list_id, discover=True)
        reads = self._apis_for(
            RemindersList(
                id=list_id,
                title="",
                scope=scope,
                zone_name=zone_name,
                zone_owner=zone_owner,
            )
        )[0]
        return reads.list_reminders(
            list_id=list_id,
            include_completed=include_completed,
            results_limit=results_limit,
            scope=scope,
            zone_name=zone_name,
            zone_owner=zone_owner,
        )

    def alarms_for(self, reminder: Reminder) -> list[AlarmWithTrigger]:
        """Return alarm rows, including attached location or date triggers."""
        return self._apis_for(reminder)[0].alarms_for(reminder)

    def tags_for(self, reminder: Reminder) -> list[Hashtag]:
        """Return hashtags currently attached to ``reminder``."""
        return self._apis_for(reminder)[0].tags_for(reminder)

    def attachments_for(self, reminder: Reminder) -> list[Attachment]:
        """Return attachments currently attached to ``reminder``."""
        return self._apis_for(reminder)[0].attachments_for(reminder)

    def recurrence_rules_for(self, reminder: Reminder) -> list[RecurrenceRule]:
        """Return recurrence rules currently attached to ``reminder``."""
        return self._apis_for(reminder)[0].recurrence_rules_for(reminder)

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

    def _record_to_alarm_trigger(
        self, rec: CKRecord
    ) -> LocationTrigger | DateTrigger | None:
        return self._mapper.record_to_alarm_trigger(rec)

    def _record_to_attachment(self, rec: CKRecord) -> Attachment | None:
        return self._mapper.record_to_attachment(rec)

    def _record_to_hashtag(self, rec: CKRecord) -> Hashtag:
        return self._mapper.record_to_hashtag(rec)

    def _record_to_recurrence_rule(self, rec: CKRecord) -> RecurrenceRule:
        return self._mapper.record_to_recurrence_rule(rec)
