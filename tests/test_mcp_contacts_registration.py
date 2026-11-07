"""Contacts protocol registration retains independent field selection."""

from unittest.mock import MagicMock

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.server import build_server
from tests.test_mcp_additional import adapters, owner_for


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


@pytest.mark.asyncio
async def test_company_discovery_and_selected_details_are_pageable() -> None:
    """A company-only contact is identifiable without exposing unselected fields."""
    client = MagicMock()
    client.contacts.all = [
        {
            "companyName": "Synthetic Company",
            "emails": [{"field": f"email-{index}"} for index in range(23)],
            "phones": [{"field": f"phone-{index}"} for index in range(21)],
        }
    ]
    server = build_server(owner=owner_for(client, services=("contacts",)))
    listed = await server.call_tool("icloud_contacts_list", {"query": "company"})
    assert isinstance(listed, CallToolResult)
    assert listed.structured_content is not None
    row = listed.structured_content["records"][0]
    assert row["company_name"] == "Synthetic Company"
    assert "emails" not in row and "phones" not in row
    arguments = {
        "handle": row["handle"],
        "include_emails": True,
        "include_phones": True,
    }
    first = await server.call_tool("icloud_contact_details", arguments)
    assert isinstance(first, CallToolResult)
    assert first.structured_content is not None
    assert not first.structured_content["complete"]
    page = first.structured_content["records"][0]
    assert len(page["emails"]) == len(page["phones"]) == 20
    assert page["next_offset"] == 20
    last = await server.call_tool("icloud_contact_details", {**arguments, "offset": 20})
    assert isinstance(last, CallToolResult)
    assert last.structured_content is not None
    assert last.structured_content["complete"]
    assert last.structured_content["records"][0]["emails"] == [
        "email-20",
        "email-21",
        "email-22",
    ]
    assert last.structured_content["records"][0]["phones"] == ["phone-20"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,limit", [(-1, 20), (True, 20), (0, 0), (0, 21), (0, True)]
)
async def test_contact_detail_paging_arguments_fail_closed(
    offset: int, limit: int
) -> None:
    """Invalid paging bounds cannot access the saved contact handle."""

    result = await adapters(MagicMock()).contact_details(
        "unknown", True, True, offset, limit
    )
    assert result.error == "invalid_argument"
