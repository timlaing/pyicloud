"""Prevent stale folder-local UIDs from silently selecting other Mail content."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.cli.commands.mail import _download
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.services.mail.models import MessageResponse
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

ContentCase = tuple[MailService, MagicMock, MagicMock, MailMessage]


@pytest.fixture(name="content")
def content_fixture() -> Iterator[ContentCase]:
    """Use real read methods, a stable namespace, and synthetic HTTP responses."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    record = MessageResponse.model_validate(
        load_fixture("mail", "messages_response.json")
    ).records[0]
    message = MailMessage(**record.model_dump(), folder=folder)
    session = MagicMock(spec=PyiCloudSession)
    body = MagicMock(spec=Response)
    body.status_code = 200
    body.json.return_value = load_fixture("mail", "body_response.json")
    session.post.return_value = body
    attachment = MagicMock(spec=Response)
    attachment.status_code = 200
    attachment.headers = {"Content-Type": "application/pdf"}
    attachment.iter_content.return_value = iter([b"synthetic PDF"])
    session.get.return_value = attachment
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, message


def consume(content: ContentCase, operation: str) -> Any:
    """Consume the entire public operation, including stream-context cleanup."""
    service, _, _, message = content
    if operation == "body":
        return service.body(message)
    with service.attachment(message, "2") as chunks:
        return b"".join(chunks)


@pytest.mark.parametrize(
    "operation,check,change",
    [
        (operation, check, change)
        for operation, check in (
            ("body", 0),
            ("body", 1),
            ("attachment", 0),
            ("attachment", 1),
            ("attachment", 2),
        )
        for change in ("recreated", "missing", "renamed")
        if change != "renamed" or check > 0
    ],
)
def test_content_guard_rejects_observed_changes(
    content: ContentCase, operation: str, check: int, change: str
) -> None:
    """Fail before reads, after responses, or at stream completion as appropriate."""
    _, session, folders, message = content
    changed = message.folder.model_copy(
        update={"uid_validity": 9999} if change == "recreated" else {"name": "Renamed"}
    )
    folders.side_effect = [[message.folder]] * check + [
        [] if change == "missing" else [changed]
    ]
    with pytest.raises(MailError, match="identity changed|folder changed"):
        consume(content, operation)
    if check == 0:
        session.post.assert_not_called()
        session.get.assert_not_called()
    elif operation == "body":
        session.post.return_value.close.assert_called_once()
    else:
        session.get.return_value.close.assert_called_once()
        if check == 1:
            session.get.return_value.iter_content.assert_not_called()


@pytest.mark.parametrize("operation", ["body", "attachment"])
def test_stable_rename_uses_current_folder_path(
    content: ContentCase, operation: str
) -> None:
    """The original folder object must not route a UID to an obsolete name."""
    _, session, folders, message = content
    renamed = message.folder.model_copy(update={"name": "Renamed"})
    folders.return_value = [renamed]
    consume(content, operation)
    if operation == "body":
        payload = session.post.call_args.kwargs["json"]
        assert payload["sessionHeaders"]["folder"] == "Renamed"
        assert payload["dontMarkAsRead"] is True
    else:
        assert (
            session.get.call_args.kwargs["params"]["guid"] == "messagepart:Renamed/7-2"
        )
        session.get.return_value.close.assert_called_once()


@pytest.mark.parametrize("operation", ["body", "attachment"])
def test_mismatched_folder_is_rejected_before_http(
    content: ContentCase, operation: str
) -> None:
    """A forged or accidentally mismatched message context cannot fetch content."""
    service, session, folders, message = content
    changed = message.model_copy(
        update={"folder": message.folder.model_copy(update={"id": "wrong"})}
    )
    selected = service, session, folders, changed
    with pytest.raises(ValueError, match="identities do not match"):
        consume(selected, operation)
    folders.assert_not_called()
    session.post.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.parametrize("operation", ["body", "attachment"])
def test_legacy_missing_namespace_preserves_bounded_reads(
    content: ContentCase, operation: str
) -> None:
    """Old metadata without UID validity retains explicitly documented limitations."""
    service, session, folders, message = content
    legacy = message.model_copy(
        update={"folder": message.folder.model_copy(update={"uid_validity": None})}
    )
    consume((service, session, folders, legacy), operation)
    folders.assert_not_called()


def test_final_stream_guard_prevents_publishing_download(content: ContentCase) -> None:
    """A completed byte transfer is not published if mailbox identity changed."""
    service, session, folders, message = content
    changed = message.folder.model_copy(update={"uid_validity": 9999})
    folders.side_effect = [[message.folder], [message.folder], [changed]]
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        output = Path(directory) / "attachment.pdf"
        with pytest.raises(MailError, match="identity changed"):
            _download(service, message, "2", output)
        assert not list(Path(directory).iterdir())
    session.get.return_value.close.assert_called_once()
