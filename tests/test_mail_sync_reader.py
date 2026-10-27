"""Offline wire, scope, expiry, and metadata checks for the private live reader."""

from collections.abc import Iterator
from typing import Any, NamedTuple
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response

from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudConnectionException,
)
from pyicloud.services.mail._sync import (
    SyncCheckpoint,
    SyncHistoryExpired,
    SyncResetRequired,
)
from pyicloud.services.mail._sync_reader import MailSyncReader
from pyicloud.services.mail.models import MailFolder, MailMessage
from pyicloud.services.mail.service import MailService
from tests.fixture_loader import load_fixture


class Case(NamedTuple):
    """An actual Mail service with offline transport and folder discovery."""

    reader: MailSyncReader
    service: MailService
    session: MagicMock
    response: MagicMock
    folders: MagicMock


def envelope(**changes: Any) -> dict[str, Any]:
    """Keep every required counter/capability field explicit in small edge cases."""
    return {
        "sessionHeaders": {
            "folder": "INBOX",
            "modseq": 100,
            "threadmodseq": 200,
            "condstore": 1,
            "qresync": 1,
            "threadmode": 1,
        },
        "events": [],
        **changes,
    }


@pytest.fixture(name="case")
def reader_fixture() -> Iterator[Case]:
    """Never use live account metadata or network in tests."""
    session, response = MagicMock(), MagicMock(spec=Response)
    session.service.account_name = "synthetic@example.test"
    response.status_code = 200
    response.json.return_value = envelope()
    session.post.return_value = response
    service = MailService(
        "https://example.test", session, {"dsid": "synthetic-account"}
    )
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=123)
    reader = MailSyncReader(service, folder, page_size=20, max_pages=10)
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield Case(reader, service, session, response, folders)


def checkpoint(case: Case, **changes: Any) -> SyncCheckpoint:
    """The cursor is tied to the adapter's independently pinned account scope."""
    return SyncCheckpoint.model_validate({
        "account": case.reader.account,
        "mailbox_id": "10",
        "folder": "INBOX",
        "uid_validity": 123,
        "modseq": 100,
        "threadmodseq": 200,
        **changes,
    })


def metadata(case: Case, **changes: Any) -> MailMessage:
    """Return typed metadata without private text, bodies, or attachment links."""
    return MailMessage.model_validate({
        "identifier": "test-message",
        "uid": 7,
        "mboxRef": {"id": "10"},
        "folder": case.reader.folder,
        "appleThrid": "test-thread",
        "flags": 1,
        **changes,
    })


def test_fresh_checkpoint_wire_and_account_privacy(case: Case) -> None:
    """Bootstrap observation uses null counters, bounded timeouts, and no redirects."""
    current = case.reader.current_checkpoint()
    assert current == checkpoint(case)
    assert "synthetic@example.test" not in current.model_dump_json()
    case.session.post.assert_called_once_with(
        "https://example.test/mailws2/v1/event/list",
        params=case.service.params,
        json={
            "sessionHeaders": {
                "folder": "INBOX",
                "modseq": None,
                "threadmodseq": None,
                "condstore": 1,
                "qresync": 1,
                "threadmode": 1,
            }
        },
        timeout=(10.0, 60.0),
        allow_redirects=False,
    )
    assert case.folders.call_count == 2
    case.response.close.assert_called_once()


def test_incremental_poll_replays_exact_counters(case: Case) -> None:
    """The delta path never discards a request checkpoint to manufacture success."""
    wire = envelope(events=["* 5 EXISTS (UID 8)"])
    wire["sessionHeaders"]["modseq"] = 101
    case.response.json.return_value = wire
    batch = case.reader.poll_changes(checkpoint(case))
    assert batch.checkpoint.modseq == 101
    assert batch.events[0].kind == "arrival"
    headers = case.session.post.call_args.kwargs["json"]["sessionHeaders"]
    assert headers["modseq"] == 100
    assert headers["threadmodseq"] == 200


def test_thread_invalidation_poll_is_validated(case: Case) -> None:
    """A deletion-shaped event still uses the exact requested counter stride."""
    case.response.json.return_value = load_fixture(
        "mail", "sync_thread_invalidation_response.json"
    )
    batch = case.reader.poll_changes(checkpoint(case))
    assert batch.events[-1].kind == "thread-invalidation"
    assert batch.checkpoint == checkpoint(case, modseq=101, threadmodseq=201)
    case.session.post.assert_called_once()


def test_expired_history_never_retries_with_null_counters(case: Case) -> None:
    """An explicit reset indication preserves the old caller checkpoint."""
    case.response.json.return_value = {
        "errorCode": "MODSEQ_TOO_OLD",
        "private": "secret",
    }
    prepared_checkpoint_1 = checkpoint(case)
    with pytest.raises(SyncHistoryExpired, match="expired") as error:
        case.reader.poll_changes(prepared_checkpoint_1)
    assert "secret" not in str(error.value)
    case.session.post.assert_called_once()
    case.response.close.assert_called_once()


