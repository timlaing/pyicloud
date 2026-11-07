"""Synthetic private file moves with two revision-bound discovered handles."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.drive_moves import DriveMoves
from pyicloud.mcp.drive_writes import DriveWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.drive import DriveNode
from tests.test_mcp_additional import owner_for
from tests.test_mcp_drive_writes import setup_writer


def setup_move() -> tuple[DriveMoves, Any, str, str, dict[str, Any], dict[str, Any]]:
    """Only fake facade data; no network or credentials."""
    writer, client, handle, data = setup_writer()
    data["parentId"] = "FOLDER::com.apple.CloudDocs::OLD"
    folder = {
        "drivewsid": "FOLDER::com.apple.CloudDocs::DEST",
        "zone": data["zone"],
        "type": "FOLDER",
        "name": "Destination",
        "etag": "D1",
        "items": [],
    }
    destination = writer.additional.remember(
        "drive-folder", DriveNode(client.drive, dict(folder))
    )
    client.drive.get_node_data.side_effect = lambda identifier, *_args: dict(
        data if identifier == data["drivewsid"] else folder
    )
    writer.ledger = ApprovalLedger(frozenset({"drive.move_file"}))
    return DriveMoves(writer), client, handle, destination, data, folder


@pytest.mark.asyncio
async def test_move_requires_receipt_and_verified_new_membership() -> None:
    """Both fresh revisions participate in one exact approval."""
    writes, client, handle, destination, data, folder = setup_move()
    preview = await writes.prepare_move(handle, destination, {})
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def move(nodes: list[DriveNode], target: DriveNode) -> None:
        with pytest.raises(ToolFailure):
            writes.additional.resolve("drive-folder", destination)
        assert nodes[0].data["etag"] == "v1"
        assert target.data["etag"] == "D1"
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[dict(data)])

    client.drive.move_nodes_to_node.side_effect = move
    assert await writes.apply(preview.request_id) == {
        "action": "drive.move_file",
        "changed": True,
    }
    client.drive.move_nodes_to_node.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["source", "destination", "parent", "conflict", "folder", "missing_revision"],
)
async def test_changed_preconditions_prevent_dispatch(case: str) -> None:
    """No overwrite-by-name, stale destination or folder relocation."""
    writes, client, handle, destination, data, folder = setup_move()
    preview = await writes.prepare_move(handle, destination, {})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "source":
        data["etag"] = "v2"
    elif case == "destination":
        folder["etag"] = "D2"
    elif case == "parent":
        data["parentId"] = "OTHER"
    elif case == "conflict":
        folder["items"] = [{**data, "drivewsid": "FILE::com.apple.CloudDocs::OTHER"}]
    elif case == "folder":
        data["type"] = "FOLDER"
    else:
        folder["etag"] = None
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["parent", "membership", "route"])
async def test_unverified_move_is_uncertain_and_replay_denied(case: str) -> None:
    """Acknowledgements and local node changes alone cannot prove success."""
    writes, client, handle, destination, data, folder = setup_move()
    preview = await writes.prepare_move(handle, destination, {})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def move(_nodes: list[DriveNode], _target: DriveNode) -> None:
        if case != "parent":
            data["parentId"] = folder["drivewsid"]
        if case != "membership":
            folder["items"] = [dict(data)]
        if case == "route":
            client.drive.service_root = "https://other.test"

    client.drive.move_nodes_to_node.side_effect = move
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.drive.move_nodes_to_node.assert_called_once()


@pytest.mark.asyncio
async def test_already_in_destination_consumes_receipt_without_dispatch() -> None:
    """An approved no-op is not a reusable receipt."""
    writes, client, handle, destination, data, folder = setup_move()
    data["parentId"] = folder["drivewsid"]
    folder["items"] = [dict(data)]
    preview = await writes.prepare_move(handle, destination, {})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    assert not (await writes.apply(preview.request_id))["changed"]
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
async def test_extra_payload_rejected_before_discovery() -> None:
    """The discovered destination selector is not an arbitrary caller path."""
    writes, client, handle, destination, _data, _folder = setup_move()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare_move(handle, destination, {"path": "arbitrary"})
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_inventory_publish_and_disabled_service() -> None:
    """Internal adapter remains bounded and respects configured service opt-ins."""
    writes, _client, handle, destination, _data, _folder = setup_move()
    preview = await writes.prepare_move(handle, destination, {})
    # pylint: disable=protected-access
    pair = writes._moves[preview.request_id]
    writes._moves = {str(index): pair for index in range(256)}
    publish = MagicMock()
    result = await writes.prepare_move(handle, destination, {}, publish)
    publish.assert_called_once_with(result)
    assert len(writes._moves) == 256
    additional = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("mail",))))
    disabled = DriveMoves(DriveWrites(additional, writes.ledger))
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled.prepare_move(handle, destination, {})
