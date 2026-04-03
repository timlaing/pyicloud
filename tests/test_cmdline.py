"""Tests for the Typer-based pyicloud CLI."""

from __future__ import annotations

import importlib
import json
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

import click
from typer.testing import CliRunner

from pyicloud.services.notes.models import Attachment as NoteAttachment
from pyicloud.services.notes.models import ChangeEvent as NoteChangeEvent
from pyicloud.services.notes.models import (
    Note,
    NoteFolder,
    NoteSummary,
)
from pyicloud.services.notes.service import NoteLockedError, NoteNotFound
from pyicloud.services.reminders.client import RemindersApiError, RemindersAuthError
from pyicloud.services.reminders.models import (
    Alarm,
    AlarmWithTrigger,
    Hashtag,
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

account_index_module = importlib.import_module("pyicloud.cli.account_index")
cli_module = importlib.import_module("pyicloud.cli.app")
context_module = importlib.import_module("pyicloud.cli.context")
output_module = importlib.import_module("pyicloud.cli.output")
app = cli_module.app

TEST_BASE = Path(tempfile.gettempdir()) / "python-test-results"
TEST_BASE.mkdir(parents=True, exist_ok=True)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="test_cmdline-", dir=TEST_BASE))


class FakeDevice:
    """Find My device fixture."""

    def __init__(self) -> None:
        self.id = "device-1"
        self.name = "Example iPhone"
        self.deviceDisplayName = "iPhone"
        self.deviceClass = "iPhone"
        self.deviceModel = "iPhone16,1"
        self.batteryLevel = 0.87
        self.batteryStatus = "Charging"
        self.location = {"latitude": 49.0, "longitude": 6.0}
        self.data = {
            "id": self.id,
            "name": self.name,
            "deviceDisplayName": self.deviceDisplayName,
            "deviceClass": self.deviceClass,
            "deviceModel": self.deviceModel,
            "batteryLevel": self.batteryLevel,
            "batteryStatus": self.batteryStatus,
            "location": self.location,
        }
        self.sound_subject: Optional[str] = None
        self.messages: list[dict[str, Any]] = []
        self.lost_mode: Optional[dict[str, str]] = None
        self.erase_message: Optional[str] = None

    def play_sound(self, subject: str = "Find My iPhone Alert") -> None:
        self.sound_subject = subject

    def display_message(self, subject: str, message: str, sounds: bool) -> None:
        self.messages.append({"subject": subject, "message": message, "sounds": sounds})

    def lost_device(self, number: str, text: str, newpasscode: str) -> None:
        self.lost_mode = {"number": number, "text": text, "newpasscode": newpasscode}

    def erase_device(self, message: str) -> None:
        self.erase_message = message


class FakeDriveResponse:
    """Download response fixture."""

    def iter_content(self, chunk_size: int = 8192):  # pragma: no cover - trivial
        yield b"hello"


class FakeDriveNode:
    """Drive node fixture."""

    def __init__(
        self,
        name: str,
        *,
        node_type: str = "folder",
        size: Optional[int] = None,
        modified: Optional[datetime] = None,
        children: Optional[list["FakeDriveNode"]] = None,
    ) -> None:
        self.name = name
        self.type = node_type
        self.size = size
        self.date_modified = modified
        self._children = children or []
        self.data = {"name": name, "type": node_type, "size": size}

    def get_children(self) -> list["FakeDriveNode"]:
        return list(self._children)

    def __getitem__(self, key: str) -> "FakeDriveNode":
        for child in self._children:
            if child.name == key:
                return child
        raise KeyError(key)

    def open(self, **kwargs) -> FakeDriveResponse:  # pragma: no cover - trivial
        return FakeDriveResponse()


class FakeAlbumContainer(list):
    """Photo album container fixture."""

    def find(self, name: Optional[str]):
        if name is None:
            return None
        for album in self:
            if album.name == name:
                return album
        return None


class FakePhoto:
    """Photo asset fixture."""

    def __init__(self, photo_id: str, filename: str) -> None:
        self.id = photo_id
        self.filename = filename
        self.item_type = "image"
        self.created = datetime(2026, 3, 1, tzinfo=timezone.utc)
        self.size = 1234

    def download(self, version: str = "original") -> bytes:
        return f"{self.id}:{version}".encode()


class FakePhotoAlbum:
    """Photo album fixture."""

    def __init__(self, name: str, photos: list[FakePhoto]) -> None:
        self.name = name
        self.fullname = f"/{name}"
        self._photos = photos

    @property
    def photos(self):
        return iter(self._photos)

    def __len__(self) -> int:
        return len(self._photos)

    def __getitem__(self, photo_id: str) -> FakePhoto:
        for photo in self._photos:
            if photo.id == photo_id:
                return photo
        raise KeyError(photo_id)


class FakeHideMyEmail:
    """Hide My Email fixture."""

    def __init__(self) -> None:
        self.aliases = [
            {
                "hme": "alpha@privaterelay.appleid.com",
                "label": "Shopping",
                "anonymousId": "alias-1",
            }
        ]

    def __iter__(self):
        return iter(self.aliases)

    def generate(self) -> str:
        return "generated@privaterelay.appleid.com"

    def reserve(
        self, email: str, label: str, note: str = "Generated"
    ) -> dict[str, Any]:
        return {"anonymousId": "alias-2", "hme": email, "label": label, "note": note}

    def update_metadata(
        self, anonymous_id: str, label: str, note: Optional[str]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"anonymousId": anonymous_id, "label": label}
        if note is not None:
            payload["note"] = note
        return payload

    def deactivate(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "active": False}

    def reactivate(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "active": True}

    def delete(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "deleted": True}


class FakeNotes:
    """Notes service fixture."""

    def __init__(self) -> None:
        attachment = NoteAttachment(
            id="Attachment/PDF",
            filename="agenda.pdf",
            uti="com.adobe.pdf",
            size=12,
            download_url="https://example.com/agenda.pdf",
            preview_url="https://example.com/agenda-preview.pdf",
            thumbnail_url="https://example.com/agenda-thumb.png",
        )
        self.recent_requests: list[int] = []
        self.iter_all_requests: list[str | None] = []
        self.folder_requests: list[tuple[str, int | None]] = []
        self.render_calls: list[dict[str, Any]] = []
        self.export_calls: list[dict[str, Any]] = []
        self.change_requests: list[str | None] = []
        self.folder_rows = [
            NoteFolder(
                id="Folder/NOTES",
                name="Notes",
                has_subfolders=False,
                count=1,
            ),
            NoteFolder(
                id="Folder/WORK",
                name="Work",
                has_subfolders=True,
                count=3,
            ),
        ]
        self.recent_rows = [
            NoteSummary(
                id="Note/DELETED",
                title="Deleted Note",
                snippet="Old note",
                modified_at=datetime(2026, 3, 5, tzinfo=timezone.utc),
                folder_id="Folder/DELETED",
                folder_name="Recently Deleted",
                is_deleted=True,
                is_locked=False,
            ),
            NoteSummary(
                id="Note/DAILY",
                title="Daily Plan",
                snippet="Ship CLI",
                modified_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
                folder_id="Folder/NOTES",
                folder_name="Notes",
                is_deleted=False,
                is_locked=False,
            ),
            NoteSummary(
                id="Note/MEETING",
                title="Meeting Notes",
                snippet="Discuss roadmap",
                modified_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
                folder_id="Folder/WORK",
                folder_name="Work",
                is_deleted=False,
                is_locked=False,
            ),
        ]
        self.all_rows = [
            self.recent_rows[2],
            NoteSummary(
                id="Note/FOLLOWUP",
                title="Meeting Follow-up",
                snippet="Send recap",
                modified_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
                folder_id="Folder/WORK",
                folder_name="Work",
                is_deleted=False,
                is_locked=False,
            ),
            self.recent_rows[1],
            # Duplicate entry to verify deduplication in search_notes_by_title.
            self.recent_rows[2],
        ]
        self.notes = {
            "Note/DAILY": Note(
                id="Note/DAILY",
                title="Daily Plan",
                snippet="Ship CLI",
                modified_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
                folder_id="Folder/NOTES",
                folder_name="Notes",
                is_deleted=False,
                is_locked=False,
                text="Ship CLI",
                html="<p>Ship CLI</p>",
                attachments=[attachment],
            ),
            "Note/MEETING": Note(
                id="Note/MEETING",
                title="Meeting Notes",
                snippet="Discuss roadmap",
                modified_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
                folder_id="Folder/WORK",
                folder_name="Work",
                is_deleted=False,
                is_locked=False,
                text="Discuss roadmap",
                html="<p>Discuss roadmap</p>",
                attachments=[attachment],
            ),
            "Note/FOLLOWUP": Note(
                id="Note/FOLLOWUP",
                title="Meeting Follow-up",
                snippet="Send recap",
                modified_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
                folder_id="Folder/WORK",
                folder_name="Work",
                is_deleted=False,
                is_locked=False,
                text="Send recap",
                html="<p>Send recap</p>",
                attachments=None,
            ),
        }
        self.change_rows = [
            NoteChangeEvent(type="updated", note=self.recent_rows[1]),
            NoteChangeEvent(type="deleted", note=self.recent_rows[0]),
        ]
        self.cursor = "notes-cursor-1"

    @staticmethod
    def _matches_id(note_id: str, query: str) -> bool:
        return note_id == query or note_id.split("/", 1)[-1] == query

    def recents(self, *, limit: int = 50):
        self.recent_requests.append(limit)
        return list(self.recent_rows[:limit])

    def folders(self):
        return list(self.folder_rows)

    def in_folder(self, folder_id: str, limit: int | None = None):
        self.folder_requests.append((folder_id, limit))
        rows = [row for row in self.all_rows if row.folder_id == folder_id]
        return list(rows[:limit] if limit is not None else rows)

    def iter_all(self, *, since: Optional[str] = None):
        self.iter_all_requests.append(since)
        return iter(self.all_rows)

    def get(self, note_id: str, *, with_attachments: bool = False):
        if self._matches_id("Note/LOCKED", note_id):
            raise NoteLockedError(f"Note is locked: {note_id}")
        for candidate_id, note in self.notes.items():
            if self._matches_id(candidate_id, note_id):
                attachments = note.attachments if with_attachments else None
                return note.model_copy(update={"attachments": attachments})
        raise NoteNotFound(f"Note not found: {note_id}")

    def render_note(self, note_id: str, **kwargs: Any) -> str:
        note = self.get(note_id, with_attachments=False)
        self.render_calls.append({"note_id": note.id, **kwargs})
        return note.html or f"<p>{note.id}</p>"

    def export_note(self, note_id: str, output_dir: str, **kwargs: Any) -> str:
        note = self.get(note_id, with_attachments=False)
        path = Path(output_dir) / f"{note.id.split('/', 1)[-1].lower()}.html"
        self.export_calls.append(
            {"note_id": note.id, "output_dir": output_dir, **kwargs}
        )
        return str(path)

    def iter_changes(self, *, since: Optional[str] = None):
        self.change_requests.append(since)
        return iter(self.change_rows)

    def sync_cursor(self) -> str:
        return self.cursor


