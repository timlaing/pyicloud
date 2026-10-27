"""Offline end-to-end CLI tests using real sync, SQLite, and metadata adapters."""

from collections.abc import Iterator
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory, gettempdir
from typing import Any, NamedTuple
from unittest.mock import MagicMock, patch

from click import unstyle
import pytest
from requests import ReadTimeout, Response
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.exceptions import (
    PyiCloudAuthRequiredException,
    PyiCloudConnectionException,
)
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.services.mail._sync_state import InventoryMessage, StoredCheckpoint
from pyicloud.services.mail._sync_storage import open_inventory
from tests.fixture_loader import load_fixture
from tests.test_mail_sync_reader import envelope

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only sync storage")


class Case(NamedTuple):
    """Use the mounted CLI and real service with offline transport and inventory."""

    api: MagicMock
    service: MailService
    folder: MailFolder
    response: MagicMock
    session: MagicMock
    inventory: MagicMock
    exact: MagicMock


@pytest.fixture(name="case")
def case_fixture() -> Iterator[Case]:
    """Never contact iCloud or change any message during CLI tests."""
    api, session, response = MagicMock(), MagicMock(), MagicMock(spec=Response)
    api.account_name = "synthetic@example.test"
    session.service = api
    response.status_code = 200
    response.json.return_value = envelope()
    session.post.return_value = response
    service = MailService("https://example.test", session, {"dsid": "synthetic"})
    api.mail = service
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=123)
    with (
        patch.object(service, "folders", return_value=[folder]),
        patch.object(service, "iter_messages") as inventory,
        patch.object(MailService, "_lookup_message") as exact,
    ):
        current = Case(api, service, folder, response, session, inventory, exact)
        inventory.side_effect = lambda *_args, **_kwargs: iter([metadata(current)])
        yield current


def metadata(case: Case, **changes: Any) -> MailMessage:
    """Remote subjects and headers must never reach local sync state or output."""
    return MailMessage.model_validate({
        "identifier": "synthetic-message",
        "uid": 7,
        "mboxRef": {"id": case.folder.id},
        "folder": case.folder,
        "appleThrid": "synthetic-thread",
        "flags": 1,
        "subject": "private subject",
        **changes,
    })


