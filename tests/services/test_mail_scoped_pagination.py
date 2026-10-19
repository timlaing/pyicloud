"""Offline checks for explicit thread and individual-message filter scans."""

from collections.abc import Iterator
from typing import Any, Literal
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.session import PyiCloudSession

ScopedCase = tuple[
    MailService,
    MagicMock,
    MagicMock,
    MailFolder,
    Literal["thread", "unread", "flagged"],
]


@pytest.fixture(name="scoped", params=["thread", "unread", "flagged"])
def scoped_fixture(request: pytest.FixtureRequest) -> Iterator[ScopedCase]:
    """A stable UID namespace and separately observed metadata-only transport."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, folder, request.param


def scan(case: ScopedCase, **options: Any) -> Iterator[MailMessage]:
    """Exercise the actual public entry point for every scope."""
    service, _, _, folder, scope = case
    if scope == "thread":
        return service.iter_thread_messages(folder, "synthetic-thread", **options)
    return service.iter_filtered_messages(folder, filter_by=scope, **options)


def record(uid: int, **updates: Any) -> dict[str, Any]:
    """Equal dates must never be used as scoped progress boundaries."""
    return {
        "identifier": f"message-{uid}",
        "uid": uid,
        "mboxRef": {"id": "10"},
        "appleThrid": "synthetic-thread",
        "stateInternalDate": 1700000000,
        **updates,
    }


def response(*records: dict[str, Any]) -> MagicMock:
    """A synthetic HTTP page, including an intentionally unusable marker."""
    result = MagicMock(spec=Response)
    result.status_code = 200
    result.json.return_value = {
        "domainObjects": list(records),
        "continuationMarker": "opaque-not-replayed",
    }
    return result


def test_scoped_pages_keep_predicates_and_require_empty_terminator(
    scoped: ScopedCase,
) -> None:
    """Short pages do not end a scan and every page retains the original scope."""
    _, session, folders, folder, scope = scoped
    pages = [response(record(10), record(9)), response(record(7)), response()]
    session.post.side_effect = pages
    iterator = scan(scoped, page_size=2)
    session.post.assert_not_called()
    folders.assert_not_called()
    messages = list(iterator)
    assert [message.uid for message in messages] == [10, 9, 7]
    assert all(message.folder == folder for message in messages)
    assert folders.call_count == 4
    for index, call in enumerate(session.post.call_args_list):
        query = call.kwargs["json"]
        assert call.args[0].endswith("/mailws2/v1/message/list")
        assert call.kwargs["timeout"] == (10, 60)
        assert call.kwargs["allow_redirects"] is False
        assert query["predicate"]["value"] == folder.id
        clauses = query["predicate"]["and"]
        assert clauses[0]["expression"]["value"] == "DELETED"
        assert clauses[0]["value"] is False
        expected = (
            {
                "type": "eq",
                "expression": {"type": "property", "property": "appleThrid"},
                "value": "synthetic-thread",
            }
            if scope == "thread"
            else {
                "type": "eq",
                "expression": {
                    "type": "fieldOf",
                    "property": "flags",
                    "value": "SEEN" if scope == "unread" else "FLAGGED",
                },
                "value": scope == "flagged",
            }
        )
        assert clauses[1] == expected
        if index:
            assert clauses[2]["type"] == "lt"
            assert clauses[2]["expression"]["property"] == "uid"
            assert clauses[2]["value"] == [9, 7][index - 1]
        assert query["orderby"]["expressions"][0]["property"] == "uid"
        assert query["orderby"]["ascending"] is False
        assert "collapseOn" not in query
        assert "continuationMarker" not in query
    for page in pages:
        page.close.assert_called_once()
    session.get.assert_not_called()


def test_scoped_large_scan_equal_dates_and_duplicates(scoped: ScopedCase) -> None:
    """Cross 1,000 records and deduplicate exact identities without losing scope."""
    _, session, _, _, _ = scoped
    session.post.side_effect = [
        response(*(record(uid) for uid in range(1002, 3, -1)), record(4)),
        response(record(3), record(2), record(1)),
        response(),
    ]
    assert [m.uid for m in scan(scoped, page_size=1000)] == list(range(1002, 0, -1))


@pytest.mark.parametrize(
    "records,reason",
    [
        ([record(10, mboxRef={"id": "other"})], "another folder"),
        ([record(9), record(10)], "descending UID"),
        ([record(10), record(10, identifier="other")], "conflicting identities"),
        ([record(10), record(9, identifier="message-10")], "multiple UIDs"),
    ],
)
def test_invalid_scope_pages_never_yield_partial_metadata(
    scoped: ScopedCase, records: list[dict[str, Any]], reason: str
) -> None:
    """Validate the entire response before exposing a scoped record."""
    _, session, _, _, _ = scoped
    session.post.return_value = response(*records)
    iterator = scan(scoped)
    with pytest.raises(MailError, match=reason):
        next(iterator)


@pytest.mark.parametrize("wrong_thread", [None, "another-thread"])
def test_wrong_thread_even_on_duplicate_is_rejected(wrong_thread: str | None) -> None:
    """Scope validation precedes deduplication and no record leaks out."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    service = MailService("https://example.test", session, {})
    session.post.return_value = response(
        record(10), record(10, appleThrid=wrong_thread)
    )
    with patch.object(service, "folders", return_value=[folder]):
        iterator = service.iter_thread_messages(folder, "synthetic-thread")
        with pytest.raises(MailError, match="another thread"):
            next(iterator)


