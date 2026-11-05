"""Calendar protocol registration is explicit and uses offline adapter ownership."""

from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
async def test_calendar_registration_routes_both_tools() -> None:
    """Every selected Calendar callback runs without enabling mutation tools."""
    owner = owner_for(MagicMock(), services=("calendar",))
    server = build_server(owner=owner)
    names = {tool.name for tool in await server.list_tools()}
    assert names == {
        "icloud_status",
        "icloud_capabilities",
        "icloud_calendar_list",
        "icloud_calendar_events",
    }
    for name, arguments in [
        ("icloud_calendar_list", {}),
        (
            "icloud_calendar_events",
            {"calendar_id": "C", "start": "2026-01-01", "end": "2026-01-01"},
        ),
    ]:
        result = await server.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        assert not result.is_error
    assert len(await build_server().list_tools()) == 2
