"""Offline attachment edge cases across the real Mail stream and CLI writer."""

from collections.abc import Iterator
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from requests import Request, Response
from requests.exceptions import (
    ChunkedEncodingError,
    ContentDecodingError,
    ReadTimeout,
    RequestException,
)
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.services.mail.models import MailMessagePage, MessageResponse
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

AttachmentCase = tuple[MailService, MagicMock, MagicMock, MailMessage]


@pytest.fixture(name="attachment")
def attachment_fixture() -> Iterator[AttachmentCase]:
    """Use synthetic metadata and a real service with a mocked byte transport."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    record = MessageResponse.model_validate(
        load_fixture("mail", "messages_response.json")
    ).records[0]
    message = MailMessage(**record.model_dump(), folder=folder)
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.headers = {"Content-Type": "application/pdf"}
    response.iter_content.return_value = iter([b"synthetic bytes"])
    session.get.return_value = response
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]):
        yield service, session, response, message


@pytest.fixture(name="output")
def output_fixture() -> Iterator[Path]:
    """Keep all staging files in the sanctioned test filesystem sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mail-attachment-", dir=root) as directory:
        try:
            yield Path(directory) / "chosen.pdf"
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def download(service: MailService, message: MailMessage, output: Path) -> Result:
    """Exercise real stream handling and publication, mocking only metadata reads."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = service
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
        patch.object(service, "folders", return_value=[message.folder]),
        patch.object(
            service,
            "messages",
            return_value=MailMessagePage(messages=[message], limit=1),
        ),
    ):
        return CliRunner().invoke(
            app,
            [
                "mail",
                "download",
                message.id,
                "--folder-id",
                message.folder.id,
                "--part-id",
                "2",
                "--limit",
                "1",
                "--output",
                str(output),
                "--format",
                "json",
            ],
        )


@pytest.mark.parametrize(
    "error_type", [ChunkedEncodingError, ContentDecodingError, ReadTimeout]
)
@pytest.mark.parametrize("partial", [False, True])
def test_transport_failures_never_publish_partial_files(
    attachment: AttachmentCase,
    output: Path,
    error_type: type[RequestException],
    partial: bool,
) -> None:
    """Real Requests stream failures reach the CLI without leaking staged bytes."""
    service, session, response, message = attachment
    error = error_type("synthetic interrupted stream")

    def chunks() -> Iterator[bytes]:
        if partial:
            yield b"partial bytes"
        raise error

    response.iter_content.return_value = chunks()
    result = download(service, message, output)
    assert isinstance(result.exception, CLIAbort)
    assert "stream failed" in str(result.exception)
    assert isinstance(result.exception.__cause__, MailError)
    assert result.exception.__cause__.__cause__ is error
    assert result.stdout == ""
    assert not list(output.parent.iterdir())
    response.close.assert_called_once()
    session.get.assert_called_once()
    session.post.assert_not_called()


@pytest.mark.parametrize("chunks", [[], [b"", b""]])
def test_zero_byte_attachment_is_a_successful_private_file(
    attachment: AttachmentCase, output: Path, chunks: list[bytes]
) -> None:
    """An explicitly empty attachment is valid, not a failed or absent download."""
    service, _, response, message = attachment
    part = message.parts[-1].model_copy(update={"size": 0})
    message = message.model_copy(update={"parts": [part]})
    response.headers["Content-Length"] = "0"
    response.iter_content.return_value = iter(chunks)
    result = download(service, message, output)
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["size"] == 0
    assert output.read_bytes() == b""
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(output.parent.iterdir()) == [output]
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "filename",
    [
        "r\u00e9sum\u00e9 \u65e5\u672c.pdf",
        "=?UTF-8?B?csOpc3Vtw6kucGRm?=",
        "UTF-8''r%C3%A9sum%C3%A9.pdf",
        "../../remote.pdf?other=value&size=1#fragment",
        "remote\r\nX-Synthetic: value.pdf",
    ],
)
def test_remote_filenames_remain_one_opaque_query_value(
    attachment: AttachmentCase, output: Path, filename: str
) -> None:
    """Do not MIME-decode names, splice query syntax, or use names as paths."""
    service, session, response, message = attachment
    part = message.parts[-1].model_copy(update={"filename": filename})
    message = message.model_copy(update={"parts": [part]})
    result = download(service, message, output)
    assert result.exit_code == 0, result.exception
    assert output.read_bytes() == b"synthetic bytes"
    assert list(output.parent.iterdir()) == [output]
    call = session.get.call_args
    prepared = Request("GET", call.args[0], params=call.kwargs["params"]).prepare()
    assert prepared.url is not None
    url = urlsplit(prepared.url)
    query = parse_qs(url.query)
    assert query["name"] == [filename]
    assert query["size"] == ["112008"]
    assert set(query) == {"guid", "type", "name", "size"}
    assert url.fragment == ""
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "actual,accepted",
    [
        (" Application/PDF ; version=1.7", True),
        ("application/octet-stream", True),
        ("image/png", False),
        ("application/json", False),
        ("application/pdf-extra", False),
    ],
)
def test_specific_mime_type_mismatch_never_publishes_bytes(
    attachment: AttachmentCase, output: Path, actual: str, accepted: bool
) -> None:
    """Match normalized advertised types exactly, allowing a binary fallback."""
    service, _, response, message = attachment
    response.headers["Content-Type"] = actual
    result = download(service, message, output)
    if accepted:
        assert result.exit_code == 0, result.exception
        assert output.read_bytes() == b"synthetic bytes"
    else:
        assert isinstance(result.exception, CLIAbort)
        assert "unexpected content type" in str(result.exception)
        assert result.stdout == ""
        assert not list(output.parent.iterdir())
        response.iter_content.assert_not_called()
    response.close.assert_called_once()
