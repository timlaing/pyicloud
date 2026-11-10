"""Offline admission rollback, owner budgets and account transport settings."""

# pylint: disable=protected-access

import os
from threading import Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from requests import Response, Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import ProtocolError

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import (
    BoundedAdapter,
    ServerConfig,
    SessionOwner,
    ToolFailure,
    transport_failure,
)
from pyicloud.mcp.drive_upload_bytes import DriveUploadBytes
from pyicloud.mcp.header_deadline import HeaderDeadlineExceeded
from pyicloud.mcp.one_shot import transport_settings
from pyicloud.mcp.upload_transport import UploadNotDispatched, UploadTransport
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("attempted", [False, True])
async def test_first_upload_dispatch_controls_resource_restoration(
    attempted: bool,
) -> None:
    """Restore only work proven to precede dispatch, never an admitted lost response."""
    writer, client, handle, token, _data, _pipeline = setup_adapter()
    writer.pipeline = DriveUploadBytes(UploadTransport(writer.hosts))
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    stage = writer.staging.peek(token)
    clock = [10.0]

    def mime(_name: str) -> tuple[None, None]:
        if not attempted:
            clock[0] = 100
        return None, None

    with (
        patch(
            "pyicloud.mcp.drive_upload_bytes.time.monotonic",
            side_effect=lambda: clock[0],
        ),
        patch("pyicloud.mcp.drive_upload_bytes.mimetypes.guess_type", side_effect=mime),
        patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=OSError("Synthetic lost response"),
        ) as send,
    ):
        expected = "mutation_uncertain" if attempted else "upload_not_dispatched"
        with pytest.raises(WriteDenied, match=expected):
            await writer.apply(preview.request_id)
    if attempted:
        send.assert_called_once()
        with pytest.raises(WriteDenied):
            writer.staging.peek(token)
        with pytest.raises(WriteDenied):
            writer.ledger.approve_from_operator(
                preview.request_id, preview.digest, high_risk=True
            )
    else:
        send.assert_not_called()
        assert writer.staging.peek(token) == stage
        assert writer.additional.resolve("drive-folder", handle)
        writer.ledger.approve_from_operator(
            preview.request_id, preview.digest, high_risk=True
        )
    assert client.drive.session.get_adapter.return_value.before_dispatch is None


def test_nested_header_timeout_and_exception_cycles_are_sanitized() -> None:
    """Nested protocol wrapping preserves timeout classification without cycles."""
    timeout = HeaderDeadlineExceeded("Synthetic timeout")
    wrapped = RequestsConnectionError(ProtocolError("Connection aborted", timeout))
    assert str(transport_failure(wrapped)) == "timeout"
    other = RequestsConnectionError("Synthetic upstream failure")
    other.__cause__ = other
    assert str(transport_failure(other)) == "upstream_failure"


