"""Offline Drive review regressions without credentials or actual mutations."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.drive_acknowledged import _invalid_folder_payload
from pyicloud.mcp.drive_upload_bytes import _commit_matches
from pyicloud.mcp.drive_writes import DriveWrites
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_integer_account_route_can_prepare_acknowledged_write(
    recover: bool,
) -> None:
    """Integer and string account IDs describe the same observed account."""
    writer, client, handle, _data = setup_action(recover)
    client.params["dsid"] = 123
    client.drive.params["dsid"] = 123
    action = "drive.recover_item" if recover else "drive.create_folder"
    preview = await writer.prepare_acknowledged(
        action, handle, {} if recover else {"name": "New"}
    )
    assert preview.action == action


@pytest.mark.asyncio
async def test_integer_account_upload_can_prepare_without_dispatch() -> None:
    """Upload account checks normalize dsid before comparison."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    client.params["dsid"] = 123
    client.drive.params["dsid"] = 123
    preview = await writer.prepare_upload(handle, token, 1000)
    assert preview.action == "drive.upload_file"
    pipeline.upload.assert_not_called()


def test_direct_upload_adapter_registers_staging_cleanup() -> None:
    """Owner shutdown callbacks release stages even without the preview wrapper."""
    writer, _client, _handle, token, _data, _pipeline = setup_adapter()
    owner = cast(Any, writer.additional.reads.owner)
    callback = owner.add_cleanup.call_args.args[0]
    assert callback == writer.staging.close
    callback()
    with pytest.raises(WriteDenied):
        writer.staging.peek(token)


@pytest.mark.asyncio
async def test_oversized_stage_is_rejected_before_preview_and_reads() -> None:
    """A larger staging configuration cannot authorize bytes the pipeline rejects."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    with (
        patch.object(
            writer.staging,
            "peek",
            return_value=SimpleNamespace(size=4 * 1024 * 1024 + 1),
        ),
        pytest.raises(WriteDenied, match="invalid_upload_bytes"),
    ):
        await writer.prepare_upload(handle, token, 1000)
    assert not writer.ledger._pending
    client.drive.get_node_data.assert_not_called()
    pipeline.upload.assert_not_called()


def test_surrogate_names_are_invalid_leaf_payloads() -> None:
    """Unpaired surrogates must not raise raw Unicode encoding errors."""
    data = {"name": "\ud800"}
    assert DriveWrites._invalid_rename(data, data["name"])
    assert _invalid_folder_payload(data, data["name"])


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["identity", "sharing"])
async def test_second_destination_response_is_revalidated(changed: str) -> None:
    """A child refresh cannot replace the destination route after initial checks."""
    writer, client, source, destination, data, folder = setup_move()
    count = 0

    def fresh(identifier: str, *_args: object) -> dict[str, Any]:
        nonlocal count
        if identifier == data["drivewsid"]:
            return dict(data)
        count += 1
        result = dict(folder)
        if count == 2:
            if changed == "identity":
                result["drivewsid"] = "OTHER"
            else:
                result["shareID"] = "foreign"
        return result

    client.drive.get_node_data.side_effect = fresh
    with pytest.raises(WriteDenied):
        await writer.prepare_move(source, destination, {})
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "ok",
        "top_failure",
        "result_failure",
        "wrong_id",
        "missing",
        "duplicate",
        "boolean",
    ],
)
def test_commit_requires_success_and_exact_reserved_document(case: str) -> None:
    """Structured commit acknowledgements cannot accept unrelated documents."""
    result: dict[str, Any] = {
        "status": {"status_code": 0},
        "document": {"document_id": "allocated"},
    }
    response: dict[str, Any] = {"status": {"status_code": 0}, "results": [result]}
    if case == "top_failure":
        response["status"]["status_code"] = 1
    elif case == "result_failure":
        result["status"]["status_code"] = 1
    elif case == "wrong_id":
        result["document"]["document_id"] = "unrelated"
    elif case == "missing":
        response["results"] = []
    elif case == "duplicate":
        response["results"] = [result, result]
    elif case == "boolean":
        response["status"]["status_code"] = False
    assert _commit_matches(response, "allocated") is (case == "ok")
    assert not _commit_matches({"status": "OK"}, "allocated")
