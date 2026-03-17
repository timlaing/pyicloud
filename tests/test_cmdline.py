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
from uuid import uuid4

from typer.testing import CliRunner

account_index_module = importlib.import_module("pyicloud.cli.account_index")
cli_module = importlib.import_module("pyicloud.cli.app")
context_module = importlib.import_module("pyicloud.cli.context")
app = cli_module.app

TEST_ROOT = Path("/tmp/python-test-results/test_cmdline")


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

    def __init__(
        self,
        *,
        username: str = "user@example.com",
        session_dir: Optional[Path] = None,
    ) -> None:
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
        self.logout = MagicMock(side_effect=self._logout)
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
    keyring_passwords: Optional[set[str]] = None,
) -> FakeAPI:
    fake_api = FakeAPI(username=username, session_dir=session_dir)
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
        keyring_has=lambda candidate: candidate in (keyring_passwords or set()),
    )
    return fake_api


def _invoke(
    fake_api: FakeAPI,
    *args: str,
    username: Optional[str] = "user@example.com",
    password: Optional[str] = "secret",
    interactive: bool = False,
    session_dir: Optional[Path] = None,
    keyring_passwords: Optional[set[str]] = None,
):
    runner = _runner()
    session_dir = session_dir or _unique_session_dir("invoke")
    cli_args = [
        *([] if username is None else ["--username", username]),
        *([] if password is None else ["--password", password]),
        "--session-dir",
        str(session_dir),
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
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate in (keyring_passwords or set()),
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
    ):
        return runner.invoke(app, cli_args)


def test_root_help() -> None:
    """The root command should expose the service subcommands and format option."""

    result = _runner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--username" in result.stdout
    assert "before the command or on the final command" in result.stdout
    assert "--format" in result.stdout
    assert "--json" not in result.stdout
    assert "--debug" not in result.stdout
    for command in (
        "account",
        "auth",
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
        "auth",
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


def test_leaf_help_includes_execution_context_options() -> None:
    """Leaf command help should show shared execution-context options."""

    result = _runner().invoke(app, ["account", "summary", "--help"])

    assert result.exit_code == 0
    assert "--username" in result.stdout
    assert "--format" in result.stdout
    assert "--session-dir" in result.stdout


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


def test_command_local_format_option_outputs_json() -> None:
    """Leaf commands should accept --format after the final subcommand."""

    session_dir = _unique_session_dir("leaf-format")
    result = _invoke_with_cli_args(
        FakeAPI(session_dir=session_dir),
        [
            "--username",
            "user@example.com",
            "--password",
            "secret",
            "--session-dir",
            str(session_dir),
            "--non-interactive",
            "account",
            "summary",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["account_name"] == "user@example.com"


def test_leaf_execution_context_overrides_root_values() -> None:
    """Leaf execution-context options should take precedence over root values."""

    session_dir = _unique_session_dir("leaf-precedence")
    fake_api = FakeAPI(username="leaf@example.com", session_dir=session_dir)

    def fake_service(*, apple_id: str, **kwargs: Any) -> FakeAPI:
        assert apple_id == "leaf@example.com"
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
    ):
        result = _runner().invoke(
            app,
            [
                "--username",
                "root@example.com",
                "--password",
                "root-secret",
                "--session-dir",
                "/tmp/root-session",
                "--format",
                "json",
                "--non-interactive",
                "auth",
                "login",
                "--username",
                "leaf@example.com",
                "--password",
                "leaf-secret",
                "--session-dir",
                str(session_dir),
                "--format",
                "text",
            ],
        )

    assert result.exit_code == 0
    assert "Authenticated session is ready." in result.stdout
    assert result.stdout.lstrip()[0] != "{"


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
    ):
        result = _runner().invoke(
            app,
            [
                "--password",
                "secret",
                "--session-dir",
                str(session_dir),
                "--non-interactive",
                "auth",
                "login",
                "--username",
                "leaf@example.com",
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
    ):
        result = _runner().invoke(
            app,
            [
                "--username",
                "user@example.com",
                "--password",
                "secret",
                "--non-interactive",
                "account",
                "summary",
                "--session-dir",
                str(session_dir),
            ],
        )

    assert result.exit_code == 0
    assert "Account: user@example.com" in result.stdout


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
            app, ["--session-dir", str(session_dir), "account", "summary"]
        )
    assert result.exit_code != 0
    assert (
        result.exception.args[0]
        == "You are not logged into any iCloud accounts. To log in, run: "
        "icloud --username <apple-id> auth login"
    )


def test_delete_from_keyring() -> None:
    """The keyring delete path should work without invoking a subcommand."""

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
                    "--username",
                    "user@example.com",
                    "--session-dir",
                    str(session_dir),
                    "--delete-from-keyring",
                ],
            )
    assert result.exit_code == 0
    delete_password.assert_called_once_with("user@example.com")
    assert "Deleted stored password from keyring." in result.stdout
    assert account_index_module.load_accounts(session_dir) == {}


def test_delete_from_keyring_remains_root_only() -> None:
    """Utility flags like --delete-from-keyring should remain root-only."""

    result = _runner().invoke(
        app,
        ["auth", "login", "--delete-from-keyring"],
    )

    assert result.exit_code != 0
    combined_output = result.stdout + result.stderr
    assert "No such option: --delete-from-keyring" in combined_output


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
            ["--session-dir", str(session_dir), "--non-interactive", "auth", "status"],
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


def test_auth_login_and_status_commands() -> None:
    """Auth status and login should expose stable text and JSON payloads."""

    fake_api = FakeAPI()
    status_result = _invoke(fake_api, "--format", "json", "auth", "status")
    login_result = _invoke(fake_api, "--format", "json", "auth", "login")

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
        "icloud --username <apple-id> auth login"
    )
    assert post_logout_explicit_result.exit_code != 0
    assert (
        post_logout_explicit_result.exception.args[0]
        == "You are not logged into iCloud for solo@example.com. Run: "
        "icloud --username solo@example.com auth login"
    )
    assert login_result.exit_code == 0
    assert [
        entry["username"]
        for entry in account_index_module.prune_accounts(
            session_dir, lambda candidate: candidate == "solo@example.com"
        )
    ] == ["solo@example.com"]


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
            side_effect=lambda candidate: candidate
            in {"alpha@example.com", "beta@example.com"},
        ),
    ):
        result = _runner().invoke(
            app,
            [
                "--session-dir",
                str(session_dir),
                "--non-interactive",
                "auth",
                "login",
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
                "--session-dir",
                str(session_dir),
                "--non-interactive",
                "account",
                "summary",
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


def test_auth_login_non_interactive_requires_credentials() -> None:
    """Auth login should fail cleanly when non-interactive mode lacks credentials."""

    with (
        patch.object(
            context_module, "configurable_ssl_verification", return_value=nullcontext()
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(context_module.utils, "get_password", return_value=None),
    ):
        result = _runner().invoke(
            app,
            ["--username", "user@example.com", "--non-interactive", "auth", "login"],
        )
    assert result.exit_code != 0
    assert "No password supplied and no stored password was found." in str(
        result.exception
    )


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
            "--format",
            "json",
            "auth",
            "logout",
            *args,
            username=None,
            session_dir=session_dir,
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
            "--format",
            "json",
            "auth",
            "logout",
            "--remove-keyring",
            username=None,
            session_dir=session_dir,
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

    message = "No local accounts were found; pass --username to bootstrap one."
    with patch.object(cli_module, "app", side_effect=context_module.CLIAbort(message)):
        code = cli_module.main()
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert message in captured.err
