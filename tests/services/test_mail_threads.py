"""Offline safety and boundedness checks for folder-scoped thread expansion."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionException,
)
from pyicloud.services.mail import MailError, MailFolder, MailService, MailThreadPage
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

ThreadCase = tuple[MailService, MagicMock, MagicMock, MailFolder]


@pytest.fixture(name="thread")
def thread_fixture() -> Iterator[ThreadCase]:
    """Two synthetic thread members and a stable, separately checked mailbox."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "thread_messages_response.json")
    session.post.return_value = response
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, folder


def test_thread_expansion_queries_metadata_not_bodies(thread: ThreadCase) -> None:
    """Expand a search-group ID into bounded, folder-bound message metadata."""
    service, session, folders, folder = thread
    result = service.thread_messages(folder, "synthetic-thread-1", limit=2)

    assert isinstance(result, MailThreadPage)
    assert result.thread_id == "synthetic-thread-1"
    assert [m.id for m in result.messages] == ["thread-message-7", "thread-message-6"]
    assert all(m.folder == folder for m in result.messages)
    assert result.messages[0].parts[0].id == "1"
    assert result.limit == 2
    assert result.complete is False
    assert folders.call_count == 2
    session.post.assert_called_once()
    session.get.assert_not_called()
    session.post.return_value.close.assert_called_once()
    call = session.post.call_args
    assert call.args[0].endswith("/mailws2/v1/message/list")
    assert call.kwargs["timeout"] == (10, 60)
    assert call.kwargs["allow_redirects"] is False
    query = call.kwargs["json"]
    assert query["limit"] == 2
    assert "parts" in query["properties"]
    assert query["predicate"]["value"] == folder.id
    assert query["predicate"]["and"][0]["value"] is False
    assert query["predicate"]["and"][1] == {
        "type": "eq",
        "expression": {"type": "property", "property": "appleThrid"},
        "value": "synthetic-thread-1",
    }
    assert query["orderby"]["ascending"] is False
    assert query["orderby"]["expressions"][0]["property"] == "stateInternalDate"
    assert "collapseOn" not in query
    assert "continuationMarker" not in query


@pytest.mark.parametrize("count", [0, 1, 2])
def test_short_and_empty_pages_are_still_incomplete(
    thread: ThreadCase, count: int
) -> None:
    """Neither a short response nor an absent marker proves conversation coverage."""
    service, session, _, folder = thread
    data = session.post.return_value.json.return_value
    data["domainObjects"] = data["domainObjects"][:count]
    data.pop("continuationMarker")
    result = service.thread_messages(folder, "synthetic-thread-1")
    assert result.complete is False
    assert len(result.messages) == count
    assert result.limit == 50
    session.post.assert_called_once()


def test_duplicate_identities_are_not_returned_twice(thread: ThreadCase) -> None:
    """Repeated identical message/UID pairs do not inflate the page's output."""
    service, session, _, folder = thread
    data = session.post.return_value.json.return_value
    data["domainObjects"].insert(0, data["domainObjects"][0])
    result = service.thread_messages(folder, "synthetic-thread-1", limit=3)
    assert [m.uid for m in result.messages] == [7, 6]
    assert result.complete is False


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("uid", 7, "conflicting identities"),
        ("identifier", "thread-message-7", "multiple UIDs"),
    ],
)
def test_conflicting_identity_mappings_fail(
    thread: ThreadCase, field: str, value: Any, reason: str
) -> None:
    """Ambiguous UID/message mappings must not be bound to a caller's mailbox."""
    service, session, _, folder = thread
    session.post.return_value.json.return_value["domainObjects"][1][field] = value
    with pytest.raises(MailError, match=reason):
        service.thread_messages(folder, "synthetic-thread-1", limit=2)


@pytest.mark.parametrize(
    "updates",
    [
        {"mboxRef": {"id": "another-folder"}},
        {"appleThrid": "another-thread"},
        {"appleThrid": None},
    ],
)
def test_wrong_thread_or_folder_never_returns_a_page(
    thread: ThreadCase, updates: dict[str, Any]
) -> None:
    """Reject an ignored scope predicate before exposing any selected metadata."""
    service, session, _, folder = thread
    session.post.return_value.json.return_value["domainObjects"][1].update(updates)
    with pytest.raises(MailError, match="another thread or folder"):
        service.thread_messages(folder, "synthetic-thread-1", limit=2)
    session.post.return_value.close.assert_called_once()


