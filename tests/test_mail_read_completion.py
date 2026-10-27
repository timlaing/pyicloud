"""Exact metadata selection across the supported Mail read commands."""

from collections.abc import Iterator
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIState
from pyicloud.services.mail import (
    MailError,
    MailFolder,
    MailMessage,
    MailMessageExport,
    MailMessagePage,
    MailPreview,
    MailService,
)


@pytest.fixture(name="mail")
def mail_fixture() -> MagicMock:
    """Metadata for an older message, with no body or remote transport."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    message = MailMessage.model_validate({
        "identifier": "older-message",
        "uid": 7,
        "mboxRef": {"id": folder.id},
        "folder": folder,
        "subject": "<untrusted>\u001b",
    })
    mail = MagicMock(spec=MailService)
    mail.folders.return_value = [folder]
    mail.message.return_value = message
    mail.message_by_uid.return_value = message
    mail.messages.return_value = MailMessagePage(messages=[message], limit=10)
    mail.preview.return_value = MailPreview(
        message_id=message.id, folder=folder, uid=7, text="Synthetic preview"
    )
    mail.export_message.return_value = MailMessageExport(message=message)
    return mail


@pytest.fixture(name="output")
def output_fixture() -> Iterator[Path]:
    """Keep explicit export output in the sanctioned test sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        try:
            yield Path(directory) / "selected.json"
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def invoke(mail: MagicMock, *args: str) -> Result:
    """Invoke mounted CLI commands without opening a real account."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = mail
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(app, ["mail", *args])


@pytest.mark.parametrize("selector", [["older-message"], ["--uid", "7"]])
@pytest.mark.parametrize("mode", ["text", "json"])
def test_get_is_exact_metadata_only(
    mail: MagicMock, selector: list[str], mode: str
) -> None:
    """Neither lookup mode lists messages or retrieves private content."""
    result = invoke(mail, "get", *selector, "--folder-id", "10", "--format", mode)
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["id"] == "older-message"
    assert "\u001b" not in result.stdout
    folder = mail.folders.return_value[0]
    if selector[0] == "--uid":
        mail.message_by_uid.assert_called_once_with(folder, 7)
        mail.message.assert_not_called()
    else:
        mail.message.assert_called_once_with(folder, "older-message")
        mail.message_by_uid.assert_not_called()
    mail.messages.assert_not_called()
    mail.body.assert_not_called()
    mail.attachment.assert_not_called()
    mail.preview.assert_not_called()


@pytest.mark.parametrize(
    "selector",
    [[], [" "], ["older-message", "--uid", "7"], ["--uid", "0"], ["--uid", "bad"]],
)
def test_get_invalid_selector_never_authenticates(selector: list[str]) -> None:
    """Require exactly one valid selector before opening an account."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app, ["mail", "get", *selector, "--folder-id", "10"]
        )
    assert result.exit_code != 0
    get_api.assert_not_called()


def test_get_help_is_offline() -> None:
    """Help remains usable without authentication or any service access."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", "get", "--help"])
    assert result.exit_code == 0
    get_api.assert_not_called()


@pytest.mark.parametrize("selector", [["older-message"], ["--uid", "7"]])
def test_get_missing_message_has_no_success_output(
    mail: MagicMock, selector: list[str]
) -> None:
    """Missing messages are errors rather than empty or broadened listings."""
    mail.message.return_value = None
    mail.message_by_uid.return_value = None
    result = invoke(mail, "get", *selector, "--folder-id", "10", "--format", "json")
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "not found" in str(result.exception)
    mail.messages.assert_not_called()


def test_get_namespace_error_is_translated(mail: MagicMock) -> None:
    """Mailbox failures keep their readable error and never trigger a fallback."""
    mail.message.side_effect = MailError("Mailbox identity changed")
    result = invoke(mail, "get", "older-message", "--folder-id", "10")
    assert result.exit_code != 0
    assert "identity changed" in str(result.exception)
    mail.messages.assert_not_called()


@pytest.mark.parametrize("command", ["preview", "export"])
@pytest.mark.parametrize("bounded", [False, True])
def test_preview_export_select_consistently(
    mail: MagicMock, output: Path, command: str, bounded: bool
) -> None:
    """Defaults find older IDs exactly; explicit limits retain bounded selection."""
    args = [command, "older-message", "--folder-id", "10", "--format", "json"]
    if command == "export":
        args.extend(["--output", str(output)])
    if bounded:
        args.extend(["--limit", "10"])
    result = invoke(mail, *args)
    assert result.exit_code == 0, result.exception
    if bounded:
        mail.messages.assert_called_once_with(mail.folders.return_value[0], limit=10)
        mail.message.assert_not_called()
    else:
        mail.message.assert_called_once_with(
            mail.folders.return_value[0], "older-message"
        )
        mail.messages.assert_not_called()
    mail.body.assert_not_called()
    mail.attachment.assert_not_called()


@pytest.mark.parametrize("command", ["preview", "export"])
def test_failed_exact_selection_never_reads_or_writes(
    mail: MagicMock, output: Path, command: str
) -> None:
    """A missing older ID cannot open a different message or create an export."""
    mail.message.return_value = None
    args = [command, "missing", "--folder-id", "10"]
    if command == "export":
        args.extend(["--output", str(output)])
    result = invoke(mail, *args)
    assert result.exit_code != 0
    assert not output.exists()
    mail.messages.assert_not_called()
    mail.preview.assert_not_called()
    mail.export_message.assert_not_called()


@pytest.mark.parametrize("command", ["preview", "export"])
def test_unknown_namespace_requires_explicit_legacy_selection(
    mail: MagicMock, output: Path, command: str
) -> None:
    """Missing UID validity never silently changes exact selection into a scan."""
    mail.folders.return_value[0] = mail.folders.return_value[0].model_copy(
        update={"uid_validity": None}
    )
    args = [command, "older-message", "--folder-id", "10"]
    if command == "export":
        args.extend(["--output", str(output)])
    result = invoke(mail, *args)
    assert result.exit_code != 0
    assert "--limit N" in str(result.exception)
    mail.message.assert_not_called()
    mail.messages.assert_not_called()
