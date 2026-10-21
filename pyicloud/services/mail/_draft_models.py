"""Observed draft save, attachment upload and recoverable discard wire shapes."""
# cspell:words cachedpart

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from ._flags import FlagDelta
from ._moves import MailboxReplacement
from ._write_models import WriteModel

UploadIdentifier = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"),
]
CachedPartIdentifier = Annotated[
    str,
    Field(pattern=r"^cachedpart:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"),
]


def _ordinary_attachment(value: object) -> Literal[False]:
    """Literal validation otherwise equates integer zero with false."""
    if value is not False:
        raise ValueError("Ordinary attachments require a false thumbnail flag")
    return False


class DraftUploadedPart(WriteModel):
    """A cached ordinary upload, not a Mail Drop or remote-fetch instruction."""

    guid: CachedPartIdentifier = Field(repr=False)
    datatype: Annotated[str, Field(min_length=1)]
    encoding: Literal["base64"]
    name: Annotated[str, Field(min_length=1)] = Field(repr=False)
    url: Annotated[str, Field(min_length=1)] = Field(repr=False)
    size: Annotated[int, Field(ge=0)]


class DraftUploadResponse(WriteModel):
    """The modern upload envelope differs from the legacy msgPart response."""

    uid: UploadIdentifier = Field(repr=False)
    message_part: DraftUploadedPart = Field(alias="messagePart", repr=False)


class DraftAttachmentReference(WriteModel):
    """A cached upload reference serialized inside an ordinary draft save."""

    guid: CachedPartIdentifier = Field(repr=False)
    name: Annotated[str, Field(min_length=1)] = Field(repr=False)
    url: Annotated[str, Field(min_length=1)] = Field(repr=False)
    content_id: CachedPartIdentifier = Field(alias="contentID", repr=False)
    size: Annotated[int, Field(ge=0)]
    mail_drop_expiration_date: None = Field(alias="mailDropExpirationDate")
    is_mail_drop_thumbnail: Annotated[
        Literal[False], BeforeValidator(_ordinary_attachment)
    ] = Field(alias="isMailDropThumbnail")
    datatype: Annotated[str, Field(min_length=1)]


class DraftAttachmentRemapping(WriteModel):
    """A save can replace attachment identities alongside its rotating UID."""

    request_guid: Annotated[str, Field(min_length=1)] = Field(
        alias="requestGuid", repr=False
    )
    new_guid: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, alias="newGuid", repr=False
    )
    content_id: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, alias="contentId", repr=False
    )


class DraftSessionHeaders(WriteModel):
    """Draft saves have null context, not stale mailbox counters."""

    folder: None = None
    modseq: None = None
    threadmodseq: None = None
    condstore: Literal[1] = 1
    qresync: Literal[1] = 1
    threadmode: Literal[1] = 1


class DraftCreateRequest(WriteModel):
    """Save explicit address lists and previously validated cached references."""

    date: str
    sender: str = Field(alias="from", repr=False)
    to: list[str] = Field(default_factory=list, repr=False)
    cc: list[str] = Field(default_factory=list, repr=False)
    bcc: list[str] = Field(default_factory=list, repr=False)
    subject: str = Field(repr=False)
    text: str = Field(alias="textBody", repr=False)
    html: str = Field(alias="htmlBody", repr=False)
    attachments: list[DraftAttachmentReference] = Field(
        default_factory=list, repr=False
    )
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
    attachments: list[DraftAttachmentRemapping] | None = Field(default=None, repr=False)


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
