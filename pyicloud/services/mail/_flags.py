"""Narrow wire models for the observed single-message flag mutation."""

from typing import Annotated, Literal

from pydantic import Field

from ._write_models import UpdateResponse, WriteModel

MailFlag = Literal["seen", "flagged"]
FlagIntent = Literal["setMessageFlag", "clearMessageFlag"]
FlagCounter = Annotated[int, Field(strict=True, gt=0)]


class FlagDelta(WriteModel):
    """Exactly one named bit is changed by internally constructed requests."""

    set: tuple[MailFlag] | None = None
    clear: tuple[MailFlag] | None = None


class FlagPatch(WriteModel):
    """A flags-only patch, never a move or replacement numeric bitmask."""

    flags: FlagDelta


class FlagOperation(WriteModel):
    """Only one exact message identifier is submitted per request."""

    ids: tuple[str]
    patch: FlagPatch


class FlagRequest(WriteModel):
    """The browser-observed email/set request, without session counter guesses."""

    domain: Literal["email"] = "email"
    batch_update: tuple[FlagOperation] = Field(alias="batchUpdate")


class FlagAcknowledgement(WriteModel):
    """A server acknowledgement counter, not a reusable synchronization token."""

    modseq: FlagCounter


class FlagResponse(UpdateResponse[FlagAcknowledgement]):
    """Require all observed outcome fields; absent fields are not success."""
