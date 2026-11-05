"""Explicit minimized Contacts registration; this service has no write adapter."""

# cspell:words mcpserver

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..additional import AdditionalReads
from ..reads import ReadResult


def register(
    server: MCPServer,
    _context: Any,
    _reads: Any,
    additional: AdditionalReads,
    _content: Any,
) -> None:
    """Register name search and individually selected contact detail fields."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_contacts_list", annotations=annotations)
    async def contacts(query: str = "", limit: int = 50) -> ReadResult:
        """Search contact names; phone and email fields are omitted from lists."""
        return await additional.contacts(query, limit)

    @server.tool(name="icloud_contact_details", annotations=annotations)
    async def contact_details(
        handle: str, include_emails: bool = False, include_phones: bool = False
    ) -> ReadResult:
        """Read explicitly selected email and phone fields for a discovered handle."""
        return await additional.contact_details(handle, include_emails, include_phones)