@pytest.fixture(name="directory")
def directory_fixture() -> Iterator[Path]:
    """Use a real private on-disk inventory confined to the filesystem sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as temporary:
        directory = Path(temporary) / "state"
        try:
            yield directory
        finally:
            if directory.exists():
                for child in directory.iterdir():
                    child.unlink()
                directory.rmdir()


def invoke(case: Case, directory: Path, *args: str) -> Result:
    """Run the real command without opening live session files."""
    with (
        patch.object(CLIState, "get_api", return_value=case.api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(
            app,
            [
                "mail",
                "sync",
                "--folder-id",
                case.folder.id,
                "--state-dir",
                str(directory),
                *args,
            ],
        )


def snapshot(
    directory: Path,
) -> tuple[StoredCheckpoint | None, tuple[InventoryMessage, ...]]:
    """Inspect committed state after the CLI has closed and released its lock."""
    with open_inventory(directory) as state:
        return state.read_snapshot()


@pytest.mark.parametrize("mode", ["json", "text"])
def test_initial_sync_is_private_content_free_and_read_only(
    case: Case, directory: Path, mode: str
) -> None:
    """A first run creates one complete minimal inventory and reports only a summary."""
    result = invoke(
        case, directory, "--format", mode, "--page-size", "2", "--max-pages", "5"
    )
    assert result.exit_code == 0, result.exception
    saved, rows = snapshot(directory)
    assert saved is not None
    assert saved.revision == 1
    assert len(rows) == 1
    assert rows[0].uid == 7
    case.inventory.assert_called_once_with(case.folder, page_size=2, max_pages=5)
    assert len(case.session.post.call_args_list) == 2
    assert all(
        call.args == ("https://example.test/mailws2/v1/event/list",)
        for call in case.session.post.call_args_list
    )
    assert not case.session.get.called
    assert "private subject" not in result.stdout
    assert "synthetic@example.test" not in result.stdout
    assert "synthetic-thread" not in result.stdout
    assert "synthetic-message" not in result.stdout
    assert b"private subject" not in (directory / "inventory.sqlite3").read_bytes()
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "inventory.sqlite3").stat().st_mode & 0o777 == 0o600
    if mode == "json":
        assert json.loads(result.stdout) == {
            "format_version": 1,
            "folder_id": "10",
            "state_path": str(directory.resolve() / "inventory.sqlite3"),
            "status": "baseline",
            "revision": 1,
            "message_count": 1,
            "metadata_only": True,
            "read_only": True,
            "atomic_snapshot": False,
        }
    else:
        assert "baseline" in result.stdout
        assert "Not an atomic snapshot or backup" in result.stdout


def test_flag_free_invalidation_removes_only_verified_local_membership(
    case: Case, directory: Path
) -> None:
    """The CLI consumes observed NIL invalidations without guessing remote flags."""
    assert invoke(case, directory).exit_code == 0
    wire = load_fixture("mail", "sync_thread_invalidation_response.json")
    final = envelope()
    final["sessionHeaders"].update(modseq=101, threadmodseq=201)
    case.response.json.side_effect = [wire, final]
    case.exact.return_value = None
    with patch.object(
        MailService, "_iter_thread_messages", return_value=iter([])
    ) as thread:
        result = invoke(case, directory, "--format", "json")
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["message_count"] == 0
    assert not snapshot(directory)[1]
    assert thread.call_count == 1
    assert thread.call_args.args == (case.service, case.folder, "synthetic-thread")
    assert thread.call_args.kwargs["page_size"] == 50
    assert thread.call_args.kwargs["max_pages"] == 10000
    assert callable(thread.call_args.kwargs["check_folder"])
    assert case.exact.call_count == 1
    assert case.exact.call_args.args == (case.service, case.folder, "uid", 7)
    assert callable(case.exact.call_args.kwargs["check_folder"])


def test_restart_replays_saved_checkpoint_without_rescan(
    case: Case, directory: Path
) -> None:
    """A no-op retains the local revision, with no baseline or content reads."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    case.inventory.reset_mock()
    case.session.post.reset_mock()
    result = invoke(case, directory, "--format", "json")
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["status"] == "unchanged"
    assert snapshot(directory) == saved
    case.inventory.assert_not_called()
    case.session.post.assert_called_once()
    headers = case.session.post.call_args.kwargs["json"]["sessionHeaders"]
    assert headers["modseq"] == 100
    assert headers["threadmodseq"] == 200


def test_flag_change_updates_only_local_metadata(case: Case, directory: Path) -> None:
    """Numeric flags are read authoritatively, never written back to Apple."""
    assert invoke(case, directory).exit_code == 0
    wire = envelope(events=["* 1 FETCH (UID 7 MODSEQ (101) FLAGS (\\Seen))"])
    wire["sessionHeaders"]["modseq"] = 101
    final = envelope()
    final["sessionHeaders"]["modseq"] = 101
    case.response.json.side_effect = [wire, final]
    case.exact.return_value = metadata(case, flags=2)
    case.inventory.reset_mock()
    result = invoke(case, directory, "--format", "json")
    assert result.exit_code == 0, result.exception
    payload = json.loads(result.stdout)
    assert payload["status"] == "updated"
    assert payload["revision"] == 2
    assert snapshot(directory)[1][0].flags == 2
    case.inventory.assert_not_called()
    assert case.exact.call_args.args == (case.service, case.folder, "uid", 7)
    assert callable(case.exact.call_args.kwargs["check_folder"])