class FakeReminders:
    """Reminders service fixture."""

    def __init__(self) -> None:
        self.list_rows = {
            "List/INBOX": RemindersList(
                id="List/INBOX",
                title="Inbox",
                color='{"daHexString":"#007AFF","ckSymbolicColorName":"blue"}',
                count=0,
            ),
            "List/WORK": RemindersList(
                id="List/WORK",
                title="Work",
                color='{"daHexString":"#34C759","ckSymbolicColorName":"green"}',
                count=0,
            ),
        }
        self.reminder_rows = {
            "Reminder/A": Reminder(
                id="Reminder/A",
                list_id="List/INBOX",
                title="Buy milk",
                desc="2 percent",
                completed=False,
                due_date=datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc),
                priority=1,
                flagged=True,
                all_day=False,
                time_zone="Europe/Luxembourg",
                alarm_ids=["Alarm/A"],
                hashtag_ids=["Hashtag/ERRANDS"],
                attachment_ids=["Attachment/LINK"],
                recurrence_rule_ids=["Recurrence/WEEKLY"],
                parent_reminder_id="Reminder/PARENT",
                created=datetime(2026, 3, 1, tzinfo=timezone.utc),
                modified=datetime(2026, 3, 4, tzinfo=timezone.utc),
            ),
            "Reminder/B": Reminder(
                id="Reminder/B",
                list_id="List/INBOX",
                title="Pay rent",
                desc="",
                completed=True,
                completed_date=datetime(2026, 3, 2, tzinfo=timezone.utc),
                priority=0,
                flagged=False,
                all_day=False,
                created=datetime(2026, 3, 1, tzinfo=timezone.utc),
                modified=datetime(2026, 3, 2, tzinfo=timezone.utc),
            ),
            "Reminder/C": Reminder(
                id="Reminder/C",
                list_id="List/WORK",
                title="Prepare deck",
                desc="Slides for review",
                completed=False,
                priority=5,
                flagged=False,
                all_day=False,
                created=datetime(2026, 3, 3, tzinfo=timezone.utc),
                modified=datetime(2026, 3, 4, tzinfo=timezone.utc),
            ),
        }
        self.alarm_rows = {
            "Alarm/A": Alarm(
                id="Alarm/A",
                alarm_uid="alarm-a",
                reminder_id="Reminder/A",
                trigger_id="Trigger/A",
            )
        }
        self.trigger_rows = {
            "Trigger/A": LocationTrigger(
                id="Trigger/A",
                alarm_id="Alarm/A",
                title="Office",
                address="1 Infinite Loop",
                latitude=37.3318,
                longitude=-122.0312,
                radius=150.0,
                proximity=Proximity.ARRIVING,
                location_uid="office",
            )
        }
        self.hashtag_rows = {
            "Hashtag/ERRANDS": Hashtag(
                id="Hashtag/ERRANDS",
                name="errands",
                reminder_id="Reminder/A",
                created=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
        }
        self.attachment_rows = {
            "Attachment/LINK": URLAttachment(
                id="Attachment/LINK",
                reminder_id="Reminder/A",
                url="https://example.com/checklist",
                uti="public.url",
            )
        }
        self.recurrence_rows = {
            "Recurrence/WEEKLY": RecurrenceRule(
                id="Recurrence/WEEKLY",
                reminder_id="Reminder/A",
                frequency=RecurrenceFrequency.WEEKLY,
                interval=1,
                occurrence_count=0,
                first_day_of_week=1,
            )
        }
        self.snapshot_requests: list[dict[str, Any]] = []
        self.change_requests: list[str | None] = []
        self.cursor = "reminders-cursor-1"

    @staticmethod
    def _matches_id(record_id: str, query: str) -> bool:
        return record_id == query or record_id.split("/", 1)[-1] == query

    def _find_reminder(self, reminder_id: str) -> Reminder:
        for candidate_id, reminder in self.reminder_rows.items():
            if self._matches_id(candidate_id, reminder_id):
                return reminder
        raise LookupError(f"Reminder not found: {reminder_id}")

    def lists(self):
        for row in self.list_rows.values():
            row.count = sum(
                1
                for reminder in self.reminder_rows.values()
                if reminder.list_id == row.id and not reminder.deleted
            )
        return list(self.list_rows.values())

    def reminders(self, list_id: Optional[str] = None):
        rows = [
            reminder
            for reminder in self.reminder_rows.values()
            if not reminder.deleted and (list_id is None or reminder.list_id == list_id)
        ]
        return list(rows)

    def list_reminders(
        self,
        list_id: str,
        include_completed: bool = False,
        results_limit: int = 200,
    ) -> ListRemindersResult:
        normalized = list_id if list_id.startswith("List/") else f"List/{list_id}"
        self.snapshot_requests.append(
            {
                "list_id": normalized,
                "include_completed": include_completed,
                "results_limit": results_limit,
            }
        )
        reminders = [
            reminder
            for reminder in self.reminder_rows.values()
            if reminder.list_id == normalized
            and not reminder.deleted
            and (include_completed or not reminder.completed)
        ][:results_limit]
        reminder_ids = {reminder.id for reminder in reminders}
        return ListRemindersResult(
            reminders=reminders,
            alarms={
                alarm_id: alarm
                for alarm_id, alarm in self.alarm_rows.items()
                if alarm.reminder_id in reminder_ids
            },
            triggers={
                trigger_id: trigger
                for trigger_id, trigger in self.trigger_rows.items()
                if any(
                    alarm.trigger_id == trigger_id
                    for alarm in self.alarm_rows.values()
                    if alarm.reminder_id in reminder_ids
                )
            },
            attachments={
                attachment_id: attachment
                for attachment_id, attachment in self.attachment_rows.items()
                if attachment.reminder_id in reminder_ids
            },
            hashtags={
                hashtag_id: hashtag
                for hashtag_id, hashtag in self.hashtag_rows.items()
                if hashtag.reminder_id in reminder_ids
            },
            recurrence_rules={
                rule_id: rule
                for rule_id, rule in self.recurrence_rows.items()
                if rule.reminder_id in reminder_ids
            },
        )

    def get(self, reminder_id: str) -> Reminder:
        return self._find_reminder(reminder_id)

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
        next_id = f"Reminder/CREATED-{len(self.reminder_rows) + 1}"
        reminder = Reminder(
            id=next_id,
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
            created=datetime(2026, 3, 30, tzinfo=timezone.utc),
            modified=datetime(2026, 3, 30, tzinfo=timezone.utc),
        )
        self.reminder_rows[reminder.id] = reminder
        return reminder

    def update(self, reminder: Reminder) -> None:
        self.reminder_rows[reminder.id] = reminder

    def delete(self, reminder: Reminder) -> None:
        reminder.deleted = True
        self.reminder_rows[reminder.id] = reminder

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
        index = len(self.alarm_rows) + 1
        alarm = Alarm(
            id=f"Alarm/{index}",
            alarm_uid=f"alarm-{index}",
            reminder_id=reminder.id,
            trigger_id=f"Trigger/{index}",
        )
        trigger = LocationTrigger(
            id=f"Trigger/{index}",
            alarm_id=alarm.id,
            title=title,
            address=address,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            proximity=proximity,
            location_uid=f"location-{index}",
        )
        self.alarm_rows[alarm.id] = alarm
        self.trigger_rows[trigger.id] = trigger
        reminder.alarm_ids.append(alarm.id)
        return alarm, trigger

    def create_hashtag(self, reminder: Reminder, name: str) -> Hashtag:
        hashtag = Hashtag(
            id=f"Hashtag/{name.upper()}",
            name=name,
            reminder_id=reminder.id,
            created=datetime(2026, 3, 30, tzinfo=timezone.utc),
        )
        self.hashtag_rows[hashtag.id] = hashtag
        reminder.hashtag_ids.append(hashtag.id)
        return hashtag

    def update_hashtag(self, hashtag: Hashtag, name: str) -> None:
        hashtag.name = name

    def delete_hashtag(self, reminder: Reminder, hashtag: Hashtag) -> None:
        reminder.hashtag_ids = [
            row_id for row_id in reminder.hashtag_ids if row_id != hashtag.id
        ]
        self.hashtag_rows.pop(hashtag.id, None)

    def create_url_attachment(
        self, reminder: Reminder, url: str, uti: str = "public.url"
    ) -> URLAttachment:
        attachment = URLAttachment(
            id=f"Attachment/{len(self.attachment_rows) + 1}",
            reminder_id=reminder.id,
            url=url,
            uti=uti,
        )
        self.attachment_rows[attachment.id] = attachment
        reminder.attachment_ids.append(attachment.id)
        return attachment

    def update_attachment(
        self,
        attachment: URLAttachment,
        *,
        url: Optional[str] = None,
        uti: Optional[str] = None,
        filename: Optional[str] = None,
        file_size: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        if url is not None:
            attachment.url = url
        if uti is not None:
            attachment.uti = uti

    def delete_attachment(self, reminder: Reminder, attachment: URLAttachment) -> None:
        reminder.attachment_ids = [
            row_id for row_id in reminder.attachment_ids if row_id != attachment.id
        ]
        self.attachment_rows.pop(attachment.id, None)

    def create_recurrence_rule(
        self,
        reminder: Reminder,
        *,
        frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY,
        interval: int = 1,
        occurrence_count: int = 0,
        first_day_of_week: int = 0,
    ) -> RecurrenceRule:
        rule = RecurrenceRule(
            id=f"Recurrence/{len(self.recurrence_rows) + 1}",
            reminder_id=reminder.id,
            frequency=frequency,
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        )
        self.recurrence_rows[rule.id] = rule
        reminder.recurrence_rule_ids.append(rule.id)
        return rule

    def update_recurrence_rule(
        self,
        recurrence_rule: RecurrenceRule,
        *,
        frequency: Optional[RecurrenceFrequency] = None,
        interval: Optional[int] = None,
        occurrence_count: Optional[int] = None,
        first_day_of_week: Optional[int] = None,
    ) -> None:
        if frequency is not None:
            recurrence_rule.frequency = frequency
        if interval is not None:
            recurrence_rule.interval = interval
        if occurrence_count is not None:
            recurrence_rule.occurrence_count = occurrence_count
        if first_day_of_week is not None:
            recurrence_rule.first_day_of_week = first_day_of_week

    def delete_recurrence_rule(
        self, reminder: Reminder, recurrence_rule: RecurrenceRule
    ) -> None:
        reminder.recurrence_rule_ids = [
            row_id
            for row_id in reminder.recurrence_rule_ids
            if row_id != recurrence_rule.id
        ]
        self.recurrence_rows.pop(recurrence_rule.id, None)

    def alarms_for(self, reminder: Reminder) -> list[AlarmWithTrigger]:
        rows = []
        for alarm_id in reminder.alarm_ids:
            alarm = self.alarm_rows[alarm_id]
            rows.append(
                AlarmWithTrigger(
                    alarm=alarm,
                    trigger=self.trigger_rows.get(alarm.trigger_id),
                )
            )
        return rows

    def tags_for(self, reminder: Reminder) -> list[Hashtag]:
        return [
            self.hashtag_rows[row_id]
            for row_id in reminder.hashtag_ids
            if row_id in self.hashtag_rows
        ]

    def attachments_for(self, reminder: Reminder) -> list[URLAttachment]:
        return [
            self.attachment_rows[row_id]
            for row_id in reminder.attachment_ids
            if row_id in self.attachment_rows
        ]

    def recurrence_rules_for(self, reminder: Reminder) -> list[RecurrenceRule]:
        return [
            self.recurrence_rows[row_id]
            for row_id in reminder.recurrence_rule_ids
            if row_id in self.recurrence_rows
        ]

    def iter_changes(self, *, since: Optional[str] = None):
        self.change_requests.append(since)
        return iter(
            [
                ReminderChangeEvent(
                    type="updated",
                    reminder_id="Reminder/A",
                    reminder=self.reminder_rows["Reminder/A"],
                ),
                ReminderChangeEvent(
                    type="deleted",
                    reminder_id="Reminder/Z",
                    reminder=None,
                ),
            ]
        )

    def sync_cursor(self) -> str:
        return self.cursor


class FakeAPI:
    """Authenticated API fixture."""

    def __init__(
        self,
        *,
        username: str = "user@example.com",
        session_dir: Optional[Path] = None,
        china_mainland: bool = False,
    ) -> None:
        self.requires_2fa = False
        self.requires_2sa = False
        self.is_trusted_session = True
        self.is_china_mainland = china_mainland
        self.fido2_devices: list[dict[str, Any]] = []
        self.trusted_devices: list[dict[str, Any]] = []
        self.two_factor_delivery_method = "unknown"
        self.two_factor_delivery_notice = None
        self.request_2fa_code = MagicMock(return_value=False)
        self.validate_2fa_code = MagicMock(return_value=True)
        self.confirm_security_key = MagicMock(return_value=True)
        self.send_verification_code = MagicMock(return_value=True)
        self.validate_verification_code = MagicMock(return_value=True)
        self.trust_session = MagicMock(return_value=True)
        self.account_name = username
        session_dir = session_dir or _unique_session_dir("fake-api")
        session_stub = "".join(
            character for character in username if character.isalnum()
        )
        self.session = SimpleNamespace(
            session_path=str(session_dir / f"{session_stub}.session"),
            cookiejar_path=str(session_dir / f"{session_stub}.cookiejar"),
        )
        self.get_auth_status = MagicMock(
            return_value={
                "authenticated": True,
                "trusted_session": True,
                "requires_2fa": False,
                "requires_2sa": False,
            }
        )
        self.data: dict[str, Any] = {}
        self.params: dict[str, Any] = {}
        self._webservices: Any = None
        self.logout = MagicMock(side_effect=self._logout)
        self.devices = [FakeDevice()]
        self.account = SimpleNamespace(
            devices=[
                {
                    "name": "Example iPhone",
                    "modelDisplayName": "iPhone 16 Pro",
                    "deviceClass": "iPhone",
                    "id": "acc-device-1",
                }
            ],
            family=[
                SimpleNamespace(
                    full_name="Jane Doe",
                    apple_id="jane@example.com",
                    dsid="123",
                    age_classification="adult",
                    has_parental_privileges=True,
                )
            ],
            storage=SimpleNamespace(
                usage=SimpleNamespace(
                    used_storage_in_bytes=100,
                    available_storage_in_bytes=900,
                    total_storage_in_bytes=1000,
                    used_storage_in_percent=10.0,
                ),
                usages_by_media={
                    "photos": SimpleNamespace(
                        label="Photos", color="FFFFFF", usage_in_bytes=80
                    )
                },
            ),
            summary_plan={"summary": {"limit": 50, "limitUnits": "GIB"}},
        )
        self.calendar = SimpleNamespace(
            get_calendars=lambda: [
                {
                    "guid": "cal-1",
                    "title": "Home",
                    "color": "#fff",
                    "shareType": "owner",
                }
            ],
            get_events=lambda **kwargs: [
                {
                    "guid": "event-1",
                    "pGuid": "cal-1",
                    "title": "Dentist",
                    "startDate": "2026-03-01T09:00:00Z",
                    "endDate": "2026-03-01T10:00:00Z",
                }
            ],
        )
        self.contacts = SimpleNamespace(
            all=[
                {
                    "firstName": "John",
                    "lastName": "Appleseed",
                    "phones": [{"field": "+1 555-0100"}],
                    "emails": [{"field": "john@example.com"}],
                }
            ],
            me=SimpleNamespace(
                first_name="John",
                last_name="Appleseed",
                photo={"url": "https://example.com/photo.jpg"},
                raw_data={"contacts": [{"firstName": "John"}]},
            ),
        )
        drive_file = FakeDriveNode(
            "report.txt",
            node_type="file",
            size=42,
            modified=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        self.drive = SimpleNamespace(
            root=FakeDriveNode("root", children=[drive_file]),
            trash=FakeDriveNode("trash"),
        )
        photo_album = FakePhotoAlbum("All Photos", [FakePhoto("photo-1", "img.jpg")])
        self.photos = SimpleNamespace(
            albums=FakeAlbumContainer([photo_album]),
            all=photo_album,
        )
        self.hidemyemail = FakeHideMyEmail()
        self.notes = FakeNotes()
        self.reminders = FakeReminders()

    def _logout(
        self,
        *,
        keep_trusted: bool = False,
        all_sessions: bool = False,
        clear_local_session: bool = True,
    ) -> dict[str, Any]:
        if clear_local_session:
            for path in (self.session.session_path, self.session.cookiejar_path):
                try:
                    Path(path).unlink()
                except FileNotFoundError:
                    pass
            self.get_auth_status.return_value = {
                "authenticated": False,
                "trusted_session": False,
                "requires_2fa": False,
                "requires_2sa": False,
            }
        return {
            "payload": {
                "trustBrowser": keep_trusted,
                "allBrowsers": all_sessions,
            },
            "remote_logout_confirmed": True,
            "local_session_cleared": clear_local_session,
        }


def _runner() -> CliRunner:
    return CliRunner()


def _plain_output(result: Any) -> str:
    return click.unstyle(result.output)


def _unique_session_dir(label: str = "session") -> Path:
    path = TEST_ROOT / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remember_local_account(
    session_dir: Path,
    username: str,
    *,
    has_session_file: bool = False,
    has_cookiejar_file: bool = False,
    china_mainland: bool | None = None,
    keyring_passwords: Optional[set[str]] = None,
) -> FakeAPI:
    fake_api = FakeAPI(
        username=username,
        session_dir=session_dir,
        china_mainland=bool(china_mainland),
    )
    if has_session_file:
        with open(fake_api.session.session_path, "w", encoding="utf-8"):
            pass
    if has_cookiejar_file:
        with open(fake_api.session.cookiejar_path, "w", encoding="utf-8"):
            pass
    account_index_module.remember_account(
        session_dir,
        username=username,
        session_path=fake_api.session.session_path,
        cookiejar_path=fake_api.session.cookiejar_path,
        china_mainland=china_mainland,
        keyring_has=lambda candidate: candidate in (keyring_passwords or set()),
    )
    return fake_api


def _invoke(
    fake_api: FakeAPI,
    *args: str,
    username: Optional[str] = "user@example.com",
    password: Optional[str] = None,
    interactive: Optional[bool] = None,
    session_dir: Optional[Path] = None,
    china_mainland: Optional[bool] = None,
    accept_terms: Optional[bool] = None,
    with_family: Optional[bool] = None,
    output_format: Optional[str] = None,
    log_level: Optional[str] = None,
    http_proxy: Optional[str] = None,
    https_proxy: Optional[str] = None,
    no_verify_ssl: bool = False,
    keyring_passwords: Optional[set[str]] = None,
):
    runner = _runner()
    session_dir = session_dir or _unique_session_dir("invoke")
    cli_args = list(args)
    command_path = tuple(args[:3])
    supports_auth_login = command_path[:2] == ("auth", "login")
    supports_devices = args[:1] == ("devices",)
    supports_keyring_delete = command_path[:3] == ("auth", "keyring", "delete")

    if username is not None:
        cli_args.extend(["--username", username])
    if session_dir is not None:
        cli_args.extend(["--session-dir", str(session_dir)])
    if supports_auth_login and password is None:
        password = "secret"
    if supports_auth_login and interactive is None:
        interactive = False
    if supports_auth_login and password is not None:
        cli_args.extend(["--password", password])
    if supports_auth_login and interactive is not None:
        cli_args.append("--interactive" if interactive else "--non-interactive")
    if supports_auth_login and china_mainland:
        cli_args.append("--china-mainland")
    if supports_auth_login and accept_terms:
        cli_args.append("--accept-terms")
    if not supports_keyring_delete and http_proxy is not None:
        cli_args.extend(["--http-proxy", http_proxy])
    if not supports_keyring_delete and https_proxy is not None:
        cli_args.extend(["--https-proxy", https_proxy])
    if not supports_keyring_delete and no_verify_ssl:
        cli_args.append("--no-verify-ssl")
    if supports_devices and with_family:
        cli_args.append("--with-family")
    if output_format is not None:
        cli_args.extend(["--format", output_format])
    if log_level is not None:
        cli_args.extend(["--log-level", log_level])
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate in (keyring_passwords or set()),
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            side_effect=lambda candidate: (
                "stored-secret" if candidate in (keyring_passwords or set()) else None
            ),
        ),
    ):
        return runner.invoke(app, cli_args)


def _invoke_with_cli_args(
    fake_api: FakeAPI,
    cli_args: list[str],
    *,
    keyring_passwords: Optional[set[str]] = None,
):
    runner = _runner()
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate in (keyring_passwords or set()),
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            side_effect=lambda candidate: (
                "stored-secret" if candidate in (keyring_passwords or set()) else None
            ),
        ),
    ):
        return runner.invoke(app, cli_args)


