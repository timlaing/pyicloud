"""Tests for the Typer-based pyicloud CLI."""

from __future__ import annotations

import importlib
import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

cli_module = importlib.import_module("pyicloud.cli.app")
context_module = importlib.import_module("pyicloud.cli.context")
app = cli_module.app


class FakeDevice:
    """Find My device fixture."""

    def __init__(self) -> None:
        self.id = "device-1"
        self.name = "Jacob's iPhone"
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
        self, anonymous_id: str, label: str, note: str
    ) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "label": label, "note": note}

    def deactivate(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "active": False}

    def reactivate(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "active": True}

    def delete(self, anonymous_id: str) -> dict[str, Any]:
        return {"anonymousId": anonymous_id, "deleted": True}


@dataclass
class FakeReminder:
    """Reminder fixture."""

    id: str
    title: str
    completed: bool = False
    due_date: Optional[datetime] = None
    priority: int = 0
    desc: str = ""


@dataclass
class FakeNoteSummary:
    """Note summary fixture."""

    id: str
    title: str
    folder_name: str
    modified_at: datetime
    is_deleted: bool = False


@dataclass
class FakeNote:
    """Note fixture."""

    id: str
    title: str
    text: str
    attachments: Optional[list[Any]] = None


@dataclass
class FakeChange:
    """Change fixture."""

    type: str
    reminder_id: Optional[str] = None
    reminder: Optional[Any] = None
    note_id: Optional[str] = None
    note: Optional[Any] = None


class FakeReminders:
    """Reminders service fixture."""

    def __init__(self) -> None:
        self._lists = [
            SimpleNamespace(
                id="list-1",
                title="Inbox",
                color='{"daHexString":"#007AFF","ckSymbolicColorName":"blue"}',
                count=2,
            )
        ]
        self._reminders = [
            FakeReminder(id="rem-1", title="Buy milk", priority=1),
            FakeReminder(id="rem-2", title="Pay rent", completed=True),
        ]

    def lists(self) -> Iterable[Any]:
        return list(self._lists)

    def reminders(
        self, list_id: Optional[str] = None, include_completed: bool = False
    ) -> Iterable[FakeReminder]:
        if include_completed:
            return list(self._reminders)
        return [reminder for reminder in self._reminders if not reminder.completed]

    def get(self, reminder_id: str) -> FakeReminder:
        for reminder in self._reminders:
            if reminder.id == reminder_id:
                return reminder
        raise KeyError(reminder_id)

    def create(self, **kwargs: Any) -> FakeReminder:
        reminder = FakeReminder(
            id="rem-created",
            title=kwargs["title"],
            due_date=kwargs.get("due_date"),
            priority=kwargs.get("priority", 0),
            desc=kwargs.get("desc", ""),
        )
        self._reminders.append(reminder)
        return reminder

    def update(self, reminder: FakeReminder) -> None:
        return None

    def delete(self, reminder: FakeReminder) -> None:
        reminder.completed = True

    def iter_changes(self, since: Optional[str] = None):
        yield FakeChange(
            type="updated", reminder_id="rem-1", reminder=self._reminders[0]
        )


class FakeNotes:
    """Notes service fixture."""

    def __init__(self) -> None:
        self._recent = [
            FakeNoteSummary(
                id="note-deleted",
                title="Deleted Note",
                folder_name="Recently Deleted",
                modified_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
                is_deleted=True,
            ),
            FakeNoteSummary(
                id="note-1",
                title="Daily Plan",
                folder_name="Notes",
                modified_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            ),
        ]
        self._folders = [
            SimpleNamespace(
                id="folder-1",
                name="Notes",
                parent_id=None,
                has_subfolders=False,
            )
        ]

    def recents(self, *, limit: int = 50):
        return self._recent[:limit]

    def folders(self):
        return list(self._folders)

    def in_folder(self, folder_id: str, limit: int = 50):
        return self._recent[:limit]

    def iter_all(self, since: Optional[str] = None):
        return iter(self._recent)

    def get(self, note_id: str, *, with_attachments: bool = False):
        attachments = (
            [
                SimpleNamespace(
                    id="att-1", filename="file.pdf", uti="com.adobe.pdf", size=12
                )
            ]
            if with_attachments
            else None
        )
        return FakeNote(
            id=note_id, title="Daily Plan", text="Ship CLI", attachments=attachments
        )

    def render_note(self, note_id: str, **kwargs: Any) -> str:
        return f"<p>{note_id}</p>"

    def export_note(self, note_id: str, output_dir: str, **kwargs: Any) -> str:
        return str(Path(output_dir) / f"{note_id}.html")

    def iter_changes(self, since: Optional[str] = None):
        yield FakeChange(type="updated", note_id="note-1", note=self.get("note-1"))


