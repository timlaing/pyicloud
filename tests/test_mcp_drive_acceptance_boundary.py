"""Offline acceptance, pinned policy and metadata boundary regressions."""

# pylint: disable=protected-access

import hashlib
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Response, Session

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import DownloadState, download_chunk
from pyicloud.mcp.context import BoundedAdapter, ToolFailure
from pyicloud.mcp.drive_upload_bytes import DriveUploadBytes, validate_upload_session
from pyicloud.mcp.one_shot import request_once
from pyicloud.mcp.upload_staging import StagedUpload
from pyicloud.mcp.upload_transport import (
    UploadDispatchState,
    UploadNotDispatched,
    UploadTransport,
)
from pyicloud.services.drive import DriveService
from tests.test_mcp_additional import adapters
from tests.test_mcp_content import content_for
from tests.test_mcp_content import response as body_response
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_upload_previews import setup_upload


@pytest.mark.asyncio
async def test_acceptance_route_switch_preserves_upload_resources() -> None:
    """A receipt callback cannot change the route against which approval was bound."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)

    def accept(request_id: str) -> None:
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)
        client.drive.document_root = "https://foreign.example.test"

    with pytest.raises(WriteDenied, match="mutation_unverified"):
        await writer.apply(preview.request_id, accept)
    assert writer.staging.peek(token).data
    assert writer.additional.resolve("drive-folder", handle)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.parametrize(
    "url", ["https://user:password@example.test/write", "http://example.test/write"]
)
def test_pinned_adapter_does_not_bypass_dispatch_url_validation(url: str) -> None:
    """Pinned transport identity never permits insecure or credential-bearing URLs."""
    session = Session()
    session.trust_env = False
    adapter = BoundedAdapter(30)
    with (
        patch("requests.adapters.HTTPAdapter.send") as send,
        pytest.raises(WriteDenied, match="insecure_mutation_route"),
    ):
        request_once(session, "POST", url, adapter=adapter)
    send.assert_not_called()
    session.close()


def test_first_adapter_lookup_failure_has_proven_no_dispatch() -> None:
    """A rejected first lookup cannot be confused with a lost network response."""
    session = cast(Any, Session())
    session.get_adapter = MagicMock(side_effect=ValueError("Synthetic policy lookup"))
    state = UploadDispatchState()
    hosts = frozenset({"example.test"})
    with pytest.raises(UploadNotDispatched):
        UploadTransport.register(
            session, "https://example.test/register", hosts, {}, dispatch_state=state
        )
    assert not state.attempted
    session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("preview_only", [False, True])
async def test_missing_folder_identifier_is_a_known_upload_denial(
    preview_only: bool,
) -> None:
    """Permitted handle kinds must still contain a real identified folder."""
    writer: Any
    if preview_only:
        writer, _client, handle, token, _data = setup_upload()
    else:
        writer, _client, handle, token, _data, _pipeline = setup_adapter()
    writer.additional.resolve("drive-folder", handle).data.pop("drivewsid")
    operation = (
        writer.prepare_upload(handle, token)
        if preview_only
        else writer.prepare_upload(handle, token, 1000)
    )
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await operation


@pytest.mark.parametrize("pipeline", [False, True])
def test_malformed_document_root_is_a_safe_denial(pipeline: bool) -> None:
    """URL parser errors cannot escape any upload transport validation path."""
    drive = MagicMock(document_root="https://[bad")
    drive.params = {"dsid": "A"}
    stage = StagedUpload(
        "file", 1, hashlib.sha256(b"x").hexdigest(), time.monotonic() + 300, b"x"
    )
    if pipeline:
        uploader = DriveUploadBytes(MagicMock())
        with pytest.raises(WriteDenied, match="unsupported_upload_route"):
            uploader.upload(drive, "folder", stage, modified_ms=1000)
    else:
        with pytest.raises(WriteDenied, match="unsupported_upload_route"):
            validate_upload_session(drive, {"token": "synthetic"})


@pytest.mark.asyncio
async def test_unbounded_injected_inventory_is_rejected_without_calling_it() -> None:
    """A scan cap does not authorize an eager producer to materialize everything."""
    client = MagicMock()
    eager = MagicMock(side_effect=AssertionError("Eager inventory forbidden"))

    client.drive.root = SimpleNamespace(type="folder", get_children=eager)
    result = await adapters(client).drive("", "", 1)
    assert result.error == "unsupported_drive_inventory"
    eager.assert_not_called()


@pytest.mark.asyncio
async def test_injected_owner_download_restores_previous_adapter_deadline() -> None:
    """Repeated content calls do not inherit a previous operation's deadline."""
    client = MagicMock()
    session = Session()
    session.trust_env = False
    adapter = BoundedAdapter(30)
    session.mount("https://", adapter)
    client.session = session
    tools = content_for(client)
    resource = MagicMock(filename="photo")
    resource.download_url.return_value = "https://icloud-content.com/photo"
    resource.download_urls = {"original": "https://icloud-content.com/photo"}
    handle = tools.additional.remember("photo-asset", resource)
    response = Response()
    response.status_code = 200
    vars(response).update(_content=b"data", _content_consumed=True)
    with patch("requests.adapters.HTTPAdapter.send", return_value=response):
        assert (await tools.file("photos", handle, 0, 4)).error is None
        assert adapter.deadline is None
        assert (await tools.file("photos", handle, 0, 4)).error is None
    assert adapter.deadline is None
    assert adapter.response_limit == 4 * 1024 * 1024
    session.close()