def test_duplicate_with_wrong_scope_is_not_silently_ignored(thread: ThreadCase) -> None:
    """Scope validation happens before identity deduplication."""
    service, session, _, folder = thread
    records = session.post.return_value.json.return_value["domainObjects"]
    records[1].update(identifier=records[0]["identifier"], uid=7, appleThrid="wrong")
    with pytest.raises(MailError, match="another thread or folder"):
        service.thread_messages(folder, "synthetic-thread-1", limit=2)


def test_raw_response_cannot_exceed_requested_limit(thread: ThreadCase) -> None:
    """Even duplicate records count against the server-side response-size limit."""
    service, session, _, folder = thread
    records = session.post.return_value.json.return_value["domainObjects"]
    records.append(records[0])
    with pytest.raises(MailError, match="exceeded"):
        service.thread_messages(folder, "synthetic-thread-1", limit=2)


@pytest.mark.parametrize(
    "replacement",
    [
        [],
        [MailFolder(identifier="10", name="INBOX", uidValidity=9999)],
        [MailFolder(identifier="10", name="INBOX")],
    ],
)
@pytest.mark.parametrize("during_request", [False, True])
def test_mailbox_change_aborts_expansion(
    thread: ThreadCase, replacement: list[MailFolder], during_request: bool
) -> None:
    """Deleted/recreated mailboxes cannot lend their UIDs to a stale folder."""
    service, session, folders, folder = thread
    folders.side_effect = [[folder], replacement] if during_request else [replacement]
    with pytest.raises(MailError, match="identity changed"):
        service.thread_messages(folder, "synthetic-thread-1", limit=2)
    assert session.post.call_count == int(during_request)


def test_rename_keeps_current_message_context(thread: ThreadCase) -> None:
    """A rename is safe in the same UID namespace and updates returned folder names."""
    service, _, folders, folder = thread
    renamed = folder.model_copy(update={"name": "Renamed"})
    folders.side_effect = [[folder], [renamed]]
    result = service.thread_messages(folder, "synthetic-thread-1", limit=2)
    assert all(m.folder.name == "Renamed" for m in result.messages)


@pytest.mark.parametrize("validity", [None, 0, -1])
def test_unknown_mailbox_epoch_fails_before_http(
    thread: ThreadCase, validity: int | None
) -> None:
    """Require a known UID namespace before exposing folder-bound message objects."""
    service, session, folders, folder = thread
    unknown = folder.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="UID validity"):
        service.thread_messages(unknown, "synthetic-thread-1")
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [None, True, 7, "", "   "])
def test_invalid_thread_id_fails_before_http(thread: ThreadCase, value: Any) -> None:
    """Thread-group IDs are non-empty strings, never coerced numbers."""
    service, session, folders, folder = thread
    with pytest.raises(ValueError, match="thread_id"):
        service.thread_messages(folder, value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", 1001])
def test_invalid_limits_fail_before_http(thread: ThreadCase, value: Any) -> None:
    """Thread expansion retains the bounded-query limit, including strict types."""
    service, session, folders, folder = thread
    with pytest.raises(ValueError, match="limit"):
        service.thread_messages(folder, "synthetic-thread-1", limit=value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize(
    "data", [None, {}, {"domainObjects": None}, {"domainObjects": [{}]}]
)
def test_malformed_responses_are_not_empty_threads(
    thread: ThreadCase, data: Any
) -> None:
    """Envelope/record validation failures remain errors, not successful empty pages."""
    service, session, _, folder = thread
    session.post.return_value.json.return_value = data
    with pytest.raises(MailError):
        service.thread_messages(folder, "synthetic-thread-1")


@pytest.mark.parametrize(
    "error",
    [
        PyiCloudConnectionException("offline"),
        PyiCloudAPIResponseException("Authentication required", 403),
    ],
)
def test_transport_errors_are_preserved(thread: ThreadCase, error: Exception) -> None:
    """Network/authentication failures do not masquerade as empty conversations."""
    service, session, _, folder = thread
    session.post.side_effect = error
    with pytest.raises(type(error)):
        service.thread_messages(folder, "synthetic-thread-1")
