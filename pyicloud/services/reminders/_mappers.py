"""CloudKit record mappers for the Reminders service."""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Callable, Optional

from pyicloud.common.cloudkit import CKRecord

from ._protocol import (
    _as_raw_id,
    _decode_attachment_url,
    _decode_cloudkit_text_value,
    _decode_crdt_document,
    _ref_name,
)
from .client import RemindersApiError
from .models import (
    Alarm,
    Hashtag,
    ImageAttachment,
    LocationTrigger,
    Proximity,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    RemindersList,
    URLAttachment,
)

Attachment = URLAttachment | ImageAttachment


class RemindersRecordMapper:
    """Translate CloudKit records into Reminders domain models."""

    def __init__(
        self,
        get_raw: Callable[[], Any],
        logger: logging.Logger,
    ) -> None:
        self._get_raw = get_raw
        self._logger = logger

    @staticmethod
    def _parse_reminder_ids_payload(payload_text: str, source: str) -> list[str]:
        """Decode a JSON array of reminder IDs into normalized raw IDs."""
        try:
            payload = _json.loads(payload_text)
        except (ValueError, TypeError) as exc:
            raise RemindersApiError(
                f"Failed to parse {source}",
                payload={"source": source, "payload": payload_text},
            ) from exc

        if not isinstance(payload, list):
            raise RemindersApiError(
                f"{source} must decode to a JSON array",
                payload={"source": source, "payload": payload},
            )

        reminder_ids: list[str] = []
        for item in payload:
            if not isinstance(item, str):
                raise RemindersApiError(
                    f"{source} must contain only string reminder IDs",
                    payload={"source": source, "payload": payload},
                )
            reminder_ids.append(_as_raw_id(item, "Reminder"))

        return reminder_ids

    def _reminder_ids_for_list_record(self, rec: CKRecord) -> list[str]:
        """Load reminder membership from inline or asset-backed list fields."""
        fields = rec.fields
        reminder_ids_raw = fields.get_value("ReminderIDs")
        if reminder_ids_raw is not None:
            if not isinstance(reminder_ids_raw, str):
                raise RemindersApiError(
                    "ReminderIDs field had unexpected type",
                    payload={
                        "recordName": rec.recordName,
                        "type": type(reminder_ids_raw).__name__,
                    },
                )
            return self._parse_reminder_ids_payload(
                reminder_ids_raw,
                f"List {rec.recordName} ReminderIDs",
            )

        asset = fields.get_value("ReminderIDsAsset")
        if asset is None:
            return []

        asset_bytes = getattr(asset, "downloadedData", None)
        if asset_bytes is None:
            download_url = getattr(asset, "downloadURL", None)
            if not download_url:
                raise RemindersApiError(
                    f"List {rec.recordName} ReminderIDsAsset is missing data and downloadURL",
                    payload={"recordName": rec.recordName},
                )
            asset_bytes = self._get_raw().download_asset_bytes(download_url)

        try:
            payload_text = asset_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RemindersApiError(
                f"List {rec.recordName} ReminderIDsAsset was not valid UTF-8",
                payload={"recordName": rec.recordName},
            ) from exc

        return self._parse_reminder_ids_payload(
            payload_text,
            f"List {rec.recordName} ReminderIDsAsset",
        )

    def _coerce_text(self, value: Any, *, field_name: str, record_name: str) -> str:
        """Normalize CloudKit text-like values into ``str`` for domain models."""
        try:
            return _decode_cloudkit_text_value(value)
        except UnicodeDecodeError:
            self._logger.warning(
                "Field %s on %s was undecodable bytes; replacing invalid UTF-8",
                field_name,
                record_name,
            )
            return value.decode("utf-8", errors="replace")

    def record_to_list(self, rec: CKRecord) -> RemindersList:
        fields = rec.fields
        title = fields.get_value("Name")
        color = fields.get_value("Color")
        reminder_ids = self._reminder_ids_for_list_record(rec)
        raw_count = fields.get_value("Count")
        count = int(raw_count) if raw_count is not None else 0
        if count == 0 and reminder_ids:
            # Live list records can carry complete reminder membership while the
            # Count field stays at zero. Prefer the membership size in that case.
            count = len(reminder_ids)

        return RemindersList(
            id=rec.recordName,
            title=str(title) if title else "Untitled",
            color=str(color) if color else None,
            count=count,
            badge_emblem=fields.get_value("BadgeEmblem"),
            sorting_style=fields.get_value("SortingStyle"),
            is_group=bool(fields.get_value("IsGroup") or 0),
            reminder_ids=reminder_ids,
            record_change_tag=rec.recordChangeTag,
        )

    def record_to_reminder(self, rec: CKRecord) -> Reminder:
        fields = rec.fields
        created = fields.get_value("CreationDate")
        if created is None and rec.created is not None:
            created = rec.created.timestamp

        modified = fields.get_value("LastModifiedDate")
        if modified is None and rec.modified is not None:
            modified = rec.modified.timestamp

        title_doc = fields.get_value("TitleDocument")
        title = "Untitled"
        if title_doc:
            try:
                title = self._coerce_text(
                    _decode_crdt_document(title_doc),
                    field_name="TitleDocument",
                    record_name=rec.recordName,
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._logger.warning(
                    "TitleDocument decode failed for %s: %s",
                    rec.recordName,
                    exc,
                )
                title = "Error Decoding Title"

        notes_doc = fields.get_value("NotesDocument")
        desc = ""
        if notes_doc:
            try:
                desc = self._coerce_text(
                    _decode_crdt_document(notes_doc),
                    field_name="NotesDocument",
                    record_name=rec.recordName,
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._logger.warning(
                    "NotesDocument decode failed for %s: %s",
                    rec.recordName,
                    exc,
                )

        return Reminder(
            id=rec.recordName,
            list_id=_ref_name(fields, "List"),
            title=title,
            desc=desc,
            due_date=fields.get_value("DueDate"),
            start_date=fields.get_value("StartDate"),
            completed=bool(fields.get_value("Completed") or 0),
            completed_date=fields.get_value("CompletionDate"),
            priority=int(fields.get_value("Priority") or 0),
            flagged=bool(fields.get_value("Flagged") or 0),
            all_day=bool(fields.get_value("AllDay") or 0),
            deleted=bool(fields.get_value("Deleted") or 0),
            time_zone=fields.get_value("TimeZone"),
            alarm_ids=[
                _as_raw_id(x, "Alarm") for x in (fields.get_value("AlarmIDs") or [])
            ],
            hashtag_ids=[
                _as_raw_id(x, "Hashtag") for x in (fields.get_value("HashtagIDs") or [])
            ],
            attachment_ids=[
                _as_raw_id(x, "Attachment")
                for x in (fields.get_value("AttachmentIDs") or [])
            ],
            recurrence_rule_ids=[
                _as_raw_id(x, "RecurrenceRule")
                for x in (fields.get_value("RecurrenceRuleIDs") or [])
            ],
            parent_reminder_id=_ref_name(fields, "ParentReminder") or None,
            created=created,
            modified=modified,
            record_change_tag=rec.recordChangeTag,
        )

    def record_to_alarm(self, rec: CKRecord) -> Alarm:
        fields = rec.fields
        return Alarm(
            id=rec.recordName,
            alarm_uid=fields.get_value("AlarmUID") or "",
            reminder_id=_ref_name(fields, "Reminder"),
            trigger_id=fields.get_value("TriggerID") or "",
            record_change_tag=rec.recordChangeTag,
        )

    def record_to_alarm_trigger(self, rec: CKRecord) -> Optional[LocationTrigger]:
        fields = rec.fields
        trigger_type = fields.get_value("Type") or ""
        alarm_id = _ref_name(fields, "Alarm")

        if trigger_type == "Location":
            prox_raw = int(fields.get_value("Proximity") or 1)
            try:
                proximity = Proximity(prox_raw)
            except ValueError:
                self._logger.warning(
                    "Unknown Proximity %d on %s",
                    prox_raw,
                    rec.recordName,
                )
                proximity = Proximity.ARRIVING

            return LocationTrigger(
                id=rec.recordName,
                alarm_id=alarm_id,
                title=fields.get_value("Title") or "",
                address=fields.get_value("Address") or "",
                latitude=float(fields.get_value("Latitude") or 0.0),
                longitude=float(fields.get_value("Longitude") or 0.0),
                radius=float(fields.get_value("Radius") or 0.0),
                proximity=proximity,
                location_uid=fields.get_value("LocationUID") or "",
                record_change_tag=rec.recordChangeTag,
            )

        self._logger.warning(
            "Unsupported AlarmTrigger type '%s' on %s",
            trigger_type,
            rec.recordName,
        )
        return None

    def record_to_attachment(self, rec: CKRecord) -> Optional[Attachment]:
        fields = rec.fields
        att_type = fields.get_value("Type") or ""
        reminder_id = _ref_name(fields, "Reminder")

        if att_type == "URL":
            return URLAttachment(
                id=rec.recordName,
                reminder_id=reminder_id,
                url=_decode_attachment_url(fields.get_value("URL") or ""),
                uti=fields.get_value("UTI") or "public.url",
                record_change_tag=rec.recordChangeTag,
            )

        if att_type == "Image":
            file_asset = fields.get_value("FileAsset")
            download_url = ""
            if file_asset and hasattr(file_asset, "downloadURL"):
                download_url = file_asset.downloadURL or ""

            return ImageAttachment(
                id=rec.recordName,
                reminder_id=reminder_id,
                file_asset_url=download_url,
                filename=fields.get_value("FileName") or "",
                file_size=int(fields.get_value("FileSize") or 0),
                width=int(fields.get_value("Width") or 0),
                height=int(fields.get_value("Height") or 0),
                uti=fields.get_value("UTI") or "public.jpeg",
                record_change_tag=rec.recordChangeTag,
            )

        self._logger.warning(
            "Unknown Attachment type '%s' on %s",
            att_type,
            rec.recordName,
        )
        return None

    def record_to_hashtag(self, rec: CKRecord) -> Hashtag:
        fields = rec.fields
        return Hashtag(
            id=rec.recordName,
            name=self._coerce_text(
                fields.get_value("Name"),
                field_name="Name",
                record_name=rec.recordName,
            ),
            reminder_id=_ref_name(fields, "Reminder"),
            created=fields.get_value("CreationDate"),
            record_change_tag=rec.recordChangeTag,
        )

    def record_to_recurrence_rule(self, rec: CKRecord) -> RecurrenceRule:
        fields = rec.fields
        freq_raw = fields.get_value("Frequency") or 1
        try:
            freq = RecurrenceFrequency(freq_raw)
        except ValueError:
            freq = RecurrenceFrequency.DAILY
        return RecurrenceRule(
            id=rec.recordName,
            reminder_id=_ref_name(fields, "Reminder"),
            frequency=freq,
            interval=fields.get_value("Interval") or 1,
            occurrence_count=fields.get_value("OccurrenceCount") or 0,
            first_day_of_week=fields.get_value("FirstDayOfTheWeek") or 0,
            record_change_tag=rec.recordChangeTag,
        )
