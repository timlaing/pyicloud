"""Drive protocol metadata cannot implicitly enable download or mutation paths."""

from dataclasses import replace
from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
@pytest.mark.parametrize("content_enabled", [False, True])
async def test_drive_registration_preserves_content_opt_in(
    content_enabled: bool,
) -> None:
    """Exercise discovery/search/download wrappers without any real account access."""
    owner = owner_for(MagicMock(), services=("drive",))
    owner.config = replace(
        owner.config, content_services=("drive",) if content_enabled else ()
    )
    server = build_server(owner=owner)
    names = {tool.name for tool in await server.list_tools()}
    assert ("icloud_drive_download" in names) is content_enabled
    assert {
        "icloud_drive_list",
        "icloud_drive_trash_list",
        "icloud_drive_search",
    } <= names
    for name, arguments in [
        ("icloud_drive_list", {}),
        ("icloud_drive_trash_list", {}),
        ("icloud_drive_search", {"query": "Drive"}),
        ("icloud_drive_download", {"handle": "unknown"}),
    ]:
        if name in names:
            result = await server.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            assert not result.is_error
    assert "icloud_write_execute" not in names
    assert len(await build_server().list_tools()) == 2
