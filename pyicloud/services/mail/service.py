"""Experimental Mail reads and explicit message and folder writes."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
import os
import re
import time
from typing import Any, Literal, TypeVar
from urllib.parse import parse_qs, quote, urlsplit

from pydantic import BaseModel, ValidationError
from requests import Request, RequestException, Response, Session

from pyicloud.exceptions import PyiCloudException
from pyicloud.services.base import BaseService
from pyicloud.services.mail._conversation_drafts import prepare_source
from pyicloud.services.mail._draft_models import (
    DraftCreateRequest,
    DraftDiscardOperation,
    DraftDiscardPatch,
    DraftDiscardRequest,
    DraftSaveResponse,
)
from pyicloud.services.mail._draft_uploads import (
    attachment_references,
    upload_attachment,
)
from pyicloud.services.mail._drafts import (
    DRAFT_UNCERTAIN,
    DraftWriter,
    recipient_addresses,
)
from pyicloud.services.mail._flags import (
    FlagDelta,
    FlagIntent,
    FlagOperation,
    FlagPatch,
    FlagRequest,
    FlagResponse,
    MailFlag,
)
from pyicloud.services.mail._folder_models import (
    FolderIntent,
    FolderRequest,
)
from pyicloud.services.mail._folder_models import (
    FolderResponse as FolderMutationResponse,
)
from pyicloud.services.mail._folder_writes import FOLDER_UNCERTAIN, FolderWriter
from pyicloud.services.mail._mailboxes import MailboxSnapshot, MailboxSnapshotResponse
from pyicloud.services.mail._message_deletion import (
    DELETE_UNCERTAIN,
    MessageDeleteRequest,
    MessageDeleteResponse,
    deletion_acknowledged,
)
from pyicloud.services.mail._moves import (
    MailboxReplacement,
    MoveOperation,
    MovePatch,
    MoveRequest,
    MoveResponse,
)
from pyicloud.services.mail._send_models import DraftSendRequest, DraftSendResponse
from pyicloud.services.mail._senders import SenderPreferences, sender_identities
from pyicloud.services.mail._write_models import known_rejection
from pyicloud.services.mail.exceptions import (
    MailError,
    MailWriteRejected,
    MailWriteUncertain,
)
from pyicloud.services.mail.models import (
    MIME_PART_PATTERN,
    FolderResponse,
    MailBody,
    MailBodyRequest,
    MailDraft,
    MailDraftAttachment,
    MailExpression,
    MailFilteredThread,
    MailFlagUpdate,
    MailFolder,
    MailFolderDeletion,
    MailFolderSearchPage,
    MailMessage,
    MailMessageDeletion,
    MailMessageExport,
    MailMessagePage,
    MailMessageRecord,
    MailMoveUpdate,
    MailMultiSearchPage,
    MailOrder,
    MailPart,
    MailPredicate,
    MailPreview,
    MailPreviewRequest,
    MailQuery,
    MailSearchPage,
    MailSender,
    MailSendUpdate,
    MailSessionHeaders,
    MailThreadFilter,
    MailThreadFilterPage,
    MailThreadFilterRequest,
    MailThreadPage,
    MailThreadQuery,
    MessageResponse,
    PreviewResponse,
    SearchResponse,
    ThreadFilterResponse,
    ThreadQueryResponse,
)
from pyicloud.session import PyiCloudSession

DEFAULT_TIMEOUT = (10.0, 60.0)
MAX_RESULTS = 1000
MAX_SEARCH_FOLDERS = 20
_RESULT_LIMIT_EXCEEDED = "Mail exceeded the requested result limit"
_MESSAGE_LIST = "message/list"
_GEQS_QUERY = "geqs/query"
_BINARY_CONTENT_TYPE = "application/octet-stream"
_HTML_CONTENT_TYPE = "text/html"
_MESSAGE_SCOPE_MISMATCH = "Message and folder identities do not match"
_UID_VALIDITY_REQUIRED = "A positive mailbox UID validity is required; refresh folders"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
SearchField = Literal["all", "subject", "from", "to", "cc", "body"]
MessageFilter = Literal["unread", "flagged"]
_HEADER_PROPERTIES: list[str | dict[str, Any]] = [
    "uid",
    "identifier",
    "mboxRef",
    "appleThrid",
    "flags",
    "stateInternalDate",
    "rfc822Size",
    "hasAttachment",
    "parts",
    "previewId",
    *[
        {
            "type": "fieldOf",
            "property": "rfc822HeaderBytes",
            "value": field,
            "alias": field.lower(),
            "decoder": "encodedWords",
        }
        for field in ("From", "To", "Cc", "Subject")
    ],
]


_FLAG_OUTCOME_UNKNOWN = (
    "Mail flag update outcome is uncertain; refresh message metadata before "
    "retrying. No automatic retry was performed."
)
_MOVE_OUTCOME_UNKNOWN = (
    "Mail move outcome is uncertain; inspect both folders using fresh metadata "
    "before retrying. Message IDs may change. No automatic retry was performed."
)


def _move_acknowledgement(
    response: MoveResponse, account_id: str, message_id: str
) -> None:
    """Contradictory real-world move outcomes must never authorize a retry."""
    if response.account_id != account_id or any((
        response.created,
        response.destroyed,
        response.not_created,
        response.not_destroyed,
    )):
        raise MailWriteUncertain(_MOVE_OUTCOME_UNKNOWN)
    if response.not_updated:
        if not response.updated and known_rejection(response.not_updated, message_id):
            raise MailWriteRejected("Mail rejected the move; refresh metadata.")
        raise MailWriteUncertain(_MOVE_OUTCOME_UNKNOWN)
    if response.updated is None or set(response.updated) != {message_id}:
        raise MailWriteUncertain(_MOVE_OUTCOME_UNKNOWN)


def _flag_acknowledgement(
    response: FlagResponse, account_id: str, message_id: str
) -> int:
    """Require an exact account and message outcome without exposing server data."""
    if response.account_id != account_id or any((
        response.created,
        response.destroyed,
        response.not_created,
        response.not_destroyed,
    )):
        raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
    if response.not_updated:
        if not response.updated and known_rejection(response.not_updated, message_id):
            raise MailWriteRejected("Mail rejected the flag update; refresh metadata.")
        raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
    if response.updated is None or set(response.updated) != {message_id}:
        raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
    return response.updated[message_id].modseq


def _bind_message(record: MailMessageRecord, folder: MailFolder) -> MailMessage:
    """Preserve private part capabilities internally, not through serialization."""
    return MailMessage(
        **record.model_dump(exclude={"parts"}), parts=record.parts, folder=folder
    )


def _validate_mail_drop(part: MailPart, max_bytes: int) -> str:
    """Accept only observed, unexpired Apple large-attachment capabilities."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    url = part.mail_drop_url
    if not part.is_attachment or not url or not part.id.startswith("cloudpart:"):
        raise ValueError("Selected part is not a supported Mail Drop attachment")
    expiry = _mail_drop_url_expiry(url)
    if (
        expiry <= time.time()
        or part.mail_drop_expiration_ms is None
        or part.mail_drop_expiration_ms <= time.time() * 1000
    ):
        raise MailError(
            "Mail Drop link expired or has no known expiration; refresh metadata"
        )
    if part.size is not None and part.size > max_bytes:
        raise MailError("Mail Drop attachment exceeds max_bytes")
    return url