def test_session_http_expiry_exception_translates_and_closes(case: Case) -> None:
    """PyiCloudSession may raise before returning the HTTP error response."""
    case.response.json.return_value = {"errorCode": "MODSEQ_TOO_OLD"}
    case.session.post.side_effect = PyiCloudAPIResponseException(
        "synthetic", 400, case.response
    )
    prepared_checkpoint_1 = checkpoint(case)
    with pytest.raises(SyncHistoryExpired, match="expired"):
        case.reader.poll_changes(prepared_checkpoint_1)
    case.session.post.assert_called_once()
    case.response.close.assert_called_once()


def test_auth_response_is_closed_without_reinterpreting_its_body(case: Case) -> None:
    """Authentication stays authentication, even when an error body is misleading."""
    failure = PyiCloudAuthRequiredException("synthetic@example.test", case.response)
    case.session.post.side_effect = failure
    case.response.json.return_value = {"errorCode": "MODSEQ_TOO_OLD"}
    with pytest.raises(PyiCloudAuthRequiredException) as error:
        case.reader.current_checkpoint()
    assert error.value is failure
    case.response.json.assert_not_called()
    case.response.close.assert_called_once()


@pytest.mark.parametrize("invalid_json", [False, True])
def test_other_api_errors_keep_their_type(case: Case, invalid_json: bool) -> None:
    """Non-expiry API errors are not treated as a successful empty poll."""
    case.response.json.return_value = {"errorCode": "OTHER"}
    if invalid_json:
        case.response.json.side_effect = ValueError("private response")
    failure = PyiCloudAPIResponseException("synthetic", 403, case.response)
    case.session.post.side_effect = failure
    prepared_checkpoint_1 = checkpoint(case)
    with pytest.raises(PyiCloudAPIResponseException) as error:
        case.reader.poll_changes(prepared_checkpoint_1)
    assert error.value is failure
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        PyiCloudConnectionException("synthetic timeout"),
        PyiCloudAuthRequiredException(
            "synthetic@example.test", MagicMock(spec=Response)
        ),
        ReadTimeout("synthetic"),
    ],
)
def test_transport_and_auth_failures_are_not_resets(
    case: Case, failure: Exception
) -> None:
    """Do not confuse an unreachable service or expired auth with expired history."""
    case.session.post.side_effect = failure
    prepared_checkpoint_1 = checkpoint(case)
    with pytest.raises(type(failure)) as error:
        case.reader.poll_changes(prepared_checkpoint_1)
    assert error.value is failure
    case.session.post.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"folder": "Other"},
        {"modseq": None},
        {"threadmodseq": None},
        {"modseq": True},
        {"modseq": -1},
        {"modseq": "100"},
        {"condstore": 0},
        {"qresync": True},
    ],
)
def test_invalid_response_headers(case: Case, change: dict[str, Any]) -> None:
    """Consumed response headers are strict, including capabilities and counters."""
    wire = envelope()
    wire["sessionHeaders"].update(change)
    case.response.json.return_value = wire
    with pytest.raises(SyncResetRequired):
        case.reader.current_checkpoint()
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"sessionHeaders": {}},
        envelope(errorCode="OTHER"),
        envelope(payloadTooLarge=True),
        envelope(continuationMarker="unknown"),
        envelope(events=["* 1 EXISTS"] * 1025),
        envelope(events=["x" * 8193]),
        envelope(events=["* 1 EXPUNGE"]),
        envelope(events=None),
    ],
)
def test_incomplete_or_unknown_envelopes_fail_closed(case: Case, body: Any) -> None:
    """Missing events and unsupported fields cannot become an empty success."""
    case.response.json.return_value = body
    with pytest.raises(SyncResetRequired):
        case.reader.current_checkpoint()
    case.response.close.assert_called_once()


def test_bad_http_status_and_json_close_response(case: Case) -> None:
    """Even a nominal envelope is not accepted after redirect or HTTP failure."""
    case.response.status_code = 302
    with pytest.raises(SyncResetRequired):
        case.reader.current_checkpoint()
    case.response.status_code = 200
    case.response.json.side_effect = ValueError("private-json")
    with pytest.raises(SyncResetRequired) as error:
        case.reader.current_checkpoint()
    assert "private-json" not in str(error.value)
    assert case.response.close.call_count == 2


