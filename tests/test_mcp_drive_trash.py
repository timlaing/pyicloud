"""Synthetic reversible Drive file trashing, never permanent deletion."""

from typing import Any

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.services.drive import DriveNode
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
async def test_trash_exact_file_after_receipt_and_verify_membership() -> None:
    """Dispatch the existing etag-bearing method once after approval."""
    writes, client, handle, data = setup_writer()
    writes.ledger = ApprovalLedger(frozenset({"drive.trash_file"}))
    preview = await writes.prepare(handle, {}, action="drive.trash_file")
    client.drive.move_items_to_trash.assert_not_called()
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def trash(identifier: str, etag: str, *, allow_redirects: bool) -> dict[str, Any]:
        assert allow_redirects is False
        assert (identifier, etag) == (data["drivewsid"], "v1")
        data.update(restorePath="Before.txt", etag="v2", parentId="TRASH_ROOT")
        client.drive.trash.get_children.return_value = [
            DriveNode(client.drive, dict(data))
        ]
        return {"ignored": "not proof"}

    client.drive.move_items_to_trash.side_effect = trash
    assert await writes.apply(preview.request_id) == {
        "action": "drive.trash_file",
        "changed": True,
    }
    client.drive.move_items_to_trash.assert_called_once()
    client.drive.delete_forever_from_trash.assert_not_called()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["membership", "restore", "duplicate", "account", "parent"]
)
async def test_trash_unverified_ack_cannot_replay(case: str) -> None:
    """Missing Recently Deleted proof after dispatch is uncertain, not retryable."""
    writes, client, handle, data = setup_writer()
    writes.ledger = ApprovalLedger(frozenset({"drive.trash_file"}))
    preview = await writes.prepare(handle, {}, action="drive.trash_file")
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    callbacks: list[str] = []

    def trash(_identifier: str, _etag: str, *, allow_redirects: bool) -> None:
        assert allow_redirects is False
        callbacks.append(case)
        if case == "parent":
            data.update(restorePath="Before.txt", etag="v2", parentId="ordinary")
        node = DriveNode(client.drive, dict(data))
        client.drive.trash.get_children.return_value = (
            []
            if case == "membership"
            else [node, node]
            if case == "duplicate"
            else [node]
        )
        if case == "account":
            data["restorePath"] = "Before.txt"
            client.params["dsid"] = "OTHER"

    client.drive.move_items_to_trash.side_effect = trash
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.drive.move_items_to_trash.assert_called_once()
    assert callbacks == [case]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["folder", "already_trashed", "payload", "unknown"])
async def test_trash_unsupported_inputs_never_dispatch(case: str) -> None:
    """No recursive folders, destructive permanent actions or extra payload fields."""
    writes, client, handle, data = setup_writer()
    writes.ledger = ApprovalLedger(frozenset({"drive.trash_file"}))
    if case == "folder":
        data["type"] = "FOLDER"
    elif case == "already_trashed":
        data["restorePath"] = "Before.txt"
    with pytest.raises(WriteDenied):
        await writes.prepare(
            handle,
            {"name": "unexpected"} if case == "payload" else {},
            action="drive.delete_forever" if case == "unknown" else "drive.trash_file",
        )
    client.drive.move_items_to_trash.assert_not_called()