def _mail_drop_url_expiry(url: str) -> int:
    """Validate the capability's URL boundary and return its expiration."""
    if url != url.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise MailError("Invalid Mail Drop download URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as err:
        raise MailError("Invalid Mail Drop download URL") from err
    if (
        parsed.scheme != "https"
        or parsed.hostname != "cvws.icloud-content.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or not parsed.path.startswith("/B/")
    ):
        raise MailError("Unsupported Mail Drop download URL")
    query = parse_qs(parsed.query)
    if (
        query.get("ckc") != ["com.apple.largeattachment"]
        or query.get("ckz") != ["Apple-Webmail"]
        or any(len(query.get(key, [])) != 1 for key in ("e", "o", "a", "k", "s"))
    ):
        raise MailError("Invalid Mail Drop capability parameters")
    expiry = query["e"][0]
    if not expiry.isascii() or not expiry.isdigit():
        raise MailError("Invalid Mail Drop URL expiration")
    return int(expiry)


def _mail_drop_response_size(
    response: Response, part: MailPart, max_bytes: int
) -> int | None:
    """Validate response status, MIME type, encoding, and declared byte limits."""
    if response.status_code != 200:
        raise MailError(f"Mail Drop download returned HTTP {response.status_code}")
    actual = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    expected = part.content_type.split(";", 1)[0].strip().lower()
    if (
        not actual
        or (
            expected == _BINARY_CONTENT_TYPE
            and actual in (_HTML_CONTENT_TYPE, "application/json")
        )
        or (
            expected != _BINARY_CONTENT_TYPE
            and actual not in (expected, _BINARY_CONTENT_TYPE)
        )
    ):
        raise MailError("Mail Drop download returned an unexpected content type")
    if (
        response.headers.get("Content-Encoding", "identity").strip().lower()
        != "identity"
    ):
        raise MailError("Mail Drop download returned unsupported content encoding")
    length = response.headers.get("Content-Length")
    if length is not None and (not length.isascii() or not length.isdigit()):
        raise MailError("Invalid Mail Drop content length")
    declared = int(length) if length is not None else None
    expected_size = part.size if part.size else declared
    if declared is not None and (
        declared > max_bytes or (part.size and declared != part.size)
    ):
        raise MailError(
            "Mail Drop content length exceeds budget or differs from metadata"
        )
    return expected_size


def _mail_drop_transport_error(exc: RequestException) -> MailError:
    """Carry the failure class only; request messages embed the signed URL."""
    return MailError(f"Mail Drop download or stream failed ({type(exc).__name__})")


def _mail_drop_chunks(
    chunks: Iterator[bytes], max_bytes: int, expected_size: int | None
) -> Iterator[bytes]:
    """Enforce actual streamed size even without trustworthy advertised lengths."""
    size = 0
    try:
        for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise MailError("Mail Drop stream exceeds max_bytes")
            if chunk:
                yield chunk
    except RequestException as exc:
        # Only iter_content failures originate here; caller exceptions never
        # enter this generator, so they are not reclassified.
        raise _mail_drop_transport_error(exc) from exc
    if expected_size is not None and size != expected_size:
        raise MailError("Mail Drop stream length differs from metadata or response")


def validate_mail_endpoint(service_root: str) -> None:
    """Require the same usable HTTPS root for service calls and diagnostics."""
    parsed = urlsplit(service_root)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Mail requires a discovered HTTPS endpoint")


def _limit(value: int) -> None:
    """Reject limits outside the supported bounded-query range."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_RESULTS
    ):
        raise ValueError(f"limit must be an integer between 1 and {MAX_RESULTS}")


def _folder_predicate(folder: MailFolder) -> MailPredicate:
    """Restrict a query to non-deleted messages in the requested folder."""
    return MailPredicate(
        type="eq",
        expression=MailExpression(property="mboxRef"),
        value=folder.id,
        and_=[
            MailPredicate(
                type="eq",
                value=False,
                expression=MailExpression(
                    type="fieldOf", property="flags", value="DELETED"
                ),
            )
        ],
    )


def _validate_scan(folder: MailFolder, page_size: int, max_pages: int) -> None:
    """Validate a scan before checking mailbox identity or reading metadata."""
    _limit(page_size)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if folder.uid_validity is None or folder.uid_validity < 1:
        raise ValueError(_UID_VALIDITY_REQUIRED)


def _filtered_thread_query(
    folder: MailFolder, filter_by: MailThreadFilter, page_size: int
) -> MailThreadQuery:
    """Use browser-observed thread timestamps and projected boolean flag predicates."""
    timestamp = MailExpression(property="tsMap", type="mapValueOf", value=folder.id)
    return MailThreadQuery(
        predicate=MailPredicate(
            expression=timestamp,
            type="ge",
            value=0,
            and_=[
                MailPredicate(
                    expression=MailExpression(
                        property="flags",
                        type="fieldOf",
                        value="SEEN" if filter_by == "unread" else "FLAGGED",
                    ),
                    type="eq",
                    value=filter_by == "flagged",
                )
            ],
        ),
        orderby=MailOrder(expressions=[timestamp], ascending=False),
        limit=page_size,
    )


def _check_thread_page(
    response: ThreadQueryResponse,
    folder: MailFolder,
    page_size: int,
    seen: set[str],
    markers: set[str],
) -> None:
    """Validate an entire cursor page before exposing any of its group identities."""
    records = response.records
    if len(records) > page_size:
        raise MailError(_RESULT_LIMIT_EXCEEDED)
    if any(folder.id not in record.timestamps for record in records):
        raise MailError("Mail returned a thread from another folder")
    ids = {record.id for record in records}
    if len(ids) != len(records) or ids.intersection(seen):
        raise MailError("Mail repeated a thread identifier; enumeration is incomplete")
    marker = response.continuation_marker
    if marker is not None and (not records or marker in markers):
        raise MailError("Mail returned a non-progressing thread cursor")


def _uid_query(
    folder: MailFolder,
    page_size: int,
    before: int | None,
    match: MailPredicate | None,
) -> MailQuery:
    """Retain the selected scope while adding a strict descending UID boundary."""
    predicate = _folder_predicate(folder)
    clauses = list(predicate.and_ or [])
    if match is not None:
        clauses.append(match)
    if before is not None:
        clauses.append(
            MailPredicate(
                type="lt",
                expression=MailExpression(type="property", property="uid"),
                value=before,
            )
        )
    return MailQuery(
        domain="email",
        properties=_HEADER_PROPERTIES,
        predicate=predicate.model_copy(update={"and_": clauses}),
        limit=page_size,
        orderby=MailOrder(
            expressions=[MailExpression(type="property", property="uid")]
        ),
    )


def _search_fields(text: str, field: SearchField, limit: int) -> tuple[str, ...]:
    """Validate a bounded text query before any request is sent."""
    _limit(limit)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Search text must not be empty")
    if field not in ("all", "subject", "from", "to", "cc", "body"):
        raise ValueError("Unsupported search field")
    return ("subject", "to", "from", "cc", "body") if field == "all" else (field,)


def _unique_uid_records(records: list[MailMessageRecord]) -> list[MailMessageRecord]:
    """Deduplicate exact identities while rejecting contradictory UID mappings."""
    by_uid: dict[int, MailMessageRecord] = {}
    identifiers: set[str] = set()
    for record in records:
        existing = by_uid.get(record.uid)
        if existing is not None:
            if existing.id != record.id:
                raise MailError("Mail returned conflicting identities for a UID")
            continue
        if record.id in identifiers:
            raise MailError("Mail returned one message identifier for multiple UIDs")
        by_uid[record.uid] = record
        identifiers.add(record.id)
    return list(by_uid.values())


def _uid_page(
    records: list[MailMessageRecord], folder: MailFolder, before: int | None, size: int
) -> list[MailMessageRecord]:
    """Validate a whole UID page before any of its records can be yielded."""
    if len(records) > size:
        raise MailError(_RESULT_LIMIT_EXCEEDED)
    if any(record.mailbox.id != folder.id for record in records):
        raise MailError("Mail returned a message from another folder")
    if before is not None and any(record.uid >= before for record in records):
        raise MailError("Mail did not honor the UID paging boundary")
    unique = _unique_uid_records(records)
    if any(
        left.uid <= right.uid for left, right in zip(unique, unique[1:], strict=False)
    ):
        raise MailError("Mail did not return descending UID order")
    return unique


__all__ = ["MailError", "MailService", "MailWriteRejected", "MailWriteUncertain"]


class MailService(BaseService):
    """Bounded Mail reads and explicit message and folder writes.

    Bounded methods remain snapshots; only explicit iterators opt into paging.
    Reads never mutate mail. Folder, draft, ordinary upload and send writes are
    explicit, as is confirmed single-message Trash destruction. Rendering and
    sync are unsupported.
    Read-side authentication failures retain the library's existing exception types.
    """

    def __init__(
        self,
        service_root: str,
        session: PyiCloudSession,
        params: dict[str, Any],
        *,
        settings_root: str | None = None,
    ) -> None:
        """Validate the discovered endpoint and copy session query parameters."""
        validate_mail_endpoint(service_root)
        super().__init__(service_root.rstrip("/"), session, dict(params))
        self._settings_root = settings_root

    def _post(
        self,
        path: str,
        request: BaseModel,
        response_type: type[ResponseModel],
        *,
        keep_nulls: bool = False,
    ) -> ResponseModel:
        """Send a bounded Mail request and validate its response envelope."""
        response = self.session.post(
            f"{self.service_root}/mailws2/v1/{path}",
            params=self.params,
            json=request.model_dump(by_alias=True, exclude_none=not keep_nulls),
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                raise MailError(f"Mail returned HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise MailError("Mail returned invalid JSON") from exc
            if isinstance(data, dict) and (data.get("error") or data.get("errors")):
                raise MailError("Mail returned an error response")
            try:
                return response_type.model_validate(data)
            except ValidationError as exc:
                raise MailError("Mail returned an invalid response envelope") from exc
        finally:
            response.close()

    def folders(self) -> list[MailFolder]:
        """List active folders and labels without reading message bodies."""
        result = self._post(
            _GEQS_QUERY,
            MailQuery(
                domain="mailbox",
                include_labels=True,
                properties=["identifier", "name", "uidValidity", "messageCount"],
                predicate=MailPredicate(
                    type="eq",
                    expression=MailExpression(
                        type="property", property="isMboxDeleted"
                    ),
                    value=False,
                ),
            ),
            FolderResponse,
        )
        if result.continuation_marker:
            raise MailError("Mail folder list requires unsupported continuation")
        return result.records

    def senders(self) -> list[MailSender]:
        """Read enabled primary, alias, and custom-domain sender addresses.

        Use the authenticated account's discovered settings endpoint. A login
        address is not necessarily a Mail address, so never infer one from it.
        Disabled and inactive identities are not returned or silently enabled.
        """
        account_id = self.params.get("dsid")
        if (
            not isinstance(account_id, str)
            or not account_id.isascii()
            or not account_id.isdecimal()
        ):
            raise ValueError("A discovered decimal account identifier is required")
        if self._settings_root is None:
            raise MailError("Mail sender settings endpoint is unavailable")
        root = self._settings_root.rstrip("/")
        try:
            validate_mail_endpoint(root)
        except ValueError as exc:
            raise MailError("Mail sender settings endpoint is unusable") from exc
        response = self.session.get(
            f"{root}/cc/mail/v1/account/{quote(account_id, safe='')}"
            "/preference/web/all",
            params={"userEntryPoint": "/mail/load"},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                raise MailError("Mail sender discovery failed")
            try:
                payload = response.json()
                if isinstance(payload, dict) and (
                    payload.get("error") or payload.get("errors")
                ):
                    raise MailError("Mail sender discovery returned an error")
                data = SenderPreferences.model_validate(
                    payload, by_alias=True, by_name=False
                )
            except ValueError as exc:
                raise MailError("Mail returned invalid sender preferences") from exc
            return sender_identities(data, account_id)
        finally:
            response.close()

    def upload_draft_attachment(
        self,
        data: bytes,
        *,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> MailDraftAttachment:
        """Upload at most 25 MiB of attachment bytes, without saving or sending.

        This creates a cached part, not a draft or a Mail Drop upload. An uncertain
        outcome must not be retried blindly; automatic cache cleanup is unavailable.
        """
        return upload_attachment(
            self.session, self.service_root, self.params, data, filename, content_type
        )

    def create_draft(
        self,
        *,
        sender: str,
        subject: str = "",
        text: str = "",
        html: str = "",
        to: tuple[str, ...] = (),
        cc: tuple[str, ...] = (),
        bcc: tuple[str, ...] = (),
        attachments: tuple[MailDraftAttachment, ...] = (),
    ) -> MailDraft:
        """Create a draft with explicit recipients and uploaded parts, without sending.

        Select an enabled address from senders(). Plain-only input is escaped
        into HTML because Apple treats HTML as canonical. Caller-supplied HTML
        is not sanitized or rendered. Never blindly retry an uncertain save.
        """
        return self._draft_writer().save(
            sender,
            subject,
            text,
            html,
            None,
            to=to,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
        )

    def create_reply_draft(
        self,
        message: MailMessage,
        *,
        sender: str,
        to: tuple[str, ...],
        text: str,
        html: str = "",
        cc: tuple[str, ...] = (),
        bcc: tuple[str, ...] = (),
        attachments: tuple[MailDraftAttachment, ...] = (),
    ) -> MailDraft:
        """Save a reply to an exact source, without inferring recipients or sending.

        Supply the full desired body, including any quotes. Source attachments
        are not copied into replies; explicit uploaded handles are supported.
        """
        DraftWriter.validate_inputs(sender, "", text, html)
        for values in (to, cc, bcc):
            recipient_addresses(values)
        attachment_references(
            attachments, self.service_root, self.params.get("dsid", "")
        )
        source = prepare_source(
            message,
            "reply",
            self.message_by_uid,
            self.body,
            self.service_root,
            self.params.get("dsid", ""),
        )
        subject = source.message.subject or ""
        if not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        return self._draft_writer().save(
            sender,
            subject,
            text,
            html,
            None,
            to=to,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            threading=source.threading,
        )

    def create_forward_draft(
        self,
        message: MailMessage,
        *,
        sender: str,
        to: tuple[str, ...],
        text: str,
        html: str = "",
        cc: tuple[str, ...] = (),
        bcc: tuple[str, ...] = (),
        attachments: tuple[MailDraftAttachment, ...] = (),
        include_original_attachments: bool = True,
    ) -> MailDraft:
        """Save a forward with references and original ordinary attachments.

        Supply the full desired body, including forwarded content. The source
        must remain available while its stored attachment references are used.
        No upload, implicit recipient selection, or sending occurs.
        """
        DraftWriter.validate_inputs(sender, "", text, html)
        for values in (to, cc, bcc):
            recipient_addresses(values)
        attachment_references(
            attachments, self.service_root, self.params.get("dsid", "")
        )
        source = prepare_source(
            message,
            "forward",
            self.message_by_uid,
            self.body,
            self.service_root,
            self.params.get("dsid", ""),
            include_attachments=include_original_attachments,
        )
        subject = source.message.subject or ""
        if not subject.casefold().startswith("fwd:"):
            subject = "Fwd: " + subject
        return self._draft_writer().save(
            sender,
            subject,
            text,
            html,
            None,
            to=to,
            cc=cc,
            bcc=bcc,
            attachments=source.attachments + attachments,
            threading=source.threading,
        )

    def edit_draft(
        self,
        draft: MailDraft,
        *,
        subject: str,
        text: str,
        html: str,
        to: tuple[str, ...] | None = None,
        cc: tuple[str, ...] | None = None,
        bcc: tuple[str, ...] | None = None,
        attachments: tuple[MailDraftAttachment, ...] | None = None,
    ) -> MailDraft:
        """Replace all subject/body fields and return a new rotating-UID handle.

        Omitted recipient/attachment arguments preserve the handle's expectations;
        explicit empty tuples clear them. Forward versions Apple retains are
        reported in retained_drafts, never silently deleted. Fresh preflight
        is not an atomic save.
        """
        if not isinstance(draft, MailDraft):
            raise ValueError("A saved MailDraft handle is required")
        return self._draft_writer().save(
            draft.sender.address,
            subject,
            text,
            html,
            draft,
            to=draft.to if to is None else to,
            cc=draft.cc if cc is None else cc,
            bcc=draft.bcc if bcc is None else bcc,
            attachments=draft.attachments if attachments is None else attachments,
        )

    def send_draft(self, draft: MailDraft, *, confirm: bool = False) -> MailSendUpdate:
        """Immediately submit one saved draft, without scheduling or retrying.

        Confirmation must be explicit. Fresh preflight cannot eliminate a race
        with another editor. Success acknowledges Apple's submission, not SMTP
        delivery; an uncertain outcome may already have sent the message.
        """
        if confirm is not True:
            raise ValueError("Sending requires confirm=True")
        if not isinstance(draft, MailDraft):
            raise ValueError("A saved MailDraft handle is required")
        recipients = [recipient_addresses(v) for v in (draft.to, draft.cc, draft.bcc)]
        if not any(recipients):
            raise ValueError("Sending requires at least one explicit recipient")
        current = self._draft_writer().checked(draft)
        if current.subject != draft.message.subject:
            raise MailError("Draft subject changed; refresh before sending")
        if not any(
            s.address.casefold() == draft.sender.address.casefold()
            for s in self.senders()
        ):
            raise MailError("Draft sender is no longer enabled")
        uncertain = (
            "Mail send outcome is uncertain; the message may already have been sent. "
            "Inspect Drafts and Sent before any explicit retry."
        )
        self._submit_update(
            DraftSendRequest(messageGuid=f"Drafts/{current.uid}"),
            DraftSendResponse,
            None,
            uncertain,
            path="draft/send",
            keep_nulls=True,
        )
        try:
            self._draft_folder(current.folder)
            if self.message_by_uid(current.folder, current.uid) is not None:
                raise MailError("Sent draft is still present")
        except (PyiCloudException, RequestException, OSError, ValueError) as exc:
            raise MailWriteUncertain(uncertain) from exc
        return MailSendUpdate(
            source_message_id=current.id,
            source_folder_id=current.folder.id,
            source_uid=current.uid,
            retained_draft_ids=tuple(m.id for m in draft.retained_drafts),
        )

    def discard_draft(self, draft: MailDraft, trash: MailFolder) -> MailMoveUpdate:
        """Move one verified draft to the explicit Trash mailbox.

        The captured browser operation also requests seen. This is not permanent
        destruction. Contradictory success/error maps remain uncertain even when
        the draft has moved; inspect both folders, never retry automatically.
        """
        writer = self._draft_writer()
        current = writer.checked(draft)
        enabled = self.senders()
        if not any(
            s.address.casefold() == draft.sender.address.casefold() for s in enabled
        ):
            raise MailError("Draft sender is no longer enabled")
        self._move_folder(trash)
        if trash.name != "Deleted Messages":
            raise ValueError("Discard requires the discovered Deleted Messages mailbox")
        request = DraftDiscardRequest(
            batchUpdate=(
                DraftDiscardOperation(
                    ids=(current.id,),
                    patch=DraftDiscardPatch(
                        flags=FlagDelta(set=("seen",)),
                        mboxRef=MailboxReplacement(replace=(trash.id,)),
                    ),
                ),
            )
        )
        response = self._submit_update(
            request, MoveResponse, "deleteMessagesGems", _MOVE_OUTCOME_UNKNOWN
        )
        _move_acknowledgement(response, str(self.params["dsid"]), current.id)
        try:
            self._draft_folder(current.folder)
            if self.message_by_uid(current.folder, current.uid) is not None:
                raise MailError("Discarded draft is still present")
        except (PyiCloudException, RequestException, OSError, ValueError) as exc:
            raise MailWriteUncertain(_MOVE_OUTCOME_UNKNOWN) from exc
        return MailMoveUpdate(
            source_message_id=current.id,
            source_folder_id=current.folder.id,
            destination_folder_id=trash.id,
        )

    def _draft_writer(self) -> DraftWriter:
        """Bind draft logic without introducing a second mutation transport."""
        return DraftWriter(
            self.senders,
            self._draft_folder,
            self.message_by_uid,
            self.body,
            self._submit_draft,
            self.params.get("clientMasteringNumber", ""),
            self.service_root,
            self.params.get("dsid", ""),
        )

    def _draft_folder(self, previous: MailFolder | None) -> MailFolder:
        """The browser's canonical Drafts namespace must be current and selectable."""
        matches = [f for f in self._mailbox_inventory() if f.name == "Drafts"]
        if len(matches) != 1 or matches[0].flags != 0:
            raise MailError("One selectable Drafts mailbox is required")
        current = matches[0]
        if previous is not None and (
            previous.id != current.id
            or previous.name != current.name
            or previous.uid_validity != current.uid_validity
        ):
            raise MailError("Drafts mailbox identity changed; refresh before writing")
        return MailFolder(
            identifier=current.id, name=current.name, uidValidity=current.uid_validity
        )

    def _submit_draft(self, request: DraftCreateRequest) -> DraftSaveResponse:
        """Save with the captured null context and without a fabricated intent."""
        return self._submit_update(
            request,
            DraftSaveResponse,
            None,
            DRAFT_UNCERTAIN,
            path="message/savedraft",
            keep_nulls=True,
        )

    def set_read(self, message: MailMessage, *, read: bool) -> MailFlagUpdate:
        """Set or clear only seen, after a fresh folder-local identity lookup.

        The result acknowledges submission, not an atomic snapshot of later mail
        state. A stale message is not mutated locally. An uncertain write must be
        checked through fresh metadata before any explicit caller retry.
        """
        return self._set_flag(message, "seen", read)

    def create_folder(
        self, name: str, *, parent: MailFolder | None = None
    ) -> MailFolder:
        """Create one new leaf; preserve a discovered parent's encoded server path.

        Names are user-visible Unicode, not pre-encoded wire strings. The result
        retains the raw server name used by folders(). Never retry an uncertain
        creation or automatically delete a possibly created folder.
        """
        return self._folder_writer().create(name, parent)

    def rename_folder(self, folder: MailFolder, name: str) -> MailFolder:
        """Rename one custom folder leaf and return verified fresh namespace data."""
        return self._folder_writer().rename(folder, name)

    def delete_folder(
        self, folder: MailFolder, *, confirm: bool, allow_nonempty: bool = False
    ) -> MailFolderDeletion:
        """Permanently delete one custom folder, never move it to Trash.

        confirm=True acknowledges irreversible deletion and racy preflight.
        Observed messages require allow_nonempty=True; children are refused.
        New mail or children can still arrive between checks and dispatch:
        Apple exposes no verified atomic empty-folder precondition here.
        """
        return self._folder_writer().delete(
            folder, confirm=confirm, allow_nonempty=allow_nonempty
        )

    def _folder_writer(self) -> FolderWriter:
        """Bind folder operations to this account and the guarded HTTP transport."""
        account_id = self.params.get("dsid")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("A discovered account identifier is required for writes")
        return FolderWriter(
            account_id,
            self._mailbox_inventory,
            self._submit_folder,
            self._folder_has_mail,
        )

    def _submit_folder(
        self, request: FolderRequest, intent: FolderIntent
    ) -> FolderMutationResponse:
        """Folder mutations use mailbox/set, never the message update endpoint."""
        return self._submit_update(
            request,
            FolderMutationResponse,
            intent,
            FOLDER_UNCERTAIN,
            path="mailbox/set",
        )

    def _folder_has_mail(self, folder: MailFolder) -> bool:
        """Check one message, including deleted-flag records, without opening it."""
        response = self._post(
            _MESSAGE_LIST,
            MailQuery(
                domain="email",
                limit=1,
                properties=["identifier", "uid", "mboxRef"],
                predicate=MailPredicate(
                    type="eq",
                    expression=MailExpression(property="mboxRef"),
                    value=folder.id,
                ),
            ),
            MessageResponse,
        )
        if len(response.records) > 1 or any(
            item.mailbox.id != folder.id for item in response.records
        ):
            raise MailError("Folder emptiness query returned unexpected messages")
        if response.continuation_marker and not response.records:
            raise MailError("Folder emptiness could not be established")
        return bool(response.records)

    def set_flagged(self, message: MailMessage, *, flagged: bool) -> MailFlagUpdate:
        """Set or clear only flagged; never replace other flags or retry writes."""
        return self._set_flag(message, "flagged", flagged)

    def move(self, message: MailMessage, destination: MailFolder) -> MailMoveUpdate:
        """Move one message without changing flags or guessing its new identity.

        Fresh capability checks reject labels, non-selectable folders, Junk, and
        Drafts. Moving to Trash is recoverable mailbox movement, not permanent
        deletion or the browser's mark-read-and-trash shortcut. Acknowledgements
        refer to the old identifier only; fetch new destination metadata before
        another write. No retry or compensating move is performed.
        """
        if not isinstance(destination, MailFolder):
            raise ValueError("A discovered destination MailFolder is required")
        validity = destination.uid_validity
        if isinstance(validity, bool) or not isinstance(validity, int) or validity < 1:
            raise ValueError("A positive destination UID validity is required")
        if isinstance(message, MailMessage) and message.folder.id == destination.id:
            raise ValueError("Source and destination folders must differ")
        account_id = self._write_identity(message)
        self._move_folder(message.folder)
        self._move_folder(destination)
        request = MoveRequest(
            batchUpdate=(
                MoveOperation(
                    ids=(message.id,),
                    patch=MovePatch(
                        mboxRef=MailboxReplacement(replace=(destination.id,))
                    ),
                ),
            )
        )
        response = self._submit_update(
            request, MoveResponse, "moveMessagesGems", _MOVE_OUTCOME_UNKNOWN
        )
        _move_acknowledgement(response, account_id, message.id)
        return MailMoveUpdate(
            source_message_id=message.id,
            source_folder_id=message.folder.id,
            destination_folder_id=destination.id,
        )

    def delete_permanently(
        self, message: MailMessage, *, confirm: bool = False
    ) -> MailMessageDeletion:
        """Irreversibly destroy exactly one current message already in Trash.

        Never use this as a move-to-Trash operation or an empty-Trash API. Fresh
        identity checks are not atomic with destruction. A submitted request is
        never retried automatically, even when verification fails afterward.
        """
        if confirm is not True:
            raise ValueError("Permanent message deletion requires confirm=True")
        if not isinstance(message, MailMessage):
            raise ValueError("A folder-bound MailMessage is required")
        if message.folder.name != "Deleted Messages":
            raise ValueError("Permanent deletion requires the discovered Trash mailbox")
        account_id = self._write_identity(message)
        self._move_folder(message.folder)
        response = self._submit_update(
            MessageDeleteRequest(destroy=(message.id,)),
            MessageDeleteResponse,
            "deleteMessagesGems",
            DELETE_UNCERTAIN,
        )
        deletion_acknowledged(response, account_id, message.id)
        try:
            if (
                self.message_by_uid(message.folder, message.uid) is not None
                or self.message(message.folder, message.id) is not None
            ):
                raise MailError("Deleted message is still present")
        except (PyiCloudException, RequestException, OSError, ValueError) as exc:
            raise MailWriteUncertain(DELETE_UNCERTAIN) from exc
        return MailMessageDeletion(
            message_id=message.id, folder_id=message.folder.id, uid=message.uid
        )

    def _move_folder(self, folder: MailFolder) -> None:
        """Require an exact current namespace and known ordinary-folder semantics."""
        records = self._mailbox_inventory(folder)
        if len(records) != 1:
            raise MailError("Move requires one exact current mailbox")
        current = records[0]
        if (
            current.id != folder.id
            or current.uid_validity != folder.uid_validity
            or current.name != folder.name
        ):
            raise MailError("Mailbox identity changed; refresh folders before moving")
        # These are browser-defined server names, not translated UI labels.
        # Only the captured zero-flag ordinary mailbox shape is supported.
        if (
            current.flags != 0
            or current.name.startswith(("$category$_", "/"))
            or current.name in {"Junk", "Drafts"}
        ):
            raise MailError(
                "Moves involving labels, Junk, Drafts, or non-selectable folders "
                "are unsupported"
            )

    def _mailbox_inventory(
        self, folder: MailFolder | None = None
    ) -> list[MailboxSnapshot]:
        """Read namespace/flag fields and refuse incomplete control inventories."""
        active = MailPredicate(
            type="eq",
            expression=MailExpression(property="isMboxDeleted"),
            value=False,
        )
        predicate = (
            active
            if folder is None
            else MailPredicate(
                type="eq",
                expression=MailExpression(property="identifier"),
                value=folder.id,
                and_=[active],
            )
        )
        result = self._post(
            "geqs/query",
            MailQuery(
                domain="mailbox",
                include_labels=True,
                # Browser inventories omit limit; adding it introduces a marker
                # even when the returned folder set is otherwise identical.
                limit=None if folder is None else 2,
                properties=["identifier", "uidValidity", "name", "flags"],
                predicate=predicate,
            ),
            MailboxSnapshotResponse,
        )
        if result.continuation_marker or (
            folder is None and len(result.records) >= MAX_RESULTS
        ):
            raise MailError(
                "Mailbox inventory is incomplete; no folder mutation submitted"
            )
        if len({item.id for item in result.records}) != len(result.records):
            raise MailError("Mailbox inventory contains duplicate identities")
        return result.records

    def _set_flag(
        self, message: MailMessage, flag: MailFlag, enabled: bool
    ) -> MailFlagUpdate:
        """Validate caller inputs and current identity before one mutation."""
        if not isinstance(enabled, bool):
            raise ValueError("The requested flag state must be a boolean")
        account_id = self._write_identity(message)
        delta = FlagDelta(set=(flag,)) if enabled else FlagDelta(clear=(flag,))
        request = FlagRequest(
            batchUpdate=(
                FlagOperation(ids=(message.id,), patch=FlagPatch(flags=delta)),
            )
        )
        # Browser intent names describe unread/flagged, not the inverse seen bit.
        setting = enabled if flag == "flagged" else not enabled
        intent: FlagIntent = "setMessageFlag" if setting else "clearMessageFlag"
        response = self._submit_update(
            request, FlagResponse, intent, _FLAG_OUTCOME_UNKNOWN
        )
        counter = _flag_acknowledgement(response, account_id, message.id)
        return MailFlagUpdate(
            message_id=message.id, flag=flag, enabled=enabled, modseq=counter
        )

    def _write_identity(self, message: MailMessage) -> str:
        """Resolve exact fresh source identity before constructing a mutation."""
        if not isinstance(message, MailMessage):
            raise ValueError("A folder-bound MailMessage is required")
        if message.mailbox.id != message.folder.id:
            raise ValueError(_MESSAGE_SCOPE_MISMATCH)
        validity = message.folder.uid_validity
        if isinstance(validity, bool) or not isinstance(validity, int) or validity < 1:
            raise ValueError("A positive mailbox UID validity is required for writes")
        account_id = self.params.get("dsid")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("A discovered account identifier is required for writes")
        current = self.message_by_uid(message.folder, message.uid)
        if current is None or current.id != message.id:
            raise MailError("Message identity changed; refresh metadata before writing")
        return account_id

    def _submit_update(
        self,
        request: BaseModel,
        response_type: type[ResponseModel],
        intent: str | None,
        uncertain_message: str,
        *,
        path: Literal[
            "email/set", "mailbox/set", "message/savedraft", "draft/send"
        ] = "email/set",
        keep_nulls: bool = False,
    ) -> ResponseModel:
        """Do not normalize/replay authentication errors after submitting a write."""
        params = dict(self.params)
        params.pop("clientIntent", None)
        if intent is not None:
            params["clientIntent"] = intent
        try:
            response = self.session.request_raw(
                "POST",
                f"{self.service_root}/mailws2/v1/{path}",
                params=params,
                json=request.model_dump(
                    mode="json", by_alias=True, exclude_none=not keep_nulls
                ),
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except (PyiCloudException, OSError) as exc:
            raise MailWriteUncertain(uncertain_message) from exc
        try:
            if response.status_code != 200:
                raise MailWriteUncertain(uncertain_message)
            try:
                return response_type.model_validate(
                    response.json(), by_alias=True, by_name=False
                )
            except ValueError as exc:
                raise MailWriteUncertain(uncertain_message) from exc
        finally:
            try:
                response.close()
            except OSError as exc:
                raise MailWriteUncertain(uncertain_message) from exc

    def messages(self, folder: MailFolder, *, limit: int = 50) -> MailMessagePage:
        """Read at most limit records, newest first; completeness is unknown."""
        _limit(limit)
        response = self._post(
            _MESSAGE_LIST,
            MailQuery(
                domain="email",
                properties=_HEADER_PROPERTIES,
                predicate=_folder_predicate(folder),
                limit=limit,
                orderby=MailOrder(),
            ),
            MessageResponse,
        )
        messages: list[MailMessage] = []
        seen: set[str] = set()
        if len(response.records) > limit:
            raise MailError(_RESULT_LIMIT_EXCEEDED)
        for record in response.records:
            if record.mailbox.id != folder.id:
                raise MailError("Mail returned a message from another folder")
            if record.id not in seen:
                messages.append(_bind_message(record, folder))
                seen.add(record.id)
        return MailMessagePage(messages=messages, limit=limit)

    def message(self, folder: MailFolder, message_id: str) -> MailMessage | None:
        """Look up one exact message ID in a verified mailbox, without scanning.

        Return None when no matching non-deleted message exists. The folder
        must have positive UID validity from folders(); a changed namespace,
        ambiguous response, or inconsistent identity raises MailError.
        """
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id must be a non-empty string")
        return self._lookup_message(folder, "identifier", message_id)

    def message_by_uid(self, folder: MailFolder, uid: int) -> MailMessage | None:
        """Look up a folder-local UID, guarded by the folder's UID validity.

        A UID alone is not a globally unique message ID. Use a fresh folder
        from folders() and retain its UID validity with any saved UID.
        """
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
            raise ValueError("uid must be a positive integer")
        return self._lookup_message(folder, "uid", uid)

    def _lookup_message(
        self, folder: MailFolder, field: Literal["identifier", "uid"], value: str | int
    ) -> MailMessage | None:
        """Validate an exact metadata response before binding a UID to a folder."""
        if folder.uid_validity is None or folder.uid_validity < 1:
            raise ValueError(_UID_VALIDITY_REQUIRED)
        current = self._checked_mailbox(folder)
        predicate = _folder_predicate(current)
        predicate = predicate.model_copy(
            update={
                "and_": [
                    *(predicate.and_ or []),
                    MailPredicate(
                        type="eq",
                        expression=MailExpression(type="property", property=field),
                        value=value,
                    ),
                ]
            }
        )
        response = self._post(
            _MESSAGE_LIST,
            MailQuery(
                domain="email",
                properties=_HEADER_PROPERTIES,
                predicate=predicate,
                limit=2,
            ),
            MessageResponse,
        )
        current = self._checked_mailbox(folder)
        if response.continuation_marker or len(response.records) > 1:
            raise MailError("Mail lookup returned an ambiguous or incomplete response")
        if not response.records:
            return None
        record = response.records[0]
        actual = record.id if field == "identifier" else record.uid
        if record.mailbox.id != folder.id or actual != value:
            raise MailError("Mail lookup returned a different message or folder")
        return _bind_message(record, current)

    def iter_messages(
        self, folder: MailFolder, *, page_size: int = 50, max_pages: int = 10000
    ) -> Iterator[MailMessage]:
        """Exhaust live metadata in descending UID order, not an atomic snapshot.

        Each page has at most ``page_size`` records. An empty page establishes
        exhaustion; short pages and opaque server markers do not. The budget
        includes the terminal empty request and raises rather than truncating.
        A changed UID validity or inconsistent page aborts enumeration. Earlier
        yielded records cannot be retracted after a later failure. No bodies,
        mutations, retries, or background prefetches are performed.
        """
        yield from self._iter_messages(folder, page_size, max_pages)

    def iter_thread_messages(
        self,
        folder: MailFolder,
        thread_id: str,
        *,
        page_size: int = 50,
        max_pages: int = 10000,
    ) -> Iterator[MailMessage]:
        """Exhaust one folder's thread members using checked descending UID pages.

        Not a cross-folder conversation or an atomic snapshot. Only an empty
        page establishes exhaustion; page budgets and failures raise. Earlier
        yielded metadata cannot be retracted. No bodies or mutations are requested.
        """
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")
        match = MailPredicate(
            type="eq",
            expression=MailExpression(type="property", property="appleThrid"),
            value=thread_id,
        )
        yield from self._iter_messages(
            folder, page_size, max_pages, match=match, thread_id=thread_id
        )

    def iter_filtered_messages(
        self,
        folder: MailFolder,
        *,
        filter_by: MessageFilter,
        page_size: int = 50,
        max_pages: int = 10000,
    ) -> Iterator[MailMessage]:
        """Exhaust unread or flagged individual messages, not grouped threads.

        Flags are evaluated by the server on each page. Changing flags can add
        or remove matches during the scan; UID exhaustion is not a consistent
        snapshot of filter membership. Budget exhaustion and errors raise.
        No bodies, attachments, or mutations are requested.
        """
        if filter_by not in ("unread", "flagged"):
            raise ValueError("filter_by must be 'unread' or 'flagged'")
        match = MailPredicate(
            type="eq",
            expression=MailExpression(
                type="fieldOf",
                property="flags",
                value="SEEN" if filter_by == "unread" else "FLAGGED",
            ),
            value=filter_by == "flagged",
        )
        yield from self._iter_messages(folder, page_size, max_pages, match=match)

    def _iter_messages(
        self,
        folder: MailFolder,
        page_size: int,
        max_pages: int,
        *,
        match: MailPredicate | None = None,
        thread_id: str | None = None,
    ) -> Iterator[MailMessage]:
        """Share UID progress, exhaustion and mailbox guards across read scopes."""
        _validate_scan(folder, page_size, max_pages)
        current = self._checked_mailbox(folder)
        before: int | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            response = self._post(
                _MESSAGE_LIST,
                _uid_query(current, page_size, before, match),
                MessageResponse,
            )
            current = self._checked_mailbox(folder)
            if thread_id is not None and any(
                record.thread_id != thread_id for record in response.records
            ):
                raise MailError("Mail returned a message from another thread")
            records = _uid_page(response.records, current, before, page_size)
            if not records:
                return
            if any(record.id in seen for record in records):
                raise MailError("Mail repeated a message identifier across UID pages")
            seen.update(record.id for record in records)
            before = records[-1].uid
            for record in records:
                yield _bind_message(record, current)
        raise MailError("Mail page budget exhausted; enumeration is incomplete")

    def search(
        self,
        folder: MailFolder,
        text: str,
        *,
        field: SearchField = "all",
        limit: int = 50,
    ) -> MailSearchPage:
        """Return bounded thread-grouped text matches within one folder."""
        fields = _search_fields(text, field, limit)
        matches = [
            MailPredicate(
                type="textMatch",
                value=text,
                expression=MailExpression(
                    type="fieldOf",
                    property="rfc822MessageBytes"
                    if item == "body"
                    else "rfc822HeaderBytes",
                    value="BODY" if item == "body" else item,
                ),
            )
            for item in fields
        ]
        match = matches[0].model_copy(update={"or_": matches[1:] or None})
        predicate = _folder_predicate(folder)
        predicate = predicate.model_copy(
            update={"and_": [*(predicate.and_ or []), match]}
        )
        response = self._post(
            _GEQS_QUERY,
            MailQuery(
                domain="email",
                properties=["identifier", "subject", "preview", "count"],
                predicate=predicate,
                limit=limit,
                collapse_on="appleThrid",
                orderby=MailOrder(),
            ),
            SearchResponse,
        )
        if len(response.records) > limit:
            raise MailError(_RESULT_LIMIT_EXCEEDED)
        return MailSearchPage(results=response.records, limit=limit)

    def thread_messages(
        self, folder: MailFolder, thread_id: str, *, limit: int = 50
    ) -> MailThreadPage:
        """Read bounded metadata for a thread in one verified mailbox.

        Accept thread-group IDs from search(), not message IDs. Only messages
        in the selected folder are returned, including thread members that did
        not match the original search text. Results always have complete=False:
        neither short pages nor search counts establish conversation coverage.
        No bodies, attachments, mutations, or further pages are requested.
        """
        _limit(limit)
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")
        if folder.uid_validity is None or folder.uid_validity < 1:
            raise ValueError(_UID_VALIDITY_REQUIRED)
        current = self._checked_mailbox(folder)
        predicate = _folder_predicate(current)
        predicate = predicate.model_copy(
            update={
                "and_": [
                    *(predicate.and_ or []),
                    MailPredicate(
                        type="eq",
                        expression=MailExpression(
                            type="property", property="appleThrid"
                        ),
                        value=thread_id,
                    ),
                ]
            }
        )
        response = self._post(
            _MESSAGE_LIST,
            MailQuery(
                domain="email",
                properties=_HEADER_PROPERTIES,
                predicate=predicate,
                limit=limit,
                orderby=MailOrder(),
            ),
            MessageResponse,
        )
        current = self._checked_mailbox(folder)
        if len(response.records) > limit:
            raise MailError(_RESULT_LIMIT_EXCEEDED)
        if any(
            record.mailbox.id != folder.id or record.thread_id != thread_id
            for record in response.records
        ):
            raise MailError("Mail returned a message from another thread or folder")
        records = _unique_uid_records(response.records)
        return MailThreadPage(
            thread_id=thread_id,
            messages=[_bind_message(record, current) for record in records],
            limit=limit,
        )

    def search_folders(
        self,
        folders: list[MailFolder],
        text: str,
        *,
        field: SearchField = "all",
        limit_per_folder: int = 50,
    ) -> MailMultiSearchPage:
        """Search 1 to 20 explicitly selected folders without hidden paging.

        Results retain each folder's context and ordering. Thread IDs are not
        deduplicated across folders, and counts do not count individual messages.
        Failure in any folder raises instead of returning successful partial data.
        This is not an atomic or globally ranked search.
        """
        _search_fields(text, field, limit_per_folder)
        if not folders or len(folders) > MAX_SEARCH_FOLDERS:
            raise ValueError(f"Select between 1 and {MAX_SEARCH_FOLDERS} folders")
        if any(not isinstance(folder, MailFolder) for folder in folders):
            raise ValueError("folders must contain MailFolder objects")
        if len({folder.id for folder in folders}) != len(folders):
            raise ValueError("Folder IDs must be unique")
        pages = []
        for folder in folders:
            page = self.search(folder, text, field=field, limit=limit_per_folder)
            pages.append(MailFolderSearchPage(**page.model_dump(), folder=folder))
        return MailMultiSearchPage(pages=pages, limit_per_folder=limit_per_folder)

    def export_message(
        self, message: MailMessage, *, include_body: bool = False
    ) -> MailMessageExport:
        """Build JSON-serializable data, fetching bodies only by explicit opt-in.

        This method writes no files, downloads no attachments, and does not mark
        messages read. Body strings are untrusted data, never rendered HTML.
        """
        if not isinstance(include_body, bool):
            raise ValueError("include_body must be a boolean")
        return MailMessageExport(
            message=message, body=self.body(message) if include_body else None
        )

    def _checked_mailbox(self, folder: MailFolder) -> MailFolder:
        """Reject a disappeared or recreated mailbox before exposing metadata."""
        if folder.uid_validity is None or folder.uid_validity < 1:
            raise ValueError(_UID_VALIDITY_REQUIRED)
        current = next((item for item in self.folders() if item.id == folder.id), None)
        if current is None or current.uid_validity != folder.uid_validity:
            raise MailError(
                "Mailbox identity changed; refresh folders and restart the operation"
            )
        return current

    def preview(self, message: MailMessage) -> MailPreview | None:
        """Fetch an advertised snippet without opening a body or marking it read.

        Missing preview IDs are not guessed. An empty response returns None;
        an explicitly empty snippet remains a MailPreview with text="".
        """
        if message.preview_id is None or not message.preview_id.strip():
            raise ValueError("Message has no advertised preview ID; refresh metadata")
        if message.mailbox.id != message.folder.id:
            raise ValueError(_MESSAGE_SCOPE_MISMATCH)
        current = self._checked_mailbox(message.folder)
        requested_folder = current.name
        response = self._post(
            "message/preview",
            MailPreviewRequest(
                folder=requested_folder,
                previewIds=[message.preview_id],
                session_headers=MailSessionHeaders(folder=requested_folder),
            ),
            PreviewResponse,
            keep_nulls=True,
        )
        current = self._checked_mailbox(message.folder)
        if (
            current.name != requested_folder
            or response.context.folder != requested_folder
            or len(response.records) > 1
        ):
            raise MailError(
                "Mail preview returned an ambiguous or wrong-folder response"
            )
        if not response.records:
            return None
        record = response.records[0]
        if record.message_guid != f"{requested_folder}/{message.uid}":
            raise MailError("Mail preview returned a different message")
        return MailPreview(
            message_id=message.id, folder=current, uid=message.uid, text=record.preview
        )

    def filtered_threads(
        self, folder: MailFolder, filter_by: MailThreadFilter, *, limit: int = 50
    ) -> MailThreadFilterPage:
        """Read bounded Unread or Flagged thread groups using the browser protocol.

        These are thread groups, not per-message flags or text search filters.
        Filters are deliberately separate: combinations are not assumed to mean
        logical AND. No bodies, mutations, paging, or sync-state replay occur.
        """
        _limit(limit)
        if filter_by not in ("unread", "flagged"):
            raise ValueError("filter_by must be unread or flagged")
        current = self._checked_mailbox(folder)
        requested_folder = current.name
        key: Literal["unseen", "flagged"] = (
            "unseen" if filter_by == "unread" else "flagged"
        )
        response = self._post(
            "thread/search",
            MailThreadFilterRequest(
                maxResults=limit,
                filters={key: True},
                session_headers=MailSessionHeaders(folder=requested_folder),
            ),
            ThreadFilterResponse,
            keep_nulls=True,
        )
        current = self._checked_mailbox(folder)
        if (
            current.name != requested_folder
            or response.context.folder != requested_folder
        ):
            raise MailError("Mail filter returned a different folder")
        if len(response.records) > limit:
            raise MailError(_RESULT_LIMIT_EXCEEDED)
        if response.returned_count != len(response.records):
            raise MailError("Mail filter returned an inconsistent thread count")
        if len({record.id for record in response.records}) != len(response.records):
            raise MailError("Mail filter returned duplicate thread identities")
        return MailThreadFilterPage(
            folder=current, filter_by=filter_by, results=response.records, limit=limit
        )

    def iter_filtered_threads(
        self,
        folder: MailFolder,
        *,
        filter_by: MailThreadFilter,
        page_size: int = 50,
        max_pages: int = 10000,
    ) -> Iterator[MailFilteredThread]:
        """Follow checked cursors for lightweight Unread or Flagged thread groups.

        This is not an atomic snapshot, full digest retrieval, or text search.
        Only an empty marker-free response completes the scan. Missing or repeated
        cursors, duplicates, inconsistent scope, and budget exhaustion raise rather
        than silently truncate. Earlier yielded records cannot be retracted.
        No content reads, retries, mutations, or persisted cursors are used.
        """
        _validate_scan(folder, page_size, max_pages)
        if filter_by not in ("unread", "flagged"):
            raise ValueError("filter_by must be 'unread' or 'flagged'")
        query = _filtered_thread_query(folder, filter_by, page_size)
        markers: set[str] = set()
        seen: set[str] = set()
        for _ in range(max_pages):
            self._checked_mailbox(folder)
            response = self._post(_GEQS_QUERY, query, ThreadQueryResponse)
            current = self._checked_mailbox(folder)
            _check_thread_page(response, current, page_size, seen, markers)
            if not response.records:
                return
            marker = response.continuation_marker
            if marker is None:
                raise MailError(
                    "Mail omitted a thread cursor; enumeration is incomplete"
                )
            markers.add(marker)
            seen.update(record.id for record in response.records)
            query = query.model_copy(update={"continuation_marker": marker})
            for record in response.records:
                yield MailFilteredThread(
                    id=record.id,
                    folder=current,
                    filter_by=filter_by,
                    timestamp=record.timestamps[folder.id],
                )
        raise MailError("Mail page budget exhausted; enumeration is incomplete")

    def _content_folder(self, message: MailMessage) -> MailFolder:
        """Guard known UID namespaces while preserving legacy bounded reads."""
        if message.mailbox.id != message.folder.id:
            raise ValueError(_MESSAGE_SCOPE_MISMATCH)
        if message.folder.uid_validity is None:
            return message.folder
        return self._checked_mailbox(message.folder)

    def _verify_content_folder(
        self, message: MailMessage, requested: MailFolder
    ) -> None:
        """Reject a changed namespace or path before completing a content read."""
        current = self._content_folder(message)
        if current.name != requested.name:
            raise MailError("Mail folder changed during content retrieval; retry")

    def body(self, message: MailMessage) -> MailBody:
        """Read advertised plain-text/HTML parts without marking the message read."""
        parts = [
            part.id
            for part in message.parts
            if not part.is_attachment
            and re.fullmatch(MIME_PART_PATTERN, part.id)
            and part.content_type.split(";", 1)[0].strip().lower()
            in ("text/plain", _HTML_CONTENT_TYPE)
        ]
        if not parts:
            raise ValueError("Message has no supported text body parts")
        current = self._content_folder(message)
        result = self._post(
            "message/get",
            MailBodyRequest(
                uid=str(message.uid),
                parts=parts,
                session_headers=MailSessionHeaders(folder=current.name),
            ),
            MailBody,
            keep_nulls=True,
        )
        self._verify_content_folder(message, current)
        return result

    @contextmanager
    def mail_drop_attachment(
        self, message: MailMessage, part_index: int, *, max_bytes: int = 104857600
    ) -> Generator[Iterator[bytes], None, None]:
        """Download one 1-based part using an isolated, credential-free session.

        Supports the verified Apple host only. No redirects, netrc, inherited
        cookies, account authentication, or environment proxies are used. TLS is
        verified; REQUESTS_CA_BUNDLE may select a custom trusted CA bundle.
        Library yields cannot be retracted after a late size or mailbox failure.
        """
        if (
            isinstance(part_index, bool)
            or not isinstance(part_index, int)
            or not 1 <= part_index <= len(message.parts)
        ):
            raise ValueError("part_index must select a 1-based message part")
        part = message.parts[part_index - 1]
        url = _validate_mail_drop(part, max_bytes)
        if message.mailbox.id != message.folder.id:
            raise ValueError(_MESSAGE_SCOPE_MISMATCH)
        current = self._checked_mailbox(message.folder)
        with Session() as download:
            download.trust_env = False
            request = download.prepare_request(
                Request("GET", url, headers={"Accept-Encoding": "identity"})
            )
            # Send explicit settings without the CLI's class-wide environment merge.
            try:
                response = download.send(
                    request,
                    stream=True,
                    allow_redirects=False,
                    timeout=DEFAULT_TIMEOUT,
                    verify=os.environ.get("REQUESTS_CA_BUNDLE") or True,
                    proxies={},
                    cert=None,
                )
            except RequestException as exc:
                raise _mail_drop_transport_error(exc) from exc
            try:
                size = _mail_drop_response_size(response, part, max_bytes)
                self._verify_content_folder(message, current)
                yield _mail_drop_chunks(
                    response.iter_content(chunk_size=65536), max_bytes, size
                )
                self._verify_content_folder(message, current)
            finally:
                response.close()

    @contextmanager
    def raw_message(
        self, message: MailMessage
    ) -> Generator[Iterator[bytes], None, None]:
        """Stream Apple's original message bytes without parsing or regenerating MIME.

        Requires a verified mailbox UID namespace. Use as a context manager;
        responses close on partial consumption or failure. No redirects, retries,
        body reconstruction, or remote filenames are used. A final mailbox check
        can fail after bytes have been yielded; consumers must not publish partial
        output as success. This is not a signed archival-integrity guarantee.
        """
        if message.mailbox.id != message.folder.id:
            raise ValueError(_MESSAGE_SCOPE_MISMATCH)
        current = self._checked_mailbox(message.folder)
        response = self.session.get(
            f"{self.service_root}/mailws2/v1/message/download",
            params={
                **self.params,
                "guid": f"message:{current.name}/{message.uid}",
                "filename": "message.eml",
            },
            stream=True,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                raise MailError(
                    f"Mail raw download returned HTTP {response.status_code}"
                )
            media_type = (
                response.headers
                .get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if media_type != "message/rfc822":
                raise MailError("Mail raw download returned an unexpected content type")
            self._verify_content_folder(message, current)
            yield self._chunks(
                response.iter_content(chunk_size=65536), label="raw message"
            )
            self._verify_content_folder(message, current)
        finally:
            response.close()

    @contextmanager
    def attachment(
        self, message: MailMessage, part_id: str
    ) -> Generator[Iterator[bytes], None, None]:
        """Stream a selected attachment; never use remote filenames as paths.

        Use as a context manager, including when consuming only part of the
        stream. No redirects or retries are performed, and no file is created.
        """
        part = next(
            (p for p in message.parts if p.id == part_id and p.is_attachment), None
        )
        if part is None:
            raise ValueError("Unknown attachment part")
        if not re.fullmatch(MIME_PART_PATTERN, part.id):
            raise ValueError(
                "External or Mail Drop attachment downloads are not supported"
            )
        current = self._content_folder(message)
        query: dict[str, str | int] = {
            "guid": f"messagepart:{current.name}/{message.uid}-{part.id}",
            "type": part.content_type,
        }
        if part.filename is not None:
            query["name"] = part.filename
        if part.size is not None:
            query["size"] = part.size
        response = self.session.get(
            f"{self.service_root}/mailws2/v1/message/part",
            params=query,
            stream=True,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                raise MailError(f"Mail attachment returned HTTP {response.status_code}")
            content_type = (
                response.headers
                .get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            expected = part.content_type.split(";", 1)[0].strip().lower()
            if expected == _BINARY_CONTENT_TYPE:
                # Missing contentType metadata defaults to octet-stream here,
                # and the server may then answer with the real type instead of
                # echoing the default, so there is nothing specific to match.
                # Only reject what cannot be attachment bytes: an empty type
                # or an HTML error page served with HTTP 200.
                acceptable = bool(content_type) and content_type != _HTML_CONTENT_TYPE
            else:
                # A specifically advertised type must match, unless the server
                # fell back to octet-stream.
                acceptable = content_type in (expected, _BINARY_CONTENT_TYPE)
            if not acceptable:
                raise MailError("Mail attachment returned an unexpected content type")
            self._verify_content_folder(message, current)
            yield self._chunks(response.iter_content(chunk_size=65536))
            self._verify_content_folder(message, current)
        finally:
            response.close()

    @staticmethod
    def _chunks(
        chunks: Iterator[bytes], *, label: str = "attachment"
    ) -> Iterator[bytes]:
        """Yield nonempty chunks and translate download transport failures."""
        try:
            for chunk in chunks:
                if chunk:
                    yield chunk
        except RequestException as exc:
            raise MailError(f"Mail {label} stream failed") from exc
