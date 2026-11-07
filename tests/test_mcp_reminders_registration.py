"""Reminders protocol registration preserves discovered scoped selectors."""

from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
async def test_reminders_registration_routes_both_tools() -> None:
    """Only selected read callbacks are exposed, never ledger approval or writes."""
    server = build_server(owner=owner_for(MagicMock(), services=("reminders",)))
    assert {tool.name for tool in await server.list_tools()} == {
        "icloud_status",
        "icloud_capabilities",
        "icloud_reminders_lists",
        "icloud_reminders_list",
    }
    for name, arguments in [
        ("icloud_reminders_lists", {}),
        ("icloud_reminders_list", {"handle": "unknown"}),
    ]:
        result = await server.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        assert not result.is_error
    assert len(await build_server().list_tools()) == 2
