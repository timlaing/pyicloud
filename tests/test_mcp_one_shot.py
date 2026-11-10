"""Offline direct dispatch, exact endpoint policy and pre-consumption admission."""

from email.message import Message
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.mcp.one_shot import (
    checked_adapter,
    mutation_session,
    prepared_url,
    request_once,
)
from pyicloud.services.drive import DriveService
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.parametrize("bounded", [False, True])
def test_direct_dispatch_bypasses_custom_session_replay_and_hooks(
    bounded: bool,
) -> None:
    """Above-adapter retries cannot be reached by the direct dispatch path."""

    class ReplayingSession(Session):
        """Synthetic custom replay methods that must never be called."""

        def request(self, *_args: Any, **_kwargs: Any) -> Response:
            raise AssertionError("Custom request must not run")

        def send(self, *_args: Any, **_kwargs: Any) -> Response:
            raise AssertionError("Custom send must not run")

    session = ReplayingSession()
    session.trust_env = False
    session.cookies.set("synthetic", "value", domain="example.test", path="/")
    adapter = BoundedAdapter(30) if bounded else HTTPAdapter(max_retries=0)
    session.mount("https://", adapter)
    hook = MagicMock(side_effect=AssertionError("Response hook must not run"))
    session.hooks["response"] = [hook]
    response = Response()
    response.status_code = 200
    vars(response).update(_content=b"{}", _content_consumed=True)
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        if not bounded:
            with pytest.raises(WriteDenied, match="deadline_policy_unavailable"):
                request_once(session, "POST", "https://example.test/write")
            send.assert_not_called()
            hook.assert_not_called()
            session.close()
            return
        result = request_once(
            session,
            "POST",
            "https://example.test/write",
            json={"exact": True},
            allow_redirects=False,
        )
    assert result.status_code == 200
    send.assert_called_once()
    request = send.call_args.args[-1]
    assert request.hooks == {"response": []}
    assert "synthetic=value" in request.headers["Cookie"]
    hook.assert_not_called()
    session.close()


@pytest.mark.parametrize(
    "case", ["lookup", "retry", "custom_adapter", "custom_session", "deadline", "auth"]
)
def test_unsupported_transport_is_rejected_before_dispatch(case: str) -> None:
    """Unknown, retrying and unbounded paths fail before mutation."""
    session = cast(Any, Session())
    adapter: Any = HTTPAdapter(max_retries=0)
    if case == "retry":
        adapter = HTTPAdapter(max_retries=Retry(total=1))
    elif case == "custom_adapter":
        adapter = MagicMock()
    session.get_adapter = MagicMock(return_value=adapter)
    if case == "lookup":
        session.get_adapter.side_effect = ValueError("Synthetic lookup failure")
    if case == "custom_session":
        session = MagicMock(get_adapter=MagicMock(return_value=adapter))
    if case == "auth":
        session.auth = MagicMock()
    with pytest.raises(WriteDenied):
        checked_adapter(
            session, "https://example.test/write", bounded=case == "deadline"
        )


def test_mutation_dispatch_restores_session_after_failure() -> None:
    """The account retains its original session even after a lost response."""
    drive = MagicMock()
    session = Session()
    session.trust_env = False
    drive.session = session
    with (
        patch(
            "pyicloud.mcp.one_shot.request_once",
            side_effect=OSError("Synthetic failure"),
        ) as dispatch,
        pytest.raises(OSError),
        mutation_session(drive),
    ):
        drive.session.post("https://example.test/write", json={})
    dispatch.assert_called_once()
    assert drive.session is session
    session.close()


@pytest.mark.asyncio
async def test_exact_mutation_endpoint_adapter_is_checked_before_approval() -> None:
    """A path-specific POST retry mount cannot pass a service-root check."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    session = client.drive.session
    session.get_adapter.side_effect = lambda url: (
        HTTPAdapter(max_retries=1) if "/renameItems" in url else BoundedAdapter(30)
    )
    with pytest.raises(WriteDenied, match="mutation_retry_policy_unavailable"):
        await writer.apply(preview.request_id)
    assert writer.additional.resolve("drive-file", handle)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [False, True])
async def test_upload_endpoint_policy_failure_preserves_all_resources(
    commit: bool,
) -> None:
    """Both registration endpoints must pass admission before anything is spent."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def adapter(url: str) -> HTTPAdapter:
        if ("/update/documents" if commit else "/upload/web") in url:
            return HTTPAdapter(max_retries=0)
        return BoundedAdapter(30)

    client.drive.session.get_adapter.side_effect = adapter
    accept = MagicMock()
    with pytest.raises(WriteDenied, match="upload_deadline_policy_unavailable"):
        await writer.apply(preview.request_id, accept)
    accept.assert_not_called()
    assert writer.staging.peek(token).data
    assert writer.additional.resolve("drive-folder", handle)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


def test_query_defaults_and_response_cookies_survive_direct_dispatch() -> None:
    """Direct dispatch preserves safe session state without replay or hook calls."""
    session = Session()
    session.trust_env = False
    session.params = {"default": "yes", "shared": "session"}
    session.mount("https://", BoundedAdapter(30))
    headers = Message()
    headers.add_header("Set-Cookie", "synthetic=new; Path=/; Secure")
    response = Response()
    response.status_code = 200
    response.raw = MagicMock()
    vars(response).update(_content=b"{}", _content_consumed=True)
    response.raw._original_response = SimpleNamespace(msg=headers)  # pylint: disable=protected-access
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        request_once(
            session, "POST", "https://example.test/write", params={"shared": "drive"}
        )
        request = send.call_args.args[-1]
        assert parse_qs(urlsplit(request.url).query) == {
            "default": ["yes"],
            "shared": ["drive"],
        }
        assert request.url == prepared_url(
            session, "https://example.test/write", {"shared": "drive"}
        )
        assert session.cookies.get("synthetic") == "new"
        request_once(session, "POST", "https://example.test/commit")
        assert "synthetic=new" in send.call_args.args[-1].headers["Cookie"]
    session.close()


def test_download_token_discovery_rejects_redirect_before_followup() -> None:
    """The initial by-ID read receives the download's no-redirect policy too."""
    session = cast(Any, Session())
    session.trust_env = False
    response = Response()
    response.status_code = 302
    session.get = MagicMock(return_value=response)
    drive = DriveService(
        "https://drive.example.test", "https://docs.example.test", session, {}
    )
    with pytest.raises(PyiCloudAPIResponseException, match="redirect not followed"):
        drive.get_file("F", allow_redirects=False)
    session.get.assert_called_once()
    assert session.get.call_args.kwargs["allow_redirects"] is False
    session.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/write",
        "https://[bad",
        "https:///missing",
        "https://user:password@example.test/write",
    ],
)
def test_invalid_mutation_routes_are_known_denials(url: str) -> None:
    """Plaintext, malformed and credential-bearing routes fail before dispatch."""
    session = Session()
    session.trust_env = False
    with pytest.raises(WriteDenied, match="insecure_mutation_route"):
        checked_adapter(session, url)
    session.close()
