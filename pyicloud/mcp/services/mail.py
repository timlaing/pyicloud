"""Explicit Mail metadata and additional body-content permission registration."""

# cspell:words mcpserver

from typing import Any, Literal

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
    """Keep mailbox discovery and metadata searches separate from body permission."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_mail_folders", annotations=annotations)
    async def mail_folders(limit: int = 50) -> CallToolResult:
        """Discover typed mailbox handles for metadata-only operations."""
        return tool_result(await additional.mail_folders(limit))

    @server.tool(name="icloud_mail_messages", annotations=annotations)
    async def mail_messages(folder_handle: str, limit: int = 50) -> CallToolResult:
        """Read subjects and flags in one mailbox without bodies or addresses."""
        return tool_result(await additional.mail_messages(folder_handle, limit))

    @server.tool(name="icloud_mail_search", annotations=annotations)
    async def mail_search(
        folder_handle: str,
        query: str,
        limit: int = 50,
        field: Literal["subject", "from", "to", "body", "all"] = "subject",
    ) -> CallToolResult:
        """Search subject/from/to/body/all; body/all require Mail content opt-in.

        Results contain thread IDs and subjects only; no previews or bodies.
        """
        return tool_result(
            await additional.mail_search(folder_handle, query, limit, field)
        )

    if "mail" in context.config.content_services:

        @server.tool(name="icloud_mail_content", annotations=annotations)
        async def mail_content(handle: str, max_bytes: int = 16384) -> CallToolResult:
            """Read bounded untrusted MIME text without rendering or raw headers."""
            return tool_result(await content.mail_body(handle, max_bytes))
