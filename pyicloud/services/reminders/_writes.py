"""Write-side orchestration for the Reminders service."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import time
from typing import Any, cast
import uuid

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKModifyOperation,
    CKModifyResponse,
    CKRecord,
    CKWriteFields,
    CKWriteParent,
    CKWriteRecord,
)

from ._constants import _REMINDERS_ZONE_REQ
from ._mappers import Attachment, RemindersRecordMapper
from ._protocol import (
    _APPLE_EPOCH_OFFSET,
    _URGENT_PRESENTATION_ALARMS_MIN_VERSION,
    CRDTDecodeError,
    CRDTStructureError,
    _as_raw_id,
    _as_record_name,
    _decode_crdt_document,
    _decode_due_date_delta_alerts,
    _edit_crdt_document,
    _encode_cloudkit_text_field,
    _encode_crdt_document,
    _encode_due_date_delta_alerts,
    _encode_urgent_presentation_alarms,
    _generate_resolution_token_map,
    _merge_resolution_token_map,
)
from ._support import (
    _assert_modify_success,
    _assert_read_success,
    _raise_record_errors,
    _refresh_record_change_tag,
    _response_record_change_tag,
)
from .models import (
    Alarm,
    EarlyReminder,
    Hashtag,
    ImageAttachment,
    LocationTrigger,
    Proximity,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    UrgentPresentationAlarms,
    URLAttachment,
)

_LOGGER = logging.getLogger(__name__)


def _zone_key(value: datetime | None) -> str | None:
    """Return the IANA key of ``value``'s tzinfo when it names a zone."""
    if value is None:
        return None
    key = getattr(value.tzinfo, "key", None)
    return key if isinstance(key, str) and key else None


def _default_time_zone(due_date: datetime | None, all_day: bool) -> str | None:
    """Return the IANA time zone name to store with a timed ``due_date``.

    Apple devices treat a due date without ``TimeZone`` as floating time and
    show its UTC clock time as local time. A ``zoneinfo.ZoneInfo`` carries its
    IANA name, and naive or UTC values are stored as UTC. A fixed non-UTC
    offset has no IANA name, so ``None`` is returned and the caller has to pass
    ``time_zone`` explicitly. All-day reminders stay floating, as on devices.
    """
    if due_date is None or all_day:
        return None
    key = _zone_key(due_date)
    if key:
        return key
    if due_date.tzinfo is None or due_date.utcoffset() == timedelta(0):
        return "UTC"
    return None


def _filled_time_zone(reminder: Reminder) -> str | None:
    """Return the time zone ``update()`` should fill in for ``reminder``.

    Only a ``ZoneInfo`` due date names the caller's zone unambiguously; a UTC
    value may be a floating time read back from the server, so it is left unset
    rather than guessed. An already-set zone and all-day reminders are left
    alone. Returns ``None`` when nothing is filled.
    """
    if reminder.time_zone or reminder.all_day or reminder.due_date is None:
        return None
    return _zone_key(reminder.due_date)


