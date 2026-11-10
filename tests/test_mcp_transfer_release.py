"""Offline abandoned-transfer release and complete Trash inventory admission."""

# pylint: disable=protected-access

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from mcp.types import CallToolResult
import pytest
from requests import Session

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import DownloadState
from pyicloud.mcp.server import build_server
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_additional import owner_for
from tests.test_mcp_content import content_for
from tests.test_mcp_drive_deletions import setup_delete


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["drive", "photos"])
async def test_release_transfer_preserves_other_active_entity_tag(service: str) -> None:
    """Only the explicit service/handle/version token can release its local slot."""
    tools = content_for(MagicMock())
    first = DownloadState(
        object(), etag='"first"', active=True, binding=f"{service}:handle:original"
    )
    second = DownloadState(
        object(), etag='"second"', active=True, binding=f"{service}:other:original"
    )
    tools._downloads = {"first": first, "second": second}
    assert (
        await tools.release_transfer(service, "wrong", "first")
    ).error == "invalid_download_transfer"
    result = await tools.release_transfer(service, "handle", "first")
    assert result.records == [{"released": True}]
    assert "first" not in tools._downloads
    assert tools._downloads["second"] is second
    assert second.etag == '"second"'
    assert (
        await tools.release_transfer(service, "handle", "first")
    ).error == "invalid_download_transfer"
    assert (
        await tools.release_transfer("notes", "handle", "first")
    ).error == "invalid_argument"


@pytest.mark.asyncio
async def test_release_frees_full_registry_without_eviction() -> None:
    """Clients can free abandoned active slots without waiting for TTL expiry."""
    tools = content_for(MagicMock())
    tools._downloads = {
        str(index): DownloadState(
            object(), active=True, etag='"pinned"', binding="drive:h:original"
        )
        for index in range(1000)
    }
    assert (await tools.release_transfer("drive", "h", "0")).error is None
    tools._download_state("new", object())
    assert len(tools._downloads) == 1000
    assert tools._downloads["1"].etag == '"pinned"'


@pytest.mark.asyncio
async def test_release_tools_are_scoped_protocol_calls() -> None:
    """SDK adapters expose token release only with the content permission opt-in."""
    client = MagicMock()
    tools = content_for(client)
    tools._downloads["drive-token"] = DownloadState(
        object(), binding="drive:h:original", active=True
    )
    tools._downloads["photo-token"] = DownloadState(
        object(), binding="photos:h:original", active=True
    )
    owner = owner_for(client)
    owner.config = replace(owner.config, content_services=("drive", "photos"))
    with patch("pyicloud.mcp.server.ContentReads", return_value=tools):
        server = build_server(owner=owner)
    for name, token in [
        ("icloud_drive_download_release", "drive-token"),
        ("icloud_photo_download_release", "photo-token"),
    ]:
        result = await server.call_tool(name, {"handle": "h", "transfer_token": token})
        assert isinstance(result, CallToolResult)
        assert not result.is_error
    assert not tools._downloads


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [2001, None])
async def test_large_or_missing_trash_inventory_cannot_prove_deletion(
    size: int | None,
) -> None:
    """Incomplete data must deny proof before constructing any child objects."""
    writer, client, _handle, _data = setup_delete()
    session = Session()
    drive = DriveService(
        "https://drive.example.test",
        "https://docs.example.test",
        cast(Any, session),
        {},
    )
    drive._trash = DriveNode(
        drive, {"drivewsid": "FOLDER::Z::TRASH_ROOT", "type": "FOLDER", "items": []}
    )
    actual = SimpleNamespace(drive=drive)
    data: dict[str, Any] = {"drivewsid": "FOLDER::Z::TRASH_ROOT"}
    if size is not None:
        data["items"] = [{}] * size
    with (
        patch.object(writer, "_require_metadata_transport"),
        patch.object(drive, "get_node_data", return_value=data),
        patch.object(
            DriveNode, "__init__", autospec=True, return_value=None
        ) as constructor,
        pytest.raises(WriteDenied, match="unsupported_trash_inventory"),
    ):
        writer._trash_inventory(actual)
    constructor.assert_not_called()
    client.drive.delete_forever_from_trash.assert_not_called()
    session.close()