def test_root_help() -> None:
    """The root command should expose only help/completion utilities and subcommands."""

    result = _runner().invoke(app, ["--help"])
    text = _plain_output(result)
    assert result.exit_code == 0
    assert "--username" not in text
    assert "--password" not in text
    assert "--format" not in text
    assert "--session-dir" not in text
    assert "--http-proxy" not in text
    for command in (
        "account",
        "auth",
        "devices",
        "calendar",
        "contacts",
        "drive",
        "photos",
        "hidemyemail",
        "notes",
        "reminders",
    ):
        assert command in text


def test_root_version_prints_installed_package_version() -> None:
    """The root --version flag should print the installed pyicloud version."""

    with patch.object(cli_module, "_installed_version", return_value="9.9.9"):
        result = _runner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "9.9.9"


def test_group_help() -> None:
    """Each command group should expose help."""

    for command in (
        "account",
        "auth",
        "devices",
        "calendar",
        "contacts",
        "drive",
        "photos",
        "hidemyemail",
        "notes",
        "reminders",
    ):
        result = _runner().invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_bare_group_invocation_shows_help() -> None:
    """Bare group invocation should show help instead of a missing-command error."""

    for command in (
        "account",
        "auth",
        "devices",
        "calendar",
        "contacts",
        "drive",
        "photos",
        "hidemyemail",
        "notes",
        "reminders",
    ):
        result = _runner().invoke(app, [command])
        text = _plain_output(result)
        assert result.exit_code == 0
        assert "Usage:" in text
        assert "Missing command" not in text


