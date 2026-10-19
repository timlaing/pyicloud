"""Synthetic browser-derived preview and thread-filter protocol coverage."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.services.mail.models import MessageResponse
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

ReadCase = tuple[MailService, MagicMock, MagicMock, MailMessage]


@pytest.fixture(name="read_case")
def read_fixture() -> Iterator[ReadCase]:
    """Advertise an opaque preview key and independently verify mailbox scope."""
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    session.post.return_value = response
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    record = MessageResponse.model_validate(
        load_fixture("mail", "messages_response.json")
    ).records[0]
    message = MailMessage(**record.model_dump(), folder=folder).model_copy(
        update={"preview_id": "opaque-preview-key"}
    )
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, message


def prepare(case: ReadCase, kind: str) -> dict[str, Any]:
    """Load one sanitized response afresh so mutations remain test-local."""
    filename = (
        "preview_response.json" if kind == "preview" else "thread_filter_response.json"
    )
    data: dict[str, Any] = load_fixture("mail", filename)
    case[1].post.return_value.json.return_value = data
    return data


def run(case: ReadCase, kind: str) -> Any:
    """Exercise either public method under the same mailbox-lifecycle cases."""
    service, _, _, message = case
    if kind == "preview":
        return service.preview(message)
    return service.filtered_threads(message.folder, "unread")


def test_preview_uses_advertised_id_and_validates_scope(read_case: ReadCase) -> None:
    """Never infer a preview selector from UID or download/render the body."""
    prepare(read_case, "preview")
    service, session, folders, message = read_case
    result = service.preview(message)
    assert result is not None
    assert result.text == "Synthetic preview text."
    assert result.message_id == message.id and result.uid == message.uid
    assert result.folder == message.folder
    assert result.text not in repr(result)
    assert folders.call_count == 2
    session.post.assert_called_once()
    call = session.post.call_args
    assert call.args[0].endswith("/message/preview")
    query = call.kwargs["json"]
    assert set(query) == {"folder", "previewIds", "sessionHeaders"}
    assert query["folder"] == "INBOX"
    assert query["previewIds"] == ["opaque-preview-key"]
    assert query["sessionHeaders"]["modseq"] is None
    assert query["sessionHeaders"]["threadmodseq"] is None
    session.get.assert_not_called()
    session.post.return_value.close.assert_called_once()


@pytest.mark.parametrize("missing", [False, True])
def test_absent_and_explicitly_empty_previews_are_distinct(
    read_case: ReadCase, missing: bool
) -> None:
    """An empty snippet is successful data; no record returns None."""
    data = prepare(read_case, "preview")
    if missing:
        data["result"] = []
    else:
        data["result"][0]["preview"] = ""
    result = run(read_case, "preview")
    if missing:
        assert result is None
    else:
        assert result.text == ""


@pytest.mark.parametrize("value", [None, "", " "])
def test_missing_preview_selector_is_never_guessed(
    read_case: ReadCase, value: Any
) -> None:
    """Missing metadata must not trigger an inferred selector or a body fallback."""
    service, session, folders, message = read_case
    with pytest.raises(ValueError, match="preview ID"):
        service.preview(message.model_copy(update={"preview_id": value}))
    session.post.assert_not_called()
    folders.assert_not_called()


def test_preview_rejects_inconsistent_input_scope(read_case: ReadCase) -> None:
    """A caller cannot attach metadata from another mailbox to this folder."""
    service, session, _, message = read_case
    wrong = message.mailbox.model_copy(update={"id": "other"})
    with pytest.raises(ValueError, match="identities"):
        service.preview(message.model_copy(update={"mailbox": wrong}))
    session.post.assert_not_called()


@pytest.mark.parametrize("case", ["wrong_folder", "wrong_uid", "ambiguous"])
def test_preview_never_exposes_mismatched_or_ambiguous_text(
    read_case: ReadCase, case: str
) -> None:
    """Reject wrong response identities before returning any snippet text."""
    data = prepare(read_case, "preview")
    if case == "wrong_folder":
        data["sessionHeaders"]["folder"] = "Elsewhere"
    elif case == "wrong_uid":
        data["result"][0]["messageGuid"] = "INBOX/8"
    else:
        data["result"].append(data["result"][0])
    with pytest.raises(MailError):
        run(read_case, "preview")


@pytest.mark.parametrize(
    "filter_by,key", [("unread", "unseen"), ("flagged", "flagged")]
)
def test_filters_use_browser_threads_not_guessed_bits(
    read_case: ReadCase, filter_by: Any, key: str
) -> None:
    """Use a single observed filter and fresh context without integer flag masks."""
    prepare(read_case, "filter")
    service, session, folders, message = read_case
    result = service.filtered_threads(message.folder, filter_by, limit=3)
    assert result.filter_by == filter_by
    assert result.complete is False and result.limit == 3
    assert result.results[0].id == "synthetic-filter-thread"
    assert result.results[0].count == 2
    assert folders.call_count == 2
    session.post.assert_called_once()
    call = session.post.call_args
    assert call.args[0].endswith("/thread/search")
    query = call.kwargs["json"]
    assert query["filters"] == {key: True}
    assert query["responseType"] == "THREAD_DIGEST"
    assert query["maxResults"] == 3
    assert query["includeFolderStatus"] is True
    assert query["sessionHeaders"]["folder"] == "INBOX"
    assert query["sessionHeaders"]["modseq"] is None
    assert query["sessionHeaders"]["threadmodseq"] is None
    session.get.assert_not_called()


def test_empty_filtered_results_remain_incomplete(read_case: ReadCase) -> None:
    """An empty filter result is not a proof of conversation completeness."""
    data = prepare(read_case, "filter")
    data.update(threadList=[], totalThreadsReturned=0)
    result = run(read_case, "filter")
    assert result.results == []
    assert result.complete is False


@pytest.mark.parametrize("case", ["wrong_folder", "count", "duplicates", "oversize"])
def test_filter_response_is_checked_before_exposing_groups(
    read_case: ReadCase, case: str
) -> None:
    """Inconsistent size, identity, scope, or counts cannot become successful pages."""
    data = prepare(read_case, "filter")
    service, _, _, message = read_case
    limit = 50
    if case == "wrong_folder":
        data["sessionHeaders"]["folder"] = "Elsewhere"
    elif case == "count":
        data["totalThreadsReturned"] = 2
    else:
        data["threadList"].append(data["threadList"][0])
        data["totalThreadsReturned"] = 2
        if case == "oversize":
            limit = 1
    with pytest.raises(MailError):
        service.filtered_threads(message.folder, "unread", limit=limit)


@pytest.mark.parametrize("value", [None, True, "starred", "unread,flagged"])
def test_invalid_filter_is_not_silently_combined(
    read_case: ReadCase, value: Any
) -> None:
    """Only the two separately observed browser filter names are accepted."""
    service, session, folders, message = read_case
    with pytest.raises(ValueError, match="filter_by"):
        service.filtered_threads(message.folder, value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [True, 0, 1001, "2"])
def test_filter_limit_fails_before_http(read_case: ReadCase, value: Any) -> None:
    """Invalid bounds must not cause mailbox or thread requests."""
    service, session, folders, message = read_case
    with pytest.raises(ValueError, match="limit"):
        service.filtered_threads(message.folder, "unread", limit=value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("kind", ["preview", "filter"])
@pytest.mark.parametrize("value", [None, 0, -1])
def test_known_mailbox_epoch_is_required(
    read_case: ReadCase, kind: str, value: Any
) -> None:
    """Folder-name reads require a known, independently verified UID namespace."""
    service, session, folders, message = read_case
    changed = message.model_copy(
        update={"folder": message.folder.model_copy(update={"uid_validity": value})}
    )
    case = (service, session, folders, changed)
    with pytest.raises(ValueError, match="UID validity"):
        run(case, kind)
    folders.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize("kind", ["preview", "filter"])
@pytest.mark.parametrize("during_request", [False, True])
@pytest.mark.parametrize("deleted", [False, True])
def test_mailbox_recreation_or_deletion_aborts_reads(
    read_case: ReadCase, kind: str, during_request: bool, deleted: bool
) -> None:
    """Detect observed recreation before and after a preview or filter request."""
    prepare(read_case, kind)
    _, session, folders, message = read_case
    replacement = (
        [] if deleted else [message.folder.model_copy(update={"uid_validity": 9999})]
    )
    folders.side_effect = (
        [[message.folder], replacement] if during_request else [replacement]
    )
    with pytest.raises(MailError, match="identity changed"):
        run(read_case, kind)
    assert session.post.call_count == int(during_request)


@pytest.mark.parametrize("kind", ["preview", "filter"])
def test_safe_rename_updates_returned_context(read_case: ReadCase, kind: str) -> None:
    """A rename preserves UID validity and updates the returned folder metadata."""
    prepare(read_case, kind)
    _, _, folders, message = read_case
    renamed = message.folder.model_copy(update={"name": "Renamed"})
    folders.side_effect = [[message.folder], [renamed]]
    result = run(read_case, kind)
    assert result.folder.name == "Renamed"


@pytest.mark.parametrize("kind", ["preview", "filter"])
@pytest.mark.parametrize("data", [None, {}, {"result": [], "threadList": []}])
def test_malformed_read_responses_are_not_empty_success(
    read_case: ReadCase, kind: str, data: Any
) -> None:
    """Missing required response fields remain validation errors, not empty data."""
    read_case[1].post.return_value.json.return_value = data
    with pytest.raises(MailError):
        run(read_case, kind)
