"""Explicit service tool registration supplied by the dependent service PRs."""

from typing import Any


def register_services(
    server: Any, context: Any, reads: Any, additional: Any, content: Any
) -> None:
    """No remote service tools are registered by the foundation slice alone."""
    del server, context, reads, additional, content