@pytest.mark.parametrize(
    "change",
    [
        {"account": "wrong"},
        {"mailbox_id": "other"},
        {"folder": "Renamed"},
        {"uid_validity": 124},
    ],
)
def test_foreign_checkpoint_rejected_before_poll(
    case: Case, change: dict[str, Any]
) -> None:
    """A persisted cursor cannot be replayed into a different folder or account."""
    prepared_checkpoint_1 = checkpoint(case, **change)
    with pytest.raises(SyncResetRequired, match="another scope"):
        case.reader.poll_changes(prepared_checkpoint_1)
    case.session.post.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "duplicate", "renamed", "recreated"])
def test_folder_guard_before_and_after_poll(case: Case, kind: str) -> None:
    """Folder membership and identity are checked on both sides of each poll."""
    folder = case.reader.folder
    broken = {
        "missing": [],
        "duplicate": [folder, folder],
        "renamed": [folder.model_copy(update={"name": "Renamed"})],
        "recreated": [folder.model_copy(update={"uid_validity": 124})],
    }[kind]
    case.folders.side_effect = [[folder], broken]
    with pytest.raises(SyncResetRequired, match="folder changed"):
        case.reader.current_checkpoint()
    case.response.close.assert_called_once()


def test_account_switch_guard(case: Case) -> None:
    """Mutable service parameters cannot redirect saved state into another account."""
    case.service.params["dsid"] = "other-account"
    with pytest.raises(SyncResetRequired, match="account changed"):
        case.reader.current_checkpoint()
    case.session.post.assert_not_called()


@pytest.mark.parametrize(
    "account,dsid",
    [
        ("", "test"),
        ("  ", "test"),
        (None, "test"),
        ("test", None),
        ("test", True),
        ("test", ""),
    ],
)
def test_missing_account_scope_rejected(case: Case, account: Any, dsid: Any) -> None:
    """A display name or missing DSID is not sufficient synchronization scope."""
    case.session.service.account_name = account
    case.service.params["dsid"] = dsid
    with pytest.raises(SyncResetRequired):
        MailSyncReader(case.service, case.reader.folder)


@pytest.mark.parametrize(
    "options",
    [
        {"page_size": 0},
        {"page_size": 1001},
        {"page_size": True},
        {"max_pages": 0},
        {"max_pages": True},
    ],
)
def test_invalid_paging_configuration(case: Case, options: dict[str, Any]) -> None:
    """Do not silently accept a coercible or unbounded scan configuration."""
    with pytest.raises(ValueError):
        MailSyncReader(case.service, case.reader.folder, **options)


@pytest.mark.parametrize("validity", [None, 0, True])
def test_invalid_initial_namespace(case: Case, validity: Any) -> None:
    """Require positive, non-boolean UID validity before doing any work."""
    prepared_argument_1 = case.reader.folder.model_copy(
        update={"uid_validity": validity}
    )
    with pytest.raises(ValueError):
        MailSyncReader(case.service, prepared_argument_1)


def test_inventory_and_thread_delegate_to_complete_iterators(case: Case) -> None:
    """Never substitute bounded list/thread snapshots for a complete inventory."""
    record = metadata(case)
    with (
        patch.object(
            case.service, "iter_messages", return_value=iter([record])
        ) as scan,
        patch.object(
            MailService, "_iter_thread_messages", return_value=iter([record])
        ) as thread,
    ):
        assert list(case.reader.iter_inventory())[0].uid == 7
        assert list(case.reader.iter_thread_inventory("test-thread"))[0].uid == 7
    scan.assert_called_once_with(case.reader.folder, page_size=20, max_pages=10)
    assert thread.call_count == 1
    assert thread.call_args.args == (case.service, case.reader.folder, "test-thread")
    assert thread.call_args.kwargs["page_size"] == 20
    assert thread.call_args.kwargs["max_pages"] == 10
    assert callable(thread.call_args.kwargs["check_folder"])


def test_inventory_exhaustion_checks_final_scope(case: Case) -> None:
    """A rename at scan completion invalidates all previously yielded records."""
    case.folders.side_effect = [[case.reader.folder], []]
    with (
        patch.object(case.service, "iter_messages", return_value=iter([])),
        pytest.raises(SyncResetRequired),
    ):
        list(case.reader.iter_inventory())


@pytest.mark.parametrize(
    "changes",
    [
        {"mboxRef": {"id": "other"}},
        {"folder": MailFolder(identifier="other", name="INBOX", uidValidity=123)},
        {"folder": MailFolder(identifier="10", name="Renamed", uidValidity=123)},
        {"folder": MailFolder(identifier="10", name="INBOX", uidValidity=124)},
        {"flags": -1},
        {"flags": None},
    ],
)
def test_foreign_or_invalid_metadata(case: Case, changes: dict[str, Any]) -> None:
    """Do not bind metadata from another namespace to the current checkpoint."""
    with (
        patch.object(
            case.service,
            "iter_messages",
            return_value=iter([metadata(case, **changes)]),
        ),
        pytest.raises(SyncResetRequired),
    ):
        list(case.reader.iter_inventory())


