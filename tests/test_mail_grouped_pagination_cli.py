"""Grouped-filter cursor CLI output and fail-before-success guarantees."""

from collections.abc import Iterator
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIState
from pyicloud.services.mail import (
    MailError,
    MailFilteredThread,
    MailFolder,
    MailService,
)


@pytest.fixture(name="mail")
def mail_fixture() -> MagicMock:
    """Only metadata enumeration is available to the command."""
    mail = MagicMock(spec=MailService)
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    mail.folders.return_value = [folder]
    mail.iter_filtered_threads.return_value = [
        MailFilteredThread(
            id="<literal>\u001b[31m", folder=folder, filter_by="flagged", timestamp=123
        )
    ]
    return mail


def invoke(mail: MagicMock, *options: str) -> Result:
    """Run the mounted command without real authentication or requests."""
    api = MagicMock()
    api.mail = mail
    api.account_name = "user@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(app, ["mail", "filter-threads", *options])


@pytest.mark.parametrize("mode", ["text", "json"])
@pytest.mark.parametrize("filter_by", ["unread", "flagged"])
def test_cursor_enumeration_modes(mail: MagicMock, mode: str, filter_by: str) -> None:
    """Thread identities are not individual messages, subjects, or text-search hits."""
    result = invoke(
        mail,
        "--folder-id",
        "10",
        "--filter",
        filter_by,
        "--page-size",
        "1",
        "--max-pages",
        "3",
        "--format",
        mode,
    )
    assert result.exit_code == 0, result.exception
    mail.iter_filtered_threads.assert_called_once_with(
        mail.folders.return_value[0], filter_by=filter_by, page_size=1, max_pages=3
    )
    if mode == "json":
        payload = json.loads(result.stdout)
        assert payload["exhausted"] is True
        assert payload["snapshot"] is False
        assert payload["filter_by"] == filter_by
        assert len(payload["threads"]) == 1
    else:
        assert "not a snapshot" in result.stdout
        assert "<literal>" in result.stdout
        assert "\u001b[31m" not in result.stdout
    mail.filtered_threads.assert_not_called()
    mail.body.assert_not_called()
    mail.preview.assert_not_called()
    mail.attachment.assert_not_called()


@pytest.mark.parametrize("mode", ["text", "json"])
def test_late_error_has_no_partial_success_output(mail: MagicMock, mode: str) -> None:
    """Buffer all metadata so a partial scan never appears successful."""
    first = mail.iter_filtered_threads.return_value[0]

    def failed() -> Iterator[MailFilteredThread]:
        yield first
        raise MailError("Enumeration is incomplete")

    mail.iter_filtered_threads.return_value = failed()
    result = invoke(mail, "--folder-id", "10", "--filter", "flagged", "--format", mode)
    assert result.exit_code != 0
    assert "<literal>" not in result.stdout
    assert '"exhausted": true' not in result.stdout


def test_empty_scan_and_defaults(mail: MagicMock) -> None:
    """Only a successful empty iterator reports terminal exhaustion."""
    mail.iter_filtered_threads.return_value = []
    result = invoke(
        mail, "--folder-id", "10", "--filter", "flagged", "--format", "json"
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["threads"] == []
    mail.iter_filtered_threads.assert_called_once_with(
        mail.folders.return_value[0], filter_by="flagged", page_size=50, max_pages=10000
    )


def test_unknown_folder_fails_without_scan(mail: MagicMock) -> None:
    """Never guess folder names or silently scan a different mailbox."""
    result = invoke(mail, "--folder-id", "unknown", "--filter", "flagged")
    assert result.exit_code != 0
    mail.iter_filtered_threads.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--filter", "starred"],
        ["--filter", "flagged", "--page-size", "0"],
        ["--filter", "flagged", "--page-size", "1001"],
        ["--filter", "flagged", "--max-pages", "0"],
    ],
)
def test_invalid_options_never_authenticate(options: list[str]) -> None:
    """Reject unsupported filters and limits before opening an account."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app, ["mail", "filter-threads", "--folder-id", "10", *options]
        )
    assert result.exit_code != 0
    get_api.assert_not_called()


def test_help_is_offline() -> None:
    """The command documents its lightweight scope without authentication."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", "filter-threads", "--help"])
    assert result.exit_code == 0
    assert "digests" in result.stdout
    get_api.assert_not_called()
