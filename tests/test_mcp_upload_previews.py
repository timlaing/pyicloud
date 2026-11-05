"""Staged upload previews only; no byte transfer, caller paths or approvals inferred."""

import base64
import hashlib
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.upload_previews import UploadPreviews
from pyicloud.mcp.upload_staging import UploadStaging
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_drive_writes import setup_writer


def setup_upload() -> tuple[UploadPreviews, Any, str, str, dict[str, Any]]:
    """Entirely fake private folder and explicitly sealed synthetic bytes."""
    writer, client, _file_handle, data = setup_writer()
    data.update(
        drivewsid="FOLDER::com.apple.CloudDocs::DEST",
        type="FOLDER",
        name="Destination",
        items=[],
    )
    destination = writer.additional.remember(
        "drive-folder", DriveNode(client.drive, dict(data))
    )
    client.drive.get_node_data.side_effect = lambda _identifier, *_args: dict(data)
    client.drive.document_root = "https://documents.example.test"
    cast(Any, writer.additional.reads.owner).add_cleanup = MagicMock()
    writer.ledger = ApprovalLedger(
        frozenset({"drive.upload_file"}),
        high_risk_actions=frozenset({"drive.upload_file"}),
    )
    staging = UploadStaging()
    payload = b"synthetic payload"
    token = staging.begin("New.txt", len(payload), hashlib.sha256(payload).hexdigest())
    staging.append(token, 0, base64.b64encode(payload).decode())
    staging.seal(token)
    return UploadPreviews(writer, staging), client, destination, token, data


@pytest.mark.asyncio
async def test_preview_binds_exact_metadata_without_transfer_or_consumption() -> None:
    """Sealed bytes remain unchanged; even approval cannot invoke an upload yet."""
    previews, client, destination, token, _data = setup_upload()
    publish = MagicMock()
    preview = await previews.prepare_upload(destination, token, publish)
    assert preview.risk == "high"
    assert preview.payload == {
        "name": "New.txt",
        "size": 17,
        "sha256": hashlib.sha256(b"synthetic payload").hexdigest(),
    }
    publish.assert_called_once_with(preview)
    assert previews.staging.peek(token).data == b"synthetic payload"
    assert previews.staging.describe(token) == {**preview.payload, "sealed": True}
    previews.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    with pytest.raises(WriteDenied, match="upload_execution_unavailable"):
        await previews.apply(preview.request_id)
    client.drive.send_file.assert_not_called()
    client.drive.session.post.assert_not_called()
    owner = cast(Any, previews.additional.reads.owner)
    owner.add_cleanup.assert_called_once_with(previews.staging.close)
    owner.add_cleanup.call_args.args[0]()
    with pytest.raises(WriteDenied, match="expired_or_unknown"):
        previews.staging.peek(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["collision", "shared", "trash", "file", "document_root", "untyped"]
)
async def test_unqualified_destinations_never_publish(case: str) -> None:
    """No overwrites, unsupported target or arbitrary route fallback."""
    previews, client, destination, token, data = setup_upload()
    if case == "collision":
        data["items"] = [
            {
                "drivewsid": "FILE::com.apple.CloudDocs::EXISTING",
                "type": "FILE",
                "name": "new",
                "extension": "txt",
            }
        ]
    elif case == "shared":
        data["shareID"] = {"owner": "OTHER"}
    elif case == "trash":
        data["restorePath"] = "Destination"
    elif case == "file":
        data["type"] = "FILE"
    elif case == "document_root":
        client.drive.document_root = None
    else:
        destination = previews.additional.remember("drive-folder", object())
    with pytest.raises(WriteDenied):
        await previews.prepare_upload(destination, token)
    client.drive.send_file.assert_not_called()


@pytest.mark.asyncio
async def test_unsealed_bytes_and_missing_service_are_rejected() -> None:
    """A byte declaration is not a complete payload or upload permission."""
    previews, client, destination, _token, _data = setup_upload()
    pending = previews.staging.begin("Pending.txt", 1, hashlib.sha256(b"x").hexdigest())
    with pytest.raises(WriteDenied, match="upload_not_sealed"):
        await previews.prepare_upload(destination, pending)
    previews.additional.reads.owner.config = type(
        previews.additional.reads.owner.config
    )()
    with pytest.raises(WriteDenied, match="write_disabled"):
        await previews.prepare_upload(destination, pending)
    client.drive.send_file.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [{"shareID": {"owner": "OTHER"}}, {"type": "FILE"}, {"restorePath": "Moved"}],
)
async def test_child_refresh_cannot_change_destination_scope_silently(
    changed: dict[str, Any],
) -> None:
    """Refreshed metadata must remain the exact private destination identity."""
    previews, client, destination, token, data = setup_upload()
    client.drive.get_node_data.side_effect = [
        dict(data),
        {**data, **changed},
    ]
    with pytest.raises(WriteDenied):
        await previews.prepare_upload(destination, token)
    client.drive.send_file.assert_not_called()


def test_document_service_route_accessor_is_read_only() -> None:
    """Core exposes observed routing without accepting a caller-selected endpoint."""
    service = DriveService(
        "https://drive.example.test", "https://documents.example.test", MagicMock(), {}
    )
    assert service.document_root == "https://documents.example.test"
    with pytest.raises(AttributeError):
        cast(Any, service).document_root = "https://other.test"
