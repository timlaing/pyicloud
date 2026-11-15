"""Offline Notes mutation dispatch bypasses replaying sessions and response hooks."""

# pylint: disable=protected-access

from email.message import Message
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from requests import PreparedRequest, Response, Session
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError

from pyicloud.const import HEADER_DATA
from pyicloud.services.notes import NotesService, NotesWriteUncertain
from pyicloud.services.notes.models.write import NotesModifyOperation, NotesWriteRecord
from pyicloud.session import PyiCloudSession


class RecoveringSession(Session):
    """Synthetic session that would replay a POST after losing its response."""

    def request(self, *args: Any, **kwargs: Any) -> Response:
        try:
            return super().request(*args, **kwargs)
        except RequestsConnectionError:
            return super().request(*args, **kwargs)


def reply(*, deleted: bool = False, status: int = 200) -> Response:
    """Use a correlated synthetic acknowledgement; no real IDs or credentials."""
    response = Response()
    response.status_code = status
    record: dict[str, Any] = {"recordName": "synthetic"}
    if deleted:
        record["deleted"] = True
    else:
        record.update(recordType="Note", recordChangeTag="new", fields={})
    vars(response).update(
        _content=json.dumps({"records": [record]}).encode(), _content_consumed=True
    )
    return response


def modify(notes: NotesService) -> Any:
    """Exercise the low-level API without unrelated metadata discovery reads."""
    operation = NotesModifyOperation(
        operationType="create",
        record=NotesWriteRecord(recordName="synthetic", fields={}),
    )
    return notes.raw.modify_note(operation)


@pytest.mark.parametrize("delete", [False, True])
def test_lost_response_is_not_replayed_by_recovering_session(delete: bool) -> None:
    """Both guarded create/update and irreversible deletion dispatch at most once."""
    session = RecoveringSession()
    session.trust_env = False
    notes = NotesService("https://example.test", cast(Any, session), {})
    invoke = (
        (lambda: notes.raw.delete_empty_folder("synthetic"))
        if delete
        else (lambda: modify(notes))
    )
    with (
        patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=[
                RequestsConnectionError("Synthetic lost response"),
                reply(deleted=delete),
            ],
        ) as send,
        pytest.raises(NotesWriteUncertain),
    ):
        invoke()
    send.assert_called_once()
    adapter = session.get_adapter("https://example.test")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 0
    session.close()


def test_mutation_bypasses_custom_dispatch_hooks_and_adapter_instance_send() -> None:
    """Only the validated pinned base-adapter method can perform the mutation."""
    session = cast(Any, Session())
    session.trust_env = False
    session.post = MagicMock(side_effect=AssertionError("Session POST forbidden"))
    session.request = MagicMock(side_effect=AssertionError("Session request forbidden"))
    session.send = MagicMock(side_effect=AssertionError("Session send forbidden"))
    hook = MagicMock(side_effect=AssertionError("Response hook forbidden"))
    session.hooks["response"] = [hook]
    adapter = session.get_adapter("https://example.test")
    adapter.send = MagicMock(side_effect=AssertionError("Instance send forbidden"))
    other = HTTPAdapter(max_retries=1)
    session.get_adapter = MagicMock(side_effect=[adapter, other])
    notes = NotesService("https://example.test", session, {})
    response = reply()
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        assert modify(notes).record_change_tag == "new"
    send.assert_called_once()
    assert send.call_args.args[0] is adapter
    request: PreparedRequest = send.call_args.args[1]
    assert request.hooks == {"response": []}
    session.get_adapter.assert_called_once()
    for forbidden in (session.post, session.request, session.send, adapter.send, hook):
        forbidden.assert_not_called()
    assert session.hooks["response"] == [hook]
    session.close()


def test_exact_prepared_url_is_checked_and_retrying_mount_is_rejected() -> None:
    """Session query defaults cannot select an unchecked longest-prefix mount."""
    session = Session()
    session.trust_env = False
    session.params = {"default": "yes"}
    notes = NotesService("https://example.test", cast(Any, session), {})
    url = notes.raw._client._http.build_url("/records/modify")
    session.mount(url + "&default=yes", HTTPAdapter(max_retries=2))
    with (
        patch("requests.adapters.HTTPAdapter.send") as send,
        pytest.raises(NotesWriteUncertain),
    ):
        modify(notes)
    send.assert_not_called()
    session.close()


