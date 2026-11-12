"""Structured input for private rich-text Notes writes."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from pyicloud.common.models import FrozenServiceModel

LIST_STYLES = frozenset({"bullet", "dash", "numbered", "checklist"})
MAX_LIST_INDENT = 8


def is_web_link(value: str) -> bool:
    """Accept external HTTP(S) links without credentials or control characters."""
    if not value or any(
        char.isspace() or ord(char) < 32 or char in "\x7f\\" for char in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        # Accessing the port also validates it if the URL includes one.
        port = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and (port is None or port > 0)
        )
    except ValueError:
        return False


class NoteTextRun(FrozenServiceModel):
    """A single line fragment with optional inline formatting."""

    text: str = Field(min_length=1, repr=False)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    link: str | None = Field(default=None, repr=False, max_length=4096)

    @field_validator("link")
    @classmethod
    def _validate_link(cls, value: str | None) -> str | None:
        if value is not None and not is_web_link(value):
            raise ValueError("Notes links must be HTTP(S) URLs without credentials")
        return value


class NoteParagraph(FrozenServiceModel):
    """One paragraph, heading, or list item; an empty run tuple is a blank line."""

    runs: tuple[NoteTextRun, ...] = Field(repr=False)
    style: Literal[
        "body", "heading", "subheading", "bullet", "dash", "numbered", "checklist"
    ] = "body"
    indent: int = Field(default=0, ge=0, le=MAX_LIST_INDENT, strict=True)
    checked: bool | None = None

    @model_validator(mode="after")
    def _validate_list_options(self) -> NoteParagraph:
        if self.indent and self.style not in LIST_STYLES:
            raise ValueError("Only list items can be indented")
        if self.checked is not None and self.style != "checklist":
            raise ValueError("Only checklist items have a checked state")
        if self.style == "checklist" and not self.runs:
            raise ValueError("A checklist item must have text")
        return self


class RichNoteBody(FrozenServiceModel):
    """Ordered paragraphs for creating or replacing a rich-text Notes body."""

    paragraphs: tuple[NoteParagraph, ...] = Field(repr=False)