class FakeAPI:
    """Authenticated API fixture."""

    def __init__(self) -> None:
        self.requires_2fa = False
        self.requires_2sa = False
        self.is_trusted_session = True
        self.fido2_devices: list[dict[str, Any]] = []
        self.trusted_devices: list[dict[str, Any]] = []
        self.validate_2fa_code = MagicMock(return_value=True)
        self.confirm_security_key = MagicMock(return_value=True)
        self.send_verification_code = MagicMock(return_value=True)
        self.validate_verification_code = MagicMock(return_value=True)
        self.trust_session = MagicMock(return_value=True)
        self.account_name = "user@example.com"
        self.devices = [FakeDevice()]
        self.account = SimpleNamespace(
            devices=[
                {
                    "name": "Jacob's iPhone",
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
        self.reminders = FakeReminders()
        self.notes = FakeNotes()


def _runner() -> CliRunner:
    return CliRunner()


def _invoke(
    fake_api: FakeAPI,
    *args: str,
    interactive: bool = False,
):
    runner = _runner()
    cli_args = [
        "--username",
        "user@example.com",
        "--password",
        "secret",
        *([] if interactive else ["--non-interactive"]),
        *args,
    ]
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
    ):
        return runner.invoke(app, cli_args)


def test_root_help() -> None:
    """The root command should expose the service subcommands and format option."""

    result = _runner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--format" in result.stdout
    assert "--json" not in result.stdout
    assert "--debug" not in result.stdout
    for command in (
        "account",
        "devices",
        "calendar",
        "contacts",
        "drive",
        "photos",
        "hidemyemail",
        "reminders",
        "notes",
    ):
        assert command in result.stdout


def test_group_help() -> None:
    """Each command group should expose help."""

    for command in (
        "account",
        "devices",
        "calendar",
        "contacts",
        "drive",
        "photos",
        "hidemyemail",
        "reminders",
        "notes",
    ):
        result = _runner().invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_account_summary_command() -> None:
    """Account summary should render the storage overview."""

    result = _invoke(FakeAPI(), "account", "summary")
    assert result.exit_code == 0
    assert "Account: user@example.com" in result.stdout
    assert "Storage: 10.0% used" in result.stdout


def test_format_option_outputs_json() -> None:
    """The root format option should support machine-readable JSON."""

    result = _invoke(FakeAPI(), "--format", "json", "account", "summary")
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["account_name"] == "user@example.com"
    assert payload["devices_count"] == 1


def test_default_log_level_is_warning() -> None:
    """Authenticated commands should default pyicloud logs to warning."""

    with patch.object(context_module.logging, "basicConfig") as basic_config:
        result = _invoke(FakeAPI(), "account", "summary")
    assert result.exit_code == 0
    basic_config.assert_called_once_with(level=context_module.logging.WARNING)


def test_missing_username_errors_cleanly() -> None:
    """Authenticated commands should fail without a traceback when username is missing."""

    with patch.object(
        context_module, "configurable_ssl_verification", return_value=nullcontext()
    ):
        result = _runner().invoke(app, ["account", "summary"])
    assert result.exit_code != 0
    assert "The --username option is required" in result.exception.args[0]


def test_delete_from_keyring() -> None:
    """The keyring delete path should work without invoking a subcommand."""

    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=True
        ),
        patch.object(
            context_module.utils, "delete_password_in_keyring"
        ) as delete_password,
    ):
        result = _runner().invoke(
            app,
            ["--username", "user@example.com", "--delete-from-keyring"],
        )
    assert result.exit_code == 0
    delete_password.assert_called_once_with("user@example.com")
    assert "Deleted stored password from keyring." in result.stdout