def test_single_dispatch_preserves_session_settings_and_response_cookies() -> None:
    """One-shot writes retain account query defaults, headers, proxies and CA policy."""
    session = Session()
    session.trust_env = False
    session.params = {"default": "yes"}
    session.headers["X-Synthetic"] = "value"
    session.cookies.set("synthetic", "old", domain="example.test", path="/")
    session.verify = "synthetic-ca.pem"
    session.cert = "synthetic-client.pem"
    session.proxies = {"https": "http://proxy.example.test"}
    notes = NotesService("https://example.test", cast(Any, session), {})
    response = reply()
    headers = Message()
    headers.add_header("Set-Cookie", "synthetic=new; Path=/; Secure")
    response.raw = cast(
        Any, SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
    )
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        assert modify(notes).record_change_tag == "new"
    request: PreparedRequest = send.call_args.args[1]
    assert "default=yes" in (request.url or "")
    assert request.headers["X-Synthetic"] == "value"
    assert "synthetic=old" in request.headers["Cookie"]
    assert session.cookies.get("synthetic") == "new"
    assert send.call_args.kwargs["verify"] == "synthetic-ca.pem"
    assert send.call_args.kwargs["cert"] == "synthetic-client.pem"
    assert send.call_args.kwargs["proxies"] == session.proxies
    assert send.call_args.kwargs["stream"] is False
    session.close()


def test_body_failure_preserves_refreshed_cookie_and_closes_response() -> None:
    """A lost body cannot strand stale credentials or trigger another mutation."""
    session = Session()
    session.trust_env = False
    session.cookies.set("synthetic", "old", domain="example.test", path="/")
    notes = NotesService("https://example.test", cast(Any, session), {})
    response = reply()
    headers = Message()
    headers.add_header("Set-Cookie", "synthetic=new; Path=/; Secure")
    response.raw = cast(
        Any, SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
    )
    close = MagicMock(wraps=response.close)
    cast(Any, response).close = close
    with (
        patch("requests.adapters.HTTPAdapter.send", return_value=response) as send,
        patch.object(
            Response,
            "content",
            new_callable=PropertyMock,
            side_effect=RequestsConnectionError("Synthetic lost body"),
        ),
        pytest.raises(NotesWriteUncertain),
    ):
        modify(notes)
    assert session.cookies.get("synthetic") == "new"
    close.assert_called_once()
    send.assert_called_once()
    session.close()


@pytest.mark.parametrize("status", [302, 307])
def test_mutation_redirect_has_no_second_dispatch(status: int) -> None:
    """Direct adapter dispatch never follows a redirect or reports it as success."""
    session = Session()
    session.trust_env = False
    notes = NotesService("https://example.test", cast(Any, session), {})
    response = reply(status=status)
    response.headers["Location"] = "https://example.test/other"
    with (
        patch("requests.adapters.HTTPAdapter.send", return_value=response) as send,
        pytest.raises(NotesWriteUncertain),
    ):
        modify(notes)
    send.assert_called_once()
    session.close()


def test_pyicloud_response_headers_are_retained_without_session_dispatch() -> None:
    """Known persistence helpers retain account response state without replay hooks."""
    session = object.__new__(PyiCloudSession)
    Session.__init__(session)
    session.trust_env = False
    session._data = {}
    notes = NotesService("https://example.test", session, {})
    response = reply()
    header, key = next(iter(HEADER_DATA.items()))
    response.headers[header] = "synthetic"
    with (
        patch("requests.adapters.HTTPAdapter.send", return_value=response),
        patch.object(PyiCloudSession, "_save_session_data", autospec=True) as save,
    ):
        assert modify(notes).record_change_tag == "new"
    assert session._data[key] == "synthetic"
    save.assert_called_once_with(session)
    session.close()


def test_unsupported_mutation_session_is_rejected_before_dispatch() -> None:
    """A duck-typed custom transport cannot bypass the single-dispatch path."""
    session = SimpleNamespace(post=MagicMock())
    notes = NotesService("https://example.test", cast(Any, session), {})
    with pytest.raises(NotesWriteUncertain):
        modify(notes)
    session.post.assert_not_called()


def test_unrelated_cloudkit_requests_keep_session_dispatch() -> None:
    """Normal read POSTs still use their configured session and retry behavior."""
    session = cast(Any, Session())
    session.trust_env = False
    session.post = MagicMock(return_value=reply())
    notes = NotesService("https://example.test", session, {})
    notes.raw._client.raw_post("/records/query", {})
    session.post.assert_called_once()
    session.close()