class RemindersWriteAPI:
    """Encapsulates mutation behavior for the Reminders service."""

    def __init__(
        self,
        get_raw: Callable[[], Any],
        mapper: RemindersRecordMapper,
        logger: logging.Logger,
        *,
        account_id: str | None = None,
    ) -> None:
        self._get_raw = get_raw
        self._mapper = mapper
        self._logger = logger
        # The signed-in account's directory id; Apple stores it in the
        # early-reminder payload, so it doubles as the default account id
        # when no app-written payload has revealed one yet.
        self._account_id = account_id

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
        uti: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        width: int | None = None,
        height: int | None = None,
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
        record_change_tag: str | None = None,
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
        completed_date: datetime | None,
        now_ms: int,
    ) -> datetime | None:
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
        fields: dict[str, Any],
        record_change_tag: str | None = None,
        parent_record_name: str | None = None,
    ) -> CKWriteRecord:
        """Build a typed CloudKit modify-record payload."""
        parent = None
        if parent_record_name:
            parent = CKWriteParent(recordName=parent_record_name)

        return CKWriteRecord(
            recordName=record_name,
            recordType=record_type,
            recordChangeTag=record_change_tag,
            fields=cast(CKWriteFields, fields),
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
        token_map = _generate_resolution_token_map([
            token_field_name,
            "lastModifiedDate",
        ])
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
        record_change_tag: str | None,
        fields: dict[str, Any],
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
        return cast(CKModifyResponse, modify_response)

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
        child_fields: dict[str, Any],
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
        child_record_name = _as_record_name(child.id, prefix)
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

        child_fields: dict[str, Any] = {
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
        due_date: datetime | None = None,
        priority: int = 0,
        flagged: bool = False,
        all_day: bool = False,
        time_zone: str | None = None,
        parent_reminder_id: str | None = None,
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

        time_zone = time_zone or _default_time_zone(due_date, all_day)
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

    def _lookup_current_fields(self, record_name: str) -> Any | None:
        """Return the server's current fields for ``record_name``, if found.

        A record the server reports as ``NOT_FOUND`` yields ``None``; any other
        lookup error is raised, so an update never replaces the server's CRDT
        state with a fresh document by mistake.
        """
        resp = self._get_raw().lookup(
            record_names=[record_name],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        records = getattr(resp, "records", None) or []
        _raise_record_errors(
            [
                rec
                for rec in records
                if isinstance(rec, CKErrorItem) and rec.serverErrorCode != "NOT_FOUND"
            ],
            "Lookup reminder before update",
        )
        for rec in records:
            if isinstance(rec, CKRecord) and rec.recordName == record_name:
                return rec.fields
        return None

    @staticmethod
    def _text_document_for_update(
        current_fields: Any | None,
        field_name: str,
        text: str,
    ) -> str | None:
        """Build the CRDT document to write for ``text``, or ``None`` if unchanged.

        When the server already holds a document, the change is written as a
        CRDT edit on top of it so Apple devices accept it. A fresh document is
        only built when there is nothing to edit: no server copy, or one whose
        text is already ``text``, or one that carries no CRDT structure at all.
        """
        existing = (
            current_fields.get_value(field_name) if current_fields is not None else None
        )
        if not existing:
            return _encode_crdt_document(text)
        if _decode_crdt_document(existing) == text:
            return None
        try:
            return _edit_crdt_document(existing, text)
        except CRDTStructureError as exc:
            # Substrings are absent, so there is no replica state to preserve
            # and a fresh document reaches devices as well as an edit would.
            _LOGGER.debug("%s has no CRDT structure to edit (%s)", field_name, exc)
            return _encode_crdt_document(text)

    def _changed_text_documents(
        self,
        reminder: Reminder,
        current_fields: Any | None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Return the text documents the caller changed, and their token names.

        A text the caller did not change since the reminder was read is left
        alone, keeping whatever the server holds — including a newer edit made
        on a device.
        """
        text_fields: dict[str, Any] = {}
        token_names: list[str] = []
        for field_name, token_name, text, read_text in (
            ("TitleDocument", "titleDocument", reminder.title, reminder.server_title),
            (
                "NotesDocument",
                "notesDocument",
                reminder.desc or "",
                reminder.server_desc,
            ),
        ):
            if read_text is not None and text == read_text:
                continue
            document = self._text_document_for_update(current_fields, field_name, text)
            if document is not None:
                text_fields[field_name] = {"type": "STRING", "value": document}
                token_names.append(token_name)
        return text_fields, token_names

    @staticmethod
    def _update_base_fields(
        reminder: Reminder,
        completion_date_ms: int | None,
        now_ms: int,
        fields_mod: list[str],
        time_zone: str | None,
    ) -> dict[str, Any]:
        """Return the non-text fields of an update, recording their token names.

        ``reminder.due_date`` is normalized to UTC in place, which is what the
        caller sees afterwards. ``time_zone`` is the zone to store — either the
        one already on ``reminder`` or one filled in by the caller — so the
        model is not mutated before the write is known to have succeeded.
        """
        due_date = reminder.due_date
        if due_date is not None and due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
            reminder.due_date = due_date
        parent_reminder: dict[str, Any] | None = None
        if reminder.parent_reminder_id:
            parent_reminder = {
                "recordName": _as_record_name(
                    reminder.parent_reminder_id,
                    "Reminder",
                ),
                "action": "VALIDATE",
            }
        fields_mod.extend(["dueDate", "timeZone", "parentReminder"])
        return {
            "Completed": {"type": "INT64", "value": 1 if reminder.completed else 0},
            "CompletionDate": {"type": "TIMESTAMP", "value": completion_date_ms},
            "Priority": {"type": "INT64", "value": reminder.priority},
            "Flagged": {"type": "INT64", "value": 1 if reminder.flagged else 0},
            "AllDay": {"type": "INT64", "value": 1 if reminder.all_day else 0},
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
            "DueDate": (
                {"type": "TIMESTAMP", "value": int(due_date.timestamp() * 1000)}
                if due_date is not None
                else {"type": "TIMESTAMP", "value": None}
            ),
            "TimeZone": (
                {"type": "STRING", "value": time_zone}
                if time_zone
                else {"type": "STRING", "value": None}
            ),
            "ParentReminder": (
                {"type": "REFERENCE", "value": parent_reminder}
                if parent_reminder is not None
                else {"type": "REFERENCE", "value": None}
            ),
        }

    def update(self, reminder: Reminder) -> None:
        """Update an existing reminder, refusing unreadable text documents."""
        if reminder.undecodable_text_fields:
            # A changed text document is written as an edit on top of the
            # server copy, which cannot be done safely for an unreadable value.
            raise CRDTDecodeError(
                "Reminder text could not be decoded; refusing to overwrite it"
            )
        reminder_record_name = self._reminder_record_name(reminder.id)
        current_fields = self._lookup_current_fields(reminder_record_name)
        now_ms = int(time.time() * 1000)

        fields_mod = [
            "completed",
            "completionDate",
            "priority",
            "flagged",
            "allDay",
            "lastModifiedDate",
        ]
        text_fields, text_tokens = self._changed_text_documents(
            reminder, current_fields
        )
        fields_mod.extend(text_tokens)
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

        # A ZoneInfo due date names the caller's zone unambiguously; a UTC
        # value may be a floating time read back from the server, so it is left
        # alone. The filled zone is written with the record and only stored on
        # the model once the write succeeds, so a failed update does not leave
        # the local object claiming a zone the server never received.
        filled_zone = _filled_time_zone(reminder)
        time_zone = reminder.time_zone or filled_zone

        fields: dict[str, Any] = {
            **text_fields,
            **self._update_base_fields(
                reminder, completion_date_ms, now_ms, fields_mod, time_zone
            ),
            "ResolutionTokenMap": {
                "type": "STRING",
                "value": _merge_resolution_token_map(
                    current_fields.get_value("ResolutionTokenMap")
                    if current_fields is not None
                    else None,
                    fields_mod,
                ),
            },
        }

        self._submit_single_record_update(
            operation_name="Update reminder",
            record_name=reminder_record_name,
            record_type="Reminder",
            record_change_tag=reminder.record_change_tag,
            fields=fields,
            model_obj=reminder,
        )
        if filled_zone is not None:
            reminder.time_zone = filled_zone
        reminder.completed_date = completion_date
        reminder.modified = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        reminder.server_title = reminder.title
        reminder.server_desc = reminder.desc or ""

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

    @staticmethod
    def _normalized_early_reminders(
        alerts: Sequence[EarlyReminder],
        existing: tuple[EarlyReminder, ...],
    ) -> list[EarlyReminder]:
        """Validate alerts and keep app-assigned metadata where it matches.

        The app stamps each alert with an identifier and a creation date; a
        replacement alert with the same offset reuses them, so a device sees
        an edited alert rather than a deleted-and-recreated one.
        """
        by_offset = {(alert.count, alert.unit): alert for alert in existing}
        normalized: list[EarlyReminder] = []
        for alert in alerts:
            previous = by_offset.get((alert.count, alert.unit))
            identifier = alert.identifier
            if not identifier and previous is not None:
                identifier = previous.identifier
            if not identifier:
                identifier = str(uuid.uuid4()).upper()
            creation_date = alert.creation_date
            if creation_date is None and previous is not None:
                creation_date = previous.creation_date
            normalized.append(
                EarlyReminder(
                    count=alert.count,
                    unit=alert.unit,
                    identifier=identifier,
                    creation_date=creation_date,
                )
            )
        return normalized

    def set_early_reminders(
        self,
        reminder: Reminder,
        alerts: Sequence[EarlyReminder],
    ) -> None:
        """Replace a reminder's early reminders (the "Early Reminder" setting).

        Apple stores these in the reminder's ``DueDateDeltaAlertsData`` field
        rather than as alarm records. The payload carries a per-account
        ``accountIdentifier``: the value the app itself wrote is reused when
        one is stored on the reminder, and otherwise the signed-in account's
        directory id is used. An empty ``alerts`` sequence removes every early
        reminder.
        """
        reminder_record_name = self._reminder_record_name(reminder.id)
        current_fields = self._lookup_current_fields(reminder_record_name)
        existing_value = (
            current_fields.get_value("DueDateDeltaAlertsData")
            if current_fields is not None
            else None
        )
        existing_payload, existing_alerts = _decode_due_date_delta_alerts(
            existing_value
        )
        account_id = (
            existing_payload.get("accountIdentifier")
            or reminder.early_reminder_account_id
            or self._account_id
        )
        if alerts and not account_id:
            raise ValueError(
                "No account identifier known for early reminders; the "
                "signed-in account's directory id is unavailable and the "
                "reminder carries no app-written early-reminder payload"
            )
        reminder_identifier = str(
            existing_payload.get("reminderIdentifier") or reminder_record_name
        )
        normalized = self._normalized_early_reminders(alerts, existing_alerts)

        now_ms = int(time.time() * 1000)
        fields: dict[str, Any] = {
            "DueDateDeltaAlertsData": _encode_due_date_delta_alerts(
                reminder_identifier=reminder_identifier,
                account_identifier=account_id or "",
                alerts=normalized,
            ),
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
            "ResolutionTokenMap": {
                "type": "STRING",
                "value": _merge_resolution_token_map(
                    current_fields.get_value("ResolutionTokenMap")
                    if current_fields is not None
                    else None,
                    ["dueDateDeltaAlertsData", "lastModifiedDate"],
                ),
            },
        }

        self._submit_single_record_update(
            operation_name="Set early reminders",
            record_name=reminder_record_name,
            record_type="Reminder",
            record_change_tag=reminder.record_change_tag,
            fields=fields,
            model_obj=reminder,
        )
        reminder.early_reminders = tuple(normalized)
        reminder.early_reminder_account_id = account_id
        reminder.modified = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)

    def set_urgent(
        self,
        reminder: Reminder,
        enabled: bool,
        *,
        person_id: str | None = None,
    ) -> None:
        """Enable or disable Urgent presentation alarms on a reminder.

        The state lives in an uploaded asset (``UrgentPresentationAlarmsAsData``)
        plus a checksum string. Apple only exposes the per-account
        ``person_id`` on reminders where the app itself set Urgent, so it must
        be passed in or already known from a read. The checksum Apple writes is
        not reproducible here (its algorithm is unknown); the SHA-512 of the
        asset bytes has been accepted by the server in its place, and Urgent
        alarms were observed to fire. Treat this as best-effort.
        """
        reminder_record_name = self._reminder_record_name(reminder.id)
        resolved_person_id = person_id or (
            reminder.urgent.person_id if reminder.urgent else None
        )
        if not resolved_person_id:
            raise ValueError(
                "No person id known for Urgent alarms; read a reminder the "
                "Reminders app marked Urgent, or pass person_id"
            )

        current_fields = self._lookup_current_fields(reminder_record_name)
        asset_bytes = _encode_urgent_presentation_alarms(
            enabled=enabled,
            person_id=resolved_person_id,
        )
        asset_token = self._get_raw().upload_asset(
            record_type="Reminder",
            field_name="UrgentPresentationAlarmsAsData",
            record_name=reminder_record_name,
            data=asset_bytes,
            zone_id=_REMINDERS_ZONE_REQ,
        )
        checksum = hashlib.sha512(asset_bytes).hexdigest()
        now_ms = int(time.time() * 1000)
        modified_on = time.time() - _APPLE_EPOCH_OFFSET

        fields: dict[str, Any] = {
            "UrgentPresentationAlarmsAsData": {
                "type": "ASSETID",
                "value": asset_token.model_dump(mode="json", exclude_none=True),
            },
            "UrgentPresentationAlarmsChecksum": {
                "type": "STRING",
                "value": checksum,
                "isEncrypted": True,
            },
            "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
            "ResolutionTokenMap": {
                "type": "STRING",
                "value": _merge_resolution_token_map(
                    current_fields.get_value("ResolutionTokenMap")
                    if current_fields is not None
                    else None,
                    [
                        "urgentPresentationAlarmsAsData",
                        "urgentPresentationAlarmsChecksum",
                        "lastModifiedDate",
                    ],
                ),
            },
        }

        self._submit_single_record_update(
            operation_name="Set urgent presentation alarms",
            record_name=reminder_record_name,
            record_type="Reminder",
            record_change_tag=reminder.record_change_tag,
            fields=fields,
            model_obj=reminder,
        )
        reminder.urgent = UrgentPresentationAlarms(
            enabled=enabled,
            person_id=resolved_person_id,
            modified_on=modified_on,
            minimum_supported_version=_URGENT_PRESENTATION_ALARMS_MIN_VERSION,
            checksum=checksum,
        )
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
            _as_raw_id(x, "Alarm") for x in (reminder.alarm_ids or [])
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
                "Name": _encode_cloudkit_text_field(name),
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
            "Name": _encode_cloudkit_text_field(name),
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

    def update_attachment(  # noqa: S3776
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
        frequency: RecurrenceFrequency | None = None,
        interval: int | None = None,
        occurrence_count: int | None = None,
        first_day_of_week: int | None = None,
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
            frequency=(recurrence_rule.frequency if frequency is None else frequency),
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
