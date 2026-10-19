"""Offline cross-folder search and opt-in JSON export checks."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import (
    MailFolder,
    MailMessage,
    MailMessageExport,
    MailMultiSearchPage,
    MailService,
)
from pyicloud.services.mail.models import MessageResponse
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@pytest.fixture(name="session")
def session_fixture() -> MagicMock:
    """A local-only transport with a synthetic grouped-search response."""
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "search_response.json")
    session.post.return_value = response
    return session


@pytest.fixture(name="service")
def service_fixture(session: MagicMock) -> MailService:
    """Use the ordinary Mail service, not a mocked search/export method."""
    return MailService("https://example.test", session, {})


@pytest.fixture(name="folders")
def folders_fixture() -> list[MailFolder]:
    """A message's folder and another explicitly selected folder."""
    return [
        MailFolder(identifier="10", name="INBOX", uidValidity=1234),
        MailFolder(identifier="20", name="Sent", uidValidity=5678),
    ]


@pytest.fixture(name="message")
def message_fixture(
    folders: list[MailFolder], service: MailService
) -> Iterator[MailMessage]:
    """Existing synthetic MIME metadata; no real mailbox content."""
    record = MessageResponse.model_validate(
        load_fixture("mail", "messages_response.json")
    ).records[0]
    with patch.object(service, "folders", return_value=folders):
        yield MailMessage(**record.model_dump(), folder=folders[0])


def test_search_folders_preserves_scope_and_bounded_thread_groups(
    service: MailService, session: MagicMock, folders: list[MailFolder]
) -> None:
    """The same thread ID in two folders remains two explicitly scoped results."""
    result = service.search_folders(
        folders, "example", field="subject", limit_per_folder=3
    )
    assert isinstance(result, MailMultiSearchPage)
    assert result.limit_per_folder == 3
    assert result.complete is False
    assert [page.folder for page in result.pages] == folders
    assert all(page.limit == 3 and page.complete is False for page in result.pages)
    assert [page.results[0].id for page in result.pages] == [
        "synthetic-message-a",
        "synthetic-message-a",
    ]
    assert session.post.call_count == 2
    for call, folder in zip(session.post.call_args_list, folders, strict=True):
        assert call.args[0].endswith("/geqs/query")
        query = call.kwargs["json"]
        assert query["predicate"]["value"] == folder.id
        assert query["limit"] == 3
        assert query["collapseOn"] == "appleThrid"
    session.get.assert_not_called()


def test_search_folders_keeps_empty_pages(
    service: MailService, session: MagicMock, folders: list[MailFolder]
) -> None:
    """An empty folder is represented without implying global completeness."""
    session.post.return_value.json.return_value = {"domainObjects": []}
    result = service.search_folders(folders, "example")
    assert len(result.pages) == 2
    assert all(page.results == [] for page in result.pages)
    assert result.limit_per_folder == 50
    assert result.complete is False


@pytest.mark.parametrize("case", ["empty", "duplicate", "too_many", "not_folder"])
def test_invalid_folder_selection_fails_before_requests(
    service: MailService, session: MagicMock, folders: list[MailFolder], case: str
) -> None:
    """Validate every folder before starting any search."""
    choices: dict[str, Any] = {
        "empty": [],
        "duplicate": [folders[0], folders[0]],
        "too_many": [MailFolder(identifier=str(i), name="Folder") for i in range(21)],
        "not_folder": [folders[0], "20"],
    }
    with pytest.raises(ValueError):
        service.search_folders(choices[case], "example")
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "text,field,limit",
    [
        (" ", "all", 50),
        (None, "all", 50),
        ("example", "unknown", 50),
        ("example", "all", 0),
        ("example", "all", True),
        ("example", "all", 1001),
    ],
)
def test_invalid_multi_search_query_fails_before_requests(
    service: MailService,
    session: MagicMock,
    folders: list[MailFolder],
    text: Any,
    field: Any,
    limit: Any,
) -> None:
    """A bad query cannot cause earlier selected folders to be searched."""
    with pytest.raises(ValueError):
        service.search_folders(folders, text, field=field, limit_per_folder=limit)
    session.post.assert_not_called()


def test_later_folder_failure_does_not_return_partial_success(
    service: MailService, session: MagicMock, folders: list[MailFolder]
) -> None:
    """Preserve the transport exception instead of silently dropping a folder."""
    response = session.post.return_value
    session.post.side_effect = [response, PyiCloudConnectionException("offline")]
    with pytest.raises(PyiCloudConnectionException):
        service.search_folders(folders, "example")
    assert session.post.call_count == 2


def test_metadata_export_never_fetches_a_body_or_attachment(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Export is metadata-only unless a caller explicitly opts into body reads."""
    result = service.export_message(message)
    assert isinstance(result, MailMessageExport)
    assert result.format_version == 1
    assert result.message == message
    assert result.body is None
    assert MailMessageExport.model_validate_json(result.model_dump_json()) == result
    session.post.assert_not_called()
    session.get.assert_not_called()


def test_export_body_is_explicit_unrendered_data(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Body export uses the existing non-mutating body request, not raw MIME."""
    session.post.return_value.json.return_value = load_fixture(
        "mail", "body_response.json"
    )
    result = service.export_message(message, include_body=True)
    assert result.body is not None
    assert result.body.parts[1].content == "<p>Synthetic HTML.</p>"
    session.post.assert_called_once()
    call = session.post.call_args
    assert call.args[0].endswith("/message/get")
    assert call.kwargs["json"]["dontMarkAsRead"] is True
    session.get.assert_not_called()


@pytest.mark.parametrize("value", [None, 1, "yes"])
def test_export_opt_in_is_strict(
    service: MailService, session: MagicMock, message: MailMessage, value: Any
) -> None:
    """Truthy strings and numbers must not unexpectedly fetch private bodies."""
    with pytest.raises(ValueError, match="boolean"):
        service.export_message(message, include_body=value)
    session.post.assert_not_called()


def test_body_export_error_is_not_successful_metadata_only_export(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Do not silently omit a body the caller explicitly asked to retrieve."""
    session.post.side_effect = PyiCloudConnectionException("offline")
    with pytest.raises(PyiCloudConnectionException):
        service.export_message(message, include_body=True)
