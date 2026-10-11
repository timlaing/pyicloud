"""MkDocs build hooks for the PyiCloud documentation site."""

from __future__ import annotations

import re
from typing import Any

# mkdocs-typer2 injects CLI usage lines as plain text inside <code> nodes. The
# mkdocs-autorefs inline processor then reads sequences such as
# "[OPTIONS][path]" as a cross-reference "[text][identifier]" and warns when the
# target does not exist. Unwrapping the generated autoref tags on the CLI page
# keeps the visible text while silencing the spurious warning.
_AUTOREF_TAG_RE = re.compile(r"<autoref\b[^>]*>(?P<title>.*?)</autoref>", re.DOTALL)
_LEGACY_AUTOREF_RE = re.compile(
    r"<span data-autorefs-[^>]*>(?P<title>.*?)</span>", re.DOTALL
)


def on_page_content(  # pylint: disable=unused-argument
    html: str,
    page: Any,
    config: Any,
    files: Any,
) -> str:
    """Strip autoref wrappers from the generated CLI reference page."""
    if page.file.src_path != "api/cli.md":
        return html
    html = _AUTOREF_TAG_RE.sub(r"\g<title>", html)
    return _LEGACY_AUTOREF_RE.sub(r"\g<title>", html)
