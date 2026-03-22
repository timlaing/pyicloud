"""Write-side orchestration for the Reminders service."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from pyicloud.common.cloudkit import (
    CKModifyOperation,
    CKModifyResponse,
    CKRecord,
    CKWriteParent,
    CKWriteRecord,
)

from ._constants import _REMINDERS_ZONE_REQ
from ._mappers import Attachment, RemindersRecordMapper
from ._protocol import (
    _as_raw_id,
    _as_record_name,
    _encode_crdt_document,
    _generate_resolution_token_map,
)
from ._support import (
    _assert_modify_success,
    _assert_read_success,
    _refresh_record_change_tag,
    _response_record_change_tag,
)
from .models import (
    Alarm,
    Hashtag,
    ImageAttachment,
    LocationTrigger,
    Proximity,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    URLAttachment,
)


class RemindersWriteAPI:
    """Encapsulates mutation behavior for the Reminders service."""

    def __init__(
        self,
        get_raw: Callable[[], Any],
        mapper: RemindersRecordMapper,
        logger: logging.Logger,
    ) -> None:
        self._get_raw = get_raw
        self._mapper = mapper
        self._logger = logger

    @staticmethod
    def _reminder_record_name(reminder_id: str) -> str:
        """Normalize reminder IDs so writes accept shorthand and canonical forms."""
        return _as_record_name(reminder_id, "Reminder")

    @staticmethod
    def _validated_location_trigger(
        *,
        trigger_id: str,
        alarm_id: str,
        title: str,
        address: str,
        latitude: float,
        longitude: float,
        radius: float,
        proximity: Proximity,
        location_uid: str,
    ) -> LocationTrigger:
        """Validate geofence data before sending a remote write."""
        return LocationTrigger(
            id=trigger_id,
            alarm_id=alarm_id,
            title=title,
            address=address,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            proximity=proximity,
            location_uid=location_uid,
        )

    @staticmethod
    def _validated_image_attachment(
        attachment: ImageAttachment,
        *,
        uti: Optional[str] = None,
        filename: Optional[str] = None,
        file_size: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> ImageAttachment:
        """Validate image metadata updates before sending a remote write."""
        return ImageAttachment(
            id=attachment.id,
            reminder_id=attachment.reminder_id,
            file_asset_url=attachment.file_asset_url,
            filename=attachment.filename if filename is None else filename,
            file_size=attachment.file_size if file_size is None else int(file_size),
            width=attachment.width if width is None else int(width),
            height=attachment.height if height is None else int(height),
            uti=attachment.uti if uti is None else uti,
            record_change_tag=attachment.record_change_tag,
        )

    @classmethod
    def _validated_recurrence_rule(
        cls,
        *,
        recurrence_id: str,
        reminder_id: str,
        frequency: RecurrenceFrequency,
        interval: int,
        occurrence_count: int,
        first_day_of_week: int,
        record_change_tag: Optional[str] = None,
    ) -> RecurrenceRule:
        """Validate recurrence values before mutating remote state."""
        return RecurrenceRule(
            id=_as_record_name(recurrence_id, "RecurrenceRule"),
            reminder_id=cls._reminder_record_name(reminder_id),
            frequency=frequency,
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
            record_change_tag=record_change_tag,
        )

    @staticmethod
    def _completion_datetime(
        *,
        completed: bool,
        completed_date: Optional[datetime],
        now_ms: int,
    ) -> Optional[datetime]:
        """Resolve the completion timestamp to persist for a reminder write."""
        if not completed:
            return None
        if completed_date is None:
            return datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        if completed_date.tzinfo is None:
            return completed_date.replace(tzinfo=timezone.utc)
        return completed_date

    @staticmethod
    def _write_record(
        *,
        record_name: str,
        record_type: str,
        fields: Dict[str, Any],
        record_change_tag: Optional[str] = None,
        parent_record_name: Optional[str] = None,
    ) -> CKWriteRecord:
        """Build a typed CloudKit modify-record payload."""
        parent = None
        if parent_record_name:
            parent = CKWriteParent(recordName=parent_record_name)

        return CKWriteRecord(
            recordName=record_name,
            recordType=record_type,
            recordChangeTag=record_change_tag,
            fields=fields,
            parent=parent,
        )

    def _build_linked_ids_update_op(
        self,
        *,
        reminder: Reminder,
        field_name: str,
        token_field_name: str,
        raw_ids: list[str],
    ) -> CKModifyOperation:
        """Build a Reminder update operation for an ID-list field."""
        now_ms = int(time.time() * 1000)
        token_map = _generate_resolution_token_map(
            [token_field_name, "lastModifiedDate"]
        )
        reminder_record_name = self._reminder_record_name(reminder.id)
        return CKModifyOperation(
            operationType="update",
            record=self._write_record(
                record_name=reminder_record_name,
                record_type="Reminder",
                record_change_tag=reminder.record_change_tag,
                fields={
                    field_name: {"type": "STRING_LIST", "value": raw_ids},
                    "ResolutionTokenMap": {"type": "STRING", "value": token_map},
                    "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
                },
            ),
        )

    def _submit_single_record_update(
        self,
        *,
        operation_name: str,
        record_name: str,
        record_type: str,
        record_change_tag: Optional[str],
        fields: Dict[str, Any],
        model_obj: Any,
    ) -> CKModifyResponse:
        """Run a one-record update and refresh the local object's change tag."""
        op = CKModifyOperation(
            operationType="update",
            record=self._write_record(
                record_name=record_name,
                record_type=record_type,
                record_change_tag=record_change_tag,
                fields=fields,
            ),
        )
        modify_response = self._get_raw().modify(
            operations=[op],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_modify_success(modify_response, operation_name)
        _refresh_record_change_tag(modify_response, model_obj, record_name)
        return modify_response

    def _lookup_created_reminder(self, record_name: str) -> Reminder:
        """Fetch a freshly-created reminder by record name."""
        resp = self._get_raw().lookup(
            record_names=[record_name],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_read_success(resp.records, "Lookup reminder")

        for rec in resp.records:
            if isinstance(rec, CKRecord) and rec.recordName == record_name:
                return self._mapper.record_to_reminder(rec)

        raise LookupError(f"Reminder not found: {record_name}")

    def _create_linked_child(
        self,
        *,
        reminder: Reminder,
        reminder_ids_attr: str,
        prefix: str,
        record_type: str,
        field_name: str,
        token_field_name: str,
        child_fields: Dict[str, Any],
        operation_name: str,
    ) -> tuple[str, CKModifyResponse]:
        """Create a linked child record and update the reminder ID list."""
        child_uuid = str(uuid.uuid4()).upper()
        child_record_name = f"{prefix}/{child_uuid}"
        reminder_record_name = self._reminder_record_name(reminder.id)
        linked_ids = [
            _as_raw_id(x, prefix) for x in (getattr(reminder, reminder_ids_attr) or [])
        ]
        linked_ids.append(child_uuid)

        reminder_op = self._build_linked_ids_update_op(
            reminder=reminder,
            field_name=field_name,
            token_field_name=token_field_name,
            raw_ids=linked_ids,
        )
        child_op = CKModifyOperation(
            operationType="create",
            record=self._write_record(
                record_name=child_record_name,
                record_type=record_type,
                fields=child_fields,
                parent_record_name=reminder_record_name,
            ),
        )

        modify_response = self._get_raw().modify(
            operations=[reminder_op, child_op],
            zone_id=_REMINDERS_ZONE_REQ,
            atomic=True,
        )
        _assert_modify_success(modify_response, operation_name)

        setattr(reminder, reminder_ids_attr, linked_ids)
        _refresh_record_change_tag(modify_response, reminder, reminder_record_name)
        return child_record_name, modify_response

    def _delete_linked_child(
        self,
        *,
        reminder: Reminder,
        reminder_ids_attr: str,
        child: Any,
        prefix: str,
        record_type: str,
        field_name: str,
        token_field_name: str,
        operation_name: str,
    ) -> None:
        """Soft-delete a linked child record and update the reminder ID list."""
        child_record_name = _as_record_name(getattr(child, "id"), prefix)
        child_uuid = _as_raw_id(child_record_name, prefix)
        reminder_record_name = self._reminder_record_name(reminder.id)
        child_reminder_id = getattr(child, "reminder_id", None)
        if child_reminder_id and (
            self._reminder_record_name(child_reminder_id) != reminder_record_name
        ):
            raise ValueError(
                f"{prefix} child {child_record_name} is linked to "
                f"{self._reminder_record_name(child_reminder_id)}, not "
                f"{reminder_record_name}"
            )

        linked_ids = [
            _as_raw_id(x, prefix)
            for x in (getattr(reminder, reminder_ids_attr) or [])
            if _as_raw_id(x, prefix) != child_uuid
        ]
        reminder_op = self._build_linked_ids_update_op(
            reminder=reminder,
            field_name=field_name,
            token_field_name=token_field_name,
            raw_ids=linked_ids,
        )

        child_fields: Dict[str, Any] = {
            "Deleted": {"type": "INT64", "value": 1},
        }
        if child_reminder_id:
            child_fields["Reminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": self._reminder_record_name(child_reminder_id),
                    "action": "VALIDATE",
                },
            }

        child_op = CKModifyOperation(
            operationType="update",
            record=self._write_record(
                record_name=child_record_name,
                record_type=record_type,
                record_change_tag=getattr(child, "record_change_tag", None),
                fields=child_fields,
            ),
        )

        modify_response = self._get_raw().modify(
            operations=[reminder_op, child_op],
            zone_id=_REMINDERS_ZONE_REQ,
            atomic=True,
        )
        _assert_modify_success(modify_response, operation_name)

        setattr(reminder, reminder_ids_attr, linked_ids)
        _refresh_record_change_tag(modify_response, reminder, reminder_record_name)
        _refresh_record_change_tag(modify_response, child, child_record_name)

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
        """Create a new Reminder inside a List, optionally as a child reminder."""
        reminder_uuid = str(uuid.uuid4()).upper()
        record_name = f"Reminder/{reminder_uuid}"

        title_doc = _encode_crdt_document(title)
        notes_doc = _encode_crdt_document(desc)

        fields_mod = [
            "allDay",
            "titleDocument",
            "notesDocument",
            "parentReminder",
            "priority",
            "icsDisplayOrder",
            "creationDate",
            "list",
            "flagged",
            "completed",
            "completionDate",
            "lastModifiedDate",
            "recurrenceRuleIDs",
            "dueDate",
            "timeZone",
        ]
        token_map = _generate_resolution_token_map(fields_mod)
        now_ms = int(time.time() * 1000)

        record_fields: dict[str, Any] = {
            "AllDay": {"type": "INT64", "value": 1 if all_day else 0},
            "Completed": {"type": "INT64", "value": 1 if completed else 0},
            "CompletionDate": {
                "type": "TIMESTAMP",
                "value": now_ms if completed else None,
            },
            "CreationDate": {"type": "TIMESTAMP", "value": now_ms},
            "Deleted": {"type": "INT64", "value": 0},
            "Flagged": {"type": "INT64", "value": 1 if flagged else 0},
            "Imported": {"type": "INT64", "value": 0},
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
            "List": {
                "type": "REFERENCE",
                "value": {"recordName": list_id, "action": "VALIDATE"},
            },
            "NotesDocument": {"type": "STRING", "value": notes_doc},
            "Priority": {"type": "INT64", "value": priority},
            "ResolutionTokenMap": {"type": "STRING", "value": token_map},
            "TitleDocument": {"type": "STRING", "value": title_doc},
        }

        if due_date is not None:
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)
            record_fields["DueDate"] = {
                "type": "TIMESTAMP",
                "value": int(due_date.timestamp() * 1000),
            }

        if time_zone:
            record_fields["TimeZone"] = {"type": "STRING", "value": time_zone}

        if parent_reminder_id:
            record_fields["ParentReminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": _as_record_name(parent_reminder_id, "Reminder"),
                    "action": "VALIDATE",
                },
            }

        op = CKModifyOperation(
            operationType="create",
            record=self._write_record(
                record_name=record_name,
                record_type="Reminder",
                fields=record_fields,
                parent_record_name=list_id,
            ),
        )

        modify_response = self._get_raw().modify(
            operations=[op],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        _assert_modify_success(modify_response, "Create reminder")

        return self._lookup_created_reminder(record_name)

    def update(self, reminder: Reminder) -> None:
        """Update an existing reminder."""
        reminder_record_name = self._reminder_record_name(reminder.id)
        title_doc = _encode_crdt_document(reminder.title)
        notes_doc = _encode_crdt_document(reminder.desc or "")
        now_ms = int(time.time() * 1000)

        fields_mod = [
            "titleDocument",
            "notesDocument",
            "completed",
            "completionDate",
            "priority",
            "flagged",
            "allDay",
            "lastModifiedDate",
        ]
        completion_date = self._completion_datetime(
            completed=reminder.completed,
            completed_date=reminder.completed_date,
            now_ms=now_ms,
        )
        completion_date_ms = (
            int(completion_date.timestamp() * 1000)
            if completion_date is not None
            else None
        )

        fields: dict[str, Any] = {
            "TitleDocument": {"type": "STRING", "value": title_doc},
            "NotesDocument": {"type": "STRING", "value": notes_doc},
            "Completed": {"type": "INT64", "value": 1 if reminder.completed else 0},
            "CompletionDate": {
                "type": "TIMESTAMP",
                "value": completion_date_ms,
            },
            "Priority": {"type": "INT64", "value": reminder.priority},
            "Flagged": {"type": "INT64", "value": 1 if reminder.flagged else 0},
            "AllDay": {"type": "INT64", "value": 1 if reminder.all_day else 0},
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
        }
        if reminder.due_date is not None:
            due_date = reminder.due_date
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)
                reminder.due_date = due_date
            fields["DueDate"] = {
                "type": "TIMESTAMP",
                "value": int(due_date.timestamp() * 1000),
            }
        else:
            fields["DueDate"] = {"type": "TIMESTAMP", "value": None}
        fields_mod.append("dueDate")
        if reminder.time_zone:
            fields["TimeZone"] = {"type": "STRING", "value": reminder.time_zone}
        else:
            fields["TimeZone"] = {"type": "STRING", "value": None}
        fields_mod.append("timeZone")
        if reminder.parent_reminder_id:
            fields["ParentReminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": _as_record_name(
                        reminder.parent_reminder_id,
                        "Reminder",
                    ),
                    "action": "VALIDATE",
                },
            }
        else:
            fields["ParentReminder"] = {"type": "REFERENCE", "value": None}
        fields_mod.append("parentReminder")
        fields["ResolutionTokenMap"] = {
            "type": "STRING",
            "value": _generate_resolution_token_map(fields_mod),
        }

        self._submit_single_record_update(
            operation_name="Update reminder",
            record_name=reminder_record_name,
            record_type="Reminder",
            record_change_tag=reminder.record_change_tag,
            fields=fields,
            model_obj=reminder,
        )
        reminder.completed_date = completion_date
        reminder.modified = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)

    def delete(self, reminder: Reminder) -> None:
        """Delete a reminder using soft-update (Deleted: 1)."""
        reminder_record_name = self._reminder_record_name(reminder.id)
        fields_mod = ["deleted", "lastModifiedDate"]
        token_map = _generate_resolution_token_map(fields_mod)
        now_ms = int(time.time() * 1000)

        fields: dict[str, Any] = {
            "Deleted": {"type": "INT64", "value": 1},
            "ResolutionTokenMap": {"type": "STRING", "value": token_map},
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
        }

        self._submit_single_record_update(
            operation_name="Delete reminder",
            record_name=reminder_record_name,
            record_type="Reminder",
            record_change_tag=reminder.record_change_tag,
            fields=fields,
            model_obj=reminder,
        )
        reminder.deleted = True
        reminder.modified = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)

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
        """Attach a location-based alarm trigger to an existing Reminder."""
        alarm_uuid = str(uuid.uuid4()).upper()
        trigger_uuid = str(uuid.uuid4()).upper()
        location_uid = str(uuid.uuid4()).upper()
        alarm_record_name = f"Alarm/{alarm_uuid}"
        trigger_record_name = f"AlarmTrigger/{trigger_uuid}"
        reminder_record_name = self._reminder_record_name(reminder.id)
        now_ms = int(time.time() * 1000)

        apple_epoch_secs = time.time() - 978307200.0
        due_date_nonce = 100_000_000_000 + apple_epoch_secs
        trigger = self._validated_location_trigger(
            trigger_id=trigger_record_name,
            alarm_id=alarm_record_name,
            title=title,
            address=address,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            proximity=proximity,
            location_uid=location_uid,
        )

        existing_alarm_ids = [
            _as_raw_id(x, "Alarm") for x in list(reminder.alarm_ids or [])
        ]
        existing_alarm_ids.append(alarm_uuid)
        token_map = _generate_resolution_token_map(["alarmIDs", "lastModifiedDate"])
        reminder_op = CKModifyOperation(
            operationType="update",
            record=self._write_record(
                record_name=reminder_record_name,
                record_type="Reminder",
                record_change_tag=reminder.record_change_tag,
                fields={
                    "AlarmIDs": {"type": "STRING_LIST", "value": existing_alarm_ids},
                    "ResolutionTokenMap": {"type": "STRING", "value": token_map},
                    "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
                },
            ),
        )

        alarm_op = CKModifyOperation(
            operationType="create",
            record=self._write_record(
                record_name=alarm_record_name,
                record_type="Alarm",
                fields={
                    "AlarmUID": {"value": alarm_uuid, "type": "STRING"},
                    "Deleted": {"value": 0, "type": "INT64"},
                    "Imported": {"value": 0, "type": "INT64"},
                    "Reminder": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": reminder_record_name,
                            "action": "VALIDATE",
                        },
                    },
                    "TriggerID": {"value": trigger_uuid, "type": "STRING"},
                    "DueDateResolutionTokenAsNonce": {
                        "value": due_date_nonce,
                        "type": "DOUBLE",
                    },
                },
                parent_record_name=reminder_record_name,
            ),
        )

        trigger_op = CKModifyOperation(
            operationType="create",
            record=self._write_record(
                record_name=trigger_record_name,
                record_type="AlarmTrigger",
                fields={
                    "Address": {
                        "value": address,
                        "isEncrypted": True,
                        "type": "STRING",
                    },
                    "Alarm": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": alarm_record_name,
                            "action": "VALIDATE",
                        },
                    },
                    "Deleted": {"value": 0, "type": "INT64"},
                    "Latitude": {
                        "value": trigger.latitude,
                        "isEncrypted": True,
                        "type": "DOUBLE",
                    },
                    "LocationUID": {"value": location_uid, "type": "STRING"},
                    "Longitude": {
                        "value": trigger.longitude,
                        "isEncrypted": True,
                        "type": "DOUBLE",
                    },
                    "Proximity": {"value": int(trigger.proximity), "type": "INT64"},
                    "Radius": {"value": trigger.radius, "type": "DOUBLE"},
                    "ReferenceFrameString": {
                        "value": "1",
                        "isEncrypted": True,
                        "type": "STRING",
                    },
                    "Title": {
                        "value": trigger.title,
                        "isEncrypted": True,
                        "type": "STRING",
                    },
                    "Type": {"value": "Location", "type": "STRING"},
                },
                parent_record_name=alarm_record_name,
            ),
        )

        modify_response = self._get_raw().modify(
            operations=[reminder_op, alarm_op, trigger_op],
            zone_id=_REMINDERS_ZONE_REQ,
            atomic=True,
        )
        _assert_modify_success(modify_response, "Add location trigger")

        reminder.alarm_ids = existing_alarm_ids
        _refresh_record_change_tag(modify_response, reminder, reminder_record_name)

        alarm = Alarm(
            id=alarm_record_name,
            alarm_uid=alarm_uuid,
            reminder_id=reminder_record_name,
            trigger_id=trigger_uuid,
            record_change_tag=_response_record_change_tag(
                modify_response,
                alarm_record_name,
            ),
        )
        trigger.record_change_tag = _response_record_change_tag(
            modify_response,
            trigger_record_name,
        )
        return alarm, trigger

    def create_hashtag(self, reminder: Reminder, name: str) -> Hashtag:
        """Create a hashtag linked to a reminder and update Reminder.HashtagIDs."""
        now_ms = int(time.time() * 1000)
        reminder_record_name = self._reminder_record_name(reminder.id)
        hashtag_record_name, modify_response = self._create_linked_child(
            reminder=reminder,
            reminder_ids_attr="hashtag_ids",
            prefix="Hashtag",
            record_type="Hashtag",
            field_name="HashtagIDs",
            token_field_name="hashtagIDs",
            child_fields={
                "Name": {"type": "STRING", "value": name},
                "Deleted": {"type": "INT64", "value": 0},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {
                        "recordName": reminder_record_name,
                        "action": "VALIDATE",
                    },
                },
                "CreationDate": {"type": "TIMESTAMP", "value": now_ms},
            },
            operation_name="Create hashtag",
        )
        return Hashtag(
            id=hashtag_record_name,
            name=name,
            reminder_id=reminder_record_name,
            record_change_tag=_response_record_change_tag(
                modify_response,
                hashtag_record_name,
            ),
        )

    def update_hashtag(self, hashtag: Hashtag, name: str) -> None:
        """Update an existing hashtag name."""
        fields: dict[str, Any] = {
            "Name": {"type": "STRING", "value": name},
        }
        if hashtag.reminder_id:
            fields["Reminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": self._reminder_record_name(hashtag.reminder_id),
                    "action": "VALIDATE",
                },
            }

        hashtag_record_name = _as_record_name(hashtag.id, "Hashtag")
        self._submit_single_record_update(
            operation_name="Update hashtag",
            record_name=hashtag_record_name,
            record_type="Hashtag",
            record_change_tag=hashtag.record_change_tag,
            fields=fields,
            model_obj=hashtag,
        )
        hashtag.name = name

    def delete_hashtag(self, reminder: Reminder, hashtag: Hashtag) -> None:
        """Soft-delete a hashtag and remove it from Reminder.HashtagIDs."""
        self._delete_linked_child(
            reminder=reminder,
            reminder_ids_attr="hashtag_ids",
            child=hashtag,
            prefix="Hashtag",
            record_type="Hashtag",
            field_name="HashtagIDs",
            token_field_name="hashtagIDs",
            operation_name="Delete hashtag",
        )

    def create_url_attachment(
        self,
        reminder: Reminder,
        url: str,
        uti: str = "public.url",
    ) -> URLAttachment:
        """Create a URL attachment and link it from Reminder.AttachmentIDs."""
        reminder_record_name = self._reminder_record_name(reminder.id)
        attachment_record_name, modify_response = self._create_linked_child(
            reminder=reminder,
            reminder_ids_attr="attachment_ids",
            prefix="Attachment",
            record_type="Attachment",
            field_name="AttachmentIDs",
            token_field_name="attachmentIDs",
            child_fields={
                "Type": {"type": "STRING", "value": "URL"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {
                        "recordName": reminder_record_name,
                        "action": "VALIDATE",
                    },
                },
                "URL": {
                    "type": "STRING",
                    "value": url,
                    "isEncrypted": True,
                },
                "UTI": {"type": "STRING", "value": uti},
                "Imported": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
            operation_name="Create attachment",
        )
        return URLAttachment(
            id=attachment_record_name,
            reminder_id=reminder_record_name,
            url=url,
            uti=uti,
            record_change_tag=_response_record_change_tag(
                modify_response,
                attachment_record_name,
            ),
        )

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
        """Update an attachment record (URL fields or image metadata fields)."""
        fields: dict[str, Any] = {}

        if isinstance(attachment, URLAttachment):
            has_mutation = url is not None or uti is not None
            if not has_mutation:
                raise ValueError("No attachment fields provided for update")
            if url is not None:
                fields["URL"] = {
                    "type": "STRING",
                    "value": url,
                    "isEncrypted": True,
                }
            if uti is not None:
                fields["UTI"] = {"type": "STRING", "value": uti}
            fields["Type"] = {"type": "STRING", "value": "URL"}
        else:
            has_mutation = any(
                value is not None for value in (uti, filename, file_size, width, height)
            )
            if not has_mutation:
                raise ValueError("No attachment fields provided for update")
            validated_attachment = self._validated_image_attachment(
                attachment,
                uti=uti,
                filename=filename,
                file_size=file_size,
                width=width,
                height=height,
            )
            if uti is not None:
                fields["UTI"] = {"type": "STRING", "value": uti}
            if filename is not None:
                fields["FileName"] = {"type": "STRING", "value": filename}
            if file_size is not None:
                fields["FileSize"] = {"type": "INT64", "value": int(file_size)}
            if width is not None:
                fields["Width"] = {"type": "INT64", "value": int(width)}
            if height is not None:
                fields["Height"] = {"type": "INT64", "value": int(height)}
            fields["Type"] = {"type": "STRING", "value": "Image"}

        if attachment.reminder_id:
            fields["Reminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": self._reminder_record_name(attachment.reminder_id),
                    "action": "VALIDATE",
                },
            }

        attachment_record_name = _as_record_name(attachment.id, "Attachment")
        self._submit_single_record_update(
            operation_name="Update attachment",
            record_name=attachment_record_name,
            record_type="Attachment",
            record_change_tag=attachment.record_change_tag,
            fields=fields,
            model_obj=attachment,
        )

        if isinstance(attachment, URLAttachment):
            if url is not None:
                attachment.url = url
            if uti is not None:
                attachment.uti = uti
        else:
            attachment.uti = validated_attachment.uti
            attachment.filename = validated_attachment.filename
            attachment.file_size = validated_attachment.file_size
            attachment.width = validated_attachment.width
            attachment.height = validated_attachment.height

    def delete_attachment(self, reminder: Reminder, attachment: Attachment) -> None:
        """Soft-delete an attachment and unlink it from Reminder.AttachmentIDs."""
        self._delete_linked_child(
            reminder=reminder,
            reminder_ids_attr="attachment_ids",
            child=attachment,
            prefix="Attachment",
            record_type="Attachment",
            field_name="AttachmentIDs",
            token_field_name="attachmentIDs",
            operation_name="Delete attachment",
        )

    def create_recurrence_rule(
        self,
        reminder: Reminder,
        *,
        frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY,
        interval: int = 1,
        occurrence_count: int = 0,
        first_day_of_week: int = 0,
    ) -> RecurrenceRule:
        """Create a recurrence rule and link it from Reminder.RecurrenceRuleIDs."""
        validated_rule = self._validated_recurrence_rule(
            recurrence_id="RecurrenceRule/NEW",
            reminder_id=reminder.id,
            frequency=frequency,
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        )
        reminder_record_name = self._reminder_record_name(reminder.id)
        recurrence_record_name, modify_response = self._create_linked_child(
            reminder=reminder,
            reminder_ids_attr="recurrence_rule_ids",
            prefix="RecurrenceRule",
            record_type="RecurrenceRule",
            field_name="RecurrenceRuleIDs",
            token_field_name="recurrenceRuleIDs",
            child_fields={
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {
                        "recordName": reminder_record_name,
                        "action": "VALIDATE",
                    },
                },
                "Frequency": {
                    "type": "INT64",
                    "value": int(validated_rule.frequency),
                },
                "Interval": {"type": "INT64", "value": int(validated_rule.interval)},
                "OccurrenceCount": {
                    "type": "INT64",
                    "value": int(validated_rule.occurrence_count),
                },
                "FirstDayOfTheWeek": {
                    "type": "INT64",
                    "value": int(validated_rule.first_day_of_week),
                },
                "Imported": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
            operation_name="Create recurrence rule",
        )
        validated_rule.id = recurrence_record_name
        validated_rule.reminder_id = reminder_record_name
        validated_rule.record_change_tag = _response_record_change_tag(
            modify_response,
            recurrence_record_name,
        )
        return validated_rule

    def update_recurrence_rule(
        self,
        recurrence_rule: RecurrenceRule,
        *,
        frequency: Optional[RecurrenceFrequency] = None,
        interval: Optional[int] = None,
        occurrence_count: Optional[int] = None,
        first_day_of_week: Optional[int] = None,
    ) -> None:
        """Update an existing recurrence rule."""
        fields: dict[str, Any] = {}
        has_mutation = any(
            value is not None
            for value in (frequency, interval, occurrence_count, first_day_of_week)
        )
        if not has_mutation:
            raise ValueError("No recurrence rule fields provided for update")
        validated_rule = self._validated_recurrence_rule(
            recurrence_id=recurrence_rule.id,
            reminder_id=recurrence_rule.reminder_id,
            frequency=frequency or recurrence_rule.frequency,
            interval=recurrence_rule.interval if interval is None else interval,
            occurrence_count=(
                recurrence_rule.occurrence_count
                if occurrence_count is None
                else occurrence_count
            ),
            first_day_of_week=(
                recurrence_rule.first_day_of_week
                if first_day_of_week is None
                else first_day_of_week
            ),
            record_change_tag=recurrence_rule.record_change_tag,
        )

        if frequency is not None:
            fields["Frequency"] = {"type": "INT64", "value": int(frequency)}
        if interval is not None:
            fields["Interval"] = {"type": "INT64", "value": int(interval)}
        if occurrence_count is not None:
            fields["OccurrenceCount"] = {
                "type": "INT64",
                "value": int(occurrence_count),
            }
        if first_day_of_week is not None:
            fields["FirstDayOfTheWeek"] = {
                "type": "INT64",
                "value": int(first_day_of_week),
            }
        if recurrence_rule.reminder_id:
            fields["Reminder"] = {
                "type": "REFERENCE",
                "value": {
                    "recordName": self._reminder_record_name(
                        recurrence_rule.reminder_id
                    ),
                    "action": "VALIDATE",
                },
            }

        recurrence_record_name = _as_record_name(recurrence_rule.id, "RecurrenceRule")
        self._submit_single_record_update(
            operation_name="Update recurrence rule",
            record_name=recurrence_record_name,
            record_type="RecurrenceRule",
            record_change_tag=recurrence_rule.record_change_tag,
            fields=fields,
            model_obj=recurrence_rule,
        )

        recurrence_rule.frequency = validated_rule.frequency
        recurrence_rule.interval = validated_rule.interval
        recurrence_rule.occurrence_count = validated_rule.occurrence_count
        recurrence_rule.first_day_of_week = validated_rule.first_day_of_week

    def delete_recurrence_rule(
        self,
        reminder: Reminder,
        recurrence_rule: RecurrenceRule,
    ) -> None:
        """Soft-delete a recurrence rule and unlink it from the reminder."""
        self._delete_linked_child(
            reminder=reminder,
            reminder_ids_attr="recurrence_rule_ids",
            child=recurrence_rule,
            prefix="RecurrenceRule",
            record_type="RecurrenceRule",
            field_name="RecurrenceRuleIDs",
            token_field_name="recurrenceRuleIDs",
            operation_name="Delete recurrence rule",
        )
