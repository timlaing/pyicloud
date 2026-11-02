"""Local stdio protocol registration, without implicit Apple authentication."""

# cspell:words mcpserver

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel


class ServerStatus(BaseModel):
    """Safe local status, containing no account identifiers or saved-state paths."""

    authenticated: bool = False
    remote_services_enabled: bool = False
    message: str = "No account configured. Remote tools are not enabled."


def build_server() -> MCPServer:
    """Build the protocol server without constructing a client or reading secrets."""
    server = MCPServer(
        "PyiCloud", instructions="Local status only; no Apple account is configured."
    )

    @server.tool(
        name="icloud_status",
        description="Report local server status without contacting Apple.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def status() -> ServerStatus:
        """No account access or implicit login is performed."""
        return ServerStatus()

    return server
