"""Read-side orchestration for the Reminders service."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKFVInt64,
    CKFVReference,
    CKQueryFilterBy,
    CKQueryObject,
    CKRecord,
    CKReference,
    CKTombstoneRecord,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
)

from ._constants import _REMINDERS_ZONE, _REMINDERS_ZONE_REQ
from ._mappers import Attachment, RemindersRecordMapper
from ._protocol import _as_record_name
from ._support import _assert_read_success
from .client import RemindersApiError
from .models import (
    Alarm,
    AlarmWithTrigger,
    Hashtag,
    ListRemindersResult,
    LocationTrigger,
    RecurrenceRule,
    Reminder,
    ReminderChangeEvent,
    RemindersList,
)

TRelated = TypeVar("TRelated")


class RemindersReadAPI:
    """Encapsulates read/query behavior for the Reminders service."""

    def __init__(
        self,
        get_raw: Callable[[], Any],
        mapper: RemindersRecordMapper,
        logger: logging.Logger,
    ) -> None:
        self._get_raw = get_raw
        self._mapper = mapper
        self._logger = logger

    def _iter_zone_change_pages(
        self,
        *,
        desired_record_types: Optional[List[str]],
        desired_keys: Optional[List[str]] = None,
        sync_token: Optional[str] = None,
        reverse: Optional[bool] = None,
    ) -> Iterable[CKZoneChangesZone]:
        """Yield paged /changes/zone results, advancing the zone sync token."""
        next_sync_token = sync_token
        more_coming = True

        while more_coming:
            response = self._get_raw().changes(
                zone_req=CKZoneChangesZoneReq(
                    zoneID=_REMINDERS_ZONE,
                    desiredRecordTypes=desired_record_types,
                    desiredKeys=desired_keys,
                    reverse=reverse,
                    syncToken=next_sync_token,
                )
            )
            if not response.zones:
                return

            more_coming = False
            for zone in response.zones:
                yield zone
                next_sync_token = zone.syncToken
                more_coming = more_coming or bool(zone.moreComing)

    def lists(self) -> Iterable[RemindersList]:
        """Fetch reminders lists as a full snapshot."""
        for zone in self._iter_zone_change_pages(desired_record_types=["List"]):
            _assert_read_success(zone.records, "Fetch reminder lists")
            for rec in zone.records:
                if not isinstance(rec, CKRecord) or rec.recordType != "List":
                    continue
                yield self._mapper.record_to_list(rec)

    def reminders(self, list_id: Optional[str] = None) -> Iterable[Reminder]:
        """Fetch reminders as a full snapshot, optionally filtered by list."""
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
        """Return the latest usable sync token for the Reminders zone."""
        query_token = self._get_raw().current_sync_token(zone_id=_REMINDERS_ZONE_REQ)
        if query_token:
            return query_token

        sync_token: Optional[str] = None
        for zone in self._iter_zone_change_pages(
            desired_record_types=[],
            desired_keys=[],
            reverse=False,
        ):
            sync_token = zone.syncToken

        if sync_token:
            return sync_token

        raise RemindersApiError("Unable to obtain sync token for Reminders zone")

    def iter_changes(
        self, *, since: Optional[str] = None
    ) -> Iterable[ReminderChangeEvent]:
        """Iterate reminder changes since an optional sync token."""
        for zone in self._iter_zone_change_pages(
            desired_record_types=["Reminder"],
            sync_token=since,
            reverse=False,
        ):
            for rec in zone.records:
                if isinstance(rec, CKRecord):
                    if rec.recordType != "Reminder":
                        continue

                    reminder = self._mapper.record_to_reminder(rec)
                    evt_type = "deleted" if reminder.deleted else "updated"
                    yield ReminderChangeEvent(
                        type=evt_type,
                        reminder_id=reminder.id,
                        reminder=reminder,
                    )
                    continue

                if isinstance(rec, CKTombstoneRecord):
                    yield ReminderChangeEvent(
                        type="deleted",
                        reminder_id=rec.recordName,
                        reminder=None,
                    )
                    continue

                if isinstance(rec, CKErrorItem):
                    record_name = rec.recordName or "<unknown record>"
                    reason = rec.reason or "no reason provided"
                    raise RemindersApiError(
                        "Iterating reminder changes failed for "
                        f"{record_name}: {rec.serverErrorCode} ({reason})",
                        payload={
                            "recordName": rec.recordName,
                            "serverErrorCode": rec.serverErrorCode,
                            "reason": rec.reason,
                        },
                    )

    def get(self, reminder_id: str) -> Reminder:
        """Fetch a single reminder by ID."""
        record_name = _as_record_name(reminder_id, "Reminder")
        resp = self._get_raw().lookup(
            record_names=[record_name],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_read_success(resp.records, "Lookup reminder")

        target = None
        for rec in resp.records:
            if isinstance(rec, CKRecord) and rec.recordName == record_name:
                target = rec
                break

        if not target:
            raise LookupError(f"Reminder not found: {record_name}")

        return self._mapper.record_to_reminder(target)

    def _lookup_related_records(
        self,
        *,
        raw_ids: List[str],
        prefix: str,
        record_type: str,
        mapper: Callable[[CKRecord], Optional[TRelated]],
        operation_name: str,
    ) -> List[TRelated]:
        """Fetch and map linked child records while preserving lookup order."""
        if not raw_ids:
            return []

        resp = self._get_raw().lookup(
            record_names=[_as_record_name(uid, prefix) for uid in raw_ids],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_read_success(resp.records, operation_name)

        mapped_records: List[TRelated] = []
        for rec in resp.records:
            if not isinstance(rec, CKRecord) or rec.recordType != record_type:
                continue
            mapped = mapper(rec)
            if mapped is not None:
                mapped_records.append(mapped)
        return mapped_records

    @staticmethod
    def _scope_related_records(
        records: Dict[str, TRelated],
        *,
        relation_getter: Callable[[TRelated], Optional[str]],
        allowed_ids: set[str],
    ) -> Dict[str, TRelated]:
        """Filter a related-record map down to rows linked to allowed parent IDs."""
        return {
            record_id: record
            for record_id, record in records.items()
            if relation_getter(record) in allowed_ids
        }

    def _ingest_compound_record(
        self,
        rec: CKRecord,
        *,
        reminders_map: Dict[str, Reminder],
        alarms: Dict[str, Alarm],
        triggers: Dict[str, LocationTrigger],
        attachments: Dict[str, Attachment],
        hashtags: Dict[str, Hashtag],
        recurrence_rules: Dict[str, RecurrenceRule],
    ) -> None:
        """Route one compound reminderList record into its typed collection."""
        record_type = rec.recordType
        if record_type == "Reminder":
            reminder = self._mapper.record_to_reminder(rec)
            reminders_map[reminder.id] = reminder
            return

        if record_type == "Alarm":
            alarm = self._mapper.record_to_alarm(rec)
            alarms[alarm.id] = alarm
            return

        if record_type == "AlarmTrigger":
            trigger = self._mapper.record_to_alarm_trigger(rec)
            if trigger:
                triggers[trigger.id] = trigger
            return

        if record_type == "Attachment":
            attachment = self._mapper.record_to_attachment(rec)
            if attachment:
                attachments[attachment.id] = attachment
            return

        if record_type == "Hashtag":
            hashtag = self._mapper.record_to_hashtag(rec)
            hashtags[hashtag.id] = hashtag
            return

        if record_type == "RecurrenceRule":
            recurrence_rule = self._mapper.record_to_recurrence_rule(rec)
            recurrence_rules[recurrence_rule.id] = recurrence_rule

    def list_reminders(
        self,
        list_id: str,
        include_completed: bool = False,
        results_limit: int = 200,
    ) -> ListRemindersResult:
        """Fetch all records for a list using the compound ``reminderList`` query."""
        query = CKQueryObject(
            recordType="reminderList",
            filterBy=[
                CKQueryFilterBy(
                    comparator="EQUALS",
                    fieldName="List",
                    fieldValue=CKFVReference(
                        type="REFERENCE",
                        value=CKReference(recordName=list_id, action="VALIDATE"),
                    ),
                ),
                CKQueryFilterBy(
                    comparator="EQUALS",
                    fieldName="includeCompleted",
                    fieldValue=CKFVInt64(
                        type="INT64",
                        value=1 if include_completed else 0,
                    ),
                ),
                CKQueryFilterBy(
                    comparator="EQUALS",
                    fieldName="LookupValidatingReference",
                    fieldValue=CKFVInt64(type="INT64", value=1),
                ),
            ],
        )

        reminders_map: Dict[str, Reminder] = {}
        alarms: Dict[str, Alarm] = {}
        triggers: Dict[str, LocationTrigger] = {}
        attachments: Dict[str, Attachment] = {}
        hashtags: Dict[str, Hashtag] = {}
        recurrence_rules: Dict[str, RecurrenceRule] = {}

        continuation: Optional[str] = None
        while True:
            resp = self._get_raw().query(
                query=query,
                zone_id=_REMINDERS_ZONE_REQ,
                results_limit=results_limit,
                continuation=continuation,
            )
            _assert_read_success(resp.records, "List reminders query")

            for rec in resp.records:
                if not isinstance(rec, CKRecord):
                    continue
                self._ingest_compound_record(
                    rec,
                    reminders_map=reminders_map,
                    alarms=alarms,
                    triggers=triggers,
                    attachments=attachments,
                    hashtags=hashtags,
                    recurrence_rules=recurrence_rules,
                )

            continuation = resp.continuationMarker
            if not continuation:
                break

        scoped_reminders = [
            reminder
            for reminder in reminders_map.values()
            if reminder.list_id == list_id
        ]
        scoped_reminder_ids = {reminder.id for reminder in scoped_reminders}

        scoped_alarms = self._scope_related_records(
            alarms,
            relation_getter=lambda alarm: alarm.reminder_id,
            allowed_ids=scoped_reminder_ids,
        )
        scoped_alarm_ids = set(scoped_alarms.keys())

        scoped_triggers = self._scope_related_records(
            triggers,
            relation_getter=lambda trigger: trigger.alarm_id,
            allowed_ids=scoped_alarm_ids,
        )
        scoped_attachments = self._scope_related_records(
            attachments,
            relation_getter=lambda attachment: attachment.reminder_id,
            allowed_ids=scoped_reminder_ids,
        )
        scoped_hashtags = self._scope_related_records(
            hashtags,
            relation_getter=lambda hashtag: hashtag.reminder_id,
            allowed_ids=scoped_reminder_ids,
        )
        scoped_recurrence_rules = self._scope_related_records(
            recurrence_rules,
            relation_getter=lambda recurrence_rule: recurrence_rule.reminder_id,
            allowed_ids=scoped_reminder_ids,
        )

        return ListRemindersResult(
            reminders=scoped_reminders,
            alarms=scoped_alarms,
            triggers=scoped_triggers,
            attachments=scoped_attachments,
            hashtags=scoped_hashtags,
            recurrence_rules=scoped_recurrence_rules,
        )

    def alarms_for(self, reminder: Reminder) -> List[AlarmWithTrigger]:
        """Fetch alarms + triggers for a reminder via lookup."""
        if not reminder.alarm_ids:
            return []

        resp = self._get_raw().lookup(
            record_names=[_as_record_name(uid, "Alarm") for uid in reminder.alarm_ids],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_read_success(resp.records, "Lookup alarms")

        alarms = []
        for rec in resp.records:
            if isinstance(rec, CKRecord) and rec.recordType == "Alarm":
                alarm = self._mapper.record_to_alarm(rec)
                alarms.append(alarm)

        trigger_ids = [
            _as_record_name(alarm.trigger_id, "AlarmTrigger")
            for alarm in alarms
            if alarm.trigger_id
        ]
        trigger_map = {}
        if trigger_ids:
            trigger_response = self._get_raw().lookup(
                record_names=trigger_ids,
                zone_id=_REMINDERS_ZONE_REQ,
            )
            _assert_read_success(trigger_response.records, "Lookup alarm triggers")
            for rec in trigger_response.records:
                if isinstance(rec, CKRecord) and rec.recordType == "AlarmTrigger":
                    trigger = self._mapper.record_to_alarm_trigger(rec)
                    if trigger:
                        trigger_map[_as_record_name(trigger.id, "AlarmTrigger")] = (
                            trigger
                        )
        return [
            AlarmWithTrigger(
                alarm=alarm,
                trigger=(
                    trigger_map.get(_as_record_name(alarm.trigger_id, "AlarmTrigger"))
                    if alarm.trigger_id
                    else None
                ),
            )
            for alarm in alarms
        ]

    def tags_for(self, reminder: Reminder) -> List[Hashtag]:
        """Fetch hashtags for a reminder via lookup."""
        return self._lookup_related_records(
            raw_ids=reminder.hashtag_ids,
            prefix="Hashtag",
            record_type="Hashtag",
            mapper=self._mapper.record_to_hashtag,
            operation_name="Lookup hashtags",
        )

    def attachments_for(self, reminder: Reminder) -> List[Attachment]:
        """Fetch attachments for a reminder via lookup."""
        return self._lookup_related_records(
            raw_ids=reminder.attachment_ids,
            prefix="Attachment",
            record_type="Attachment",
            mapper=self._mapper.record_to_attachment,
            operation_name="Lookup attachments",
        )

    def recurrence_rules_for(self, reminder: Reminder) -> List[RecurrenceRule]:
        """Fetch recurrence rules for a reminder via lookup."""
        return self._lookup_related_records(
            raw_ids=reminder.recurrence_rule_ids,
            prefix="RecurrenceRule",
            record_type="RecurrenceRule",
            mapper=self._mapper.record_to_recurrence_rule,
            operation_name="Lookup recurrence rules",
        )
