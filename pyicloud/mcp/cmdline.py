"""Dependency-light entry point for the optional local MCP server."""

# Optional imports must remain behind offline argument handling.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import argparse
from importlib.metadata import version
import sys


def main() -> None:
    """Parse offline options before loading the optional protocol implementation."""
    parser = argparse.ArgumentParser(
        prog="icloud-mcp", description="Run the local PyiCloud MCP server over stdio."
    )
    parser.add_argument("--version", action="version", version=version("pyicloud"))
    parser.parse_args()
    try:
        from pyicloud.mcp.server import (
            build_server,  # pylint: disable=import-outside-toplevel
        )
    except ModuleNotFoundError as error:
        dependency = str(error.name or "")
        if dependency != "mcp" and not dependency.startswith("mcp."):
            raise
        print(
            'Install the MCP extension with pip install "pyicloud[mcp]".',
            file=sys.stderr,
        )
        raise SystemExit(2) from error
    build_server().run(transport="stdio")
