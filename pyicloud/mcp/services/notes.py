"""Explicit Notes title and opted-in plaintext registration; writes are excluded."""

# cspell:words mcpserver

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ToolAnnotations

from ..additional import AdditionalReads
from ..content import ContentReads
from ..context import SessionOwner
from ..reads import ReadResult
from .calendar import tool_result


def register(
    server: MCPServer,
    context: SessionOwner,
    _reads: Any,
    additional: AdditionalReads,
    content: ContentReads,
) -> None:
    """Keep Notes metadata separate from the additional startup content opt-in."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_notes_list", annotations=annotations)
    async def notes(query: str = "", limit: int = 50) -> ReadResult:
        """Search titles within 200 recent summaries, without text or attachments."""
        return await additional.notes(query, limit)

    @server.tool(name="icloud_notes_search", annotations=annotations)
    async def notes_search(
        query: str, limit: int = 50, scan_limit: int = 500
    ) -> ReadResult:
        """Search bounded all-note title metadata, not just recents or bodies."""
        return await additional.search("notes", "", query, limit, scan_limit)

    if "notes" in context.config.content_services:

        @server.tool(name="icloud_note_content", annotations=annotations)
        async def note_content(handle: str, max_bytes: int = 16384) -> CallToolResult:
            """Read bounded untrusted plaintext without attachments or rendering."""
            return tool_result(await content.note(handle, max_bytes))