@pytest.mark.asyncio
async def test_owner_binds_injected_adapter_and_restores_budget() -> None:
    """A real owner supplies one absolute budget, not the injected adapter's timeout."""
    client = SimpleNamespace(
        get_auth_status=lambda: {"authenticated": True},
        session=SimpleNamespace(close=lambda: None),
    )
    adapter = BoundedAdapter(120)
    owner = SessionOwner(
        ServerConfig(username="synthetic", session_dir="synthetic", timeout=1),
        lambda: client,
    )

    def operation(_client: Any) -> None:
        owner.bind_adapter(adapter)
        assert adapter.deadline == owner.active_deadline
        owner.bind_adapter(adapter)
        assert adapter.deadline == owner.active_deadline

    try:
        with patch("threading.Thread", Thread):
            await owner.run(operation)
        assert adapter.deadline is None
        assert owner.active_deadline is None
        with pytest.raises(ToolFailure, match="timeout"):
            owner.bind_adapter(adapter)
        with pytest.raises(ToolFailure, match="timeout"):
            owner._invoke(operation, 0)
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_plain_mutation_adapter_is_denied_before_approval_consumption() -> None:
    """Injected non-retrying adapters must also enforce absolute header deadlines."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.session.get_adapter.return_value = Session().get_adapter(
        "https://example.test"
    )
    with pytest.raises(WriteDenied, match="deadline_policy_unavailable"):
        await writer.apply(preview.request_id)
    assert writer.additional.resolve("drive-file", handle)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()


@pytest.mark.parametrize("bundle", ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_account_dispatch_preserves_proxy_ca_and_explicit_query_precedence(
    bundle: str,
) -> None:
    """Account transport settings are retained without enabling hooks or netrc."""
    session = Session()
    session.params = {"dsid": "wrong", "token": "wrong", "default": "yes"}
    session.mount("https://", BoundedAdapter(30))
    response = Response()
    response.status_code = 200
    vars(response).update(_content=b"[]", _content_consumed=True)
    environment = {
        bundle: "synthetic-ca.pem",
        "HTTPS_PROXY": "http://proxy.example.test",
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        patch("requests.adapters.HTTPAdapter.send", return_value=response) as send,
    ):
        UploadTransport.register(
            session,
            "https://example.test/register",
            frozenset({"example.test"}),
            {},
            params={"dsid": "bound", "token": "synthetic"},
        )
    assert send.call_args.kwargs["verify"] == "synthetic-ca.pem"
    assert send.call_args.kwargs["proxies"]["https"] == "http://proxy.example.test"
    query = parse_qs(urlsplit(send.call_args.args[0].url).query)
    assert query == {"dsid": ["bound"], "token": ["synthetic"], "default": ["yes"]}
    session.close()


def test_unsupported_account_proxy_is_a_preflight_denial() -> None:
    """Unsupported proxy transports cannot reach approval consumption."""
    session = Session()
    session.trust_env = False
    session.proxies = {"https": "ftp://proxy.example.test"}
    with pytest.raises(WriteDenied, match="unsupported_proxy"):
        transport_settings(session, "https://example.test/write")
    session.close()


@pytest.mark.asyncio
async def test_final_move_inventory_requires_real_destination_revision() -> None:
    """A valid child listing cannot hide an invalid final destination revision."""
    writer, client, handle, destination, data, folder = setup_move()
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    calls = [0]

    def refresh(identifier: str, *_args: Any) -> dict[str, Any]:
        if identifier == data["drivewsid"]:
            return dict(data)
        calls[0] += 1
        return {**folder, "etag": "null" if calls[0] == 4 else folder["etag"]}

    def moved(*_args: Any, **_kwargs: Any) -> None:
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[dict(data)])

    client.drive.get_node_data.side_effect = refresh
    client.drive.move_nodes_to_node.side_effect = moved
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    client.drive.move_nodes_to_node.assert_called_once()


@pytest.mark.asyncio
async def test_commit_rejection_cannot_restore_an_attempted_upload() -> None:
    """Even proven absence of commit dispatch cannot undo an earlier reservation."""
    writer, _client, handle, token, _data, _pipeline = setup_adapter()
    transport = UploadTransport(writer.hosts)
    writer.pipeline = DriveUploadBytes(transport)
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def registration(*_args: Any, **kwargs: Any) -> Any:
        state = kwargs.get("dispatch_state")
        if state is not None:
            state.mark()
            return [
                {
                    "document_id": "allocated",
                    "url": "https://content.example.test/upload",
                }
            ]
        raise UploadNotDispatched("upload_not_dispatched")

    receipt = {
        "singleFile": {
            "size": 17,
            "receipt": "synthetic",
            "fileChecksum": "sum",
            "referenceChecksum": "ref",
            "wrappingKey": "key",
        }
    }
    with (
        patch.object(transport, "register", side_effect=registration),
        patch.object(transport, "transfer", return_value=receipt),
        patch(
            "pyicloud.mcp.drive_upload_bytes.mimetypes.guess_type",
            return_value=(None, None),
        ),
        pytest.raises(WriteDenied, match="mutation_uncertain"),
    ):
        await writer.apply(preview.request_id)
    with pytest.raises(WriteDenied):
        writer.staging.peek(token)
    with pytest.raises(WriteDenied):
        writer.ledger.approve_from_operator(
            preview.request_id, preview.digest, high_risk=True
        )
    with pytest.raises(ToolFailure):
        writer.additional.resolve("drive-folder", handle)
