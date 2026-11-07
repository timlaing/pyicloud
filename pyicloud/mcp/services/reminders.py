"""Explicit scoped Reminders metadata registration for the MCP service slice."""

# cspell:words mcpserver

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..reads import ReadResult, ReadTools


def register(
    server: MCPServer, _context: Any, reads: ReadTools, _additional: Any, _content: Any
) -> None:
    """Register read-only Reminders tools after the shared explicit service gate."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_reminders_lists", annotations=annotations)
    async def lists(limit: int = 50) -> ReadResult:
        """Discover handles valid for ten minutes or until the next enumeration."""
        return await reads.lists(limit)

    @server.tool(name="icloud_reminders_list", annotations=annotations)
    async def reminders(
        handle: str, include_completed: bool = False, limit: int = 50
    ) -> ReadResult:
        """Read a discovered handle without ambiguous route fallback."""
        return await reads.reminders(handle, include_completed, limit)
