"""Elevated permanent deletion tests touch fake private trash records only."""

# pylint: disable=protected-access

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.drive_deletions import DriveDeletions
from pyicloud.mcp.drive_writes import DriveTarget
from pyicloud.services.drive import DriveNode
from tests.test_mcp_drive_writes import setup_writer


def setup_delete() -> tuple[DriveDeletions, Any, str, dict[str, Any]]:
    """Use real DriveNode deletion method with mocked facade methods only."""
    writer, client, _ordinary, data = setup_writer()
    data.update(parentId="TRASH_ROOT", restorePath="Before.txt")
    node = DriveNode(client.drive, dict(data))
    handle = writer.additional.remember("drive-trash-file", node)
    client.drive.trash.get_children.return_value = [node]
    writer.ledger = ApprovalLedger(
        frozenset({"drive.delete_forever_file"}),
        high_risk_actions=frozenset({"drive.delete_forever_file"}),
    )
    return DriveDeletions(writer), client, handle, data


@pytest.mark.asyncio
async def test_elevated_delete_retires_handle_and_proves_both_absences() -> None:
    """No arbitrary acknowledgement can prove permanent target removal."""
    deletes, client, handle, data = setup_delete()
    publish = MagicMock()
    preview = await deletes.prepare_delete(handle, {}, publish)
    publish.assert_called_once_with(preview)
    assert preview.risk == "high"
    assert preview.payload == {}
    with pytest.raises(WriteDenied, match="high_risk_operator_approval_required"):
        deletes.ledger.approve_from_operator(preview.request_id, preview.digest)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await deletes.apply(preview.request_id)
    client.drive.delete_forever_from_trash.assert_not_called()

    def delete(identifier: str, etag: str, *, allow_redirects: bool) -> dict[str, str]:
        assert allow_redirects is False
        assert (identifier, etag) == (data["drivewsid"], "v1")
        with pytest.raises(ToolFailure):
            deletes.additional.resolve("drive-trash-file", handle)
        client.drive.trash.get_children.return_value = []
        client.drive.get_node_data.side_effect = PyiCloudAPIResponseException(
            "Synthetic absence", 404
        )
        return {"ignored": "No proof"}

    client.drive.delete_forever_from_trash.side_effect = delete

    def accept(identifier: str) -> None:
        deletes.ledger.approve_from_operator(identifier, preview.digest, high_risk=True)

    assert await deletes.apply(preview.request_id, accept) == {
        "action": "drive.delete_forever_file",
        "deleted": True,
    }
    client.drive.delete_forever_from_trash.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await deletes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["etag", "path", "folder", "shared", "membership"])
async def test_changed_or_unsupported_trash_context_never_dispatches(case: str) -> None:
    """Exact private file/location/revision/membership must remain approved."""
    deletes, client, handle, data = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    deletes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    if case == "etag":
        data["etag"] = "v2"
    elif case == "path":
        data["restorePath"] = "Other.txt"
    elif case == "folder":
        data["type"] = "FOLDER"
    elif case == "shared":
        data["shareID"] = {"owner": "OTHER"}
    else:
        client.drive.trash.get_children.return_value = []
    with pytest.raises(WriteDenied):
        await deletes.apply(preview.request_id)
    client.drive.delete_forever_from_trash.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["tombstone", "still_listed", "route", "transport", "lookup_error"]
)
async def test_unverified_permanent_delete_has_no_retry_handle(case: str) -> None:
    """Absence from trash alone can be recovery/movement, not deletion proof."""
    deletes, client, handle, data = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    deletes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def delete(_identifier: str, _etag: str) -> None:
        if case == "transport":
            raise OSError("private")
        if case != "still_listed":
            client.drive.trash.get_children.return_value = []
        if case == "route":
            client.drive.service_root = "https://other.test"
        elif case == "lookup_error":
            client.drive.get_node_data.side_effect = PyiCloudAPIResponseException(
                "Synthetic error", 500
            )
        else:
            client.drive.get_node_data.side_effect = lambda _identifier: {
                **data,
                "isDeleted": True,
                "etag": "null",
            }

    client.drive.delete_forever_from_trash.side_effect = delete
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await deletes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await deletes.apply(preview.request_id)
    with pytest.raises(ToolFailure):
        deletes.additional.resolve("drive-trash-file", handle)
    client.drive.delete_forever_from_trash.assert_called_once()


@pytest.mark.asyncio
async def test_permission_payload_kind_and_inventory_guards() -> None:
    """No recursive/ordinary-handle/caller-confirm selection or implicit high risk."""
    deletes, client, handle, _data = setup_delete()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await deletes.prepare_delete(handle, {"confirm": True})
    untyped = deletes.additional.remember("drive-trash-file", object())
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await deletes.prepare_delete(untyped, {})
    ordinary = deletes.additional.remember("drive-file", object())
    with pytest.raises(ToolFailure):
        await deletes.prepare_delete(ordinary, {})
    target = DriveTarget(_data["drivewsid"], _data.get("zone"))
    deletes._deletions = {str(index): (handle, target) for index in range(256)}
    await deletes.prepare_delete(handle, {})
    assert len(deletes._deletions) == 256
    deletes.additional.reads.owner.config = type(
        deletes.additional.reads.owner.config
    )()
    with pytest.raises(WriteDenied, match="write_disabled"):
        await deletes.prepare_delete(handle, {})
    client.drive.delete_forever_from_trash.assert_not_called()


@pytest.mark.asyncio
async def test_permanent_delete_refuses_downgraded_preview_or_consumed_tier() -> None:
    """Standard-tier policy or receipts cannot reach irreversible SDK calls."""
    deletes, client, handle, _data = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    with (
        patch.object(
            deletes.ledger, "preview", return_value=replace(preview, risk="standard")
        ),
        pytest.raises(WriteDenied, match="high_risk_operator_approval_required"),
    ):
        await deletes.prepare_delete(handle, {})
    with (
        patch.object(
            deletes.ledger, "consume", return_value=replace(preview, risk="standard")
        ),
        pytest.raises(WriteDenied, match="high_risk_operator_approval_required"),
    ):
        await deletes.apply(preview.request_id)
    assert deletes.additional.resolve("drive-trash-file", handle)
    client.drive.delete_forever_from_trash.assert_not_called()
