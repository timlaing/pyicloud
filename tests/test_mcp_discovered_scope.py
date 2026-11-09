"""Offline discovered-scope, local cleanup and metadata-binding regressions."""

# pylint: disable=protected-access

from dataclasses import replace
from threading import Thread
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import ContentReads, DownloadState
from pyicloud.mcp.context import ServerConfig, SessionOwner
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.server import build_server
from pyicloud.services.drive import DriveNode
from tests.test_mcp_additional import adapters, owner_for
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
async def test_release_uses_local_lane_when_authentication_is_unavailable() -> None:
    """Expired session credentials cannot prevent reclaiming local transfer slots."""
    factory = MagicMock(side_effect=AssertionError("Authentication probe forbidden"))
    config = ServerConfig(
        username="synthetic",
        session_dir="synthetic",
        services=("drive",),
        content_services=("drive",),
    )
    owner = SessionOwner(config, factory)
    content = ContentReads(AdditionalReads(ReadTools(owner)))
    content._downloads["token"] = DownloadState(
        object(), active=True, binding="drive:h:original"
    )
    try:
        with patch("threading.Thread", Thread):
            assert (await content.release_transfer("drive", "h", "token")).error is None
        factory.assert_not_called()
        assert not content._downloads
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["browse", "search"])
async def test_folder_with_foreign_connection_never_reads_it(service: str) -> None:
    """A discovered folder cannot borrow an unchecked session for enumeration."""
    client = MagicMock()
    foreign = MagicMock()
    folder = DriveNode(
        foreign, {"drivewsid": "FOLDER::Z::F", "type": "FOLDER", "items": []}
    )
    reads = adapters(client)
    handle = reads.remember("drive-folder", folder)
    result = (
        await reads.drive(handle, "", 1)
        if service == "browse"
        else await reads.search("drive", handle, "match", 1, 1)
    )
    assert result.error == "unsupported_drive_connection"
    foreign.get_node_data.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", [None, 123, ["bad"]])
async def test_malformed_discovered_identifier_has_known_denial(
    identifier: Any,
) -> None:
    """Invalid IDs are rejected before splitting or metadata/transport access."""
    writer, client, handle, _data = setup_writer()
    node = writer.additional.resolve("drive-file", handle)
    if identifier is None:
        node.data.pop("drivewsid")
    else:
        node.data["drivewsid"] = identifier
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await writer.prepare(handle, {"name": "After"})
    client.drive.get_node_data.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["move_source", "move_destination", "create", "recover", "delete"]
)
async def test_discovered_sharing_cannot_be_erased_by_fresh_private_metadata(
    action: str,
) -> None:
    """Original share context remains a denial even when later responses omit it."""
    writer: Any
    if action.startswith("move"):
        writer, client, handle, destination, _data, _folder = setup_move()
        selected = handle if action == "move_source" else destination
        kind = "drive-file" if action == "move_source" else "drive-folder"
        node = writer.additional.resolve(kind, selected)
        operation = writer.prepare_move(handle, destination, {})
    elif action == "delete":
        writer, client, handle, _data = setup_delete()
        node = writer.additional.resolve("drive-trash-file", handle)
        operation = writer.prepare_delete(handle, {})
    else:
        writer, client, handle, _data = setup_action(action == "recover")
        node = writer.additional.resolve(
            "drive-trash-file" if action == "recover" else "drive-folder", handle
        )
        operation = writer.prepare_acknowledged(
            "drive.recover_item" if action == "recover" else "drive.create_folder",
            handle,
            {} if action == "recover" else {"name": "New"},
        )
    node.data["share"] = {"private": False}
    with pytest.raises(WriteDenied, match="unsupported_shared_write"):
        await operation
    client.drive.get_node_data.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_refresh_cannot_change_bound_route() -> None:
    """Recovery preflight rejects a route switch during Trash metadata refresh."""
    writer, client, handle, _data = setup_action(True)
    original = client.drive.trash.get_children.return_value

    def listing(**_kwargs: Any) -> Any:
        client.drive.service_root = "https://foreign.example.test"
        return original

    client.drive.trash.get_children.side_effect = listing
    with pytest.raises(WriteDenied, match="stale_preview"):
        await writer.prepare_acknowledged("drive.recover_item", handle, {})
    assert not writer.ledger._pending


@pytest.mark.asyncio
async def test_release_annotations_require_local_state_change_approval() -> None:
    """Transfer invalidation is not advertised as a read-only speculative call."""
    client = MagicMock()
    owner = owner_for(client)
    owner.config = replace(owner.config, content_services=("drive", "photos"))
    server = build_server(owner=owner)
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in ("icloud_drive_download_release", "icloud_photo_download_release"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
        assert annotations.open_world_hint is False
