"""Original MIME streaming and private CLI publication without reconstruction."""

from base64 import b64encode
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response
from requests.exceptions import ChunkedEncodingError, ContentDecodingError, ReadTimeout
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.session import PyiCloudSession

RAW = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: reader@example.test\r\n"
    b"Subject: Synthetic original\r\n"
    b"X-Synthetic: folded\r\n\tcontinued\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: multipart/mixed; boundary=synthetic-boundary\r\n\r\n"
    b"--synthetic-boundary\r\nContent-Type: text/plain\r\n\r\nHello.\r\n"
    b"--synthetic-boundary\r\nContent-Type: application/pdf\r\n"
    b"Content-Disposition: attachment; filename=../../untrusted.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\n"
    + b64encode(b"%PDF-1.7\n")
    + b"\r\n--synthetic-boundary--\r\n"
)
Case = tuple[MailService, MagicMock, MagicMock, MagicMock, MailMessage]


@pytest.fixture(name="case")
def case_fixture() -> Iterator[Case]:
    """Use the real service with invented message scope and a byte response."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    message = MailMessage.model_validate({
        "identifier": "synthetic-id",
        "uid": 7,
        "mboxRef": {"id": "10"},
        "folder": folder,
    })
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.headers = {
        "Content-Type": "message/rfc822",
        "Content-Disposition": 'attachment; filename="../../remote.eml"',
    }
    response.iter_content.return_value = iter([RAW[:35], b"", RAW[35:]])
    session.get.return_value = response
    service = MailService(
        "https://example.test", session, {"dsid": "synthetic-account"}
    )
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, response, folders, message


@pytest.fixture(name="output")
def output_fixture() -> Iterator[Path]:
    """Keep private staging files inside the sanctioned filesystem sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="mail-raw-") as directory:
        try:
            yield Path(directory) / "chosen.eml"
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def invoke(case: Case, output: Path, mode: str = "json") -> Result:
    """Mock only CLI account/lookup, leaving stream and file publication real."""
    service, _, _, _, message = case
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = service
    with (
        patch.object(CLIState, "open"),
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(service, "message", return_value=message),
    ):
        return CliRunner().invoke(
            app,
            [
                "mail",
                "export-eml",
                message.id,
                "--folder-id",
                message.folder.id,
                "--output",
                str(output),
                "--format",
                mode,
            ],
        )


@pytest.mark.parametrize(
    "media_type", ["message/rfc822", " Message/RFC822 ; charset=utf-8"]
)
def test_library_preserves_original_bytes(case: Case, media_type: str) -> None:
    """Never decode MIME, normalize line endings, or regenerate header folding."""
    service, session, response, folders, message = case
    response.headers["Content-Type"] = media_type
    with service.raw_message(message) as chunks:
        raw = b"".join(chunks)
    assert raw == RAW
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert not parsed.defects
    assert next(parsed.iter_attachments()).get_payload(decode=True) == b"%PDF-1.7\n"
    call = session.get.call_args
    assert call.args[0] == "https://example.test/mailws2/v1/message/download"
    assert call.kwargs == {
        "params": {
            "dsid": "synthetic-account",
            "guid": "message:INBOX/7",
            "filename": "message.eml",
        },
        "stream": True,
        "timeout": (10.0, 60.0),
        "allow_redirects": False,
    }
    assert service.params == {"dsid": "synthetic-account"}
    assert folders.call_count == 3
    session.post.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize("mode", ["text", "json"])
def test_cli_private_non_overwriting_raw_file(
    case: Case, output: Path, mode: str
) -> None:
    """Publish exact bytes privately and ignore all remote filename suggestions."""
    result = invoke(case, output, mode)
    assert result.exit_code == 0, result.exception
    assert output.read_bytes() == RAW
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(output.parent.iterdir()) == [output]
    assert "Synthetic original" not in result.stdout
    if mode == "json":
        assert json.loads(result.stdout)["size"] == len(RAW)
    else:
        assert str(output) in result.stdout.replace("\n", "")


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html",
        "application/json",
        "application/octet-stream",
        "",
        "message/rfc822-extra",
    ],
)
def test_wrong_media_type_is_not_an_eml(
    case: Case, output: Path, content_type: str
) -> None:
    """Do not save an HTML login page, error JSON, or an unverified binary fallback."""
    case[2].headers["Content-Type"] = content_type
    result = invoke(case, output)
    assert isinstance(result.exception, CLIAbort)
    assert "unexpected content type" in str(result.exception)
    assert not list(output.parent.iterdir())
    case[2].iter_content.assert_not_called()
    case[2].close.assert_called_once()


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
def test_failure_status_never_publishes_bytes(
    case: Case, output: Path, status: int
) -> None:
    """Neither redirects nor failed requests can become a successful raw export."""
    case[2].status_code = status
    result = invoke(case, output)
    assert result.exit_code != 0
    assert not list(output.parent.iterdir())
    case[2].close.assert_called_once()