def test_notes_and_reminders_leaf_help() -> None:
    """New service groups and reminder subgroups should expose leaf help."""

    for cli_args in (
        ["notes", "search", "--help"],
        ["reminders", "create", "--help"],
        ["reminders", "alarm", "--help"],
        ["reminders", "alarm", "add-location", "--help"],
        ["reminders", "hashtag", "--help"],
        ["reminders", "attachment", "--help"],
        ["reminders", "recurrence", "--help"],
    ):
        result = _runner().invoke(app, cli_args)
        assert result.exit_code == 0


def test_leaf_help_includes_execution_context_options() -> None:
    """Leaf command help should show the command-local options it supports."""

    result = _runner().invoke(app, ["account", "summary", "--help"])
    text = _plain_output(result)

    assert result.exit_code == 0
    assert "--username" in text
    assert "--format" in text
    assert "--session-dir" in text
    assert "--password" not in text
    assert "--with-family" not in text


def test_auth_login_help_scopes_authentication_options() -> None:
    """Auth login help should expose auth-only options on the leaf command."""

    result = _runner().invoke(app, ["auth", "login", "--help"])
    text = _plain_output(result)

    assert result.exit_code == 0
    assert "--username" in text
    assert "--password" in text
    assert "--china-mainland" in text
    assert "--interactive" in text
    assert "--accept-terms" in text
    assert "--with-family" not in text


def test_devices_help_scopes_device_options() -> None:
    """Devices help should expose device-specific options on device commands only."""

    result = _runner().invoke(app, ["devices", "list", "--help"])
    text = _plain_output(result)

    assert result.exit_code == 0
    assert "--with-family" in text


def test_account_summary_command() -> None:
    """Account summary should render the storage overview."""

    result = _invoke(FakeAPI(), "account", "summary")
    assert result.exit_code == 0
    assert "Account: user@example.com" in result.stdout
    assert "Storage: 10.0% used" in result.stdout


def test_format_option_outputs_json() -> None:
    """Leaf --format should support machine-readable JSON."""

    result = _invoke(FakeAPI(), "account", "summary", output_format="json")
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["account_name"] == "user@example.com"
    assert payload["devices_count"] == 1


def test_command_local_format_option_outputs_json() -> None:
    """Leaf commands should accept --format after the final subcommand."""

    session_dir = _unique_session_dir("leaf-format")
    result = _invoke_with_cli_args(
        FakeAPI(session_dir=session_dir),
        [
            "account",
            "summary",
            "--username",
            "user@example.com",
            "--session-dir",
            str(session_dir),
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["account_name"] == "user@example.com"


def test_old_root_execution_options_fail_cleanly() -> None:
    """Root execution options should no longer be accepted."""

    for cli_args in (
        ["--username", "user@example.com", "auth", "login"],
        ["--password", "secret", "auth", "login"],
        ["--session-dir", "/tmp/pyicloud", "account", "summary"],
        ["--format", "json", "account", "summary"],
        ["--delete-from-keyring"],
    ):
        result = _runner().invoke(app, cli_args)
        assert result.exit_code != 0
        assert "No such option" in _plain_output(result)


def test_auth_login_accepts_command_local_username() -> None:
    """Auth login should accept --username after the final subcommand."""

    session_dir = _unique_session_dir("leaf-username")
    fake_api = FakeAPI(username="leaf@example.com", session_dir=session_dir)

    def fake_service(*, apple_id: str, **_kwargs: Any) -> FakeAPI:
        assert apple_id == "leaf@example.com"
        return fake_api

    with (
        patch.object(context_module, "PyiCloudService", side_effect=fake_service),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "auth",
                "login",
                "--username",
                "leaf@example.com",
                "--password",
                "secret",
                "--session-dir",
                str(session_dir),
                "--non-interactive",
            ],
        )

    assert result.exit_code == 0
    assert "leaf@example.com" in result.stdout


def test_leaf_session_dir_option_is_used_for_service_commands() -> None:
    """Leaf --session-dir should be honored by service commands."""

    session_dir = _unique_session_dir("leaf-session-dir")
    fake_api = FakeAPI(session_dir=session_dir)

    def fake_service(*, apple_id: str, **kwargs: Any) -> FakeAPI:
        assert apple_id == "user@example.com"
        assert kwargs["cookie_directory"] == str(session_dir)
        return fake_api

    with (
        patch.object(context_module, "PyiCloudService", side_effect=fake_service),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "account",
                "summary",
                "--username",
                "user@example.com",
                "--session-dir",
                str(session_dir),
                "--format",
                "text",
            ],
        )

    assert result.exit_code == 0
    assert "Account: user@example.com" in result.stdout


def test_china_mainland_is_login_only() -> None:
    """China mainland selection should only be accepted on auth login."""

    status_result = _runner().invoke(app, ["auth", "status", "--china-mainland"])
    service_result = _runner().invoke(app, ["account", "summary", "--china-mainland"])
    status_text = _plain_output(status_result)
    service_text = _plain_output(service_result)

    assert status_result.exit_code != 0
    assert "No such option" in status_text
    assert "--china-mainland" in status_text
    assert service_result.exit_code != 0
    assert "No such option" in service_text
    assert "--china-mainland" in service_text


def test_auth_login_persists_china_mainland_metadata() -> None:
    """Auth login should persist China mainland metadata for later commands."""

    session_dir = _unique_session_dir("china-mainland")

    def fake_service(*, apple_id: str, china_mainland: Any, **_kwargs: Any) -> FakeAPI:
        if apple_id == "cn@example.com":
            assert china_mainland is True
            return FakeAPI(
                username="cn@example.com",
                session_dir=session_dir,
                china_mainland=True,
            )
        raise AssertionError("Unexpected account")

    with (
        patch.object(context_module, "PyiCloudService", side_effect=fake_service),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
    ):
        login_result = _runner().invoke(
            app,
            [
                "auth",
                "login",
                "--username",
                "cn@example.com",
                "--password",
                "secret",
                "--session-dir",
                str(session_dir),
                "--china-mainland",
                "--non-interactive",
            ],
        )

    assert login_result.exit_code == 0
    assert (
        account_index_module.load_accounts(session_dir)["cn@example.com"][
            "china_mainland"
        ]
        is True
    )


def test_persisted_china_mainland_metadata_is_used_for_service_commands() -> None:
    """Stored China mainland metadata should be reused by later service probes."""

    session_dir = _unique_session_dir("china-mainland-probe")
    _remember_local_account(
        session_dir,
        "cn@example.com",
        has_session_file=True,
        china_mainland=True,
    )

    def fake_service(*, apple_id: str, china_mainland: Any, **_kwargs: Any) -> FakeAPI:
        assert apple_id == "cn@example.com"
        assert china_mainland is True
        return FakeAPI(
            username="cn@example.com",
            session_dir=session_dir,
            china_mainland=True,
        )

    with (
        patch.object(context_module, "PyiCloudService", side_effect=fake_service),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "account",
                "summary",
                "--username",
                "cn@example.com",
                "--session-dir",
                str(session_dir),
            ],
        )

    assert result.exit_code == 0
    assert "Account: cn@example.com" in result.stdout


def test_default_log_level_is_warning() -> None:
    """Authenticated commands should default pyicloud logs to warning."""

    with patch.object(context_module.logging, "basicConfig") as basic_config:
        result = _invoke(FakeAPI(), "account", "summary")
    assert result.exit_code == 0
    basic_config.assert_called_once_with(level=context_module.logging.WARNING)


def test_no_local_accounts_require_username() -> None:
    """Authenticated service commands should require a logged-in session."""

    session_dir = _unique_session_dir("no-local-accounts")
    with patch.object(
        context_module, "configurable_ssl_verification", return_value=nullcontext()
    ):
        result = _runner().invoke(
            app, ["account", "summary", "--session-dir", str(session_dir)]
        )
    assert result.exit_code != 0
    assert (
        result.exception.args[0]
        == "You are not logged into any iCloud accounts. To log in, run: "
        "icloud auth login --username <apple-id>"
    )


def test_auth_keyring_delete() -> None:
    """The keyring delete subcommand should delete stored credentials."""

    session_dir = _unique_session_dir("delete-keyring")
    _remember_local_account(
        session_dir,
        "user@example.com",
        keyring_passwords={"user@example.com"},
    )
    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "delete_password_in_keyring"
        ) as delete_password,
    ):
        with patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: not delete_password.called,
        ):
            result = _runner().invoke(
                app,
                [
                    "auth",
                    "keyring",
                    "delete",
                    "--username",
                    "user@example.com",
                    "--session-dir",
                    str(session_dir),
                ],
            )
    assert result.exit_code == 0
    delete_password.assert_called_once_with("user@example.com")
    assert "Deleted stored password from keyring." in result.stdout
    assert account_index_module.load_accounts(session_dir) == {}


def test_auth_keyring_delete_requires_explicit_username() -> None:
    """Deleting stored credentials should require an explicit username."""

    result = _runner().invoke(
        app,
        ["auth", "keyring", "delete"],
    )

    assert result.exit_code != 0
    assert (
        result.exception.args[0]
        == "The --username option is required for auth keyring delete."
    )


def test_auth_status_probe_is_non_interactive() -> None:
    """Auth status should probe persisted sessions without prompting for login."""

    session_dir = _unique_session_dir("auth-status")
    fake_api = _remember_local_account(
        session_dir,
        "user@example.com",
        has_session_file=True,
    )
    fake_api.get_auth_status.return_value = {
        "authenticated": False,
        "trusted_session": False,
        "requires_2fa": False,
        "requires_2sa": False,
    }
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(context_module.utils, "get_password", side_effect=AssertionError),
        patch.object(context_module.typer, "prompt", side_effect=AssertionError),
    ):
        result = _runner().invoke(
            app,
            ["auth", "status", "--session-dir", str(session_dir)],
        )
    assert result.exit_code == 0
    assert "You are not logged into any iCloud accounts." in result.stdout


