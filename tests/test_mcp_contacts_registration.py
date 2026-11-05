"""Contacts protocol registration retains independent field selection."""

from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
async def test_contacts_registration_routes_both_tools() -> None:
    """Only name discovery and explicitly requested handle details are registered."""
    server = build_server(owner=owner_for(MagicMock(), services=("contacts",)))
    assert {tool.name for tool in await server.list_tools()} == {
        "icloud_status",
        "icloud_capabilities",
        "icloud_contacts_list",
        "icloud_contact_details",
    }
    for name, arguments in [
        ("icloud_contacts_list", {}),
        ("icloud_contact_details", {"handle": "unknown"}),
    ]:
        result = await server.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        assert not result.is_error
    assert len(await build_server().list_tools()) == 2
