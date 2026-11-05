"""Explicit Mail metadata and additional body-content permission registration."""

# cspell:words mcpserver

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..additional import AdditionalReads
from ..content import ContentReads
from ..context import SessionOwner
from ..reads import ReadResult


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
    async def mail_folders(limit: int = 50) -> ReadResult:
        """Discover typed mailbox handles for metadata-only operations."""
        return await additional.mail_folders(limit)

    @server.tool(name="icloud_mail_messages", annotations=annotations)
    async def mail_messages(folder_handle: str, limit: int = 50) -> ReadResult:
        """Read subjects and flags in one mailbox without bodies or addresses."""
        return await additional.mail_messages(folder_handle, limit)

    @server.tool(name="icloud_mail_search", annotations=annotations)
    async def mail_search(
        folder_handle: str,
        query: str,
        limit: int = 50,
        field: Literal["subject", "from", "to", "body", "all"] = "subject",
    ) -> ReadResult:
        """Search subject/from/to/body/all; body/all require Mail content opt-in.

        Results contain thread IDs and subjects only; no previews or bodies.
        """
        return await additional.mail_search(folder_handle, query, limit, field)

    if "mail" in context.config.content_services:

        @server.tool(name="icloud_mail_content", annotations=annotations)
        async def mail_content(handle: str, max_bytes: int = 16384) -> ReadResult:
            """Read bounded untrusted MIME text without rendering or raw headers."""
            return await content.mail_body(handle, max_bytes)
