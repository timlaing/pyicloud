"""Offline operation lifetime, immutable receipt context and Photos budget tests."""

# pylint: disable=protected-access

from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Response, Session

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.mcp.upload_transport import UploadNotDispatched
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_content import content_for
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_photo_uploads import setup_upload


@pytest.mark.asyncio
async def test_injected_download_budget_resets_for_each_operation() -> None:
    """Many completed reads cannot exhaust a shared injected session's budget."""
    session = Session()
    session.trust_env = False
    adapter = BoundedAdapter(30)
    session.mount("https://", adapter)
    client = MagicMock(session=session)
    tools = content_for(client)
    asset = MagicMock(filename="photo")
    asset.download_url.return_value = "https://icloud-content.com/photo"
    handle = tools.additional.remember("photo-asset", asset)
    response = Response()
    response.status_code = 200
    vars(response).update(_content=b"data", _content_consumed=True)
    with patch("requests.adapters.HTTPAdapter.send", return_value=response):
        for _index in range(35):
            assert (await tools.file("photos", handle, 0, 4)).error is None
            assert adapter.deadline is None
    assert adapter.requests_left == 31
    session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("root_kind", ["service", "document"])
async def test_drive_metadata_roots_reject_plaintext_before_read(
    root_kind: str,
) -> None:
    """Even zero-file metadata refresh cannot expose an HTTP account request."""
    session = Session()
    session.trust_env = False
    session.mount("https://", BoundedAdapter(30))
    service_root = (
        "http://drive.icloud.com"
        if root_kind == "service"
        else "https://drive.icloud.com"
    )
    document_root = (
        "http://docs.icloud.com"
        if root_kind == "document"
        else "https://docs.icloud.com"
    )
    drive = DriveService(service_root, document_root, cast(Any, session), {})
    tools = content_for(MagicMock())
    handle = tools.additional.remember(
        "drive-file",
        DriveNode(
            drive,
            {"drivewsid": "FILE::Z::F", "name": "empty", "type": "FILE", "size": 0},
        ),
    )
    with patch("requests.adapters.HTTPAdapter.send") as send:
        assert (await tools.file("drive", handle, 0, 4)).error == "invalid_resource_url"
    send.assert_not_called()
    session.close()


@pytest.mark.asyncio
async def test_drive_injected_deadline_restores_on_preview_and_failure() -> None:
    """Repeated preparation and apply retain per-operation adapter lifetimes."""
    writer, client, handle, _data = setup_writer()
    adapter = client.drive.session.get_adapter.return_value
    for _index in range(2):
        preview = await writer.prepare(handle, {"name": "After"})
        assert adapter.deadline is None
        with pytest.raises(WriteDenied, match="operator_approval_required"):
            await writer.apply(preview.request_id)
        assert adapter.deadline is None


@pytest.mark.asyncio
async def test_bare_document_fragment_never_consumes_upload() -> None:
    """An empty fragment delimiter is not part of a valid registration root."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    client.drive.document_root += "#"
    with pytest.raises(WriteDenied):
        await writer.prepare_upload(handle, token, 1000)
    assert writer.staging.peek(token).data
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_photos_stage_expiry_during_policy_keeps_decision() -> None:
    """Atomic stage acquisition must fail before approval is consumed."""

    writer, _client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def policy(_client: Any) -> None:
        stage = writer.staging.peek(token)
        writer.staging._stages[token] = replace(stage, expires_at=0)

    with (
        patch.object(writer, "_validate_session_policy", side_effect=policy),
        pytest.raises(WriteDenied),
    ):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_photos_acceptance_cannot_replace_upload_context() -> None:
    """Upload route changes after receipt acceptance are denied before taking bytes."""
    writer, client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)

    def accept(request_id: str) -> None:
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)
        client.photos.upload_root = "https://foreign.example.test"

    with pytest.raises(WriteDenied, match="stale_preview"):
        await writer.apply(preview.request_id, accept)
    assert writer.staging.peek(token).data
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_photos_retry_retains_consumed_receipt_acceptance() -> None:
    """Two proven rejections do not require consuming the same mailbox receipt twice."""
    writer, _client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)
    pipeline.upload.side_effect = UploadNotDispatched("upload_not_dispatched")
    accepted: list[str] = []

    def accept(request_id: str) -> None:
        assert not accepted
        accepted.append(request_id)
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)

    for _attempt in range(2):
        with pytest.raises(UploadNotDispatched):
            await writer.apply(preview.request_id, accept)
    assert accepted == [preview.request_id]
    assert writer.staging.peek(token).data
