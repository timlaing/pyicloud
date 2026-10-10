"""Targeted branch-coverage tests for the notes, photos, and reminders CLI commands.

These tests focus on the text-output paths, validation errors, and optional
argument branches that the broader ``test_cmdline.py`` suite exercises mostly
through JSON output.
"""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.commands import photos as photos_cmd
from pyicloud.cli.commands import reminders as reminders_cmd
from pyicloud.services.notes.models import Note
from pyicloud.services.photos import PhotoSyncOptions, PhotoSyncResult
from pyicloud.services.reminders.models import (
    ImageAttachment,
    ListRemindersResult,
    RemindersList,
)
from tests.test_cmdline import (
    TEST_ROOT,
    FakeAPI,
    FakeNotes,
    FakePhotosService,
    FakeReminders,
    context_module,
)


def invoke(fake_api: Any, *args: str, output_format: str | None = None) -> Result:
    """Invoke the full CLI with a fake authenticated API and optional JSON mode."""

    runner = CliRunner()
    session_dir = TEST_ROOT / f"cov-{uuid4().hex}"
    cli_args = list(args)
    cli_args.extend([
        "--username",
        "user@example.com",
        "--session-dir",
        str(session_dir),
    ])
    if output_format is not None:
        cli_args.extend(["--format", output_format])
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module,
            "configurable_ssl_verification",
            return_value=nullcontext(),
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
    ):
        return runner.invoke(app, cli_args)


class DuplicateReminders(FakeReminders):
    """Reminders service whose lists return overlapping records."""

    def lists(self) -> list[RemindersList]:
        """Return every configured list."""
        return list(self.list_rows.values())

    def list_reminders(
        self,
        list_id: str,
        include_completed: bool = False,
        results_limit: int = 200,
    ) -> ListRemindersResult:
        """Return every reminder for any list, producing cross-list duplicates."""
        _ = list_id, include_completed
        rows = list(self.reminder_rows.values())[:results_limit]
        return ListRemindersResult(
            reminders=rows,
            alarms={},
            triggers={},
            attachments={},
            hashtags={},
            recurrence_rules={},
        )


class SparseNotes(FakeNotes):
    """Notes service returning a note with no optional metadata."""

    def get(self, note_id: str, *, with_attachments: bool = False) -> Note:
        """Return an entirely empty note."""
        _ = note_id, with_attachments
        return Note(
            id="Note/SPARSE",
            title="Sparse",
            snippet=None,
            modified_at=None,
            folder_id=None,
            folder_name=None,
            is_deleted=False,
            is_locked=False,
            text=None,
            attachments=None,
        )


class FiniteWatchPhotosService(FakePhotosService):
    """Photos service whose watch iterator always terminates quickly."""

    def watch(
        self,
        options: PhotoSyncOptions,
        *,
        interval_seconds: int,
        iterations: int | None = None,
    ) -> Iterator[PhotoSyncResult]:
        """Ignore the requested bound and run a finite two-iteration watch."""
        return FakePhotosService.watch(
            self,
            options,
            interval_seconds=interval_seconds,
            iterations=2,
        )


class InterruptPhotosService(FakePhotosService):
    """Photos service whose sync simulates an operator interrupt."""

    def sync(self, options: PhotoSyncOptions) -> PhotoSyncResult:
        """Raise a keyboard interrupt from the sync call."""
        raise KeyboardInterrupt


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


def test_reminders_helper_branches() -> None:
    """Reminder id, label, and attachment helpers should cover edge branches."""

    assert reminders_cmd._normalize_prefixed_id("", "List") == ""
    assert reminders_cmd._normalize_prefixed_id("List/INBOX", "List") == "List/INBOX"
    assert reminders_cmd._normalize_prefixed_id("INBOX", "List") == "List/INBOX"

    assert reminders_cmd._id_matches("Reminder/A", "") is False
    assert reminders_cmd._id_matches("Reminder/A", "Reminder/A") is True
    assert reminders_cmd._id_matches("Reminder/A", "A") is True

    assert reminders_cmd._proximity_label(None) is None
    assert reminders_cmd._frequency_label(None) is None

    image = ImageAttachment(
        id="Attachment/IMG",
        reminder_id="Reminder/A",
        filename="pic.png",
        file_size=10,
        width=4,
        height=5,
    )
    assert reminders_cmd._attachment_kind(image) == "image"
    assert reminders_cmd._attachment_kind(cast(Any, object())) == "object"


