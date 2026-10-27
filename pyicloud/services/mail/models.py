"""Typed Mail envelopes, bounded read results, and write acknowledgements."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer

from ._draft_models import DraftAttachmentReference

Identifier = Annotated[str, Field(strict=True, min_length=1)]
MIME_PART_PATTERN = r"^[0-9]+(?:\.[0-9]+)*$"
PartID = Annotated[str, Field(pattern=MIME_PART_PATTERN)]
MailThreadFilter = Literal["unread", "flagged"]


class MailModel(BaseModel):
    """Ignore additive server fields while validating the fields we consume."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)


class MailFolder(MailModel):
    """A mailbox identifier and its server name, not a localized folder guess."""

    id: Identifier = Field(alias="identifier")
    name: Identifier
    uid_validity: int | None = Field(default=None, alias="uidValidity")
    message_count: int | None = Field(default=None, alias="messageCount")


class MailSender(MailModel):
    """An address explicitly enabled by the authenticated Mail account."""

    address: Identifier = Field(repr=False)
    display_name: str = Field(default="", repr=False)


class MailPart(MailModel):
    """MIME metadata; size can describe the encoded representation."""

    id: Identifier = Field(alias="partId", repr=False)
    content_type: str = Field(default="application/octet-stream", alias="contentType")
    encoding: str | None = None
    size: int | None = Field(default=None, ge=0)
    filename: str | None = Field(default=None, alias="fileName", repr=False)
    is_attachment: bool = Field(default=False, alias="attach")
    mail_drop_url: str | None = Field(
        default=None, alias="mailDropStreamingUrl", exclude=True, repr=False
    )
    mail_drop_expiration_ms: float | None = Field(
        default=None, alias="expirationDate", ge=0, allow_inf_nan=False
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_mail_drop(self) -> bool:
        """Identify Mail Drop metadata without exposing its bearer URL."""
        return bool(self.mail_drop_url)

    @field_serializer("id")
    def safe_part_id(self, value: str) -> str:
        """Cloud-part identifiers embed signed URLs; keep them out of JSON output."""
        return (
            "cloudpart:<redacted>"
            if self.is_mail_drop or value.startswith("cloudpart:")
            else value
        )


class MailboxReference(MailModel):
    """The mailbox owning a message's local UID."""

    id: Identifier


class MailMessageRecord(MailModel):
    """Detailed message metadata returned by message/list."""

    id: Identifier = Field(alias="identifier")
    uid: int = Field(gt=0)
    mailbox: MailboxReference = Field(alias="mboxRef")
    thread_id: str | None = Field(default=None, alias="appleThrid")
    preview_id: Identifier | None = Field(default=None, alias="previewId", repr=False)
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


class MailDraft(MailModel):
    """A verified saved draft; every successful edit returns a replacement handle."""

    message: MailMessage
    sender: MailSender
    to: tuple[str, ...] = Field(default=(), repr=False)
    cc: tuple[str, ...] = Field(default=(), repr=False)
    bcc: tuple[str, ...] = Field(default=(), repr=False)
    attachments: tuple[MailDraftAttachment, ...] = Field(default=(), repr=False)


class MailDraftAttachment(MailModel):
    """One ordinary cached upload bound to its account and discovered gateway."""

    account_id: Identifier = Field(repr=False)
    service_root: Identifier = Field(repr=False)
    reference: DraftAttachmentReference = Field(repr=False)


class MailFlagUpdate(MailModel):
    """One acknowledged flag patch; the input message's metadata is not mutated."""

    message_id: Identifier
    flag: Literal["seen", "flagged"]
    enabled: bool = Field(strict=True)
    modseq: int = Field(strict=True, gt=0)


class MailMoveUpdate(MailModel):
    """Source acknowledgement only; fresh destination metadata is required."""

    source_message_id: Identifier
    source_folder_id: Identifier
    destination_folder_id: Identifier


class MailFolderDeletion(MailModel):
    """An acknowledged permanent folder deletion, not a move to Trash."""

    folder_id: Identifier
    permanent: Literal[True] = True


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


class MailFolderSearchPage(MailSearchPage):
    """Thread-group matches with their explicit folder context."""

    folder: MailFolder


class MailMultiSearchPage(MailModel):
    """Separate bounded pages, never a globally ranked or complete search."""

    pages: list[MailFolderSearchPage]
    limit_per_folder: int
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


class MailMessageExport(MailModel):
    """Versioned JSON data, not a raw MIME message or a complete-mailbox backup."""

    format_version: Literal[1] = 1
    message: MailMessage
    body: MailBody | None = None


class MailPreview(MailModel):
    """Untrusted snippet data for a verified folder-local message identity."""

    message_id: Identifier
    folder: MailFolder
    uid: int = Field(gt=0)
    text: str = Field(repr=False)


class MailThreadDigest(MailModel):
    """A browser-filtered thread group, not an individual message."""

    id: Identifier = Field(alias="threadId")
    record_type: Literal["threadDigest"] = Field(alias="jsonType")
    timestamp: float = Field(allow_inf_nan=False)
    subject: str | None = Field(default=None, repr=False)
    preview: str | None = Field(default=None, repr=False)
    count: int | None = Field(default=None, ge=0)
    folder_message_count: int | None = Field(
        default=None, alias="folderMessageCount", ge=0
    )


class MailThreadFilterPage(MailModel):
    """One bounded folder's filtered thread groups; completeness is unknown."""

    folder: MailFolder
    filter_by: MailThreadFilter
    results: list[MailThreadDigest]
    limit: int
    complete: Literal[False] = False


class MailFilteredThread(MailModel):
    """Lightweight filtered group identity, not a digest or a message."""

    id: Identifier
    folder: MailFolder
    filter_by: MailThreadFilter
    timestamp: float = Field(allow_inf_nan=False, ge=0)


class ThreadQueryRecord(MailModel):
    """Thread-domain metadata with independently checked folder membership."""

    id: Identifier = Field(alias="identifier", pattern=r"\S")
    timestamps: dict[
        str, Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]
    ] = Field(alias="tsMap", repr=False)