def test_auth_status_without_username_ignores_keyring_only_accounts() -> None:
    """Implicit auth status should report active sessions, not stored credentials."""

    session_dir = _unique_session_dir("status-keyring-only")
    _remember_local_account(
        session_dir,
        "user@example.com",
        keyring_passwords={"user@example.com"},
    )

    result = _invoke(
        FakeAPI(username="user@example.com", session_dir=session_dir),
        "auth",
        "status",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"user@example.com"},
    )

    assert result.exit_code == 0
    assert "You are not logged into any iCloud accounts." in result.stdout
    assert "user@example.com" not in result.stdout


def test_auth_status_explicit_username_marks_missing_storage_inline() -> None:
    """Text auth status should inline missing storage markers instead of extra boolean rows."""

    session_dir = _unique_session_dir("status-missing-storage")
    fake_api = _remember_local_account(
        session_dir,
        "user@example.com",
        keyring_passwords={"user@example.com"},
    )
    fake_api.get_auth_status.return_value = {
        "authenticated": False,
        "trusted_session": False,
        "requires_2fa": False,
        "requires_2sa": False,
    }

    result = _invoke(
        fake_api,
        "auth",
        "status",
        username="user@example.com",
        session_dir=session_dir,
        keyring_passwords={"user@example.com"},
    )

    assert result.exit_code == 0
    assert "Password in Keyring" in result.stdout
    assert "Stored Password" not in result.stdout
    assert "Session File" in result.stdout
    assert "Cookie Jar" in result.stdout
    assert result.stdout.count("(missing)") == 2
    assert "Session File Exists" not in result.stdout
    assert "Cookie Jar Exists" not in result.stdout


def test_auth_login_and_status_commands() -> None:
    """Auth status and login should expose stable text and JSON payloads."""

    fake_api = FakeAPI()
    status_result = _invoke(fake_api, "auth", "status", output_format="json")
    login_result = _invoke(fake_api, "auth", "login", output_format="json")

    status_payload = json.loads(status_result.stdout)
    login_payload = json.loads(login_result.stdout)

    assert status_result.exit_code == 0
    assert status_payload["authenticated"] is True
    assert status_payload["trusted_session"] is True
    assert status_payload["account_name"] == "user@example.com"
    assert login_result.exit_code == 0
    assert login_payload["authenticated"] is True
    assert login_payload["session_path"] == fake_api.session.session_path


def test_single_known_account_supports_implicit_local_context() -> None:
    """Implicit local context should work only while an active session exists."""

    session_dir = _unique_session_dir("implicit-context")
    _remember_local_account(
        session_dir,
        "solo@example.com",
        has_session_file=True,
        keyring_passwords={"solo@example.com"},
    )

    status_result = _invoke(
        FakeAPI(username="solo@example.com", session_dir=session_dir),
        "auth",
        "status",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    account_result = _invoke(
        FakeAPI(username="solo@example.com", session_dir=session_dir),
        "account",
        "summary",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    devices_result = _invoke(
        FakeAPI(username="solo@example.com", session_dir=session_dir),
        "devices",
        "list",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    logout_api = FakeAPI(username="solo@example.com", session_dir=session_dir)
    logout_result = _invoke(
        logout_api,
        "auth",
        "logout",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    post_logout_account_result = _invoke(
        logout_api,
        "account",
        "summary",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    post_logout_explicit_result = _invoke(
        logout_api,
        "account",
        "summary",
        username="solo@example.com",
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )
    login_result = _invoke(
        FakeAPI(username="solo@example.com", session_dir=session_dir),
        "auth",
        "login",
        username=None,
        session_dir=session_dir,
        keyring_passwords={"solo@example.com"},
    )

    assert status_result.exit_code == 0
    assert "solo@example.com" in status_result.stdout
    assert account_result.exit_code == 0
    assert devices_result.exit_code == 0
    assert logout_result.exit_code == 0
    assert post_logout_account_result.exit_code != 0
    assert (
        post_logout_account_result.exception.args[0]
        == "You are not logged into any iCloud accounts. To log in, run: "
        "icloud auth login --username <apple-id>"
    )
    assert post_logout_explicit_result.exit_code != 0
    assert (
        post_logout_explicit_result.exception.args[0]
        == "You are not logged into iCloud for solo@example.com. Run: "
        "icloud auth login --username solo@example.com"
    )
    assert login_result.exit_code == 0
    assert [
        entry["username"]
        for entry in account_index_module.prune_accounts(
            session_dir, lambda candidate: candidate == "solo@example.com"
        )
    ] == ["solo@example.com"]


def test_get_api_uses_keyring_password_for_session_backed_service_commands() -> None:
    """Service commands should preload the stored password for service reauth."""

    session_dir = _unique_session_dir("service-reauth-password")
    _remember_local_account(
        session_dir,
        "solo@example.com",
        has_session_file=True,
        keyring_passwords={"solo@example.com"},
    )

    probe_api = FakeAPI(username="solo@example.com", session_dir=session_dir)
    service_api = FakeAPI(username="solo@example.com", session_dir=session_dir)
    constructor_calls: list[dict[str, Any]] = []

    def build_api(**kwargs: Any) -> FakeAPI:
        constructor_calls.append(kwargs)
        return probe_api if len(constructor_calls) == 1 else service_api

    state = context_module.CLIState(
        username=None,
        password=None,
        china_mainland=None,
        interactive=False,
        accept_terms=False,
        with_family=False,
        session_dir=str(session_dir),
        http_proxy=None,
        https_proxy=None,
        no_verify_ssl=False,
        log_level=context_module.LogLevel.WARNING,
        output_format=output_module.OutputFormat.TEXT,
    )

    with (
        patch.object(context_module, "PyiCloudService", side_effect=build_api),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate == "solo@example.com",
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            return_value="stored-secret",
        ),
    ):
        api = state.get_api()

    assert api is service_api
    assert len(constructor_calls) == 2
    assert constructor_calls[0]["apple_id"] == "solo@example.com"
    assert constructor_calls[0]["password"] is None
    assert constructor_calls[0]["authenticate"] is False
    assert constructor_calls[1]["apple_id"] == "solo@example.com"
    assert constructor_calls[1]["password"] == "stored-secret"
    assert constructor_calls[1]["authenticate"] is False
    probe_api.get_auth_status.assert_called_once_with()
    service_api.get_auth_status.assert_called_once_with()


def test_get_api_hydrates_session_backed_service_commands_from_probe_state() -> None:
    """Service commands should reuse validated probe state for webservice access."""

    session_dir = _unique_session_dir("service-reauth-hydration")
    _remember_local_account(
        session_dir,
        "solo@example.com",
        has_session_file=True,
        keyring_passwords={"solo@example.com"},
    )

    probe_api = FakeAPI(username="solo@example.com", session_dir=session_dir)
    probe_api.data = {
        "dsInfo": {"dsid": "1234567890", "hsaVersion": 2},
        "hsaTrustedBrowser": True,
        "webservices": {"findme": {"url": "https://example.invalid/findme"}},
    }
    service_api = FakeAPI(username="solo@example.com", session_dir=session_dir)
    service_api.data = {}
    service_api.params = {}
    service_api._webservices = None
    service_api.get_auth_status.side_effect = AssertionError(
        "service API should be hydrated from the probe state"
    )
    constructor_calls: list[dict[str, Any]] = []

    def build_api(**kwargs: Any) -> FakeAPI:
        constructor_calls.append(kwargs)
        return probe_api if len(constructor_calls) == 1 else service_api

    state = context_module.CLIState(
        username=None,
        password=None,
        china_mainland=None,
        interactive=False,
        accept_terms=False,
        with_family=False,
        session_dir=str(session_dir),
        http_proxy=None,
        https_proxy=None,
        no_verify_ssl=False,
        log_level=context_module.LogLevel.WARNING,
        output_format=output_module.OutputFormat.TEXT,
    )

    with (
        patch.object(context_module, "PyiCloudService", side_effect=build_api),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate == "solo@example.com",
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            return_value="stored-secret",
        ),
    ):
        api = state.get_api()

    assert api is service_api
    assert len(constructor_calls) == 2
    assert service_api.data == probe_api.data
    assert service_api.params["dsid"] == "1234567890"
    assert service_api._webservices == probe_api.data["webservices"]
    probe_api.get_auth_status.assert_called_once_with()
    service_api.get_auth_status.assert_not_called()


def test_multiple_local_accounts_require_explicit_username_for_auth_login() -> None:
    """Auth login should list local accounts when bootstrap discovery is ambiguous."""

    session_dir = _unique_session_dir("multiple-contexts")
    _remember_local_account(
        session_dir,
        "alpha@example.com",
        keyring_passwords={"alpha@example.com", "beta@example.com"},
    )
    _remember_local_account(
        session_dir,
        "beta@example.com",
        keyring_passwords={"alpha@example.com", "beta@example.com"},
    )

    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: (
                candidate in {"alpha@example.com", "beta@example.com"}
            ),
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "auth",
                "login",
                "--session-dir",
                str(session_dir),
                "--non-interactive",
            ],
        )

    assert result.exit_code != 0
    assert "Multiple local accounts were found" in result.exception.args[0]
    assert "alpha@example.com" in result.exception.args[0]
    assert "beta@example.com" in result.exception.args[0]


def test_multiple_active_sessions_require_explicit_username() -> None:
    """Service commands should not guess when multiple active sessions exist."""

    session_dir = _unique_session_dir("multiple-active-sessions")
    alpha_api = _remember_local_account(
        session_dir,
        "alpha@example.com",
        has_session_file=True,
    )
    beta_api = _remember_local_account(
        session_dir,
        "beta@example.com",
        has_session_file=True,
    )
    apis = {
        "alpha@example.com": alpha_api,
        "beta@example.com": beta_api,
    }

    def fake_service(*, apple_id: str, **_kwargs: Any) -> FakeAPI:
        return apis[apple_id]

    with (
        patch.object(context_module, "PyiCloudService", side_effect=fake_service),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "account",
                "summary",
                "--session-dir",
                str(session_dir),
            ],
        )

    assert result.exit_code != 0
    assert "Multiple logged-in iCloud accounts were found" in result.exception.args[0]
    assert "alpha@example.com" in result.exception.args[0]
    assert "beta@example.com" in result.exception.args[0]


def test_explicit_username_overrides_ambiguous_local_context() -> None:
    """Explicit usernames should continue to work when multiple local accounts exist."""

    session_dir = _unique_session_dir("explicit-override")
    _remember_local_account(
        session_dir,
        "alpha@example.com",
        keyring_passwords={"alpha@example.com", "beta@example.com"},
    )
    _remember_local_account(
        session_dir,
        "beta@example.com",
        keyring_passwords={"alpha@example.com", "beta@example.com"},
    )

    result = _invoke(
        FakeAPI(username="beta@example.com", session_dir=session_dir),
        "account",
        "summary",
        username="beta@example.com",
        session_dir=session_dir,
        keyring_passwords={"alpha@example.com", "beta@example.com"},
    )

    assert result.exit_code == 0
    assert "beta@example.com" in result.stdout


