"""Offline CLI checks for explicit multi-folder search and private JSON export."""

from collections.abc import Iterator
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import (
    MailBody,
    MailError,
    MailFolder,
    MailFolderSearchPage,
    MailMessage,
    MailMessageExport,
    MailMultiSearchPage,
    MailPreview,
    MailSearchHit,
    MailService,
    MailThreadDigest,
    MailThreadFilterPage,
)
from pyicloud.services.mail.models import MailBodyPart, MailMessagePage


@pytest.fixture(name="mail")
def mail_fixture() -> MagicMock:
    """Synthetic bounded metadata and untrusted body data; no HTTP calls."""
    mail = MagicMock(spec=MailService)
    folders = [
        MailFolder(identifier="10", name="[bold]INBOX[/bold]"),
        MailFolder(identifier="20", name="Sent"),
    ]
    mail.folders.return_value = folders
    message = MailMessage.model_validate({
        "id": "message-7",
        "uid": 7,
        "mailbox": {"id": "10"},
        "folder": folders[0],
        "subject": "private subject",
    })
    mail.messages.return_value = MailMessagePage(messages=[message], limit=50)
    mail.preview.return_value = MailPreview(
        message_id=message.id,
        folder=folders[0],
        uid=7,
        text="<b>Untrusted snippet</b>\u001b",
    )
    mail.filtered_threads.return_value = MailThreadFilterPage(
        folder=folders[0],
        filter_by="unread",
        limit=50,
        results=[
            MailThreadDigest(
                threadId="filtered-thread",
                jsonType="threadDigest",
                timestamp=1700000000,
                subject="[bold]Filtered[/bold]",
                count=2,
            )
        ],
    )
    hit = MailSearchHit(identifier="same-thread", subject="[bold]Subject[/bold]")
    mail.search_folders.return_value = MailMultiSearchPage(
        pages=[
            MailFolderSearchPage(folder=f, results=[hit], limit=50) for f in folders
        ],
        limit_per_folder=50,
    )
    body = MailBody(
        guid="message:INBOX/7",
        longHeader="Subject: private subject",
        contentType="text/html",
        parts=[MailBodyPart(guid="part-1", content="<script>private</script>")],
    )
    mail.export_message.side_effect = lambda selected, include_body=False: (
        MailMessageExport(message=selected, body=body if include_body else None)
    )
    return mail


@pytest.fixture(name="output")
def output_fixture() -> Iterator[Path]:
    """Create files only inside the sanctioned filesystem sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mail-export-", dir=root) as directory:
        try:
            yield Path(directory) / "selected.json"
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def invoke(mail: MagicMock, *args: str) -> Result:
    """Use the actual mounted commands with an isolated account context."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = mail
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(app, ["mail", *args])


@pytest.mark.parametrize("mode", ["text", "json"])
def test_search_folders_is_explicit_scoped_and_incomplete(
    mail: MagicMock, mode: str
) -> None:
    """Do not flatten thread identities or render untrusted table markup."""
    result = invoke(
        mail,
        "search-folders",
        "example",
        "--folder-id",
        "10",
        "--folder-id",
        "20",
        "--field",
        "subject",
        "--limit-per-folder",
        "3",
        "--format",
        mode,
    )
    assert result.exit_code == 0, result.exception
    mail.search_folders.assert_called_once_with(
        mail.folders.return_value,
        "example",
        field="subject",
        limit_per_folder=3,
    )
    mail.body.assert_not_called()
    if mode == "json":
        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert [p["folder"]["id"] for p in payload["pages"]] == ["10", "20"]
    else:
        assert "[bold]" in result.stdout
        assert "Incomplete per-folder" in result.stdout


