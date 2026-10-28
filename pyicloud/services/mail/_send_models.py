"""Observed final-send envelopes; scheduling is a separate operation."""

from typing import Annotated, Literal

from pydantic import Field

from ._draft_models import DraftSessionHeaders
from ._write_models import WriteModel

ZeroCounter = Annotated[int, Field(ge=0, le=0)]


class DraftSendRequest(WriteModel):
    """Send exactly one saved Drafts UID with null mailbox context."""

    message_guid: Annotated[str, Field(pattern=r"^Drafts/[1-9][0-9]*$")] = Field(
        alias="messageGuid"
    )
    session_headers: DraftSessionHeaders = Field(
        default_factory=DraftSessionHeaders, alias="sessionHeaders"
    )


class DraftSendContext(WriteModel):
    """The captured send acknowledgement resets mailbox capabilities."""

    folder: Literal["Drafts"]
    modseq: Annotated[int, Field(gt=0)]
    threadmodseq: ZeroCounter
    condstore: ZeroCounter
    qresync: ZeroCounter
    threadmode: ZeroCounter


class DraftSendResponse(WriteModel):
    """EXPUNGE identifies a sequence number, never a UID or a delivered message."""

    session_headers: DraftSendContext = Field(alias="sessionHeaders")
    events: list[Annotated[str, Field(pattern=r"^\* [1-9][0-9]* EXPUNGE$")]] = Field(
        min_length=1, max_length=1
    )