def test_authenticated_commands_update_account_index() -> None:
    """Successful authenticated commands should index the resolved account."""

    session_dir = _unique_session_dir("index-update")
    fake_api = FakeAPI(username="indexed@example.com", session_dir=session_dir)

    result = _invoke(
        fake_api,
        "account",
        "summary",
        username="indexed@example.com",
        session_dir=session_dir,
    )

    indexed_accounts = account_index_module.load_accounts(session_dir)

    assert result.exit_code == 0
    assert "indexed@example.com" in indexed_accounts
    assert indexed_accounts["indexed@example.com"]["session_path"] == (
        fake_api.session.session_path
    )


def test_account_index_prunes_stale_entries_but_keeps_keyring_backed_accounts() -> None:
    """Local account discovery should prune stale entries and retain keyring-backed ones."""

    session_dir = _unique_session_dir("index-prune")
    stale_api = _remember_local_account(
        session_dir,
        "stale@example.com",
        has_session_file=True,
    )
    Path(stale_api.session.session_path).unlink()
    kept_api = _remember_local_account(
        session_dir,
        "kept@example.com",
        keyring_passwords={"kept@example.com"},
    )

    discovered = account_index_module.prune_accounts(
        session_dir,
        lambda candidate: candidate == "kept@example.com",
    )

    assert [entry["username"] for entry in discovered] == ["kept@example.com"]
    assert list(account_index_module.load_accounts(session_dir)) == ["kept@example.com"]
    assert kept_api.session.session_path.endswith("keptexamplecom.session")


def test_account_index_save_is_atomic() -> None:
    """Account index writes should use an atomic replace into accounts.json."""

    session_dir = _unique_session_dir("index-atomic")
    accounts = {
        "user@example.com": {
            "username": "user@example.com",
            "last_used_at": "2026-03-18T00:00:00+00:00",
            "session_path": str(session_dir / "userexamplecom.session"),
            "cookiejar_path": str(session_dir / "userexamplecom.cookiejar"),
        }
    }

    with patch.object(
        account_index_module.os,
        "replace",
        wraps=account_index_module.os.replace,
    ) as replace:
        account_index_module._save_accounts(session_dir, accounts)

    replace.assert_called_once()
    assert replace.call_args.args[1] == account_index_module.account_index_path(
        session_dir
    )
    assert account_index_module.load_accounts(session_dir) == accounts


def test_auth_login_non_interactive_requires_credentials() -> None:
    """Auth login should fail cleanly when non-interactive mode lacks credentials."""

    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "auth",
                "login",
                "--username",
                "user@example.com",
                "--non-interactive",
            ],
        )
    assert result.exit_code != 0
    assert "No password supplied and no stored password was found." in str(
        result.exception
    )


def test_auth_login_explicit_password_does_not_delete_stored_keyring_secret() -> None:
    """Explicit bad passwords should not delete a previously stored keyring password."""

    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module,
            "PyiCloudService",
            side_effect=context_module.PyiCloudFailedLoginException("bad password"),
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=True
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            return_value="stored-secret",
        ),
        patch.object(
            context_module.utils, "delete_password_in_keyring"
        ) as delete_password,
    ):
        result = _runner().invoke(
            app,
            [
                "auth",
                "login",
                "--username",
                "user@example.com",
                "--password",
                "wrong-secret",
                "--non-interactive",
            ],
        )

    assert result.exit_code != 0
    assert str(result.exception) == "Bad username or password for user@example.com"
    delete_password.assert_not_called()


def test_auth_logout_variants_and_remote_failure() -> None:
    """Auth logout should map semantic flags to Apple's payload and keep keyring intact."""

    def invoke_logout(*args: str, failing_api: Optional[FakeAPI] = None):
        session_dir = _unique_session_dir("auth-logout")
        _remember_local_account(
            session_dir,
            "user@example.com",
            has_session_file=True,
            keyring_passwords={"user@example.com"},
        )
        return _invoke(
            failing_api or FakeAPI(session_dir=session_dir),
            "auth",
            "logout",
            *args,
            username=None,
            session_dir=session_dir,
            output_format="json",
            keyring_passwords={"user@example.com"},
        )

    default_result = invoke_logout()
    keep_trusted_result = invoke_logout("--keep-trusted")
    all_sessions_result = invoke_logout("--all-sessions")
    combined_result = invoke_logout("--keep-trusted", "--all-sessions")

    assert default_result.exit_code == 0
    assert json.loads(default_result.stdout)["payload"] == {
        "trustBrowser": False,
        "allBrowsers": False,
    }
    assert keep_trusted_result.exit_code == 0
    assert json.loads(keep_trusted_result.stdout)["payload"] == {
        "trustBrowser": True,
        "allBrowsers": False,
    }
    assert all_sessions_result.exit_code == 0
    assert json.loads(all_sessions_result.stdout)["payload"] == {
        "trustBrowser": False,
        "allBrowsers": True,
    }
    assert combined_result.exit_code == 0
    assert json.loads(combined_result.stdout)["payload"] == {
        "trustBrowser": True,
        "allBrowsers": True,
    }

    session_dir = _unique_session_dir("auth-logout-failure")
    _remember_local_account(
        session_dir,
        "user@example.com",
        has_session_file=True,
        keyring_passwords={"user@example.com"},
    )
    failing_api = FakeAPI(session_dir=session_dir)
    failing_api.logout = MagicMock(
        return_value={
            "payload": {"trustBrowser": False, "allBrowsers": False},
            "remote_logout_confirmed": False,
            "local_session_cleared": True,
        }
    )
    with patch.object(
        context_module.utils, "delete_password_in_keyring"
    ) as delete_password:
        failure_result = _invoke(
            failing_api,
            "auth",
            "logout",
            username=None,
            session_dir=session_dir,
            keyring_passwords={"user@example.com"},
        )
    assert failure_result.exit_code == 0
    assert "remote logout was not confirmed" in failure_result.stdout
    delete_password.assert_not_called()


def test_auth_logout_remove_keyring_is_explicit() -> None:
    """Auth logout should only delete stored passwords when requested."""

    session_dir = _unique_session_dir("auth-logout-remove-keyring")
    _remember_local_account(
        session_dir,
        "user@example.com",
        has_session_file=True,
        keyring_passwords={"user@example.com"},
    )

    with patch.object(
        context_module.utils, "delete_password_in_keyring"
    ) as delete_password:
        result = _invoke(
            FakeAPI(session_dir=session_dir),
            "auth",
            "logout",
            "--remove-keyring",
            username=None,
            session_dir=session_dir,
            output_format="json",
            keyring_passwords={"user@example.com"},
        )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["stored_password_removed"] is True
    delete_password.assert_called_once_with("user@example.com")


def test_security_key_flow() -> None:
    """Auth login should confirm the selected security key."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.fido2_devices = [{"id": "sk-1"}]
    result = _invoke(fake_api, "auth", "login")
    assert result.exit_code == 0
    fake_api.confirm_security_key.assert_called_once_with({"id": "sk-1"})


def test_trusted_device_2sa_flow() -> None:
    """Auth login should send and validate a 2SA verification code."""

    fake_api = FakeAPI()
    fake_api.requires_2sa = True
    fake_api.trusted_devices = [{"deviceName": "Trusted Device", "phoneNumber": "+1"}]
    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "auth", "login", interactive=True)
    assert result.exit_code == 0
    fake_api.send_verification_code.assert_called_once_with(fake_api.trusted_devices[0])
    fake_api.validate_verification_code.assert_called_once_with(
        fake_api.trusted_devices[0], "123456"
    )

def test_sms_2fa_flow_requests_sms_before_prompt() -> None:
    """Auth login should request SMS delivery before prompting for the code."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.two_factor_delivery_method = "sms"
    fake_api.request_2fa_code.return_value = True
    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "auth", "login", interactive=True)
    assert result.exit_code == 0
    assert "Requested a 2FA code by SMS." in result.stdout
    fake_api.request_2fa_code.assert_called_once_with()
    fake_api.validate_2fa_code.assert_called_once_with("123456")


def test_trusted_device_2fa_flow_reports_device_prompt() -> None:
    """Auth login should report trusted-device prompt delivery when bridge succeeds."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True

    def request_prompt() -> bool:
        fake_api.two_factor_delivery_method = "trusted_device"
        return True

    fake_api.request_2fa_code.side_effect = request_prompt

    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code == 0
    assert "Requested a 2FA prompt on your trusted Apple devices." in result.stdout
    fake_api.validate_2fa_code.assert_called_once_with("123456")


def test_code_prompt_aborts_when_request_2fa_code_requires_security_key() -> None:
    """Auth login should not enter the numeric 2FA prompt loop for key-only challenges."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.request_2fa_code.return_value = False

    result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code != 0
    assert result.exception.args[0] == (
        "This 2FA challenge requires a security key. Connect one and retry."
    )
    fake_api.validate_2fa_code.assert_not_called()


def test_trusted_device_2fa_retries_invalid_codes_before_success() -> None:
    """Auth login should allow up to three trusted-device 2FA attempts."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True

    def request_prompt() -> bool:
        fake_api.two_factor_delivery_method = "trusted_device"
        return True

    fake_api.request_2fa_code.side_effect = request_prompt
    fake_api.validate_2fa_code.side_effect = [False, False, True]

    with patch.object(
        context_module.typer,
        "prompt",
        side_effect=["111111", "222222", "333333"],
    ):
        result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code == 0
    assert "Invalid 2FA code. 2 attempt(s) remaining." in result.stdout
    assert "Invalid 2FA code. 1 attempt(s) remaining." in result.stdout
    assert fake_api.validate_2fa_code.call_args_list == [
        call("111111"),
        call("222222"),
        call("333333"),
    ]


def test_sms_2fa_aborts_after_three_invalid_codes() -> None:
    """Auth login should stop after three invalid 2FA attempts."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.two_factor_delivery_method = "sms"
    fake_api.request_2fa_code.return_value = True
    fake_api.validate_2fa_code.side_effect = [False, False, False]

    with patch.object(
        context_module.typer,
        "prompt",
        side_effect=["111111", "222222", "333333"],
    ):
        result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code != 0
    assert result.exception.args[0] == "Failed to verify the 2FA code."
    assert "Invalid 2FA code. 2 attempt(s) remaining." in result.stdout
    assert "Invalid 2FA code. 1 attempt(s) remaining." in result.stdout
    assert fake_api.validate_2fa_code.call_args_list == [
        call("111111"),
        call("222222"),
        call("333333"),
    ]


