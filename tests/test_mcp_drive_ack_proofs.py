"""Offline acknowledgements, acceptance context and producer cleanup proofs."""

# pylint: disable=protected-access

import hashlib
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from requests import Response, Session

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.mcp.photo_upload_bytes import (
    PhotoUploadBytes,
    validate_photo_upload_session,
)
from pyicloud.mcp.upload_staging import StagedUpload
from pyicloud.mcp.upload_transport import UploadNotDispatched, UploadTransport
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_additional import adapters
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_photo_uploads import setup_upload


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["drive.rename", "drive.trash_file"])
async def test_acceptance_cannot_replace_mutation_account_or_route(action: str) -> None:
    """An accepted decision stays bound before any approval or handle is spent."""
    writer, client, handle, _data = setup_writer()

    writer.ledger = ApprovalLedger(frozenset({action}))
    preview = await writer.prepare(
        handle, {"name": "After"} if action == "drive.rename" else {}, action=action
    )

    def accept(request_id: str) -> None:
        writer.ledger.approve_from_operator(request_id, preview.digest)
        client.drive.service_root = "https://foreign.example.test"

    with pytest.raises(WriteDenied, match="stale_preview"):
        await writer.apply(preview.request_id, accept)
    assert writer.additional.resolve("drive-file", handle)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()
    client.drive.move_items_to_trash.assert_not_called()


def test_retirement_ignores_unhashable_unrelated_identity_metadata() -> None:
    """Unrelated malformed discovery records cannot interrupt another retirement."""
    writer, client, handle, data = setup_writer()
    other = writer.additional.remember(
        "drive-file", DriveNode(client.drive, {**data, "drivewsid": ["bad"]})
    )
    writer.additional.retire("drive-file", handle)
    assert writer.additional.resolve("drive-file", other)


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["browse", "trash", "search"])
@pytest.mark.parametrize("fail", [False, True])
async def test_original_drive_iterator_closes_on_limit_or_error(
    service: str, fail: bool
) -> None:
    """Closing an islice alone is insufficient; the original producer is owned."""
    closed: list[bool] = []

    def producer(**_kwargs: Any) -> Any:
        try:
            yield SimpleNamespace(
                name="match" if not fail else object(), type="file", date_modified=None
            )
            raise AssertionError("The limit must stop production")
        finally:
            closed.append(True)

    client = MagicMock()
    client.drive.root = SimpleNamespace(type="folder", iter_children=producer)
    client.drive.trash = SimpleNamespace(type="folder", iter_children=producer)
    tools = adapters(client)
    if service == "browse":
        result = await tools.drive("", "match", 1)
    elif service == "trash":
        result = await tools.drive_trash("match", 1)
    else:
        result = await tools.search("drive", "", "match", 1, 1)
    assert (result.error is not None) is fail
    assert closed == [True]


@pytest.mark.parametrize("status", [304, 300])
def test_non_redirect_status_does_not_raise_redirect_error(status: int) -> None:
    """Conditional and other non-redirect responses retain legacy HTTP handling."""
    response = Response()
    response.status_code = status
    DriveService._raise_if_error(response)


@pytest.mark.asyncio
async def test_generic_absence_without_correlated_delete_ack_is_uncertain() -> None:
    """An unrelated 404 after recovery is never sufficient final deletion proof."""
    writer, client, handle, _data = setup_delete()
    preview = await writer.prepare_delete(handle, {})
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    client.drive.delete_forever_from_trash.return_value = {"status": "OK"}
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    client.drive.delete_forever_from_trash.assert_called_once()


@pytest.mark.asyncio
async def test_photos_bad_adapter_preserves_stage_and_approval() -> None:
    """The shared stricter registration policy is checked before Photos consumes."""
    writer, client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    client.photos.session.mount(
        "https://", Session().get_adapter("https://example.test")
    )
    with pytest.raises(WriteDenied, match="deadline_policy_unavailable"):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_photos_proven_no_dispatch_restores_resources() -> None:
    """A typed transport proof, unlike a lost response, retains original resources."""
    writer, _client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.side_effect = UploadNotDispatched("upload_not_dispatched")
    with pytest.raises(UploadNotDispatched):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )


def test_registration_preserves_specific_session_denial() -> None:
    """Retry-policy diagnostics cannot hide an incompatible session policy."""
    session = Session()
    session.auth = ("synthetic", "synthetic")
    session.mount("https://", BoundedAdapter(30))
    hosts = frozenset({"example.test"})
    with pytest.raises(WriteDenied, match="mutation_session_policy_unavailable"):
        UploadTransport.register(session, "https://example.test/register", hosts, {})
    session.close()


@pytest.mark.parametrize("root", ["https://[bad", "https://upload.example.test?"])
def test_photos_preflight_rejects_malformed_or_query_roots(root: str) -> None:
    """Photo preflight URL failures are known denials before any staged resource use."""
    session = Session()
    params = {"dsid": "123"}
    with pytest.raises(WriteDenied, match="unsupported_upload_route"):
        validate_photo_upload_session(session, root, params)
    session.close()


def test_photos_pipeline_preserves_first_transport_no_dispatch_proof() -> None:
    """The shared proof remains typed through Photos reservation validation."""
    transport = MagicMock()
    transport.register.side_effect = UploadNotDispatched("upload_not_dispatched")
    pipeline = PhotoUploadBytes(transport)
    stage = StagedUpload(
        "photo.jpg", 1, hashlib.sha256(b"x").hexdigest(), time.monotonic() + 300, b"x"
    )
    params = {"dsid": "123"}
    session = MagicMock()
    with pytest.raises(UploadNotDispatched):
        pipeline.upload(
            session,
            "https://upload.example.test",
            params,
            "PrimarySync",
            stage,
            modified_ms=1000,
        )
    transport.transfer.assert_not_called()
