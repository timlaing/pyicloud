"""Offline coverage of the experimental read-only Mail CLI."""

from collections.abc import Iterator
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.mail import (
    MailError,
    MailFolder,
    MailMessage,
    MailPart,
    MailService,
)
from pyicloud.services.mail.models import (
    MailBody,
    MailBodyPart,
    MailMessagePage,
    MailSearchHit,
    MailSearchPage,
)


@pytest.fixture(name="mail")
def mail_fixture() -> MagicMock:
    """A folder-bound message and thread hit; no transport is available."""
    folder = MailFolder(
        identifier="folder-1", name="INBOX", messageCount=1, uidValidity=123
    )
    message = MailMessage.model_validate({
        "id": "message-1",
        "uid": 7,
        "mailbox": {"id": folder.id},
        "folder": folder,
        "subject": "[bold]Literal subject[/bold]",
        "sender": "sender@example.test",
        "parts": [MailPart(partId="2", attach=True, fileName="../../remote.pdf")],
    })
    mail = MagicMock(spec=MailService)
    mail.folders.return_value = [folder]
    mail.messages.return_value = MailMessagePage(messages=[message], limit=50)
    mail.message.return_value = message
    mail.search.return_value = MailSearchPage(
        results=[MailSearchHit(identifier="thread-1", subject="Example", count=1)],
        limit=50,
    )
    mail.body.return_value = MailBody(
        guid="message-1",
        longHeader="Subject: Example",
        contentType="text/html",
        parts=[MailBodyPart(guid="part-1", content='<script>alert("test")</script>')],
    )
    mail.attachment.return_value.__enter__.return_value = iter([b"%PDF-", b"example"])
    return mail


@pytest.fixture(name="output_dir")
def output_directory_fixture() -> Iterator[Path]:
    """Keep every test file inside the sanctioned filesystem sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mail-cli-", dir=root) as directory:
        try:
            yield Path(directory)
        finally:
            # The sandbox checks absolute paths, including during cleanup.
            for child in Path(directory).iterdir():
                child.unlink()


def invoke(mail: MagicMock, *args: str) -> Result:
    """Run the real mounted CLI with a stubbed account and Mail service."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = mail
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(app, ["mail", *args])