def test_wrong_thread_response_rejected(case: Case) -> None:
    """The full thread iterator still has its returned identity checked locally."""
    with (
        patch.object(
            MailService, "_iter_thread_messages", return_value=iter([metadata(case)])
        ),
        pytest.raises(SyncResetRequired),
    ):
        list(case.reader.iter_thread_inventory("other-thread"))


def test_exact_uid_refresh_and_absence(case: Case) -> None:
    """The adapter preserves authoritative absence and validates returned UIDs."""
    with patch.object(
        MailService, "_lookup_message", return_value=metadata(case)
    ) as lookup:
        record = case.reader.message_by_uid(7)
        assert record is not None
        assert record.message_id == "test-message"
        assert lookup.call_count == 1
        assert lookup.call_args.args == (case.service, case.reader.folder, "uid", 7)
        assert callable(lookup.call_args.kwargs["check_folder"])
    with patch.object(MailService, "_lookup_message", return_value=None):
        assert case.reader.message_by_uid(7) is None
    with (
        patch.object(MailService, "_lookup_message", return_value=metadata(case)),
        pytest.raises(SyncResetRequired, match="different UID"),
    ):
        case.reader.message_by_uid(8)


@pytest.mark.parametrize("present", [False, True])
def test_exact_uid_uses_two_discovery_calls(case: Case, present: bool) -> None:
    """The shared lookup performs both stronger sync guards, without duplicate I/O."""
    case.response.json.return_value = {
        "domainObjects": [metadata(case).model_dump(exclude={"folder"}, by_alias=True)]
        if present
        else []
    }
    record = case.reader.message_by_uid(7)
    assert (record is not None) is present
    assert case.folders.call_count == 2
    case.session.post.assert_called_once()


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("change", ["missing", "renamed", "recreated", "account"])
def test_exact_uid_retains_final_identity_guard(
    case: Case, present: bool, change: str
) -> None:
    """Even absence cannot hide an account, path, or namespace change during lookup."""
    folder = case.reader.folder
    broken = {
        "missing": [],
        "renamed": [folder.model_copy(update={"name": "Renamed"})],
        "recreated": [folder.model_copy(update={"uid_validity": 124})],
        "account": [folder],
    }[change]
    case.folders.side_effect = [[folder], broken]

    def response_body() -> dict[str, Any]:
        if change == "account":
            case.service.params["dsid"] = "changed-account"
        return {
            "domainObjects": [
                metadata(case).model_dump(exclude={"folder"}, by_alias=True)
            ]
            if present
            else []
        }

    case.response.json.side_effect = response_body
    with pytest.raises(SyncResetRequired):
        case.reader.message_by_uid(7)
    case.session.post.assert_called_once()


@pytest.mark.parametrize("uid", [True, 0, -1, "7"])
def test_invalid_uid_never_reads(case: Case, uid: Any) -> None:
    """The private lookup optimization retains the public input validation."""
    with pytest.raises(ValueError, match="positive integer"):
        case.reader.message_by_uid(uid)
    case.folders.assert_not_called()
    case.session.post.assert_not_called()


@pytest.mark.parametrize("present", [False, True])
def test_thread_refresh_reuses_page_identity_checks(case: Case, present: bool) -> None:
    """No duplicate outer listings, with a pinned guard on both sides of each page."""
    record = metadata(case).model_dump(exclude={"folder"}, by_alias=True)
    case.response.json.side_effect = (
        [{"domainObjects": [record]}, {"domainObjects": []}]
        if present
        else [{"domainObjects": []}]
    )
    rows = list(case.reader.iter_thread_inventory("test-thread"))
    assert len(rows) == int(present)
    pages = 2 if present else 1
    assert case.session.post.call_count == pages
    assert case.folders.call_count == 2 * pages


def test_thread_refresh_checks_identity_after_empty_terminal_page(case: Case) -> None:
    """An empty result cannot hide a path change at scan completion."""
    case.folders.side_effect = [[case.reader.folder], []]
    case.response.json.return_value = {"domainObjects": []}
    with pytest.raises(SyncResetRequired, match="folder changed"):
        list(case.reader.iter_thread_inventory("test-thread"))


def test_thread_refresh_checks_account_before_the_next_page(case: Case) -> None:
    """A consumer-side scope switch is rejected before another metadata request."""
    case.response.json.return_value = {
        "domainObjects": [metadata(case).model_dump(exclude={"folder"}, by_alias=True)]
    }
    rows = case.reader.iter_thread_inventory("test-thread")
    assert next(rows).uid == 7
    case.service.params["dsid"] = "changed-account"
    with pytest.raises(SyncResetRequired, match="account changed"):
        next(rows)
    case.session.post.assert_called_once()