@pytest.mark.parametrize(
    "error_type", [ReadTimeout, ChunkedEncodingError, ContentDecodingError]
)
def test_stream_failure_discards_staging(
    case: Case, output: Path, error_type: Any
) -> None:
    """Surface transport errors and never publish an incomplete EML file."""
    error = error_type("Synthetic interruption")

    def interrupted() -> Iterator[bytes]:
        yield RAW[:35]
        raise error

    case[2].iter_content.return_value = interrupted()
    result = invoke(case, output)
    assert isinstance(result.exception, CLIAbort)
    cause = result.exception.__cause__
    assert isinstance(cause, MailError)
    assert cause.__cause__ is error
    assert "raw message stream failed" in str(result.exception)
    assert result.stdout == ""
    assert not list(output.parent.iterdir())
    case[2].close.assert_called_once()


@pytest.mark.parametrize(
    "when,change",
    [
        (when, change)
        for when in (0, 1, 2)
        for change in ("deleted", "recreated", "renamed")
        if (when, change) != (0, "renamed")
    ],
)
def test_identity_checks_guard_publication(
    case: Case, output: Path, when: int, change: str
) -> None:
    """A failure even after consumption prevents atomically publishing raw bytes."""
    _, session, response, folders, message = case
    replacement = (
        []
        if change == "deleted"
        else [
            message.folder.model_copy(
                update={"uid_validity": 999}
                if change == "recreated"
                else {"name": "Renamed"}
            )
        ]
    )
    # CLI folder selection uses one additional initial metadata lookup.
    folders.side_effect = [[message.folder]] * (1 + when) + [replacement]
    result = invoke(case, output)
    assert result.exit_code != 0
    assert not list(output.parent.iterdir())
    assert session.get.call_count == int(when > 0 or change == "renamed")
    assert response.close.call_count == int(when > 0 or change == "renamed")


def test_stable_rename_uses_fresh_name(case: Case) -> None:
    """Use the refreshed folder path without changing the message namespace."""
    service, session, _, folders, message = case
    folders.return_value = [message.folder.model_copy(update={"name": "Renamed"})]
    with service.raw_message(message) as chunks:
        assert b"".join(chunks) == RAW
    assert session.get.call_args.kwargs["params"]["guid"] == "message:Renamed/7"


@pytest.mark.parametrize("epoch", [None, 0, -1])
def test_requires_known_mailbox_namespace(case: Case, epoch: Any) -> None:
    """Raw message download has no unsafe legacy or bounded-scan fallback."""
    service, session, _, folders, message = case
    changed = message.model_copy(
        update={"folder": message.folder.model_copy(update={"uid_validity": epoch})}
    )
    stream = service.raw_message(changed)
    with pytest.raises(ValueError, match="UID validity"), stream:
        pass
    session.get.assert_not_called()
    folders.assert_not_called()


def test_mismatched_message_scope_fails_before_http(case: Case) -> None:
    """Reject metadata bound to a different folder before resolving any UID path."""
    service, session, _, folders, message = case
    changed = message.model_copy(
        update={"mailbox": message.mailbox.model_copy(update={"id": "elsewhere"})}
    )
    stream = service.raw_message(changed)
    with pytest.raises(ValueError, match="identities"), stream:
        pass
    session.get.assert_not_called()
    folders.assert_not_called()


def test_partial_consumption_closes_response(case: Case) -> None:
    """Early consumer exit still closes the connection and checks mailbox identity."""
    service, _, response, folders, message = case
    with service.raw_message(message) as chunks:
        assert next(chunks) == RAW[:35]
    response.close.assert_called_once()
    assert folders.call_count == 3


@pytest.mark.parametrize("symlink", [False, True])
def test_existing_output_fails_before_authentication(
    output: Path, symlink: bool
) -> None:
    """Never overwrite or even authenticate when the chosen destination exists."""
    if symlink:
        target = output.parent / "target"
        target.write_bytes(b"keep")
        output.symlink_to(target)
    else:
        output.write_bytes(b"keep")
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "export-eml",
                "message",
                "--folder-id",
                "10",
                "--output",
                str(output),
            ],
        )
    assert result.exit_code != 0
    get_api.assert_not_called()
    assert output.read_bytes() == b"keep"


def test_destination_created_during_stream_is_not_overwritten(
    case: Case, output: Path
) -> None:
    """Refuse a destination race and remove the completed private staging file."""

    def chunks() -> Iterator[bytes]:
        yield RAW[:35]
        output.write_bytes(b"keep concurrent file")
        yield RAW[35:]

    case[2].iter_content.return_value = chunks()
    result = invoke(case, output)
    assert result.exit_code != 0
    assert output.read_bytes() == b"keep concurrent file"
    assert list(output.parent.iterdir()) == [output]
    case[2].close.assert_called_once()


def test_help_is_offline() -> None:
    """Explain original-byte export without opening an account."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", "export-eml", "--help"])
    assert result.exit_code == 0
    get_api.assert_not_called()