@pytest.mark.parametrize(
    "command", [[], ["--help"], ["body", "--help"], ["download", "--help"]]
)
def test_mail_help_never_authenticates(command: list[str]) -> None:
    """Group and leaf help work without a session or any service calls."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", *command])
    assert result.exit_code == 0
    assert "Usage" in result.stdout
    get_api.assert_not_called()


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_mail_folders(mail: MagicMock, output_format: str) -> None:
    """Both output modes preserve stable folder identity."""
    result = invoke(mail, "folders", "--format", output_format)
    assert result.exit_code == 0, result.exception
    mail.folders.assert_called_once()
    if output_format == "json":
        assert json.loads(result.stdout)[0]["id"] == "folder-1"
    else:
        assert "INBOX" in result.stdout


def test_mail_honors_shared_leaf_options(mail: MagicMock, output_dir: Path) -> None:
    """Mail resolves the same account, network, and output options as other groups."""
    api = MagicMock()
    api.mail = mail
    with (
        patch.object(CLIState, "get_api", autospec=True, return_value=api) as get_api,
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "folders",
                "--username",
                "selected@example.test",
                "--session-dir",
                str(output_dir),
                "--http-proxy",
                "http://localhost:9090",
                "--https-proxy",
                "http://localhost:9090",
                "--no-verify-ssl",
                "--log-level",
                "debug",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.exception
    state = get_api.call_args.args[0]
    assert state.username == "selected@example.test"
    assert state.session_dir == str(output_dir)
    assert state.http_proxy == "http://localhost:9090"
    assert state.https_proxy == "http://localhost:9090"
    assert state.no_verify_ssl is True
    assert state.log_level.value == "debug"
    assert json.loads(result.stdout)[0]["id"] == "folder-1"


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_mail_list(mail: MagicMock, output_format: str) -> None:
    """Listings remain bounded and metadata is not interpreted as markup."""
    result = invoke(
        mail,
        "list",
        "--folder-id",
        "folder-1",
        "--limit",
        "20",
        "--format",
        output_format,
    )
    assert result.exit_code == 0, result.exception
    mail.messages.assert_called_once_with(mail.folders.return_value[0], limit=20)
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert payload["messages"][0]["parts"][0]["id"] == "2"
    else:
        assert "[bold]" in result.stdout
        assert "Incomplete snapshot" in result.stdout


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_mail_search(mail: MagicMock, output_format: str) -> None:
    """Search forwards the scope and never conflates thread and message IDs."""
    result = invoke(
        mail,
        "search",
        "example",
        "--folder-id",
        "folder-1",
        "--field",
        "from",
        "--limit",
        "4",
        "--format",
        output_format,
    )
    assert result.exit_code == 0, result.exception
    mail.search.assert_called_once_with(
        mail.folders.return_value[0], "example", field="from", limit=4
    )
    if output_format == "json":
        assert json.loads(result.stdout)["results"][0]["id"] == "thread-1"
    else:
        assert "thread IDs are not message IDs" in result.stdout


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_mail_body(mail: MagicMock, output_format: str) -> None:
    """Bodies are printed as escaped data, never rendered as HTML."""
    result = invoke(
        mail,
        "body",
        "message-1",
        "--folder-id",
        "folder-1",
        "--limit",
        "30",
        "--format",
        output_format,
    )
    assert result.exit_code == 0, result.exception
    payload = json.loads(result.stdout)
    assert payload["parts"][0]["content"].startswith("<script>")
    mail.messages.assert_called_once_with(mail.folders.return_value[0], limit=30)
    mail.body.assert_called_once_with(mail.messages.return_value.messages[0])
    mail.message.assert_not_called()


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_body_defaults_to_exact_lookup(mail: MagicMock, output_format: str) -> None:
    """An older ID is resolved without listing the newest messages or paging."""
    result = invoke(
        mail, "body", "message-1", "--folder-id", "folder-1", "--format", output_format
    )
    assert result.exit_code == 0, result.exception
    mail.message.assert_called_once_with(mail.folders.return_value[0], "message-1")
    mail.messages.assert_not_called()
    mail.body.assert_called_once_with(mail.message.return_value)
    assert json.loads(result.stdout)["parts"]


@pytest.mark.parametrize("command", ["body", "download"])
@pytest.mark.parametrize("uid_validity", [None, 0, -1])
@pytest.mark.parametrize("limit", [None, 2])
def test_missing_uid_validity_guides_explicit_bounded_lookup(
    mail: MagicMock,
    output_dir: Path,
    command: str,
    uid_validity: int | None,
    limit: int | None,
) -> None:
    """Missing mailbox epochs require an explicit opt-in to bounded lookup."""
    mail.folders.return_value = [
        mail.folders.return_value[0].model_copy(update={"uid_validity": uid_validity})
    ]
    output = output_dir / "chosen.pdf"
    args = [command, "message-1", "--folder-id", "folder-1"]
    if command == "download":
        args.extend(["--part-id", "2", "--output", str(output)])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    result = invoke(mail, *args)
    mail.message.assert_not_called()
    if limit is None:
        assert isinstance(result.exception, CLIAbort)
        assert "UID validity" in str(result.exception)
        assert "--limit N" in str(result.exception)
        mail.messages.assert_not_called()
        mail.body.assert_not_called()
        mail.attachment.assert_not_called()
        assert not output.exists()
    else:
        assert result.exit_code == 0, result.exception
        mail.messages.assert_called_once_with(mail.folders.return_value[0], limit=2)
        if command == "download":
            assert output.read_bytes() == b"%PDF-example"
        else:
            mail.body.assert_called_once_with(mail.messages.return_value.messages[0])


def test_missing_exact_message_never_falls_back_to_scanning(mail: MagicMock) -> None:
    """Missing IDs and thread IDs do not cause broader mailbox reads."""
    mail.message.return_value = None
    result = invoke(mail, "body", "thread-1", "--folder-id", "folder-1")
    assert isinstance(result.exception, CLIAbort)
    assert "not found" in str(result.exception)
    assert "threads" in str(result.exception)
    mail.messages.assert_not_called()
    mail.body.assert_not_called()


def test_exact_lookup_failure_is_user_facing(mail: MagicMock) -> None:
    """Identity failures abort body retrieval with a concise error."""
    mail.message.side_effect = MailError("Mailbox identity changed")
    result = invoke(mail, "body", "message-1", "--folder-id", "folder-1")
    assert isinstance(result.exception, CLIAbort)
    assert "Mailbox identity changed" in str(result.exception)
    mail.body.assert_not_called()


@pytest.mark.parametrize("command", ["list", "search", "body", "download"])
@pytest.mark.parametrize("limit", ["0", "1001", "invalid"])
def test_invalid_limits_fail_before_authentication(command: str, limit: str) -> None:
    """All bounded commands reject invalid limits before contacting iCloud."""
    args = ["mail", command, "--folder-id", "folder-1", "--limit", limit]
    if command != "list":
        args.append("example")
    if command == "download":
        args += ["--part-id", "2", "--output", "unused.pdf"]
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    get_api.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["search", "   ", "--folder-id", "folder-1"],
        ["search", "example", "--folder-id", "folder-1", "--field", "invalid"],
        ["list"],
    ],
)
def test_invalid_search_and_missing_folder_fail_early(args: list[str]) -> None:
    """An empty search, invalid projection, or missing folder is a usage error."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", *args])
    assert result.exit_code == 2
    get_api.assert_not_called()


