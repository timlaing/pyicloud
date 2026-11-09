"""Explicit Drive discovery, bounded search and opted-in download registration."""

# cspell:words mcpserver

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ToolAnnotations

from ..additional import AdditionalReads
from ..content import ContentReads
from ..context import SessionOwner
from .calendar import tool_result


def register(
    server: MCPServer,
    context: SessionOwner,
    _reads: Any,
    additional: AdditionalReads,
    content: ContentReads,
) -> None:
    """Keep discovered Drive handles and independent content startup permission."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_drive_trash_list", annotations=annotations)
    async def drive_trash(
        query: str = "", limit: int = 50, scan_limit: int = 500
    ) -> CallToolResult:
        """List bounded trash metadata, never restore paths or content."""
        return tool_result(await additional.drive_trash(query, limit, scan_limit))

    @server.tool(name="icloud_drive_list", annotations=annotations)
    async def drive(
        folder_handle: str = "", query: str = "", limit: int = 50, scan_limit: int = 500
    ) -> CallToolResult:
        """Browse a root or discovered folder without accepting local paths."""
        return tool_result(
            await additional.drive(folder_handle, query, limit, scan_limit)
        )

    @server.tool(name="icloud_drive_search", annotations=annotations)
    async def drive_search(
        query: str, folder_handle: str = "", limit: int = 50, scan_limit: int = 500
    ) -> CallToolResult:
        """Search remote names across at most sixteen folders and bounded rows."""
        return tool_result(
            await additional.search("drive", folder_handle, query, limit, scan_limit)
        )

    if "drive" in context.config.content_services:

        @server.tool(name="icloud_drive_download", annotations=annotations)
        async def drive_download(
            handle: str,
            offset: int = 0,
            max_bytes: int = 65536,
            transfer_token: str = "",
        ) -> CallToolResult:
            """Return version-bound Base64 chunks for a discovered file, not paths."""
            return tool_result(
                await content.file(
                    "drive", handle, offset, max_bytes, transfer_token=transfer_token
                )
            )
