"""Offline Drive review regressions without credentials or actual mutations."""

# pylint: disable=protected-access

from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.drive_acknowledged import _invalid_folder_payload
from pyicloud.mcp.drive_upload_bytes import _commit_matches
from pyicloud.mcp.drive_writes import DriveWrites
from pyicloud.services.drive import DriveNode
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rename", "create", "recover", "delete", "move"])
async def test_queued_write_eviction_preserves_approval_and_handles(kind: str) -> None:
    """An evicted queued request fails before acceptance or resource consumption."""
    writer: Any
    if kind == "rename":
        writer, client, handle, _data = setup_writer()
        preview = await writer.prepare(handle, {"name": "After"})
        registry = writer._targets
        handle_kind = "drive-file"
    elif kind == "move":
        writer, client, handle, destination, _data, _folder = setup_move()
        preview = await writer.prepare_move(handle, destination, {})
        registry = writer._moves
        handle_kind = "drive-file"
    elif kind == "delete":
        writer, client, handle, _data = setup_delete()
        preview = await writer.prepare_delete(handle, {})
        registry = writer._deletions
        handle_kind = "drive-trash-file"
    else:
        writer, client, handle, _data = setup_action(kind == "recover")
        preview = await writer.prepare_acknowledged(
            "drive.recover_item" if kind == "recover" else "drive.create_folder",
            handle,
            {} if kind == "recover" else {"name": "New"},
        )
        registry = writer._requests
        handle_kind = "drive-trash-file" if kind == "recover" else "drive-folder"
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=preview.risk == "high"
    )
    client.reset_mock()

    async def queued_run(operation: Callable[[Any], Any]) -> Any:
        registry.pop(preview.request_id)
        return operation(client)

    cast(Any, writer.additional.reads.owner).run = queued_run
    accept = MagicMock()
    with patch.object(writer.ledger, "consume", wraps=writer.ledger.consume) as consume:
        with pytest.raises(WriteDenied, match="unknown_or_consumed_preview"):
            await writer.apply(preview.request_id, accept)
        consume.assert_not_called()
    accept.assert_not_called()
    assert writer.additional.resolve(handle_kind, handle)
    if kind == "move":
        assert writer.additional.resolve("drive-folder", destination)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=preview.risk == "high"
    )
    assert not client.mock_calls