def test_unknown_folder_is_not_a_name_guess(mail: MagicMock) -> None:
    """Display names and stale IDs do not select another folder implicitly."""
    result = invoke(mail, "list", "--folder-id", "INBOX")
    assert isinstance(result.exception, CLIAbort)
    assert "exact IDs" in str(result.exception)
    mail.messages.assert_not_called()


def test_unknown_message_keeps_lookup_bounded(mail: MagicMock) -> None:
    """A missing message never starts an unbounded scan or fetches a body."""
    result = invoke(mail, "body", "thread-1", "--folder-id", "folder-1", "--limit", "1")
    assert isinstance(result.exception, CLIAbort)
    assert "newest 1" in str(result.exception)
    assert "threads" in str(result.exception)
    mail.messages.assert_called_once()
    mail.body.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        MailError("Invalid response"),
        ValueError("Unsupported part"),
        PyiCloudAPIResponseException("Bad Request", 400),
        PyiCloudFailedLoginException("Expired session"),
    ],
)
def test_mail_errors_are_user_facing(mail: MagicMock, error: Exception) -> None:
    """Expected Mail and session failures become concise CLI errors."""
    mail.folders.side_effect = error
    result = invoke(mail, "folders")
    assert isinstance(result.exception, CLIAbort)
    assert "Mail" in str(result.exception)
    if isinstance(error, PyiCloudFailedLoginException):
        assert "auth login" in str(result.exception)


def test_mail_service_absence_is_user_facing() -> None:
    """An account without Mail fails with a short error, not a traceback."""
    api = MagicMock()
    type(api).mail = PropertyMock(
        side_effect=PyiCloudServiceNotActivatedException("Mail unavailable")
    )
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, ["mail", "folders"])
    assert isinstance(result.exception, CLIAbort)


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("limit", [None, 2])
def test_mail_download(
    mail: MagicMock, output_dir: Path, output_format: str, limit: int | None
) -> None:
    """Only the explicit path is used, never a remote traversal filename."""
    output = output_dir / "chosen.pdf"
    result = invoke(
        mail,
        "download",
        "message-1",
        "--folder-id",
        "folder-1",
        "--part-id",
        "2",
        "--output",
        str(output),
        "--format",
        output_format,
        *([] if limit is None else ["--limit", str(limit)]),
    )
    assert result.exit_code == 0, result.exception
    assert output.read_bytes() == b"%PDF-example"
    assert list(output_dir.iterdir()) == [output]
    mail.attachment.assert_called_once_with(mail.messages.return_value.messages[0], "2")
    mail.attachment.return_value.__exit__.assert_called_once()
    if limit is None:
        mail.message.assert_called_once_with(mail.folders.return_value[0], "message-1")
        mail.messages.assert_not_called()
    else:
        mail.messages.assert_called_once_with(mail.folders.return_value[0], limit=limit)
        mail.message.assert_not_called()
    if output_format == "json":
        assert json.loads(result.stdout)["size"] == 12
    else:
        assert str(output) in result.stdout.replace("\n", "")