@pytest.mark.parametrize("during_page", [False, True])
def test_changed_mailbox_aborts_scoped_scan(
    scoped: ScopedCase, during_page: bool
) -> None:
    """A changed epoch cannot supply scope-local UIDs to callers."""
    _, session, folders, folder, _ = scoped
    replacement = folder.model_copy(update={"uid_validity": 4321})
    folders.side_effect = [[folder], [replacement]] if during_page else [[replacement]]
    session.post.return_value = response(record(10))
    iterator = scan(scoped)
    with pytest.raises(MailError, match="identity changed"):
        next(iterator)
    assert session.post.call_count == int(during_page)


@pytest.mark.parametrize("failure", ["budget", "boundary", "transport", "http", "json"])
def test_later_failure_is_not_exhaustion(scoped: ScopedCase, failure: str) -> None:
    """Earlier records may be consumed, but no later failure claims completion."""
    _, session, _, _, _ = scoped
    second = response(record(10))
    error: type[Exception] = MailError
    if failure == "http":
        second.status_code = 500
    elif failure == "json":
        second.json.return_value = {"domainObjects": [{}]}
    later: MagicMock | PyiCloudConnectionException = second
    if failure == "transport":
        later = PyiCloudConnectionException("offline")
        error = PyiCloudConnectionException
    session.post.side_effect = [response(record(10)), later]
    iterator = scan(scoped, max_pages=1 if failure == "budget" else 10)
    assert next(iterator).uid == 10
    with pytest.raises(error):
        next(iterator)


@pytest.mark.parametrize("option", [{"page_size": 0}, {"max_pages": True}])
def test_invalid_options_make_no_requests(
    scoped: ScopedCase, option: dict[str, Any]
) -> None:
    """All scopes share strict page size and budget validation."""
    _, session, folders, _, _ = scoped
    iterator = scan(scoped, **option)
    with pytest.raises(ValueError):
        list(iterator)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [None, True, 123, "", "  ", "all"])
def test_invalid_scope_makes_no_requests(value: Any) -> None:
    """Reject unsupported filters and invalid thread identifiers before HTTP."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    service = MailService("https://example.test", session, {})
    iterator = service.iter_filtered_messages(folder, filter_by=value)
    with pytest.raises(ValueError, match="filter_by"):
        list(iterator)
    if value != "all":
        iterator = service.iter_thread_messages(folder, value)
        with pytest.raises(ValueError, match="thread_id"):
            list(iterator)
    session.post.assert_not_called()


def test_changing_flag_membership_does_not_revisit_higher_uids(
    scoped: ScopedCase,
) -> None:
    """Keep the boundary despite changed matches; a live filter is not a snapshot."""
    _, session, _, _, _ = scoped
    # A higher UID may newly match, and a lower one may stop matching between pages.
    session.post.side_effect = [response(record(10)), response(record(3)), response()]
    assert [m.uid for m in scan(scoped, page_size=1)] == [10, 3]
    clauses = session.post.call_args_list[1].kwargs["json"]["predicate"]["and"]
    assert clauses[-1]["value"] == 10
