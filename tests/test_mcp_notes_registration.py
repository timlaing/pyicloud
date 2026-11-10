"""Notes protocol metadata cannot implicitly enable body reads."""

from dataclasses import replace
from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
@pytest.mark.parametrize("content_enabled", [False, True])
async def test_notes_registration_preserves_content_opt_in(
    content_enabled: bool,
) -> None:
    """Exercise all installed Notes callbacks and both content registration states."""
    owner = owner_for(MagicMock(), services=("notes",))
    owner.config = replace(
        owner.config, content_services=("notes",) if content_enabled else ()
    )
    server = build_server(owner=owner)
    names = {tool.name for tool in await server.list_tools()}
    assert ("icloud_note_content" in names) is content_enabled
    assert {"icloud_notes_list", "icloud_notes_search"} <= names
    for name, arguments in [
        ("icloud_notes_list", {}),
        ("icloud_notes_search", {"query": "Note"}),
        ("icloud_note_content", {"handle": "unknown"}),
    ]:
        if name in names:
            result = await server.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            assert result.is_error is (name == "icloud_note_content")
    assert "icloud_write_execute" not in names
    assert len(await build_server().list_tools()) == 2