def test_download_never_overwrites(output_dir: Path) -> None:
    """An existing output is rejected before authentication or downloads."""
    output = output_dir / "existing.pdf"
    output.write_bytes(b"keep")
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "download",
                "message-1",
                "--folder-id",
                "folder-1",
                "--part-id",
                "2",
                "--output",
                str(output),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    assert output.read_bytes() == b"keep"
    get_api.assert_not_called()


def test_download_failure_removes_partial_bytes(
    mail: MagicMock, output_dir: Path
) -> None:
    """A failed stream closes its response and leaves no partial destination."""

    def chunks() -> Iterator[bytes]:
        """Simulate an interrupted attachment stream after the first bytes."""
        yield b"partial"
        raise MailError("Stream failed")

    mail.attachment.return_value.__enter__.return_value = chunks()
    output = output_dir / "failed.pdf"
    result = invoke(
        mail,
        "download",
        "message-1",
        "--folder-id",
        "folder-1",
        "--part-id",
        "2",
        "--output",
        str(output),
    )
    assert isinstance(result.exception, CLIAbort)
    assert not list(output_dir.iterdir())
    mail.attachment.return_value.__exit__.assert_called_once()


def test_download_refuses_dangling_symlinks(output_dir: Path) -> None:
    """A dangling symlink is an existing destination, not a writable filename."""
    target = output_dir / "absent.pdf"
    output = output_dir / "symlink.pdf"
    output.symlink_to(target)
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "download",
                "message-1",
                "--folder-id",
                "folder-1",
                "--part-id",
                "2",
                "--output",
                str(output),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    assert output.is_symlink()
    assert not target.exists()
    get_api.assert_not_called()


def test_download_publish_race_never_overwrites(
    mail: MagicMock, output_dir: Path
) -> None:
    """A file appearing during the stream wins over the staged download."""
    output = output_dir / "raced.pdf"

    def chunks() -> Iterator[bytes]:
        """Create a competing destination while downloading."""
        output.write_bytes(b"keep")
        yield b"download"

    mail.attachment.return_value.__enter__.return_value = chunks()
    result = invoke(
        mail,
        "download",
        "message-1",
        "--folder-id",
        "folder-1",
        "--part-id",
        "2",
        "--output",
        str(output),
    )
    assert isinstance(result.exception, CLIAbort)
    assert output.read_bytes() == b"keep"
    assert list(output_dir.iterdir()) == [output]


def test_download_missing_directory_is_user_facing(
    mail: MagicMock, output_dir: Path
) -> None:
    """Missing output parents do not produce partial files or a traceback."""
    output = output_dir / "missing" / "chosen.pdf"
    result = invoke(
        mail,
        "download",
        "message-1",
        "--folder-id",
        "folder-1",
        "--part-id",
        "2",
        "--output",
        str(output),
    )
    assert isinstance(result.exception, CLIAbort)
    mail.attachment.assert_not_called()


def test_metadata_control_characters_are_not_executed(mail: MagicMock) -> None:
    """Untrusted folder labels cannot inject ANSI or Rich markup."""
    mail.folders.return_value = [
        MailFolder(identifier="folder-1", name="[bold]literal[/bold]\x1b[31m")
    ]
    result = invoke(mail, "folders")
    assert result.exit_code == 0
    assert "[bold]literal[/bold]" in result.stdout
    assert "\x1b" not in result.stdout