def test_reminders_group_without_subcommand() -> None:
    """Bare reminder subgroup invocations should print help and exit cleanly."""

    runner = CliRunner()
    for group in ("alarm", "hashtag", "attachment", "recurrence"):
        result = runner.invoke(app, ["reminders", group])
        assert result.exit_code == 0
        assert "Usage:" in result.stdout


def test_reminders_core_text_output_paths() -> None:
    """Reminders core commands should render their text and JSON output paths."""

    fake_api = FakeAPI()

    assert invoke(fake_api, "reminders", "lists").exit_code == 0
    assert invoke(fake_api, "reminders", "lists", output_format="json").exit_code == 0
    assert invoke(fake_api, "reminders", "list").exit_code == 0
    assert invoke(fake_api, "reminders", "list", "--limit", "1").exit_code == 0
    assert invoke(fake_api, "reminders", "get", "Reminder/A").exit_code == 0
    assert (
        invoke(
            fake_api, "reminders", "get", "Reminder/A", output_format="json"
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "create",
            "--list-id",
            "INBOX",
            "--title",
            "Text reminder",
        ).exit_code
        == 0
    )
    assert invoke(fake_api, "reminders", "set-status", "Reminder/A").exit_code == 0
    assert (
        invoke(fake_api, "reminders", "snapshot", "--list-id", "INBOX").exit_code == 0
    )
    assert invoke(fake_api, "reminders", "changes").exit_code == 0
    assert invoke(fake_api, "reminders", "sync-cursor").exit_code == 0
    assert invoke(fake_api, "reminders", "delete", "Reminder/A").exit_code == 0


def test_reminders_list_duplicate_and_limit_branches() -> None:
    """The multi-list reminder listing should deduplicate and honor the limit."""

    limit_fake = FakeAPI()
    assert invoke(limit_fake, "reminders", "list", "--limit", "1").exit_code == 0

    duplicate_fake = FakeAPI()
    duplicate_fake.reminders = DuplicateReminders()
    assert invoke(duplicate_fake, "reminders", "list", "--limit", "10").exit_code == 0


def test_reminders_update_text_and_field_branches() -> None:
    """Every reminder update field branch should be exercised."""

    fake_api = FakeAPI()

    updated = invoke(
        fake_api,
        "reminders",
        "update",
        "Reminder/A",
        "--desc",
        "Updated description",
        "--completed",
        "--due-date",
        "2026-04-01T09:00:00Z",
        "--priority",
        "4",
        "--all-day",
        "--time-zone",
        "UTC",
        "--parent-reminder-id",
        "Reminder/B",
    )
    assert updated.exit_code == 0
    assert "Updated Reminder/A" in updated.stdout

    cleared = invoke(fake_api, "reminders", "update", "Reminder/A", "--clear-due-date")
    assert cleared.exit_code == 0


def test_reminders_update_conflicting_options() -> None:
    """Conflicting update options should be rejected before any service call."""

    fake_api = FakeAPI()

    assert (
        invoke(
            fake_api,
            "reminders",
            "update",
            "Reminder/A",
            "--due-date",
            "2026-04-01T09:00:00Z",
            "--clear-due-date",
        ).exit_code
        == 2
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "update",
            "Reminder/A",
            "--time-zone",
            "UTC",
            "--clear-time-zone",
        ).exit_code
        == 2
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "update",
            "Reminder/A",
            "--parent-reminder-id",
            "Reminder/B",
            "--clear-parent-reminder",
        ).exit_code
        == 2
    )


