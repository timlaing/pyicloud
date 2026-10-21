"""Observed recipient-free save and recoverable draft-discard wire shapes."""

from typing import Annotated, Literal

from pydantic import Field

from ._flags import FlagDelta
from ._moves import MailboxReplacement
from ._write_models import WriteModel


class DraftSessionHeaders(WriteModel):
    """Draft saves have null context, not stale mailbox counters."""

    folder: None = None
    modseq: None = None
    threadmodseq: None = None
    condstore: Literal[1] = 1
    qresync: Literal[1] = 1
    threadmode: Literal[1] = 1


class DraftCreateRequest(WriteModel):
    """Empty address/attachment arrays cannot accidentally send or upload data."""

    date: str
    sender: str = Field(alias="from", repr=False)
    to: list[None] = Field(default_factory=list, max_length=0)
    cc: list[None] = Field(default_factory=list, max_length=0)
    bcc: list[None] = Field(default_factory=list, max_length=0)
    subject: str = Field(repr=False)
    text: str = Field(alias="textBody", repr=False)
    html: str = Field(alias="htmlBody", repr=False)
    attachments: list[None] = Field(default_factory=list, max_length=0)
    client_build: str = Field(alias="webmailClientBuild")
    is_hme: Literal[False] = Field(default=False, alias="isHME")
    session_headers: DraftSessionHeaders = Field(
        default_factory=DraftSessionHeaders, alias="sessionHeaders"
    )


class DraftEditRequest(DraftCreateRequest):
    """Only updates include the previous Drafts/UID reference."""

    draft_guid: str = Field(alias="draftGuid")


class DraftSaveResponse(WriteModel):
    """A successful save supplies a decimal UID, not a global message ID."""

    uid: Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=20)]


class DraftDiscardPatch(WriteModel):
    """The browser's discard operation also requests the seen bit."""

    flags: FlagDelta
    mailbox: MailboxReplacement = Field(alias="mboxRef")


class DraftDiscardOperation(WriteModel):
    """One exact draft message, never all drafts or a folder delete."""

    ids: tuple[str]
    patch: DraftDiscardPatch


class DraftDiscardRequest(WriteModel):
    """Discard means a recoverable Trash move, not permanent destruction."""

    domain: Literal["email"] = "email"
    batch_update: tuple[DraftDiscardOperation] = Field(alias="batchUpdate")
