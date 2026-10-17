"""Offline safety and exhaustion checks for UID-based Mail pagination."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import MailError, MailFolder, MailService
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

PaginationCase = tuple[MailService, MagicMock, MagicMock, MailFolder]


@pytest.fixture(name="pagination")
def pagination_fixture() -> Iterator[PaginationCase]:
    """A stable mailbox and offline transport, with separately counted epoch checks."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, folder


def record(uid: int, **updates: Any) -> dict[str, Any]:
    """Minimal synthetic identity with intentionally identical message dates."""
    return {
        "identifier": f"synthetic-message-{uid}",
        "uid": uid,
        "mboxRef": {"id": "10"},
        "stateInternalDate": 1700000000.125,
        **updates,
    }


def response(records: list[dict[str, Any]]) -> MagicMock:
    """Return a minimal closed-on-consumption HTTP page."""
    result = MagicMock(spec=Response)
    result.status_code = 200
    result.json.return_value = {"domainObjects": records}
    return result


def test_uid_pages_use_strict_boundaries_and_explicit_exhaustion(
    pagination: PaginationCase,
) -> None:
    """Synthetic capture shape, equal dates, a short page, then an empty terminator."""
    service, session, folders, folder = pagination
    first = response([])
    first.json.return_value = load_fixture("mail", "messages_uid_page_response.json")
    pages = [first, response([record(8)]), response([])]
    session.post.side_effect = pages

    messages = list(service.iter_messages(folder, page_size=2))

    assert [message.uid for message in messages] == [10, 9, 8]
    assert all(message.folder == folder for message in messages)
    assert folders.call_count == 4
    queries = [call.kwargs["json"] for call in session.post.call_args_list]
    assert queries[0]["orderby"] == {
        "ascending": False,
        "expressions": [{"property": "uid", "type": "property"}],
    }
    assert len(queries[0]["predicate"]["and"]) == 1
    for query, before in zip(queries[1:], [9, 8], strict=True):
        assert query["predicate"]["value"] == folder.id
        assert query["predicate"]["and"][0]["value"] is False
        assert query["predicate"]["and"][-1] == {
            "type": "lt",
            "expression": {"type": "property", "property": "uid"},
            "value": before,
        }
        assert "continuationMarker" not in query
    for call, page in zip(session.post.call_args_list, pages, strict=True):
        assert call.args[0].endswith("/mailws2/v1/message/list")
        assert call.kwargs["timeout"] == (10, 60)
        assert call.kwargs["allow_redirects"] is False
        page.close.assert_called_once()


def test_iteration_is_lazy_and_does_not_prefetch(pagination: PaginationCase) -> None:
    """Creating or partially consuming an iterator makes no later-page requests."""
    service, session, folders, folder = pagination
    page = response([record(10), record(9)])
    session.post.return_value = page
    iterator = service.iter_messages(folder, page_size=2)
    session.post.assert_not_called()
    folders.assert_not_called()
    assert next(iterator).uid == 10
    assert next(iterator).uid == 9
    session.post.assert_called_once()
    page.close.assert_called_once()


def test_empty_mailbox_is_exhausted(pagination: PaginationCase) -> None:
    """A verified empty first page is a successful empty enumeration."""
    service, session, _, folder = pagination
    session.post.return_value = response([])
    assert not list(service.iter_messages(folder))
    session.post.assert_called_once()


def test_full_page_still_requires_empty_terminator(pagination: PaginationCase) -> None:
    """An exact multiple of page size does not lose its terminal verification."""
    service, session, _, folder = pagination
    session.post.side_effect = [
        response([record(10)]),
        response([record(9)]),
        response([]),
    ]
    assert [item.uid for item in service.iter_messages(folder, page_size=1)] == [10, 9]
    assert session.post.call_count == 3


def test_more_than_one_thousand_messages_and_equal_dates(
    pagination: PaginationCase,
) -> None:
    """A large synthetic mailbox crosses the former per-request ceiling without gaps."""
    service, session, _, folder = pagination
    session.post.side_effect = [
        response([record(uid) for uid in range(1003, 3, -1)]),
        response([record(uid) for uid in range(3, 0, -1)]),
        response([]),
    ]
    result = list(service.iter_messages(folder, page_size=1000))
    assert [item.uid for item in result] == list(range(1003, 0, -1))
    assert len({item.id for item in result}) == 1003