@pytest.mark.parametrize(
    "kind,flag,hint",
    [
        ("expiry", "--rebuild-expired", "--rebuild-expired"),
        ("count", "--allow-inventory-fallback", "--allow-inventory-fallback"),
    ],
)
def test_recovery_requires_matching_explicit_permission(
    case: Case, directory: Path, kind: str, flag: str, hint: str
) -> None:
    """Only the matching opt-in permits a complete replacement scan."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    wire: dict[str, Any] = (
        {"errorCode": "MODSEQ_TOO_OLD"}
        if kind == "expiry"
        else envelope(events=["* 2 EXISTS"])
    )
    if kind == "count":
        wire["sessionHeaders"]["modseq"] = 101
    case.response.json.return_value = wire
    case.inventory.reset_mock()
    case.session.post.reset_mock()
    result = invoke(case, directory)
    assert isinstance(result.exception, CLIAbort)
    assert hint in str(result.exception)
    assert not result.stdout
    assert snapshot(directory) == saved
    case.inventory.assert_not_called()
    case.session.post.assert_called_once()
    fresh = envelope()
    if kind == "count":
        fresh["sessionHeaders"]["modseq"] = 101
    case.response.json.side_effect = [wire, fresh, fresh]
    case.inventory.side_effect = lambda *_args, **_kwargs: iter([])
    result = invoke(case, directory, flag, "--format", "json")
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["status"] == "updated"
    assert not snapshot(directory)[1]


def test_unknown_events_never_trigger_recovery(case: Case, directory: Path) -> None:
    """Both opt-ins still reject unsupported protocol instead of discarding history."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    case.response.json.return_value = envelope(events=["unsupported private event"])
    case.inventory.reset_mock()
    result = invoke(case, directory, "--rebuild-expired", "--allow-inventory-fallback")
    assert isinstance(result.exception, CLIAbort)
    assert "fresh --state-dir" in str(result.exception)
    assert "Preserve the old directory" in str(result.exception)
    assert "unsupported private event" not in str(result.exception)
    assert not result.stdout
    assert snapshot(directory) == saved
    case.inventory.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--folder-id", "10"],
        ["--state-dir", "unused"],
        ["--folder-id", " ", "--state-dir", "unused"],
        *[
            ["--folder-id", "10", "--state-dir", "unused", option, value]
            for option, value in [
                ("--page-size", "0"),
                ("--page-size", "1001"),
                ("--max-pages", "0"),
                ("--max-messages", "0"),
                ("--max-lookups", "0"),
                ("--max-attempts", "0"),
                ("--state-timeout", "0"),
                ("--state-timeout", "61"),
                ("--state-timeout", "nan"),
            ]
        ],
    ],
)
def test_invalid_options_never_authenticate(args: list[str], directory: Path) -> None:
    """Require scope, explicit storage, and finite resource controls before I/O."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "sync",
                *[str(directory) if arg == "unused" else arg for arg in args],
            ],
        )
    assert result.exit_code != 0
    get_api.assert_not_called()


@pytest.mark.parametrize("color", [False, True])
def test_help_is_offline(color: bool) -> None:
    """The experimental boundary and recovery options must be discoverable offline."""
    with (
        patch.object(CLIState, "get_api") as get_api,
        patch("typer.rich_utils.FORCE_TERMINAL", color),
        patch("typer.rich_utils.COLOR_SYSTEM", "standard" if color else None),
    ):
        result = CliRunner().invoke(
            app,
            ["mail", "sync", "--help"],
            color=color,
        )
    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert "POSIX-only" in output
    assert "--state-dir" in output
    assert "--rebuild-expired" in output
    if color:
        assert "\x1b[" in result.stdout
    get_api.assert_not_called()


def test_unsafe_storage_fails_before_authentication(
    case: Case, directory: Path
) -> None:
    """Do not sign in for an unsafe destination or silently repair permissions."""
    directory.mkdir(mode=0o700)
    directory.chmod(0o755)
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app, ["mail", "sync", "--folder-id", "10", "--state-dir", str(directory)]
        )
    assert isinstance(result.exception, CLIAbort)
    get_api.assert_not_called()
    case.session.post.assert_not_called()


def test_locked_storage_fails_before_authentication(directory: Path) -> None:
    """Another worker's lock is a clear error, not a second concurrent scan."""
    with open_inventory(directory), patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app, ["mail", "sync", "--folder-id", "10", "--state-dir", str(directory)]
        )
    assert isinstance(result.exception, CLIAbort)
    assert "in use" in str(result.exception)
    get_api.assert_not_called()


def test_corrupt_storage_is_not_reset(case: Case, directory: Path) -> None:
    """Translate SQLite failures without telling users to discard their saved state."""
    directory.mkdir(mode=0o700)
    database = directory / "inventory.sqlite3"
    database.touch(mode=0o600)
    database.write_bytes(b"corrupt synthetic database")
    result = invoke(case, directory, "--rebuild-expired")
    assert isinstance(result.exception, CLIAbort)
    assert "preserve the database" in str(result.exception)
    assert not result.stdout
    assert database.read_bytes() == b"corrupt synthetic database"
    case.session.post.assert_not_called()


