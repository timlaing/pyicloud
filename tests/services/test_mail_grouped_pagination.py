"""Thread-domain cursor scans: synthetic protocol, bounds, and failure coverage."""

from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response

from pyicloud.services.mail import MailError, MailFolder, MailService
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

Case = tuple[MailService, MagicMock, MagicMock, MailFolder]


@pytest.fixture(name="case")
def case_fixture() -> Iterator[Case]:
    """One independently guarded mailbox and a mocked JSON transport."""
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    session.post.return_value = response
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, folder


def page() -> dict[str, Any]:
    """Load a fresh invented page via the sanctioned fixture reader."""
    return cast(dict[str, Any], load_fixture("mail", "thread_query_page_response.json"))


def scan(case: Case, **options: Any) -> Any:
    """Create a lazy public iterator without performing a request."""
    return case[0].iter_filtered_threads(case[3], filter_by="flagged", **options)


@pytest.mark.parametrize(
    "filter_by,flag,value", [("unread", "SEEN", False), ("flagged", "FLAGGED", True)]
)
def test_scoped_cursor_queries_are_immutable(
    case: Case, filter_by: Any, flag: str, value: bool
) -> None:
    """Retain exact query scope across replay and require an empty final response."""
    service, session, folders, folder = case
    first = page()
    second = {
        "domainObjects": [{"identifier": "thread-c", "tsMap": {"10": 1700000000000}}],
        "continuationMarker": "cursor-2",
    }
    session.post.return_value.json.side_effect = [first, second, {"domainObjects": []}]
    result = list(
        service.iter_filtered_threads(folder, filter_by=filter_by, page_size=2)
    )
    assert [record.id for record in result] == [
        "synthetic-thread-a",
        "synthetic-thread-b",
        "thread-c",
    ]
    assert all(
        record.folder == folder and record.filter_by == filter_by for record in result
    )
    queries = [call.kwargs["json"] for call in session.post.call_args_list]
    expected = {
        "domain": "thread",
        "properties": ["identifier", "tsMap"],
        "limit": 2,
        "strictLimit": False,
        "predicate": {
            "expression": {"property": "tsMap", "type": "mapValueOf", "value": "10"},
            "type": "ge",
            "value": 0,
            "and": [
                {
                    "expression": {
                        "property": "flags",
                        "type": "fieldOf",
                        "value": flag,
                    },
                    "type": "eq",
                    "value": value,
                }
            ],
        },
        "orderby": {
            "expressions": [{"property": "tsMap", "type": "mapValueOf", "value": "10"}],
            "ascending": False,
        },
    }
    assert queries == [
        expected,
        {**expected, "continuationMarker": "synthetic-thread-cursor"},
        {**expected, "continuationMarker": "cursor-2"},
    ]
    assert folders.call_count == 6
    assert all(
        call.args[0].endswith("/geqs/query") for call in session.post.call_args_list
    )
    assert all(
        call.kwargs["timeout"] == (10.0, 60.0)
        and call.kwargs["allow_redirects"] is False
        for call in session.post.call_args_list
    )
    assert session.post.return_value.close.call_count == 3
    session.get.assert_not_called()


def test_more_than_1000_equal_timestamps_uses_cursors(case: Case) -> None:
    """No timestamp advancement can skip a boundary bucket or thread."""
    _, session, _, _ = case
    records = [
        {"identifier": f"thread-{i}", "tsMap": {"10": 1700000000000}}
        for i in range(1003)
    ]
    session.post.return_value.json.side_effect = [
        {"domainObjects": records[:1000], "continuationMarker": "cursor-1"},
        {"domainObjects": records[1000:], "continuationMarker": "cursor-2"},
        {"domainObjects": []},
    ]
    assert len(list(scan(case, page_size=1000))) == 1003


@pytest.mark.parametrize(
    "terminal",
    [{"domainObjects": []}, {"domainObjects": [], "continuationMarker": None}],
)
def test_empty_marker_free_page_completes(case: Case, terminal: Any) -> None:
    """An explicit empty response is required even when the first page is short."""
    case[1].post.return_value.json.return_value = terminal
    assert not list(scan(case))


@pytest.mark.parametrize(
    "kind",
    [
        "missing_records",
        "missing_map",
        "wrong_folder",
        "duplicate",
        "too_many",
        "missing_cursor",
        "blank_cursor",
        "wrong_cursor_type",
        "empty_with_cursor",
        "infinite",
        "negative_timestamp",
        "string_timestamp",
        "missing_identifier",
        "blank_identifier",
        "boolean_timestamp",
    ],
)
def test_invalid_page_yields_nothing(case: Case, kind: str) -> None:
    """Reject malformed scope, cursors and whole pages before yielding records."""
    _, session, _, _ = case
    data = page()
    if kind == "missing_records":
        del data["domainObjects"]
    elif kind == "missing_map":
        del data["domainObjects"][0]["tsMap"]
    elif kind == "wrong_folder":
        data["domainObjects"][0]["tsMap"] = {"other": 1}
    elif kind == "duplicate":
        data["domainObjects"][1] = data["domainObjects"][0]
    elif kind == "too_many":
        data["domainObjects"].append({"identifier": "thread-c", "tsMap": {"10": 1}})
    elif kind == "missing_cursor":
        del data["continuationMarker"]
    elif kind == "blank_cursor":
        data["continuationMarker"] = " "
    elif kind == "wrong_cursor_type":
        data["continuationMarker"] = 12
    elif kind == "empty_with_cursor":
        data["domainObjects"] = []
    elif kind == "missing_identifier":
        del data["domainObjects"][0]["identifier"]
    elif kind == "blank_identifier":
        data["domainObjects"][0]["identifier"] = " "
    else:
        data["domainObjects"][0]["tsMap"]["10"] = {
            "infinite": float("inf"),
            "negative_timestamp": -1,
            "string_timestamp": "123",
            "boolean_timestamp": True,
        }[kind]
    session.post.return_value.json.return_value = data
    iterator = scan(case, page_size=2)
    with pytest.raises(MailError):
        next(iterator)
    session.post.return_value.close.assert_called_once()


