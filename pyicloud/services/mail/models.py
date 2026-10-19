"""Typed Mail envelopes and bounded read results."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(strict=True, min_length=1)]
MIME_PART_PATTERN = r"^[0-9]+(?:\.[0-9]+)*$"
PartID = Annotated[str, Field(pattern=MIME_PART_PATTERN)]


class MailModel(BaseModel):
    """Ignore additive server fields while validating the fields we consume."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)


class MailFolder(MailModel):
    """A mailbox identifier and its server name, not a localized folder guess."""

    id: Identifier = Field(alias="identifier")
    name: Identifier
    uid_validity: int | None = Field(default=None, alias="uidValidity")
    message_count: int | None = Field(default=None, alias="messageCount")


class MailPart(MailModel):
    """MIME metadata; size can describe the encoded representation."""

    id: Identifier = Field(alias="partId", repr=False)
    content_type: str = Field(default="application/octet-stream", alias="contentType")
    encoding: str | None = None
    size: int | None = Field(default=None, ge=0)
    filename: str | None = Field(default=None, alias="fileName", repr=False)
    is_attachment: bool = Field(default=False, alias="attach")


class MailboxReference(MailModel):
    """The mailbox owning a message's local UID."""

    id: Identifier


class MailMessageRecord(MailModel):
    """Detailed message metadata returned by message/list."""

    id: Identifier = Field(alias="identifier")
    uid: int = Field(gt=0)
    mailbox: MailboxReference = Field(alias="mboxRef")
    thread_id: str | None = Field(default=None, alias="appleThrid")
    subject: str | None = Field(default=None, repr=False)
    sender: str | None = Field(default=None, alias="from", repr=False)
    to: str | None = Field(default=None, repr=False)
    cc: str | None = Field(default=None, repr=False)
    flags: int | None = None
    internal_date: float | None = Field(
        default=None, alias="stateInternalDate", allow_inf_nan=False
    )
    size: int | None = Field(default=None, alias="rfc822Size", ge=0)
    has_attachment: bool | None = Field(default=None, alias="hasAttachment")
    parts: list[MailPart] = Field(default_factory=list)


class MailMessage(MailMessageRecord):
    """A message bound to the folder context used when it was fetched."""

    folder: MailFolder


class MailMessagePage(MailModel):
    """A bounded snapshot, not a complete mailbox or a synchronization cursor."""

    messages: list[MailMessage]
    limit: int
    complete: Literal[False] = False


class MailThreadPage(MailMessagePage):
    """Bounded thread metadata in one folder, not a complete conversation."""

    thread_id: Identifier


class MailSearchHit(MailModel):
    """A thread-grouped match, not necessarily a single message."""

    id: Identifier = Field(alias="identifier")
    subject: str | None = Field(default=None, repr=False)
    preview: str | None = Field(default=None, repr=False)
    count: int | None = Field(default=None, ge=0)


class MailSearchPage(MailModel):
    """Bounded thread-grouped search results with no completeness guarantee."""

    results: list[MailSearchHit]
    limit: int
    complete: Literal[False] = False


class MailBodyPart(MailModel):
    """Returned content is data; HTML is not sanitized for rendering."""

    guid: Identifier
    content: str = Field(repr=False)


class MailBody(MailModel):
    """Selected MIME bodies and untrusted original headers."""

    guid: Identifier
    headers: str = Field(alias="longHeader", repr=False)
    content_type: Identifier = Field(alias="contentType")
    parts: list[MailBodyPart]


class FolderResponse(MailModel):
    """Required folder query envelope."""

    records: list[MailFolder] = Field(alias="domainObjects")
    continuation_marker: str | None = Field(default=None, alias="continuationMarker")


class MessageResponse(MailModel):
    """Required message query envelope."""

    records: list[MailMessageRecord] = Field(alias="domainObjects")
    continuation_marker: str | None = Field(default=None, alias="continuationMarker")


class SearchResponse(MailModel):
    """Required grouped search envelope."""

    records: list[MailSearchHit] = Field(alias="domainObjects")


class MailExpression(MailModel):
    """A property or header/body projection in a query predicate."""

    property: str
    type: Literal["property", "fieldOf"] | None = None
    value: str | None = None


class MailPredicate(MailModel):
    """Observed comparison and conjunction shape."""

    type: Literal["eq", "textMatch", "lt"]
    expression: MailExpression
    value: str | bool | int
    and_: list[MailPredicate] | None = Field(default=None, serialization_alias="and")
    or_: list[MailPredicate] | None = Field(default=None, serialization_alias="or")


class MailOrder(MailModel):
    """Newest-first ordering, without claiming stable cross-page boundaries."""

    expressions: list[MailExpression] = Field(
        default_factory=lambda: [
            MailExpression(property="stateInternalDate", type="property")
        ]
    )
    ascending: bool = False


class MailQuery(MailModel):
    """Read query; no arbitrary endpoint or mutation payload is exposed."""

    domain: Literal["mailbox", "email"]
    properties: list[str | dict[str, Any]]
    predicate: MailPredicate
    limit: int | None = None
    include_labels: bool | None = Field(
        default=None, serialization_alias="includeLabels"
    )
    collapse_on: str | None = Field(default=None, serialization_alias="collapseOn")
    orderby: MailOrder | None = None


class MailSessionHeaders(MailModel):
    """Fresh per-call folder context; no incremental-sync state is retained."""

    folder: str
    modseq: None = None
    threadmodseq: None = None
    condstore: int = 1
    qresync: int = 1
    threadmode: int = 1


class MailBodyRequest(MailModel):
    """A body request that cannot request a read-state mutation."""

    uid: str
    parts: list[PartID]
    dont_mark_as_read: Literal[True] = Field(
        default=True, serialization_alias="dontMarkAsRead"
    )
    session_headers: MailSessionHeaders = Field(serialization_alias="sessionHeaders")
