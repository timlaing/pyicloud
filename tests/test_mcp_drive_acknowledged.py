"""Synthetic existing Drive requests, explicitly acknowledged not final-state proof."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.drive_acknowledged import DriveAcknowledged
from pyicloud.mcp.drive_writes import DriveWrites
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move


def setup_action(recover: bool) -> tuple[DriveAcknowledged, Any, str, dict[str, Any]]:
    """Real typed nodes and fake authoritative metadata; no transport."""
    writer: DriveWrites
    if recover:
        writer, client, handle, data = setup_delete()
    else:
        writer, client, _handle, handle, _data, data = setup_move()
    client.drive.params = dict(client.params)
    writer.ledger = ApprovalLedger(
        frozenset({"drive.create_folder", "drive.recover_item"})
    )
    return DriveAcknowledged(writer), client, handle, data


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("case", ["ok", "transport", "wrong", "route"])
async def test_one_raw_request_returns_acknowledgement_only(
    recover: bool, case: str
) -> None:
    """Consume source/receipt once and never invent final IDs or recovered handles."""
    writer, client, handle, data = setup_action(recover)
    action = "drive.recover_item" if recover else "drive.create_folder"
    publish = MagicMock()
    preview = await writer.prepare_acknowledged(
        action, handle, {} if recover else {"name": "New"}, publish
    )
    publish.assert_called_once_with(preview)
    response: Any = (
        {"items": [{"drivewsid": data["drivewsid"], "status": "OK"}]}
        if recover
        else {"folders": [{"name": "New"}]}
    )
    if case == "wrong":
        response = {"status": "OK"}

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        with pytest.raises(ToolFailure):
            writer.additional.resolve(
                "drive-trash-file" if recover else "drive-folder", handle
            )
        if case == "transport":
            raise OSError("private")
        if case == "route":
            client.drive.service_root = "https://other.test"
        return response

    with patch(
        "pyicloud.mcp.drive_acknowledged.UploadTransport.register", side_effect=dispatch
    ) as raw:
        with pytest.raises(WriteDenied):
            await writer.apply(preview.request_id)
        raw.assert_not_called()
        writer.ledger.approve_from_operator(preview.request_id, preview.digest)
        if case == "ok":
            assert await writer.apply(preview.request_id) == {
                "action": action,
                "acknowledged": True,
                "final_state_verified": False,
            }
            request = raw.call_args.args[3]
            if recover:
                assert request == {
                    "items": [{"drivewsid": data["drivewsid"], "etag": data["etag"]}]
                }
            else:
                assert request["destinationDrivewsId"] == data["drivewsid"]
                assert request["folders"][0]["name"] == "New"
                assert request["folders"][0]["clientId"].startswith(
                    "FOLDER::UNKNOWN_ZONE::TempId-"
                )
                assert raw.call_args.kwargs["headers"] == {"Content-Type": "plain/text"}
        else:
            with pytest.raises(WriteDenied, match="mutation_uncertain"):
                await writer.apply(preview.request_id)
        raw.assert_called_once()
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writer.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "name",
        "action",
        "scope",
        "route",
        "params",
        "collision",
        "stale",
        "restore",
        "payload",
    ],
)
async def test_unsafe_or_changed_targets_do_not_dispatch(case: str) -> None:
    """Acknowledgement-only permission does not relax preflight or binding guards."""
    recover = case in {"restore", "payload"}
    writer, client, handle, data = setup_action(recover)
    action = "drive.recover_item" if recover else "drive.create_folder"
    if case == "scope":
        data["shareID"] = {"owner": "other"}
    elif case == "route":
        client.drive.service_root = "http://bad.test"
    elif case == "params":
        client.drive.params["dsid"] = "different"
    elif case == "collision":
        data["items"] = [{"name": "New", "type": "FOLDER"}]
    elif case == "restore":
        data["restorePath"] = ""
    with patch("pyicloud.mcp.drive_acknowledged.UploadTransport.register") as raw:
        if case == "stale":
            preview = await writer.prepare_acknowledged(action, handle, {"name": "New"})
            writer.ledger.approve_from_operator(preview.request_id, preview.digest)
            data["etag"] = "different"
            with pytest.raises(WriteDenied):
                await writer.apply(preview.request_id)
        else:
            with pytest.raises(WriteDenied):
                await writer.prepare_acknowledged(
                    "bad" if case == "action" else action,
                    handle,
                    {"confirm": True}
                    if case == "payload"
                    else {}
                    if recover
                    else {"name": "../bad" if case == "name" else "New"},
                )
        raw.assert_not_called()