@pytest.mark.parametrize("kind", ["repeat_id", "repeat_cursor", "cursor_cycle"])
def test_cross_page_no_progress_raises(case: Case, kind: str) -> None:
    """Reject repeated identities and cursor cycles under concurrent changes."""
    _, session, _, _ = case
    first = page()
    second = {
        "domainObjects": [{"identifier": "thread-c", "tsMap": {"10": 1}}],
        "continuationMarker": "cursor-2",
    }
    if kind == "repeat_id":
        second["domainObjects"] = first["domainObjects"]
    elif kind == "repeat_cursor":
        second["continuationMarker"] = first["continuationMarker"]
    third = {
        "domainObjects": [{"identifier": "thread-d", "tsMap": {"10": 1}}],
        "continuationMarker": first["continuationMarker"],
    }
    session.post.return_value.json.side_effect = [first, second, third]
    iterator = scan(case, page_size=2)
    assert next(iterator).id == "synthetic-thread-a"
    assert next(iterator).id == "synthetic-thread-b"
    if kind == "cursor_cycle":
        assert next(iterator).id == "thread-c"
    with pytest.raises(MailError):
        next(iterator)


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_size", True),
        ("page_size", 0),
        ("page_size", 1001),
        ("page_size", "2"),
        ("max_pages", True),
        ("max_pages", 0),
        ("max_pages", "2"),
    ],
)
def test_invalid_bounds_make_no_requests(case: Case, field: str, value: Any) -> None:
    """Validate caller limits before authentication-dependent mailbox requests."""
    iterator = scan(case, **{field: value})
    with pytest.raises(ValueError):
        next(iterator)
    case[1].post.assert_not_called()
    case[2].assert_not_called()


@pytest.mark.parametrize("value", [True, None, "starred", "unread,flagged"])
def test_invalid_filter_is_not_combined(case: Case, value: Any) -> None:
    """Only the two observed separate predicates are accepted."""
    iterator = case[0].iter_filtered_threads(case[3], filter_by=value)
    with pytest.raises(ValueError):
        next(iterator)
    case[1].post.assert_not_called()
    case[2].assert_not_called()


@pytest.mark.parametrize("epoch", [None, 0, -1])
def test_unknown_namespace_rejected(case: Case, epoch: Any) -> None:
    """Cursor paging requires positive folder UID validity."""
    folder = case[3].model_copy(update={"uid_validity": epoch})
    iterator = case[0].iter_filtered_threads(folder, filter_by="flagged")
    with pytest.raises(ValueError):
        next(iterator)
    case[2].assert_not_called()


@pytest.mark.parametrize("when", [0, 1, 2, 3])
@pytest.mark.parametrize("replacement", ["deleted", "recreated"])
def test_mailbox_changed_before_or_after_each_request(
    case: Case, when: int, replacement: str
) -> None:
    """Check even the terminal response before declaring enumeration successful."""
    _, session, folders, folder = case
    changed = (
        []
        if replacement == "deleted"
        else [folder.model_copy(update={"uid_validity": 999})]
    )
    folders.side_effect = [[folder]] * when + [changed]
    session.post.return_value.json.side_effect = [page(), {"domainObjects": []}]
    iterator = scan(case, page_size=2)
    with pytest.raises(MailError, match="identity changed"):
        list(iterator)


def test_stable_rename_keeps_id_selected_query(case: Case) -> None:
    """The thread query uses an immutable folder ID, not a reused name."""
    _, session, folders, folder = case
    renamed = folder.model_copy(update={"name": "Renamed"})
    folders.return_value = [renamed]
    session.post.return_value.json.side_effect = [page(), {"domainObjects": []}]
    result = list(scan(case, page_size=2))
    assert all(record.folder.name == "Renamed" for record in result)


def test_budget_exhaustion_is_explicit(case: Case) -> None:
    """Previously yielded groups cannot imply complete enumeration on failure."""
    case[1].post.return_value.json.return_value = page()
    iterator = scan(case, page_size=2, max_pages=1)
    with pytest.raises(MailError, match="budget"):
        list(iterator)


def test_server_failure_propagates_and_closes_response(case: Case) -> None:
    """A non-success response is never an empty terminal page."""
    case[1].post.return_value.status_code = 503
    iterator = scan(case)
    with pytest.raises(MailError, match="HTTP 503"):
        next(iterator)
    case[1].post.return_value.close.assert_called_once()


def test_transport_failure_after_yields_is_not_exhaustion(case: Case) -> None:
    """Keep the original transport failure without retrying or reporting success."""
    _, session, _, _ = case
    response = session.post.return_value
    response.json.return_value = page()
    session.post.side_effect = [response, ReadTimeout("Synthetic timeout")]
    iterator = scan(case, page_size=2)
    assert next(iterator).id == "synthetic-thread-a"
    assert next(iterator).id == "synthetic-thread-b"
    with pytest.raises(ReadTimeout):
        next(iterator)
    assert session.post.call_count == 2
    response.close.assert_called_once()
