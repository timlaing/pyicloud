"""Shared helpers for loading JSON test fixtures.

Test modules must not touch the file system while they are imported.  Routing
every fixture read through :func:`load_fixture` keeps the reads at test time and
gives the file-system guards in ``tests/conftest.py`` a single, explicit place
to recognise as sanctioned fixture access.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
from typing import Any

FIXTURE_ROOT: Path = Path(__file__).parent / "fixtures"
_RESOLVED_FIXTURE_ROOT: Path = FIXTURE_ROOT.resolve()

_FIXTURE_READ: ContextVar[bool] = ContextVar("fixture_read", default=False)


def fixture_read_allowed() -> bool:
    """Return whether the caller is the sanctioned fixture loader."""

    return _FIXTURE_READ.get()


@contextmanager
def _fixture_read() -> Generator[None, None, None]:
    """Mark the current context as performing a sanctioned fixture read."""

    token = _FIXTURE_READ.set(True)
    try:
        yield
    finally:
        _FIXTURE_READ.reset(token)


def load_fixture(*parts: str) -> Any:
    """Load and parse a JSON fixture below ``tests/fixtures``.

    ``parts`` are joined onto the fixture root, so
    ``load_fixture("notes", "note_rendering_fixture.json")`` reads
    ``tests/fixtures/notes/note_rendering_fixture.json``.  Callers should
    request a fixture from the test that needs it rather than at import time.
    """

    path = FIXTURE_ROOT.joinpath(*parts)
    with _fixture_read():
        resolved = path.resolve()
        if not resolved.is_relative_to(_RESOLVED_FIXTURE_ROOT):
            raise ValueError(f"Fixture path escapes {FIXTURE_ROOT}: {path}")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
