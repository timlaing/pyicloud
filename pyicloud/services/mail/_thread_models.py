"""Bounded, explicit threading expectations retained by saved draft handles."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from ._write_models import WriteModel

MESSAGE_IDENTIFIER_PATTERN = r"<[^<>@\s\x00-\x1f\x7f]+@[^<>@\s\x00-\x1f\x7f]+>"
MessageIdentifier = Annotated[
    str,
    Field(pattern="^" + MESSAGE_IDENTIFIER_PATTERN + "$", max_length=998),
]


class MailDraftThreading(WriteModel):
    """Expected reply/forward headers, not permission to infer recipients."""

    mode: Literal["reply", "forward"]
    references: Annotated[
        tuple[MessageIdentifier, ...], Field(min_length=1, max_length=100)
    ] = Field(repr=False)
    in_reply_to: tuple[MessageIdentifier, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def consistent_headers(self) -> Self:
        """Replies name one parent; forwards must not become replies implicitly."""
        expected = (self.references[-1],) if self.mode == "reply" else ()
        if self.in_reply_to != expected or len(set(self.references)) != len(
            self.references
        ):
            raise ValueError("Invalid reply/forward threading expectations")
        return self