def test_security_key_flow() -> None:
    """Security-key 2FA should confirm the selected key."""

    fake_api = FakeAPI()
    fake_api.requires_2fa = True
    fake_api.fido2_devices = [{"id": "sk-1"}]
    result = _invoke(fake_api, "account", "summary")
    assert result.exit_code == 0
    fake_api.confirm_security_key.assert_called_once_with({"id": "sk-1"})


def test_trusted_device_2sa_flow() -> None:
    """2SA should send and validate a verification code."""

    fake_api = FakeAPI()
    fake_api.requires_2sa = True
    fake_api.trusted_devices = [{"deviceName": "Trusted Device", "phoneNumber": "+1"}]
    with patch.object(context_module.typer, "prompt", return_value="123456"):
        result = _invoke(fake_api, "account", "summary", interactive=True)
    assert result.exit_code == 0
    fake_api.send_verification_code.assert_called_once_with(fake_api.trusted_devices[0])
    fake_api.validate_verification_code.assert_called_once_with(
        fake_api.trusted_devices[0], "123456"
    )


def test_devices_list_and_show_commands() -> None:
    """Devices list and show should expose summary and detailed views."""

    fake_api = FakeAPI()
    list_result = _invoke(fake_api, "devices", "list", "--locate")
    show_result = _invoke(fake_api, "devices", "show", "device-1")
    raw_result = _invoke(
        fake_api, "--format", "json", "devices", "show", "device-1", "--raw"
    )
    assert list_result.exit_code == 0
    assert "Jacob's iPhone" in list_result.stdout
    assert show_result.exit_code == 0
    assert "Battery Status" in show_result.stdout
    assert raw_result.exit_code == 0
    assert json.loads(raw_result.stdout)["deviceDisplayName"] == "iPhone"


def test_devices_mutations_and_export() -> None:
    """Device actions should map to the Find My device methods."""

    fake_api = FakeAPI()
    export_path = Path("/tmp/python-test-results/test_cmdline/device.json")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    sound_result = _invoke(
        fake_api,
        "--format",
        "json",
        "devices",
        "sound",
        "device-1",
        "--subject",
        "Ping",
    )
    silent_result = _invoke(
        fake_api,
        "devices",
        "message",
        "device-1",
        "Hello",
        "--silent",
    )
    lost_result = _invoke(
        fake_api,
        "devices",
        "lost-mode",
        "device-1",
        "--phone",
        "123",
        "--message",
        "Lost",
        "--passcode",
        "4567",
    )
    export_result = _invoke(
        fake_api,
        "--format",
        "json",
        "devices",
        "export",
        "device-1",
        "--output",
        str(export_path),
    )
    assert sound_result.exit_code == 0
    assert json.loads(sound_result.stdout)["subject"] == "Ping"
    assert fake_api.devices[0].sound_subject == "Ping"
    assert silent_result.exit_code == 0
    assert fake_api.devices[0].messages[-1]["sounds"] is False
    assert lost_result.exit_code == 0
    assert fake_api.devices[0].lost_mode == {
        "number": "123",
        "text": "Lost",
        "newpasscode": "4567",
    }
    assert export_result.exit_code == 0
    assert json.loads(export_result.stdout)["path"] == str(export_path)
    assert (
        json.loads(export_path.read_text(encoding="utf-8"))["name"] == "Jacob's iPhone"
    )