def test_reminders_subgroup_text_output_paths() -> None:
    """Alarm, hashtag, attachment, and recurrence text output paths."""

    fake_api = FakeAPI()

    assert invoke(fake_api, "reminders", "alarm", "list", "Reminder/A").exit_code == 0
    assert (
        invoke(
            fake_api,
            "reminders",
            "alarm",
            "add-location",
            "Reminder/C",
            "--title",
            "Home",
            "--address",
            "1 Infinite Loop",
            "--latitude",
            "49.0",
            "--longitude",
            "6.0",
        ).exit_code
        == 0
    )
    assert invoke(fake_api, "reminders", "hashtag", "list", "Reminder/A").exit_code == 0
    assert (
        invoke(
            fake_api, "reminders", "hashtag", "create", "Reminder/C", "chores"
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "hashtag",
            "update",
            "Reminder/A",
            "ERRANDS",
            "--name",
            "errands",
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api, "reminders", "hashtag", "delete", "Reminder/A", "ERRANDS"
        ).exit_code
        == 0
    )
    assert (
        invoke(fake_api, "reminders", "attachment", "list", "Reminder/A").exit_code == 0
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "attachment",
            "create-url",
            "Reminder/A",
            "--url",
            "https://example.com/x",
        ).exit_code
        == 0
    )
    assert (
        invoke(fake_api, "reminders", "recurrence", "list", "Reminder/A").exit_code == 0
    )
    assert (
        invoke(fake_api, "reminders", "recurrence", "create", "Reminder/A").exit_code
        == 0
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "recurrence",
            "update",
            "Reminder/A",
            "WEEKLY",
            "--first-day-of-week",
            "3",
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api, "reminders", "recurrence", "delete", "Reminder/A", "WEEKLY"
        ).exit_code
        == 0
    )


def test_reminders_attachment_update_branches() -> None:
    """Attachment updates should cover image-style and URL-style attributes."""

    fake_api = FakeAPI()
    image = ImageAttachment(
        id="Attachment/IMG",
        reminder_id="Reminder/A",
        filename="pic.png",
        file_size=10,
        width=4,
        height=5,
    )
    reminder = fake_api.reminders.reminder_rows["Reminder/A"]
    reminder.attachment_ids.append("Attachment/IMG")  # pylint: disable=no-member
    cast(dict[str, Any], fake_api.reminders.attachment_rows)["Attachment/IMG"] = image

    updated = invoke(
        fake_api,
        "reminders",
        "attachment",
        "update",
        "Reminder/A",
        "IMG",
        "--filename",
        "renamed.png",
        "--file-size",
        "99",
        "--width",
        "12",
        "--height",
        "34",
    )
    assert updated.exit_code == 0
    assert "Updated Attachment/IMG" in updated.stdout

    assert (
        invoke(
            fake_api,
            "reminders",
            "attachment",
            "update",
            "Reminder/A",
            "LINK",
            "--uti",
            "public.jpeg",
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api,
            "reminders",
            "attachment",
            "update",
            "Reminder/A",
            "LINK",
            "--url",
            "https://example.com/updated",
        ).exit_code
        == 0
    )
    assert (
        invoke(
            fake_api, "reminders", "attachment", "delete", "Reminder/A", "LINK"
        ).exit_code
        == 0
    )


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def test_notes_text_output_paths() -> None:
    """Notes commands should render their text output tables and details."""

    fake_api = FakeAPI()

    assert invoke(fake_api, "notes", "folders", output_format="json").exit_code == 0
    assert invoke(fake_api, "notes", "folders").exit_code == 0

    assert invoke(fake_api, "notes", "list").exit_code == 0
    assert (
        invoke(fake_api, "notes", "list", "--folder-id", "Folder/NOTES").exit_code == 0
    )
    assert invoke(fake_api, "notes", "list", "--all").exit_code == 0

    assert invoke(fake_api, "notes", "search", "--title", "Daily Plan").exit_code == 0
    assert (
        invoke(fake_api, "notes", "search", "--title-contains", "Meeting").exit_code
        == 0
    )

    detail = invoke(fake_api, "notes", "get", "Note/DAILY", "--with-attachments")
    assert detail.exit_code == 0
    assert "Attachments" in detail.stdout

    assert invoke(fake_api, "notes", "render", "Note/DAILY").exit_code == 0

    exported = invoke(
        fake_api,
        "notes",
        "export",
        "Note/DAILY",
        "--output-dir",
        str(TEST_ROOT / "cov-notes-export"),
        "--assets-dir",
        str(TEST_ROOT / "cov-notes-assets"),
    )
    assert exported.exit_code == 0

    changes = invoke(fake_api, "notes", "changes")
    assert changes.exit_code == 0
    assert "Note Changes" in changes.stdout


def test_notes_validation_errors() -> None:
    """Notes list should reject conflicting or dependent options."""

    fake_api = FakeAPI()

    assert (
        invoke(
            fake_api, "notes", "list", "--folder-id", "Folder/NOTES", "--all"
        ).exit_code
        == 2
    )
    assert invoke(fake_api, "notes", "list", "--since", "cursor").exit_code == 2


