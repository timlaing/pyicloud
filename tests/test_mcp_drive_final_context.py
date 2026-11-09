"""Offline final-context checks, malformed targets and transport read caps."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter, ToolFailure
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
async def test_trash_listing_cannot_switch_context_before_absence_lookup() -> None:
    """A listing-context change denies proof before querying a foreign route."""
    writer, client, handle, _data = setup_delete()
    preview = await writer.prepare_delete(handle, {})
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def deleted(*_args: Any, **_kwargs: Any) -> None:
        def listing(**_options: Any) -> list[Any]:
            client.drive.service_root = "https://foreign.example.test"
            return []

        client.drive.trash.get_children.side_effect = listing
        client.drive.get_node_data.reset_mock()
        client.drive.get_node_data.side_effect = PyiCloudAPIResponseException(
            "Synthetic absence", 404
        )

    client.drive.delete_forever_from_trash.side_effect = deleted
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    client.drive.get_node_data.assert_not_called()


@pytest.mark.asyncio
async def test_final_move_refresh_cannot_change_the_bound_route() -> None:
    """Late destination evidence must match the originally approved context."""
    writer, client, handle, destination, data, folder = setup_move()
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    reads = [0]

    def details(identifier: str, *_args: Any) -> dict[str, Any]:
        if identifier == data["drivewsid"]:
            return dict(data)
        reads[0] += 1
        if reads[0] == 4:
            client.drive.service_root = "https://foreign.example.test"
        return dict(folder)

    def moved(*_args: Any, **_kwargs: Any) -> None:
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[dict(data)])

    client.drive.get_node_data.side_effect = details
    client.drive.move_nodes_to_node.side_effect = moved
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)


@pytest.mark.asyncio
async def test_empty_document_query_delimiter_never_publishes_upload() -> None:
    """An empty query delimiter is still not a valid service endpoint root."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    client.drive.document_root += "?"
    with pytest.raises(WriteDenied):
        await writer.prepare_upload(handle, token, 1000)
    assert writer.staging.peek(token).data
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rename", "source", "destination"])
async def test_non_node_selectors_are_known_preparation_denials(kind: str) -> None:
    """Unexpected registry values never leak attribute errors or reach dispatch."""
    if kind == "rename":
        writer, client, handle, _data = setup_writer()
        handle = writer.additional.remember("drive-file", object())
        operation = writer.prepare(handle, {"name": "After"})
    else:
        writer, client, handle, destination, _data, _folder = setup_move()
        if kind == "source":
            handle = writer.additional.remember("drive-file", object())
        else:
            destination = writer.additional.remember("drive-folder", object())
        operation = writer.prepare_move(handle, destination, {})
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await operation
    client.drive.rename_items.assert_not_called()
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
async def test_file_only_deletion_rejects_folder_identifier() -> None:
    """Consistent but contradictory type metadata cannot authorize a folder delete."""
    writer, client, handle, data = setup_delete()
    identifier = data["drivewsid"].replace("FILE::", "FOLDER::")
    data["drivewsid"] = identifier
    kind, node, expiry = writer.additional._handles[handle]
    node.data["drivewsid"] = identifier
    writer.additional._handles[handle] = (kind, node, expiry)
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await writer.prepare_delete(handle, {})
    client.drive.delete_forever_from_trash.assert_not_called()


@pytest.mark.asyncio
async def test_noop_rename_requires_live_handle_before_consumption() -> None:
    """An expired no-op selector cannot consume an otherwise valid decision."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "Before.txt"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    kind, node, _expiry = writer.additional._handles[handle]
    writer.additional._handles[handle] = (kind, node, 0)
    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
async def test_detail_specific_adapter_is_checked_before_metadata_read() -> None:
    """A metadata endpoint mount cannot bypass owner transport policy."""
    writer, client, handle, _data = setup_writer()
    client.drive.session.get_adapter.side_effect = lambda url: (
        SimpleNamespace(max_retries=SimpleNamespace(total=0))
        if "/retrieveItemDetailsInFolders" in url
        else BoundedAdapter(30)
    )
    with pytest.raises(WriteDenied, match="mutation_retry_policy_unavailable"):
        await writer.prepare(handle, {"name": "After"})
    client.drive.get_node_data.assert_not_called()


def test_adapter_reads_only_chunk_limit_plus_one_byte() -> None:
    """An ignored Range stops at the transport cap instead of buffering 4 MiB."""
    adapter = BoundedAdapter(30)
    adapter.deadline = 100
    adapter.response_limit = 4
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.raw.read1.side_effect = [b"abcde", b"never"]
    response._content_consumed = False
    with (
        patch("pyicloud.mcp.context.time.monotonic", return_value=10),
        pytest.raises(ToolFailure, match="response_too_large"),
    ):
        adapter._read_response(response)
    response.raw.read1.assert_called_once_with(5, decode_content=False)
    response.close.assert_called_once()