def test_download_request_options_and_cleanup_are_already_preserved() -> None:
    """Final download forwards options; assembly closes success and overflow paths."""

    session = MagicMock()
    metadata = MagicMock(status_code=200)
    metadata.json.return_value = {
        "data_token": {"url": "https://icloud-content.com/file"}
    }
    final = body_response(200, b"data")
    session.get.side_effect = [metadata, final]
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", session, {}
    )
    assert (
        drive.get_file(
            "F", headers={"Range": "bytes=0-3"}, stream=True, allow_redirects=False
        )
        is final
    )
    assert session.get.call_args.kwargs["headers"] == {"Range": "bytes=0-3"}
    assert session.get.call_args.kwargs["stream"] is True
    assert session.get.call_args.kwargs["allow_redirects"] is False
    download_chunk(DownloadState(object()), final, 0, 4, "file")
    final.close.assert_called_once()
    large = body_response(200, b"large")
    state = DownloadState(object())
    with pytest.raises(ToolFailure, match="content_too_large"):
        download_chunk(state, large, 0, 4, "file")
    large.close.assert_called_once()


@pytest.mark.asyncio
async def test_upload_destination_expiry_during_refresh_is_known_denial() -> None:
    """Expiry at snapshot admission cannot spend approval or staged bytes."""
    writer, _client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    original = writer._read

    def refreshed(*args: Any) -> Any:
        result = original(*args)
        kind, node, _expiry = writer.additional._handles[handle]
        writer.additional._handles[handle] = (kind, node, 0)
        return result

    with (
        patch.object(writer, "_read", side_effect=refreshed),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_queued_upload_expiry_is_a_known_denial() -> None:
    """Expired lane admission leaves the exact elevated decision and bytes intact."""
    writer, _client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    kind, node, _expiry = writer.additional._handles[handle]
    writer.additional._handles[handle] = (kind, node, 0)
    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_selector_eviction_also_revokes_ledger_entry() -> None:
    """An evicted exact request cannot accept approval or occupy ledger capacity."""
    writer, _client, handle, token, _data, _pipeline = setup_adapter()
    old = await writer.prepare_upload(handle, token, 1000)
    selected = writer._requests[old.request_id]
    writer._requests.update({str(index): selected for index in range(255)})
    current = await writer.prepare_upload(handle, token, 1000)
    with pytest.raises(WriteDenied, match="unknown_or_consumed_preview"):
        writer.ledger.approve_from_operator(old.request_id, old.digest, high_risk=True)
    writer.ledger.approve_from_operator(
        current.request_id, current.digest, high_risk=True
    )