def test_notes_sparse_note_text_branches() -> None:
    """A note with no optional metadata should skip the related text sections."""

    fake_api = FakeAPI()
    fake_api.notes = SparseNotes()

    result = invoke(fake_api, "notes", "get", "Note/SPARSE", "--with-attachments")
    assert result.exit_code == 0
    assert "Sparse" in result.stdout


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


def test_photos_album_resolution_errors() -> None:
    """Album resolution failures should surface clean CLI aborts."""

    fake_api = FakeAPI()

    missing_root = invoke(fake_api, "photos", "list", "--album", "Does Not Exist")
    assert missing_root.exit_code == 1
    assert missing_root.exception is not None
    assert (
        missing_root.exception.args[0] == "No album named 'Does Not Exist' was found."
    )

    assert (
        invoke(
            fake_api, "photos", "list", "--shared-stream", "--album", "Nope"
        ).exit_code
        == 1
    )

    missing_album_name = invoke(fake_api, "photos", "list", "--shared-stream")
    assert missing_album_name.exit_code == 1
    assert missing_album_name.exception is not None
    assert (
        missing_album_name.exception.args[0]
        == "The --shared-stream option requires an --album name."
    )


def test_photos_text_output_paths() -> None:
    """Photos commands should render their text output paths."""

    fake_api = FakeAPI()

    assert invoke(fake_api, "photos", "libraries").exit_code == 0
    assert invoke(fake_api, "photos", "list").exit_code == 0
    assert (
        invoke(
            fake_api, "photos", "list", "--shared-stream", "--album", "Vacation 2026"
        ).exit_code
        == 0
    )
    assert invoke(fake_api, "photos", "get", "photo-1").exit_code == 0
    assert invoke(fake_api, "photos", "changes").exit_code == 0
    assert invoke(fake_api, "photos", "sync-cursor").exit_code == 0
    assert (
        invoke(
            fake_api,
            "photos",
            "download",
            "photo-1",
            "--output",
            str(TEST_ROOT / "cov-photo.bin"),
        ).exit_code
        == 0
    )


def test_photos_error_branches() -> None:
    """Photos lookup and download failures should be reported cleanly."""

    fake_api = FakeAPI()

    missing_photo = invoke(fake_api, "photos", "get", "missing-photo")
    assert missing_photo.exit_code == 1
    assert missing_photo.exception is not None
    assert missing_photo.exception.args[0] == "No photo matched 'missing-photo'."

    assert (
        invoke(
            fake_api,
            "photos",
            "download",
            "missing-photo",
            "--output",
            str(TEST_ROOT / "cov-missing.bin"),
        ).exit_code
        == 1
    )

    cast(Any, fake_api.photos.all)._photos[0].download = lambda version="original": None
    null_download = invoke(
        fake_api,
        "photos",
        "download",
        "photo-1",
        "--output",
        str(TEST_ROOT / "cov-null.bin"),
    )
    assert null_download.exit_code == 1
    assert null_download.exception is not None
    assert (
        null_download.exception.args[0]
        == "No data was returned for that photo version."
    )


def test_photos_sync_cursor_without_support() -> None:
    """A library lacking sync_cursor support should fail cleanly."""

    fake_api = FakeAPI()
    libraries = cast(dict[str, Any], fake_api.photos.libraries)
    libraries["nocursor"] = SimpleNamespace(
        scope="private",
        zone_id={"zoneName": "NoCursorZone"},
    )

    result = invoke(fake_api, "photos", "sync-cursor", "--library", "nocursor")
    assert result.exit_code == 1
    assert result.exception is not None
    assert (
        result.exception.args[0]
        == "Photo library 'nocursor' does not support sync cursors."
    )


def test_photos_sync_text_and_skipped_items() -> None:
    """Text sync output should cover downloaded and skipped action items."""

    fake_api = FakeAPI()
    output_dir = TEST_ROOT / f"cov-sync-{uuid4().hex}"
    state_dir = TEST_ROOT / f"cov-sync-state-{uuid4().hex}"

    first = invoke(
        fake_api,
        "photos",
        "sync",
        "--directory",
        str(output_dir),
        "--state-dir",
        str(state_dir),
    )
    assert first.exit_code == 0
    assert "Photo Sync" in first.stdout

    second = invoke(
        fake_api,
        "photos",
        "sync",
        "--directory",
        str(output_dir),
        "--state-dir",
        str(state_dir),
    )
    assert second.exit_code == 0


