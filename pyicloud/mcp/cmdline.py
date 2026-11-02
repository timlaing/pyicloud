"""Dependency-light entry point for the optional local MCP server."""

# Optional imports must remain behind offline argument handling.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import argparse
from importlib.metadata import version
import logging
import sys

from .context import ServerConfig


def quiet_library_logging() -> None:
    """Prevent private library errors or data from propagating to host logs."""
    logger = logging.getLogger("pyicloud")
    logger.disabled = True
    logger.setLevel(logging.CRITICAL + 1)
    logger.propagate = False
    logger.addHandler(logging.NullHandler())


def main() -> None:
    """Parse offline options before loading the optional protocol implementation."""
    parser = argparse.ArgumentParser(
        prog="icloud-mcp", description="Run the local PyiCloud MCP server over stdio."
    )
    parser.add_argument("--version", action="version", version=version("pyicloud"))
    parser.add_argument("--username")
    parser.add_argument("--session-dir")
    parser.add_argument(
        "--service", choices=["calendar", "reminders"], action="append", default=[]
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--queue-limit", type=int, default=8)
    parser.add_argument("--china-mainland", action="store_true")
    args = parser.parse_args()
    try:
        config = ServerConfig(
            username=args.username,
            session_dir=args.session_dir,
            services=tuple(args.service),
            timeout=args.timeout,
            queue_limit=args.queue_limit,
            china_mainland=args.china_mainland,
        )
    except ValueError as error:
        parser.error(str(error))
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
    quiet_library_logging()
    build_server(config).run(transport="stdio")
