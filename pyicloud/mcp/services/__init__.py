"""Explicit service tool registration supplied by the dependent service PRs."""

from importlib import import_module
from importlib.util import find_spec
from typing import Any


def register_services(
    server: Any, context: Any, reads: Any, additional: Any, content: Any
) -> None:
    """Load only fixed installed providers for explicitly selected services."""
    for service in (
        "calendar",
        "reminders",
        "contacts",
        "drive",
        "notes",
        "photos",
        "mail",
    ):
        if service not in context.config.services:
            continue
        name = f"{__name__}.{service}"
        if find_spec(name) is None:
            continue
        provider = import_module(name)
        provider.register(server, context, reads, additional, content)
