"""Offline expiry between revalidation and atomic approval consumption."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import patch

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_acknowledged_refresh_expiry_preserves_approval(recover: bool) -> None:
    """Create/recover expiry leaves the exact decision and selector request pending."""
    writer, _client, handle, _data = setup_action(recover)
    action = "drive.recover_item" if recover else "drive.create_folder"
    preview = await writer.prepare_acknowledged(
        action, handle, {} if recover else {"name": "New"}
    )
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    original = writer._read

    def refresh(*args: Any) -> Any:
        result = original(*args)
        kind, node, _expiry = writer.additional._handles[handle]
        writer.additional._handles[handle] = (kind, node, 0)
        return result

    with (
        patch.object(writer, "_read", side_effect=refresh),
        patch("pyicloud.mcp.drive_acknowledged.UploadTransport.register") as dispatch,
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    assert preview.request_id in writer._requests
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_delete_refresh_expiry_preserves_elevated_approval() -> None:
    """Retirement fails before consuming approval if Trash reads outlive a handle."""
    writer, client, handle, _data = setup_delete()
    preview = await writer.prepare_delete(handle, {})
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    original = writer._trash_file

    def refresh(*args: Any) -> Any:
        result = original(*args)
        kind, node, _expiry = writer.additional._handles[handle]
        writer.additional._handles[handle] = (kind, node, 0)
        return result

    with (
        patch.object(writer, "_trash_file", side_effect=refresh),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    assert preview.request_id in writer._deletions
    client.drive.delete_forever_from_trash.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["drive.rename", "drive.trash_file"])
async def test_acceptance_expiry_preserves_standard_approval(action: str) -> None:
    """The final retirement check is inside consumption, including narrow windows."""
    writer, client, handle, _data = setup_writer()
    writer.ledger = ApprovalLedger(frozenset({action}))
    preview = await writer.prepare(
        handle, {"name": "After"} if action == "drive.rename" else {}, action=action
    )
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)

    def accept(_request_id: str) -> None:
        kind, node, _expiry = writer.additional._handles[handle]
        writer.additional._handles[handle] = (kind, node, 0)

    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writer.apply(preview.request_id, accept)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    assert preview.request_id in writer._sources
    client.drive.rename_items.assert_not_called()
    client.drive.move_items_to_trash.assert_not_called()