def test_search_one_selected_folder_uses_defaults(mail: MagicMock) -> None:
    """Never silently search folders the caller did not select."""
    result = invoke(mail, "search-folders", "example", "--folder-id", "20")
    assert result.exit_code == 0, result.exception
    mail.search_folders.assert_called_once_with(
        [mail.folders.return_value[1]],
        "example",
        field="all",
        limit_per_folder=50,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["example"],
        [" ", "--folder-id", "10"],
        ["example", "--folder-id", " "],
        ["example", "--folder-id", "10", "--folder-id", "10"],
        ["example", "--folder-id", "10", "--limit-per-folder", "0"],
        ["example", "--folder-id", "10", "--limit-per-folder", "1001"],
        ["example", "--folder-id", "10", "--field", "invalid"],
        ["example", *[arg for i in range(21) for arg in ["--folder-id", str(i)]]],
    ],
)
def test_invalid_multi_search_fails_before_authentication(args: list[str]) -> None:
    """Reject ambiguous or excessive folder selections before any service calls."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", "search-folders", *args])
    assert result.exit_code != 0
    get_api.assert_not_called()


def test_unknown_folder_prevents_all_searches(mail: MagicMock) -> None:
    """An invalid later folder must not cause partial search output."""
    result = invoke(
        mail, "search-folders", "example", "--folder-id", "10", "--folder-id", "missing"
    )
    assert isinstance(result.exception, CLIAbort)
    mail.search_folders.assert_not_called()
    assert result.stdout == ""


def test_search_failure_has_no_success_output(mail: MagicMock) -> None:
    """Never print earlier folder results when a later request fails."""
    mail.search_folders.side_effect = MailError("offline")
    result = invoke(mail, "search-folders", "example", "--folder-id", "10")
    assert isinstance(result.exception, CLIAbort)
    assert result.stdout == ""


@pytest.mark.parametrize("include_body", [False, True])
@pytest.mark.parametrize("mode", ["text", "json"])
def test_export_is_private_explicit_and_unrendered(
    mail: MagicMock, output: Path, include_body: bool, mode: str
) -> None:
    """Only an explicit file receives content; console output contains no body."""
    result = invoke(
        mail,
        "export",
        "message-7",
        "--folder-id",
        "10",
        "--output",
        str(output),
        "--format",
        mode,
        *(["--include-body"] if include_body else []),
    )
    assert result.exit_code == 0, result.exception
    mail.export_message.assert_called_once_with(
        mail.messages.return_value.messages[0],
        include_body=include_body,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["message"]["subject"] == "private subject"
    if include_body:
        assert payload["body"]["parts"][0]["content"] == "<script>private</script>"
    else:
        assert payload["body"] is None
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(output.parent.iterdir()) == [output]
    assert "private subject" not in result.stdout
    assert "<script>" not in result.stdout
    if mode == "json":
        assert json.loads(result.stdout)["size"] == len(output.read_bytes())
    else:
        assert str(output) in result.stdout.replace("\n", "")


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_export_existing_destination_fails_before_authentication(
    output: Path, kind: str
) -> None:
    """An existing file or dangling symlink is never overwritten."""
    if kind == "file":
        output.write_bytes(b"keep")
    else:
        output.symlink_to(output.parent / "absent")
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "export",
                "message-7",
                "--folder-id",
                "10",
                "--output",
                str(output),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    get_api.assert_not_called()
    if kind == "file":
        assert output.read_bytes() == b"keep"
    else:
        assert output.is_symlink()


def test_failed_body_export_writes_nothing(mail: MagicMock, output: Path) -> None:
    """A requested body failure cannot silently produce a metadata-only file."""
    mail.export_message.side_effect = MailError("body unavailable")
    result = invoke(
        mail,
        "export",
        "message-7",
        "--folder-id",
        "10",
        "--output",
        str(output),
        "--include-body",
    )
    assert isinstance(result.exception, CLIAbort)
    assert not list(output.parent.iterdir())


@pytest.mark.parametrize("operation", ["open", "link"])
def test_export_local_write_failure_closes_and_cleans(
    mail: MagicMock, output: Path, operation: str
) -> None:
    """Do not leak a staging file or descriptor when output cannot be published."""
    target = "os.fdopen" if operation == "open" else "os.link"
    with (
        patch(
            f"pyicloud.cli.commands.mail.{target}", side_effect=OSError("unavailable")
        ),
        patch("pyicloud.cli.commands.mail.os.close", wraps=os.close) as close,
    ):
        result = invoke(
            mail, "export", "message-7", "--folder-id", "10", "--output", str(output)
        )
    assert isinstance(result.exception, CLIAbort)
    assert not list(output.parent.iterdir())
    assert result.stdout == ""
    if operation == "open":
        close.assert_called_once()


def test_export_racing_destination_is_preserved(mail: MagicMock, output: Path) -> None:
    """Publication must not replace a file created after the initial existence check."""
    link = os.link

    def compete(source: str, destination: Path) -> None:
        output.write_bytes(b"keep")
        link(source, destination)

    with patch("pyicloud.cli.commands.mail.os.link", side_effect=compete):
        result = invoke(
            mail, "export", "message-7", "--folder-id", "10", "--output", str(output)
        )
    assert isinstance(result.exception, CLIAbort)
    assert output.read_bytes() == b"keep"
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("command", ["search-folders", "export", "preview", "filter"])
def test_help_does_not_authenticate(command: str) -> None:
    """New commands retain offline help like the existing Mail leaves."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", command, "--help"])
    assert result.exit_code == 0
    get_api.assert_not_called()


@pytest.mark.parametrize("mode", ["text", "json"])
def test_preview_outputs_unrendered_json_without_body_fallback(
    mail: MagicMock, mode: str
) -> None:
    """A snippet is literal data even when its content resembles HTML or ANSI."""
    result = invoke(mail, "preview", "message-7", "--folder-id", "10", "--format", mode)
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["text"] == "<b>Untrusted snippet</b>\u001b"
    assert "\u001b" not in result.stdout
    mail.preview.assert_called_once_with(mail.messages.return_value.messages[0])
    mail.body.assert_not_called()


def test_missing_preview_never_opens_a_body(mail: MagicMock) -> None:
    """Missing preview data is an explicit error, never an implicit body read."""
    mail.preview.return_value = None
    result = invoke(mail, "preview", "message-7", "--folder-id", "10")
    assert isinstance(result.exception, CLIAbort)
    assert "no preview" in str(result.exception)
    mail.body.assert_not_called()


@pytest.mark.parametrize("mode", ["text", "json"])
@pytest.mark.parametrize("filter_by", ["unread", "flagged"])
def test_filter_results_are_bounded_thread_groups(
    mail: MagicMock, mode: str, filter_by: str
) -> None:
    """The CLI passes a single browser filter and warns against message semantics."""
    result = invoke(
        mail,
        "filter",
        "--folder-id",
        "10",
        "--filter",
        filter_by,
        "--limit",
        "3",
        "--format",
        mode,
    )
    assert result.exit_code == 0, result.exception
    mail.filtered_threads.assert_called_once_with(
        mail.folders.return_value[0], filter_by, limit=3
    )
    if mode == "json":
        assert json.loads(result.stdout)["complete"] is False
    else:
        assert "[bold]" in result.stdout
        assert "Incomplete thread groups" in result.stdout
    mail.body.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["preview", "message-7", "--folder-id", "10", "--limit", "0"],
        ["filter", "--folder-id", "10"],
        ["filter", "--folder-id", "10", "--filter", "combined"],
        ["filter", "--folder-id", "10", "--filter", "unread", "--limit", "1001"],
    ],
)
def test_invalid_preview_filter_options_do_not_authenticate(args: list[str]) -> None:
    """Validate required filter values and bounds before resolving an account."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", *args])
    assert result.exit_code != 0
    get_api.assert_not_called()