def test_duplicate_records_within_a_page_are_not_yielded_twice(
    pagination: PaginationCase,
) -> None:
    """Identical repeated identities do not duplicate output or stall progress."""
    service, session, _, folder = pagination
    session.post.side_effect = [
        response([record(10), record(10), record(9)]),
        response([]),
    ]
    assert [item.uid for item in service.iter_messages(folder, page_size=3)] == [10, 9]


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([record(10), record(10, identifier="different")], "conflicting identities"),
        ([record(10), record(9, identifier="synthetic-message-10")], "multiple UIDs"),
        ([record(9), record(10)], "descending UID"),
        ([record(10, mboxRef={"id": "wrong-folder"})], "another folder"),
        ([record(10), record(10, mboxRef={"id": "wrong-folder"})], "another folder"),
    ],
)
def test_invalid_pages_fail_before_yielding(
    pagination: PaginationCase, records: list[dict[str, Any]], message: str
) -> None:
    """A contradictory page must not leak any of its records to callers."""
    service, session, _, folder = pagination
    session.post.return_value = response(records)
    with pytest.raises(MailError, match=message):
        next(service.iter_messages(folder, page_size=3))


def test_excess_records_are_rejected(pagination: PaginationCase) -> None:
    """A server ignoring the requested size does not silently inflate the page."""
    service, session, _, folder = pagination
    session.post.return_value = response([record(10), record(9)])
    with pytest.raises(MailError, match="exceeded"):
        list(service.iter_messages(folder, page_size=1))


@pytest.mark.parametrize("next_uid", [10, 11])
def test_non_advancing_boundary_is_rejected(
    pagination: PaginationCase, next_uid: int
) -> None:
    """Reject repeated or restarted pages instead of looping or skipping mail."""
    service, session, _, folder = pagination
    session.post.side_effect = [response([record(10)]), response([record(next_uid)])]
    with pytest.raises(MailError, match="boundary"):
        list(service.iter_messages(folder, page_size=1))
    assert session.post.call_count == 2


def test_cross_page_identifier_reuse_is_rejected(pagination: PaginationCase) -> None:
    """A changed identity cannot masquerade as a fresh lower UID."""
    service, session, _, folder = pagination
    session.post.side_effect = [
        response([record(10)]),
        response([record(9, identifier="synthetic-message-10")]),
    ]
    with pytest.raises(MailError, match="across UID pages"):
        list(service.iter_messages(folder, page_size=1))


@pytest.mark.parametrize(
    "replacement", [[], [MailFolder(identifier="10", name="INBOX", uidValidity=5678)]]
)
@pytest.mark.parametrize("during_page", [False, True])
def test_changed_mailbox_aborts_enumeration(
    pagination: PaginationCase, replacement: list[MailFolder], during_page: bool
) -> None:
    """Epoch changes or deletion are detected before exposing a page's records."""
    service, session, folders, folder = pagination
    session.post.return_value = response([record(10)])
    folders.side_effect = [[folder], replacement] if during_page else [replacement]
    with pytest.raises(MailError, match="identity changed"):
        next(service.iter_messages(folder))
    assert session.post.call_count == int(during_page)


@pytest.mark.parametrize("validity", [None, 0, -1])
def test_missing_mailbox_epoch_fails_without_http(
    pagination: PaginationCase, validity: int | None
) -> None:
    """An unbound UID namespace must not be enumerated."""
    service, session, folders, folder = pagination
    unknown = folder.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="UID validity"):
        list(service.iter_messages(unknown))
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_invalid_page_budget_fails_without_http(
    pagination: PaginationCase, value: Any
) -> None:
    """Budgets are positive integers, not truth values or coerced strings."""
    service, session, folders, folder = pagination
    with pytest.raises(ValueError, match="max_pages"):
        list(service.iter_messages(folder, max_pages=value))
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("size", [0, 1001, True, 1.5])
def test_invalid_page_size_fails_without_http(
    pagination: PaginationCase, size: Any
) -> None:
    """Iterator page sizes retain the bounded API's validation."""
    service, session, _, folder = pagination
    with pytest.raises(ValueError, match="limit"):
        list(service.iter_messages(folder, page_size=size))
    session.post.assert_not_called()


def test_page_budget_does_not_claim_completion(pagination: PaginationCase) -> None:
    """A spent budget raises even if the last allowed page was short."""
    service, session, _, folder = pagination
    session.post.return_value = response([record(10)])
    iterator = service.iter_messages(folder, page_size=50, max_pages=1)
    assert next(iterator).uid == 10
    with pytest.raises(MailError, match="incomplete"):
        next(iterator)
    session.post.assert_called_once()


def test_transport_failure_does_not_become_exhaustion(
    pagination: PaginationCase,
) -> None:
    """Earlier records may be consumed, but a later network error still propagates."""
    service, session, _, folder = pagination
    session.post.side_effect = [
        response([record(10)]),
        PyiCloudConnectionException("offline"),
    ]
    with pytest.raises(PyiCloudConnectionException):
        list(service.iter_messages(folder))


def test_mailbox_rename_preserves_current_context(pagination: PaginationCase) -> None:
    """An unchanged epoch with a renamed folder is safe and uses its current name."""
    service, session, folders, folder = pagination
    renamed = folder.model_copy(update={"name": "Renamed"})
    folders.return_value = [renamed]
    session.post.side_effect = [response([record(10)]), response([])]
    assert list(service.iter_messages(folder))[0].folder.name == "Renamed"
