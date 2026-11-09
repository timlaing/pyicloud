"""Explicit Photos discovery, scoped filename search and opted-in downloads."""

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
    """Use only discovered Photos library, album and asset handle contexts."""
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="icloud_photos_libraries", annotations=annotations)
    async def photo_libraries(limit: int = 50) -> CallToolResult:
        """Discover expiring library contexts without exposing zone owners."""
        return tool_result(await additional.photo_libraries(limit))

    @server.tool(name="icloud_photos_albums", annotations=annotations)
    async def photo_albums(library_handle: str, limit: int = 50) -> CallToolResult:
        """Browse one discovered library while preserving the album route."""
        return tool_result(await additional.photo_albums(library_handle, limit))

    @server.tool(name="icloud_photos_list", annotations=annotations)
    async def photos(album_handle: str, limit: int = 50) -> CallToolResult:
        """Read bounded photo metadata without coordinates or signed URLs."""
        return tool_result(await additional.photos(album_handle, limit))

    @server.tool(name="icloud_photos_search", annotations=annotations)
    async def photos_search(
        album_handle: str, query: str, limit: int = 50, scan_limit: int = 500
    ) -> CallToolResult:
        """Search filenames inside one discovered album under a scan budget."""
        return tool_result(
            await additional.search("photos", album_handle, query, limit, scan_limit)
        )

    if "photos" in context.config.content_services:

        @server.tool(name="icloud_photo_download_release", annotations=annotations)
        async def photo_download_release(
            handle: str, transfer_token: str, version: str = "original"
        ) -> CallToolResult:
            """Release only this local asset-version transfer; no remote mutation."""
            return tool_result(
                await content.release_transfer(
                    "photos", handle, transfer_token, version
                )
            )

        @server.tool(name="icloud_photo_download", annotations=annotations)
        async def photo_download(
            handle: str,
            version: str = "original",
            offset: int = 0,
            max_bytes: int = 65536,
            transfer_token: str = "",
        ) -> CallToolResult:
            """Download a discovered Apple asset version, never an arbitrary URL."""
            return tool_result(
                await content.file(
                    "photos", handle, offset, max_bytes, version, transfer_token
                )
            )