@pytest.mark.asyncio
async def test_failed_destination_retirement_preserves_stage_and_approval() -> None:
    """Failure before dispatch requires no restaging or spent approval."""
    writer, _client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    with (
        patch.object(
            writer.additional,
            "retire",
            side_effect=ToolFailure("expired_or_unknown_handle"),
        ),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_stage_expiry_during_refresh_preserves_destination() -> None:
    """Known expiry after refresh cannot retire a destination without dispatch."""
    writer, _client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    original_read = writer._read

    def refreshed(*args: Any) -> Any:
        result = original_read(*args)
        stage = writer.staging.peek(token)
        writer.staging._stages[token] = replace(stage, expires_at=0)
        return result

    with (
        patch.object(writer, "_read", side_effect=refreshed),
        pytest.raises(WriteDenied, match="expired_or_unknown_upload"),
    ):
        await writer.apply(preview.request_id)
    assert writer.additional.resolve("drive-folder", handle)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("field", ["shareID", "share"])
async def test_shared_trash_listing_is_refused_despite_private_detail(
    recover: bool, field: str
) -> None:
    """The independently listed target must also satisfy private-only guards."""
    writer: Any
    if recover:
        writer, client, handle, data = setup_action(True)
        operation = writer.prepare_acknowledged("drive.recover_item", handle, {})
    else:
        writer, client, handle, data = setup_delete()
        operation = writer.prepare_delete(handle, {})
    client.drive.trash.get_children.return_value = [
        DriveNode(client.drive, {**data, field: "foreign"})
    ]
    with pytest.raises(WriteDenied, match="unsupported_shared_write"):
        await operation
    client.drive.delete_forever.assert_not_called()
    client.drive.session.request_raw.assert_not_called()


@pytest.mark.asyncio
async def test_source_retirement_failure_is_not_mutation_uncertainty() -> None:
    """A handle-retirement failure before dispatch is a fixed target denial."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    with (
        patch.object(
            writer.additional,
            "retire",
            side_effect=ToolFailure("expired_or_unknown_handle"),
        ),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["create", "recover", "delete"])
async def test_other_drive_retirement_failures_are_known_denials(kind: str) -> None:
    """Acknowledged and permanent actions cannot leak raw retirement errors."""
    writer: Any
    if kind == "delete":
        writer, client, handle, _data = setup_delete()
        preview = await writer.prepare_delete(handle, {})
    else:
        writer, client, handle, _data = setup_action(kind == "recover")
        action = "drive.recover_item" if kind == "recover" else "drive.create_folder"
        preview = await writer.prepare_acknowledged(
            action, handle, {} if kind == "recover" else {"name": "New"}
        )
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=preview.risk == "high"
    )
    with (
        patch.object(
            writer.additional,
            "retire",
            side_effect=ToolFailure("expired_or_unknown_handle"),
        ),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    client.drive.session.request_raw.assert_not_called()
    client.drive.delete_forever.assert_not_called()


@pytest.mark.asyncio
async def test_queued_upload_eviction_cannot_spend_bytes_or_approval() -> None:
    """Owner-lane admission must recheck a request captured before queuing."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    async def queued_run(operation: Callable[[Any], Any]) -> Any:
        writer._requests.pop(preview.request_id)
        return operation(client)

    cast(Any, writer.additional.reads.owner).run = queued_run
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writer.apply(preview.request_id)
    assert writer.staging.peek(token).data
    assert writer.additional.resolve("drive-folder", handle)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_retry_enabled_transport_cannot_consume_or_dispatch() -> None:
    """Custom POST retries cannot turn one operator approval into two writes."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.session.get_adapter.return_value = HTTPAdapter(
        max_retries=Retry(total=2, read=2, allowed_methods={"POST"})
    )
    with pytest.raises(WriteDenied, match="mutation_retry_policy_unavailable"):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
async def test_drive_account_mismatch_cannot_spend_approval() -> None:
    """A custom client cannot authorize account A while Drive dispatches as B."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.params = {"dsid": "B"}
    with pytest.raises(WriteDenied, match="account_unavailable"):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
async def test_uncertain_rename_retires_the_selected_source_handle() -> None:
    """An unverified rename cannot expose the old filename through discovery state."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.side_effect = RuntimeError("private")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    with pytest.raises(ToolFailure):
        writer.additional.resolve("drive-file", handle)
    client.drive.rename_items.assert_called_once()


@pytest.mark.asyncio
async def test_contradictory_trash_listing_cannot_authorize_deletion() -> None:
    """Matching IDs alone cannot hide differing observed Trash revisions."""
    writer, client, handle, data = setup_delete()
    different = DriveNode(client.drive, {**data, "etag": "different"})
    client.drive.trash.get_children.return_value = [different]
    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writer.prepare_delete(handle, {})
    client.drive.delete_forever.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("drivewsid", "OTHER"),
        ("zone", "OTHER"),
        ("shareID", "foreign"),
        ("type", "FILE"),
        ("restorePath", "old"),
    ],
)
async def test_final_destination_refresh_cannot_fake_verified_move(
    field: str, value: str
) -> None:
    """An expected child cannot prove a move into an invalid final destination."""
    writer, client, handle, destination, data, folder = setup_move()
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    dispatched = False
    destination_reads = 0

    def refresh(identifier: str, *_args: object) -> dict[str, Any]:
        nonlocal destination_reads
        if identifier == data["drivewsid"]:
            return dict(data)
        observed = dict(folder)
        if dispatched:
            destination_reads += 1
            if destination_reads == 3:
                observed[field] = value
        return observed

    def move(_nodes: object, _destination: object, *, allow_redirects: bool) -> None:
        assert allow_redirects is False
        nonlocal dispatched
        dispatched = True
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[dict(data)])

    client.drive.get_node_data.side_effect = refresh
    client.drive.move_nodes_to_node.side_effect = move
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    assert destination_reads == 3
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writer.apply(preview.request_id)
    client.drive.move_nodes_to_node.assert_called_once()


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
