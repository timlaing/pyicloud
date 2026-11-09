"""Offline direct dispatch, exact endpoint policy and pre-consumption admission."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.mcp.one_shot import checked_adapter, mutation_session, request_once
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