@pytest.mark.parametrize(
    "failure,hint",
    [
        (
            PyiCloudAuthRequiredException(
                "synthetic@example.test", MagicMock(spec=Response)
            ),
            "auth login",
        ),
        (PyiCloudConnectionException("offline"), "could not reach"),
        (ReadTimeout("private signed URL must not appear"), "checking connectivity"),
    ],
)
def test_transport_failures_preserve_state_and_auth_hints(
    case: Case, directory: Path, failure: Exception, hint: str
) -> None:
    """Recovery never hides failed authentication or an unreachable service."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    case.session.post.side_effect = failure
    result = invoke(case, directory, "--rebuild-expired")
    assert isinstance(result.exception, CLIAbort)
    assert hint in str(result.exception)
    assert "private signed URL" not in str(result.exception)
    assert not result.stdout
    assert snapshot(directory) == saved


def test_partial_scan_never_prints_success_and_can_restart(
    case: Case, directory: Path
) -> None:
    """A failed first scan leaves no checkpoint; a later complete retry is permitted."""

    def interrupted(*_args: Any, **_kwargs: Any) -> Iterator[MailMessage]:
        yield metadata(case)
        raise MailError("incomplete metadata scan")

    case.inventory.side_effect = interrupted
    result = invoke(case, directory, "--format", "json")
    assert isinstance(result.exception, CLIAbort)
    assert not result.stdout
    assert snapshot(directory) == (None, ())
    case.inventory.side_effect = lambda *_args, **_kwargs: iter([metadata(case)])
    assert invoke(case, directory).exit_code == 0


def test_failed_rebuild_retains_previously_committed_rows(
    case: Case, directory: Path
) -> None:
    """Permission to rebuild is not permission to publish an interrupted inventory."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    case.response.json.side_effect = [{"errorCode": "MODSEQ_TOO_OLD"}, envelope()]
    case.inventory.side_effect = MailError("incomplete rescan")
    result = invoke(case, directory, "--rebuild-expired")
    assert isinstance(result.exception, CLIAbort)
    assert not result.stdout
    assert snapshot(directory) == saved


def test_message_budget_is_enforced_without_partial_commit(
    case: Case, directory: Path
) -> None:
    """CLI bounds reach the real scan rather than only limiting summary output."""
    case.inventory.side_effect = lambda *_args, **_kwargs: iter([
        metadata(case),
        metadata(case, uid=8, identifier="second"),
    ])
    result = invoke(case, directory, "--max-messages", "1")
    assert isinstance(result.exception, CLIAbort)
    assert "budget" in str(result.exception)
    assert snapshot(directory) == (None, ())


def test_state_cannot_switch_account(case: Case, directory: Path) -> None:
    """Recovery cannot switch the account behind a stored checkpoint."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    case.api.account_name = "another@example.test"
    case.session.post.reset_mock()
    result = invoke(case, directory, "--rebuild-expired")
    assert isinstance(result.exception, CLIAbort)
    assert snapshot(directory) == saved
    case.session.post.assert_not_called()


def test_missing_uid_validity_never_polls(case: Case, directory: Path) -> None:
    """Sync cannot fall back to an unchecked namespace or newest-N listing."""
    folder = MailFolder(identifier="10", name="INBOX")
    with patch.object(case.service, "folders", return_value=[folder]):
        result = invoke(case, directory)
    assert isinstance(result.exception, CLIAbort)
    case.session.post.assert_not_called()
    assert snapshot(directory) == (None, ())


def test_sqlite_apply_failure_is_translated(case: Case, directory: Path) -> None:
    """An operation-time storage failure closes resources and preserves prior rows."""
    assert invoke(case, directory).exit_code == 0
    saved = snapshot(directory)
    with patch(
        "pyicloud.cli.commands.mail.refresh_inventory",
        side_effect=sqlite3.OperationalError("synthetic failure"),
    ):
        result = invoke(case, directory)
    assert isinstance(result.exception, CLIAbort)
    assert "state is unavailable" in str(result.exception)
    assert snapshot(directory) == saved