class ThreadQueryResponse(MailModel):
    """Opaque continuation belongs only to the exact originating query."""

    records: list[ThreadQueryRecord] = Field(alias="domainObjects")
    continuation_marker: Annotated[str, Field(strict=True, pattern=r"\S")] | None = (
        Field(default=None, alias="continuationMarker", repr=False)
    )


class MailReadContext(MailModel):
    """Response scope only; modification sequences are not persisted or replayed."""

    folder: Identifier


class MailPreviewRecord(MailModel):
    """A required snippet field; an empty string is a valid preview."""

    message_guid: Identifier = Field(alias="messageGuid")
    preview: str = Field(repr=False)


class PreviewResponse(MailModel):
    """Required preview envelope and folder context."""

    records: list[MailPreviewRecord] = Field(alias="result")
    context: MailReadContext = Field(alias="sessionHeaders")


class ThreadFilterResponse(MailModel):
    """Digest-only response; counts are returned groups, not mailbox totals."""

    records: list[MailThreadDigest] = Field(alias="threadList")
    returned_count: int = Field(alias="totalThreadsReturned", ge=0)
    context: MailReadContext = Field(alias="sessionHeaders")


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
    type: Literal["property", "fieldOf", "mapValueOf"] | None = None
    value: str | None = None


class MailPredicate(MailModel):
    """Observed comparison and conjunction shape."""

    type: Literal["eq", "textMatch", "lt", "ge"]
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


class MailThreadQuery(MailModel):
    """Thread-domain cursor query; never a full-text search or sync request."""

    domain: Literal["thread"] = "thread"
    properties: list[str] = Field(default_factory=lambda: ["identifier", "tsMap"])
    predicate: MailPredicate
    orderby: MailOrder
    limit: int = Field(ge=1, le=1000)
    strict_limit: Literal[False] = Field(
        default=False, serialization_alias="strictLimit"
    )
    continuation_marker: str | None = Field(
        default=None, serialization_alias="continuationMarker", repr=False
    )


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


class MailPreviewRequest(MailModel):
    """One advertised preview ID; do not construct selectors from UID guesses."""

    folder: Identifier
    preview_ids: list[Identifier] = Field(
        alias="previewIds", min_length=1, max_length=1
    )
    session_headers: MailSessionHeaders = Field(serialization_alias="sessionHeaders")


class MailThreadFilterRequest(MailModel):
    """Use the observed browser thread-filter protocol without combining filters."""

    response_type: Literal["THREAD_DIGEST"] = Field(
        default="THREAD_DIGEST", alias="responseType"
    )
    include_folder_status: Literal[True] = Field(
        default=True, alias="includeFolderStatus"
    )
    max_results: int = Field(alias="maxResults", ge=1, le=1000)
    filters: dict[Literal["unseen", "flagged"], Literal[True]]
    session_headers: MailSessionHeaders = Field(serialization_alias="sessionHeaders")
