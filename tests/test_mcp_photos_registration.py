"""Photos protocol tools preserve discovered handles and startup content consent."""

from dataclasses import replace
from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
@pytest.mark.parametrize("content_enabled", [False, True])
async def test_photos_registration_preserves_content_opt_in(
    content_enabled: bool,
) -> None:
    """Exercise every installed Photos callback while retaining read-only startup."""
    owner = owner_for(MagicMock(), services=("photos",))
    owner.config = replace(
        owner.config, content_services=("photos",) if content_enabled else ()
    )
    server = build_server(owner=owner)
    names = {tool.name for tool in await server.list_tools()}
    assert ("icloud_photo_download" in names) is content_enabled
    assert {
        "icloud_photos_libraries",
        "icloud_photos_albums",
        "icloud_photos_list",
        "icloud_photos_search",
    } <= names
    for name, arguments in [
        ("icloud_photos_libraries", {}),
        ("icloud_photos_albums", {"library_handle": "unknown"}),
        ("icloud_photos_list", {"album_handle": "unknown"}),
        ("icloud_photos_search", {"album_handle": "unknown", "query": "Photo"}),
        ("icloud_photo_download", {"handle": "unknown"}),
    ]:
        if name in names:
            result = await server.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            assert result.structured_content is not None
            assert result.is_error is (result.structured_content["error"] is not None)
    assert "icloud_write_execute" not in names
    assert len(await build_server().list_tools()) == 2