def test_photos_watch_json_streaming_without_bound() -> None:
    """Unbounded JSON watch should stream each normalized payload."""

    fake_api = FakeAPI()
    fake_api.photos = FiniteWatchPhotosService()
    output_dir = TEST_ROOT / f"cov-watch-json-{uuid4().hex}"

    result = invoke(
        fake_api,
        "photos",
        "watch",
        "--directory",
        str(output_dir),
        output_format="json",
    )
    assert result.exit_code == 0


def test_photos_watch_print_only_filenames() -> None:
    """Watch should print per-run paths when only filenames are requested."""

    fake_api = FakeAPI()
    output_dir = TEST_ROOT / f"cov-watch-names-{uuid4().hex}"
    state_dir = TEST_ROOT / f"cov-watch-names-state-{uuid4().hex}"

    with patch("pyicloud.cli.commands.photos.time.sleep"):
        result = invoke(
            fake_api,
            "photos",
            "watch",
            "--directory",
            str(output_dir),
            "--state-dir",
            str(state_dir),
            "--interval",
            "1",
            "--iterations",
            "2",
            "--only-print-filenames",
        )
    assert result.exit_code == 0
    assert "run 1" in result.stdout
    assert "run 2" in result.stdout


def test_photos_watch_print_only_filenames_single_iteration() -> None:
    """A single-run filename watch should not print a per-run header."""

    fake_api = FakeAPI()
    output_dir = TEST_ROOT / f"cov-watch-names-1-{uuid4().hex}"
    state_dir = TEST_ROOT / f"cov-watch-names-1-state-{uuid4().hex}"

    result = invoke(
        fake_api,
        "photos",
        "watch",
        "--directory",
        str(output_dir),
        "--state-dir",
        str(state_dir),
        "--iterations",
        "1",
        "--only-print-filenames",
    )
    assert result.exit_code == 0
    # The start banner mentions "run 1 of 1"; the per-run header (a bare
    # "run 1" line) must not be emitted for a single iteration.
    assert result.stdout.count("run 1") == 1


def test_photos_render_sync_result_skips_skipped_items() -> None:
    """The text renderer should omit skipped items from the per-file listing."""

    state = SimpleNamespace(console=MagicMock())
    payload: dict[str, Any] = {
        "directory": "/tmp/photos",
        "state_path": "/tmp/state.json",
        "library": "root",
        "albums": [],
        "sync_cursor": "cursor",
        "short_circuited": False,
        "downloaded_count": 1,
        "skipped_count": 1,
        "deleted_count": 0,
        "listed_count": 2,
        "items": [
            {"action": "downloaded", "path": "/tmp/photos/img.jpg"},
            {"action": "skipped", "path": "/tmp/photos/skip.jpg"},
        ],
    }

    photos_cmd._render_photo_sync_result(state, payload, title="Photo Sync")

    printed = "\n".join(
        str(call.args[0]) for call in state.console.print.call_args_list
    )
    assert "downloaded: /tmp/photos/img.jpg" in printed
    assert "skip.jpg" not in printed


def test_photos_watch_handles_keyboard_interrupt() -> None:
    """A keyboard interrupt should exit with code 130."""

    fake_api = FakeAPI()
    fake_api.photos = InterruptPhotosService()
    output_dir = TEST_ROOT / f"cov-watch-interrupt-{uuid4().hex}"

    result = invoke(
        fake_api,
        "photos",
        "watch",
        "--directory",
        str(output_dir),
        "--iterations",
        "1",
    )
    assert result.exit_code == 130


def test_photos_watch_progress_helpers_without_bound() -> None:
    """The watch progress helpers should render unbounded style messages."""

    state = SimpleNamespace(console=MagicMock())

    photos_cmd._print_photo_watch_start(
        state, iteration=1, interval_seconds=5, iterations=None
    )
    photos_cmd._print_photo_watch_wait(
        state, interval_seconds=5, next_iteration=2, iterations=None
    )

    messages = [call.args[0] for call in state.console.print.call_args_list]
    assert any("Starting photo watch run 1 " in message for message in messages)
    assert any("Waiting 5s before photo watch run 2" in message for message in messages)
