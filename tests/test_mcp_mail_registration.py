"""Mail protocol descriptions and content gates match the allowed fields."""

from dataclasses import replace
from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
@pytest.mark.parametrize("content_enabled", [False, True])
async def test_mail_registration_preserves_content_opt_in(
    content_enabled: bool,
) -> None:
    """Exercise metadata/body wrappers while preventing any model-callable writer."""
    owner = owner_for(MagicMock(), services=("mail",))
    owner.config = replace(
        owner.config, content_services=("mail",) if content_enabled else ()
    )
    server = build_server(owner=owner)
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert ("icloud_mail_content" in names) is content_enabled
    assert {
        "icloud_mail_folders",
        "icloud_mail_messages",
        "icloud_mail_search",
    } <= names
    search = next(tool for tool in tools if tool.name == "icloud_mail_search")
    assert search.description is not None
    assert "body/all require Mail content opt-in" in search.description
    for name, arguments in [
        ("icloud_mail_folders", {}),
        ("icloud_mail_messages", {"folder_handle": "unknown"}),
        ("icloud_mail_search", {"folder_handle": "unknown", "query": "Subject"}),
        ("icloud_mail_content", {"handle": "unknown"}),
    ]:
        if name in names:
            result = await server.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            assert not result.is_error
    assert "icloud_write_execute" not in names
    assert len(await build_server().list_tools()) == 2