def test_trusted_device_2fa_bridge_fallback_reports_notice() -> None:
    """Auth login should print the bridge fallback notice before the SMS message."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True

    def request_sms_fallback() -> bool:
        fake_api.two_factor_delivery_method = "sms"
        fake_api.two_factor_delivery_notice = (
            "Trusted-device prompt failed; falling back to SMS."
        )
        return True

    fake_api.request_2fa_code.side_effect = request_sms_fallback

    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code == 0
    assert "Trusted-device prompt failed; falling back to SMS." in result.stdout
    assert "Requested a 2FA code by SMS." in result.stdout
    fake_api.validate_2fa_code.assert_called_once_with("123456")


def test_sms_2fa_request_failure_aborts() -> None:
    """Auth login should surface SMS delivery request failures clearly."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.request_2fa_code.side_effect = context_module.PyiCloudAPIResponseException(
        "sms request failed"
    )

    result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code != 0
    assert result.exception.args[0] == "Failed to request the 2FA SMS code."
    fake_api.validate_2fa_code.assert_not_called()


def test_trusted_device_2fa_request_failure_aborts() -> None:
    """Auth login should surface bridge delivery failures clearly."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.request_2fa_code.side_effect = (
        context_module.PyiCloudTrustedDevicePromptException("bridge failed")
    )

    result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code != 0
    assert result.exception.args[0] == (
        "Failed to request the 2FA trusted-device prompt."
    )
    fake_api.validate_2fa_code.assert_not_called()


def test_trusted_device_2fa_verification_failure_aborts() -> None:
    """Auth login should surface bridge verification failures clearly."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True

    def request_prompt() -> bool:
        fake_api.two_factor_delivery_method = "trusted_device"
        return True

    fake_api.request_2fa_code.side_effect = request_prompt
    fake_api.validate_2fa_code.side_effect = (
        context_module.PyiCloudTrustedDeviceVerificationException(
            "bridge verification failed"
        )
    )

    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "auth", "login", interactive=True)

    assert result.exit_code != 0
    assert result.exception.args[0] == ("Failed to verify the 2FA trusted-device code.")


def test_notes_commands() -> None:
    """Notes commands should expose list, detail, render, export, and sync flows."""

    fake_api = FakeAPI()

    recent_result = _invoke(fake_api, "notes", "recent")
    assert recent_result.exit_code == 0
    assert "Daily Plan" in recent_result.stdout
    assert "Deleted Note" not in recent_result.stdout

    recent_json_result = _invoke(
        fake_api,
        "notes",
        "recent",
        "--include-deleted",
        output_format="json",
    )
    recent_payload = json.loads(recent_json_result.stdout)
    assert recent_json_result.exit_code == 0
    assert [row["id"] for row in recent_payload] == [
        "Note/DELETED",
        "Note/DAILY",
        "Note/MEETING",
    ]

    folders_result = _invoke(fake_api, "notes", "folders")
    assert folders_result.exit_code == 0
    assert "Work" in folders_result.stdout

    folder_list_result = _invoke(
        fake_api,
        "notes",
        "list",
        "--folder-id",
        "Folder/WORK",
        "--limit",
        "2",
        output_format="json",
    )
    folder_payload = json.loads(folder_list_result.stdout)
    assert folder_list_result.exit_code == 0
    assert [row["id"] for row in folder_payload] == ["Note/MEETING", "Note/FOLLOWUP"]

    all_notes_result = _invoke(
        fake_api,
        "notes",
        "list",
        "--all",
        "--since",
        "notes-prev",
        "--limit",
        "2",
        output_format="json",
    )
    all_payload = json.loads(all_notes_result.stdout)
    assert all_notes_result.exit_code == 0
    assert fake_api.notes.iter_all_requests[-1] == "notes-prev"
    assert [row["id"] for row in all_payload] == ["Note/MEETING", "Note/FOLLOWUP"]

    get_result = _invoke(
        fake_api,
        "notes",
        "get",
        "Note/DAILY",
        "--with-attachments",
        output_format="json",
    )
    get_payload = json.loads(get_result.stdout)
    assert get_result.exit_code == 0
    assert get_payload["attachments"][0]["id"] == "Attachment/PDF"

    render_result = _invoke(
        fake_api,
        "notes",
        "render",
        "Note/DAILY",
        "--preview-appearance",
        "dark",
        "--pdf-height",
        "720",
        output_format="json",
    )
    render_payload = json.loads(render_result.stdout)
    assert render_result.exit_code == 0
    assert render_payload["html"] == "<p>Ship CLI</p>"
    assert fake_api.notes.render_calls[-1]["preview_appearance"] == "dark"
    assert fake_api.notes.render_calls[-1]["pdf_object_height"] == 720

    export_result = _invoke(
        fake_api,
        "notes",
        "export",
        "Note/DAILY",
        "--output-dir",
        str(TEST_ROOT / "notes-export"),
        "--export-mode",
        "lightweight",
        "--fragment",
        "--preview-appearance",
        "dark",
        "--pdf-height",
        "480",
        output_format="json",
    )
    export_payload = json.loads(export_result.stdout)
    assert export_result.exit_code == 0
    assert export_payload["path"].endswith("daily.html")
    assert fake_api.notes.export_calls[-1]["export_mode"] == "lightweight"
    assert fake_api.notes.export_calls[-1]["full_page"] is False
    assert fake_api.notes.export_calls[-1]["preview_appearance"] == "dark"
    assert fake_api.notes.export_calls[-1]["pdf_object_height"] == 480

    changes_result = _invoke(
        fake_api,
        "notes",
        "changes",
        "--since",
        "notes-prev",
        "--limit",
        "1",
        output_format="json",
    )
    changes_payload = json.loads(changes_result.stdout)
    assert changes_result.exit_code == 0
    assert fake_api.notes.change_requests[-1] == "notes-prev"
    assert changes_payload[0]["type"] == "updated"

    cursor_result = _invoke(fake_api, "notes", "sync-cursor")
    assert cursor_result.exit_code == 0
    assert cursor_result.stdout.strip() == "notes-cursor-1"