def test_calendar_and_contacts_commands() -> None:
    """Calendar and contacts groups should expose read commands."""

    fake_api = FakeAPI()
    calendars = _invoke(fake_api, "calendar", "calendars")
    contacts = _invoke(fake_api, "contacts", "me")
    assert calendars.exit_code == 0
    assert "Home" in calendars.stdout
    assert contacts.exit_code == 0
    assert "John Appleseed" in contacts.stdout


def test_drive_and_photos_commands() -> None:
    """Drive and photos commands should expose listing and download flows."""

    fake_api = FakeAPI()
    output_path = Path("/tmp/python-test-results/test_cmdline/photo.bin")
    json_output_path = Path("/tmp/python-test-results/test_cmdline/report.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    drive_result = _invoke(fake_api, "drive", "list", "/")
    photo_result = _invoke(
        fake_api,
        "photos",
        "download",
        "photo-1",
        "--output",
        str(output_path),
    )
    json_drive_result = _invoke(
        fake_api,
        "--format",
        "json",
        "drive",
        "download",
        "/report.txt",
        "--output",
        str(json_output_path),
    )
    assert drive_result.exit_code == 0
    assert "report.txt" in drive_result.stdout
    assert photo_result.exit_code == 0
    assert output_path.read_bytes() == b"photo-1:original"
    assert json_drive_result.exit_code == 0
    assert json.loads(json_drive_result.stdout)["path"] == str(json_output_path)


def test_hidemyemail_commands() -> None:
    """Hide My Email commands should expose list and generate."""

    fake_api = FakeAPI()
    list_result = _invoke(fake_api, "hidemyemail", "list")
    generate_result = _invoke(fake_api, "hidemyemail", "generate")
    assert list_result.exit_code == 0
    assert "Shopping" in list_result.stdout
    assert generate_result.exit_code == 0
    assert "generated@privaterelay.appleid.com" in generate_result.stdout


def test_reminders_commands() -> None:
    """Reminders commands should expose list and create flows."""

    fake_api = FakeAPI()
    lists_result = _invoke(fake_api, "reminders", "lists")
    list_result = _invoke(fake_api, "reminders", "list")
    create_result = _invoke(
        fake_api,
        "--format",
        "json",
        "reminders",
        "create",
        "--list-id",
        "list-1",
        "--title",
        "New task",
        "--priority",
        "5",
    )
    assert lists_result.exit_code == 0
    assert "blue (#007AFF)" in lists_result.stdout
    assert list_result.exit_code == 0
    assert "Buy milk" in list_result.stdout
    assert create_result.exit_code == 0
    assert json.loads(create_result.stdout)["id"] == "rem-created"


def test_notes_commands() -> None:
    """Notes commands should expose recent, get, render, and export flows."""

    fake_api = FakeAPI()
    output_dir = Path("/tmp/python-test-results/test_cmdline/notes")
    output_dir.mkdir(parents=True, exist_ok=True)
    recent_result = _invoke(fake_api, "notes", "recent", "--limit", "1")
    include_deleted_result = _invoke(
        fake_api, "notes", "recent", "--limit", "1", "--include-deleted"
    )
    render_result = _invoke(fake_api, "--format", "json", "notes", "render", "note-1")
    export_result = _invoke(
        fake_api,
        "--format",
        "json",
        "notes",
        "export",
        "note-1",
        str(output_dir),
    )
    assert recent_result.exit_code == 0
    assert "Daily Plan" in recent_result.stdout
    assert "Deleted Note" not in recent_result.stdout
    assert include_deleted_result.exit_code == 0
    assert "Deleted Note" in include_deleted_result.stdout
    assert render_result.exit_code == 0
    assert json.loads(render_result.stdout)["html"] == "<p>note-1</p>"
    assert export_result.exit_code == 0
    assert json.loads(export_result.stdout)["path"] == str(output_dir / "note-1.html")


def test_main_returns_clean_error_for_user_abort(capsys) -> None:
    """The entrypoint should not emit a traceback for expected CLI errors."""

    message = "The --username option is required for authenticated commands."
    with patch.object(cli_module, "app", side_effect=context_module.CLIAbort(message)):
        code = cli_module.main()
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert message in captured.err