def test_notes_search_uses_recents_first_and_fallback() -> None:
    """Notes search should probe recents first, fall back to iter_all, and dedupe."""

    fake_api = FakeAPI()

    result = _invoke(
        fake_api,
        "notes",
        "search",
        "--title-contains",
        "Meeting",
        "--limit",
        "2",
        output_format="json",
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert [row["id"] for row in payload] == ["Note/MEETING", "Note/FOLLOWUP"]
    assert fake_api.notes.recent_requests[-1] == 500
    assert fake_api.notes.iter_all_requests == [None]


def test_notes_commands_report_errors() -> None:
    """Notes commands should surface clean selection and note-specific errors."""

    fake_api = FakeAPI()

    search_result = _invoke(fake_api, "notes", "search")
    assert search_result.exit_code != 0
    assert search_result.exception.args[0] == (
        "Pass --title or --title-contains to search notes."
    )

    missing_result = _invoke(fake_api, "notes", "get", "Note/MISSING")
    assert missing_result.exit_code != 0
    assert missing_result.exception.args[0] == "Note not found: Note/MISSING"

    locked_result = _invoke(fake_api, "notes", "get", "Note/LOCKED")
    assert locked_result.exit_code != 0
    assert locked_result.exception.args[0] == "Note is locked: Note/LOCKED"


def test_notes_commands_report_reauthentication_and_unavailability() -> None:
    """Notes commands should wrap service reauth and service-unavailable failures."""

    class ReauthNotes:
        def recents(self, *, limit: int = 50):
            raise context_module.PyiCloudFailedLoginException("No password set")

    class UnavailableNotes:
        def sync_cursor(self) -> str:
            raise context_module.PyiCloudServiceUnavailable("temporarily unavailable")

    fake_api = FakeAPI()
    fake_api.notes = ReauthNotes()
    reauth_result = _invoke(fake_api, "notes", "recent")
    assert reauth_result.exit_code != 0
    assert reauth_result.exception.args[0] == (
        "Notes requires re-authentication for user@example.com. "
        "Run: icloud auth login --username user@example.com"
    )

    fake_api = FakeAPI()
    fake_api.notes = UnavailableNotes()
    unavailable_result = _invoke(fake_api, "notes", "sync-cursor")
    assert unavailable_result.exit_code != 0
    assert unavailable_result.exception.args[0] == (
        "Notes service unavailable: temporarily unavailable"
    )


def test_reminders_core_commands() -> None:
    """Reminders core commands should expose list, detail, mutation, and sync flows."""

    fake_api = FakeAPI()

    lists_result = _invoke(fake_api, "reminders", "lists")
    assert lists_result.exit_code == 0
    assert "Inbox" in lists_result.stdout
    assert "blue (#007AFF)" in lists_result.stdout

    list_result = _invoke(fake_api, "reminders", "list", output_format="json")
    list_payload = json.loads(list_result.stdout)
    assert list_result.exit_code == 0
    assert [row["id"] for row in list_payload] == ["Reminder/A", "Reminder/C"]
    assert all(not row["completed"] for row in list_payload)

    completed_result = _invoke(
        fake_api,
        "reminders",
        "list",
        "--list-id",
        "INBOX",
        "--include-completed",
        output_format="json",
    )
    completed_payload = json.loads(completed_result.stdout)
    assert completed_result.exit_code == 0
    assert [row["id"] for row in completed_payload] == ["Reminder/A", "Reminder/B"]
    assert fake_api.reminders.snapshot_requests[-1]["list_id"] == "List/INBOX"

    get_result = _invoke(fake_api, "reminders", "get", "Reminder/A")
    assert get_result.exit_code == 0
    assert "Parent Reminder" in get_result.stdout

    create_result = _invoke(
        fake_api,
        "reminders",
        "create",
        "--list-id",
        "INBOX",
        "--title",
        "Call mom",
        "--desc",
        "Saturday",
        "--priority",
        "9",
        "--flagged",
        "--all-day",
        output_format="json",
    )
    create_payload = json.loads(create_result.stdout)
    created_id = create_payload["id"]
    assert create_result.exit_code == 0
    assert create_payload["list_id"] == "List/INBOX"
    assert create_payload["flagged"] is True
    assert create_payload["all_day"] is True

    update_result = _invoke(
        fake_api,
        "reminders",
        "update",
        "Reminder/A",
        "--title",
        "Buy oat milk",
        "--not-flagged",
        "--clear-time-zone",
        "--clear-parent-reminder",
        output_format="json",
    )
    update_payload = json.loads(update_result.stdout)
    assert update_result.exit_code == 0
    assert update_payload["title"] == "Buy oat milk"
    assert update_payload["flagged"] is False
    assert update_payload["time_zone"] is None
    assert update_payload["parent_reminder_id"] is None

    status_result = _invoke(
        fake_api,
        "reminders",
        "set-status",
        "Reminder/A",
        "--completed",
        output_format="json",
    )
    status_payload = json.loads(status_result.stdout)
    assert status_result.exit_code == 0
    assert status_payload["completed"] is True

    snapshot_result = _invoke(
        fake_api,
        "reminders",
        "snapshot",
        "--list-id",
        "INBOX",
        output_format="json",
    )
    snapshot_payload = json.loads(snapshot_result.stdout)
    assert snapshot_result.exit_code == 0
    assert set(snapshot_payload) == {
        "alarms",
        "attachments",
        "hashtags",
        "recurrence_rules",
        "reminders",
        "triggers",
    }

    changes_result = _invoke(
        fake_api,
        "reminders",
        "changes",
        "--since",
        "reminders-prev",
        "--limit",
        "1",
        output_format="json",
    )
    changes_payload = json.loads(changes_result.stdout)
    assert changes_result.exit_code == 0
    assert fake_api.reminders.change_requests[-1] == "reminders-prev"
    assert changes_payload[0]["type"] == "updated"

    cursor_result = _invoke(fake_api, "reminders", "sync-cursor")
    assert cursor_result.exit_code == 0
    assert cursor_result.stdout.strip() == "reminders-cursor-1"

    delete_result = _invoke(
        fake_api,
        "reminders",
        "delete",
        created_id,
        output_format="json",
    )
    delete_payload = json.loads(delete_result.stdout)
    assert delete_result.exit_code == 0
    assert delete_payload["deleted"] is True
    assert fake_api.reminders.reminder_rows[created_id].deleted is True


def test_reminders_subgroup_commands() -> None:
    """Reminder subgroup commands should expose alarm, hashtag, attachment, and recurrence flows."""

    fake_api = FakeAPI()

    alarm_list_result = _invoke(
        fake_api,
        "reminders",
        "alarm",
        "list",
        "Reminder/A",
        output_format="json",
    )
    alarm_list_payload = json.loads(alarm_list_result.stdout)
    assert alarm_list_result.exit_code == 0
    assert alarm_list_payload[0]["alarm"]["id"] == "Alarm/A"

    alarm_create_result = _invoke(
        fake_api,
        "reminders",
        "alarm",
        "add-location",
        "Reminder/C",
        "--title",
        "Home",
        "--address",
        "Rue de Example",
        "--latitude",
        "49.61",
        "--longitude",
        "6.13",
        "--radius",
        "75",
        "--proximity",
        "leaving",
        output_format="json",
    )
    alarm_create_payload = json.loads(alarm_create_result.stdout)
    assert alarm_create_result.exit_code == 0
    assert alarm_create_payload["trigger"]["title"] == "Home"
    assert (
        fake_api.reminders.trigger_rows[alarm_create_payload["trigger"]["id"]].proximity
        == Proximity.LEAVING
    )

    hashtag_list_result = _invoke(
        fake_api,
        "reminders",
        "hashtag",
        "list",
        "Reminder/A",
        output_format="json",
    )
    hashtag_list_payload = json.loads(hashtag_list_result.stdout)
    assert hashtag_list_result.exit_code == 0
    assert hashtag_list_payload[0]["id"] == "Hashtag/ERRANDS"

    hashtag_create_result = _invoke(
        fake_api,
        "reminders",
        "hashtag",
        "create",
        "Reminder/C",
        "home",
        output_format="json",
    )
    hashtag_create_payload = json.loads(hashtag_create_result.stdout)
    hashtag_suffix = hashtag_create_payload["id"].split("/", 1)[1]
    assert hashtag_create_result.exit_code == 0

    hashtag_update_result = _invoke(
        fake_api,
        "reminders",
        "hashtag",
        "update",
        "Reminder/C",
        hashtag_suffix,
        "--name",
        "chores",
        output_format="json",
    )
    hashtag_update_payload = json.loads(hashtag_update_result.stdout)
    assert hashtag_update_result.exit_code == 0
    assert hashtag_update_payload["name"] == "chores"

    hashtag_delete_result = _invoke(
        fake_api,
        "reminders",
        "hashtag",
        "delete",
        "Reminder/C",
        hashtag_suffix,
        output_format="json",
    )
    hashtag_delete_payload = json.loads(hashtag_delete_result.stdout)
    assert hashtag_delete_result.exit_code == 0
    assert hashtag_delete_payload["deleted"] is True

    attachment_list_result = _invoke(
        fake_api,
        "reminders",
        "attachment",
        "list",
        "Reminder/A",
        output_format="json",
    )
    attachment_list_payload = json.loads(attachment_list_result.stdout)
    assert attachment_list_result.exit_code == 0
    assert attachment_list_payload[0]["id"] == "Attachment/LINK"

    attachment_create_result = _invoke(
        fake_api,
        "reminders",
        "attachment",
        "create-url",
        "Reminder/C",
        "--url",
        "https://example.com/new",
        output_format="json",
    )
    attachment_create_payload = json.loads(attachment_create_result.stdout)
    attachment_suffix = attachment_create_payload["id"].split("/", 1)[1]
    assert attachment_create_result.exit_code == 0

    attachment_update_result = _invoke(
        fake_api,
        "reminders",
        "attachment",
        "update",
        "Reminder/C",
        attachment_suffix,
        "--url",
        "https://example.org/new",
        "--uti",
        "public.url",
        output_format="json",
    )
    attachment_update_payload = json.loads(attachment_update_result.stdout)
    assert attachment_update_result.exit_code == 0
    assert attachment_update_payload["url"] == "https://example.org/new"

    attachment_delete_result = _invoke(
        fake_api,
        "reminders",
        "attachment",
        "delete",
        "Reminder/C",
        attachment_suffix,
        output_format="json",
    )
    attachment_delete_payload = json.loads(attachment_delete_result.stdout)
    assert attachment_delete_result.exit_code == 0
    assert attachment_delete_payload["deleted"] is True

    recurrence_list_result = _invoke(
        fake_api,
        "reminders",
        "recurrence",
        "list",
        "Reminder/A",
        output_format="json",
    )
    recurrence_list_payload = json.loads(recurrence_list_result.stdout)
    assert recurrence_list_result.exit_code == 0
    assert recurrence_list_payload[0]["id"] == "Recurrence/WEEKLY"

    recurrence_create_result = _invoke(
        fake_api,
        "reminders",
        "recurrence",
        "create",
        "Reminder/C",
        "--frequency",
        "monthly",
        "--interval",
        "2",
        output_format="json",
    )
    recurrence_create_payload = json.loads(recurrence_create_result.stdout)
    recurrence_suffix = recurrence_create_payload["id"].split("/", 1)[1]
    assert recurrence_create_result.exit_code == 0

    recurrence_update_result = _invoke(
        fake_api,
        "reminders",
        "recurrence",
        "update",
        "Reminder/C",
        recurrence_suffix,
        "--frequency",
        "yearly",
        "--interval",
        "3",
        "--occurrence-count",
        "4",
        output_format="json",
    )
    recurrence_update_payload = json.loads(recurrence_update_result.stdout)
    assert recurrence_update_result.exit_code == 0
    assert recurrence_update_payload["interval"] == 3
    assert recurrence_update_payload["occurrence_count"] == 4

    recurrence_delete_result = _invoke(
        fake_api,
        "reminders",
        "recurrence",
        "delete",
        "Reminder/C",
        recurrence_suffix,
        output_format="json",
    )
    recurrence_delete_payload = json.loads(recurrence_delete_result.stdout)
    assert recurrence_delete_result.exit_code == 0
    assert recurrence_delete_payload["deleted"] is True


def test_reminders_commands_report_errors() -> None:
    """Reminders commands should surface clean validation and lookup errors."""

    fake_api = FakeAPI()

    missing_result = _invoke(fake_api, "reminders", "get", "Reminder/MISSING")
    assert missing_result.exit_code != 0
    assert missing_result.exception.args[0] == "Reminder not found: Reminder/MISSING"

    update_result = _invoke(fake_api, "reminders", "update", "Reminder/A")
    assert update_result.exit_code != 0
    assert update_result.exception.args[0] == "No reminder updates were requested."

    hashtag_result = _invoke(
        fake_api,
        "reminders",
        "hashtag",
        "delete",
        "Reminder/A",
        "missing",
    )
    assert hashtag_result.exit_code != 0
    assert hashtag_result.exception.args[0] == (
        "No hashtag matched 'missing' for reminder Reminder/A."
    )

    attachment_result = _invoke(
        fake_api,
        "reminders",
        "attachment",
        "update",
        "Reminder/A",
        "LINK",
    )
    assert attachment_result.exit_code != 0
    assert attachment_result.exception.args[0] == (
        "No attachment updates were requested."
    )

    recurrence_result = _invoke(
        fake_api,
        "reminders",
        "recurrence",
        "update",
        "Reminder/A",
        "WEEKLY",
    )
    assert recurrence_result.exit_code != 0
    assert recurrence_result.exception.args[0] == (
        "No recurrence updates were requested."
    )

    class ApiErrorReminders:
        def sync_cursor(self) -> str:
            raise RemindersApiError("sync failed")

    class AuthErrorReminders:
        def sync_cursor(self) -> str:
            raise RemindersAuthError("token expired")

    fake_api = FakeAPI()
    fake_api.reminders = ApiErrorReminders()
    api_error_result = _invoke(fake_api, "reminders", "sync-cursor")
    assert api_error_result.exit_code != 0
    assert api_error_result.exception.args[0] == "sync failed"

    fake_api = FakeAPI()
    fake_api.reminders = AuthErrorReminders()
    auth_error_result = _invoke(fake_api, "reminders", "sync-cursor")
    assert auth_error_result.exit_code != 0
    assert auth_error_result.exception.args[0] == "token expired"


def test_reminders_commands_report_reauthentication_and_unavailability() -> None:
    """Reminders commands should wrap service reauth and service-unavailable failures."""

    class ReauthReminders:
        def lists(self):
            raise context_module.PyiCloudFailedLoginException("No password set")

    class UnavailableReminders:
        def sync_cursor(self) -> str:
            raise context_module.PyiCloudServiceUnavailable("temporarily unavailable")

    fake_api = FakeAPI()
    fake_api.reminders = ReauthReminders()
    reauth_result = _invoke(fake_api, "reminders", "lists")
    assert reauth_result.exit_code != 0
    assert reauth_result.exception.args[0] == (
        "Reminders requires re-authentication for user@example.com. "
        "Run: icloud auth login --username user@example.com"
    )

    fake_api = FakeAPI()
    fake_api.reminders = UnavailableReminders()
    unavailable_result = _invoke(fake_api, "reminders", "sync-cursor")
    assert unavailable_result.exit_code != 0
    assert unavailable_result.exception.args[0] == (
        "Reminders service unavailable: temporarily unavailable"
    )


def test_main_returns_clean_error_for_user_abort(capsys) -> None:
    """The entrypoint should not emit a traceback for expected CLI errors."""

    message = "No local accounts were found; pass --username to bootstrap one."
    with patch.object(cli_module, "app", side_effect=context_module.CLIAbort(message)):
        code = cli_module.main()
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert message in captured.err
