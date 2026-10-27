"""Experimental iCloud Mail reads and explicit single-message flag writes."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
import re
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from requests import RequestException

from pyicloud.exceptions import PyiCloudException
from pyicloud.services.base import BaseService
from pyicloud.services.mail._flags import (
    FlagDelta,
    FlagIntent,
    FlagOperation,
    FlagPatch,
    FlagRequest,
    FlagResponse,
    MailFlag,
)
from pyicloud.services.mail.models import (
    MIME_PART_PATTERN,
    FolderResponse,
    MailBody,
    MailBodyRequest,
    MailExpression,
    MailFlagUpdate,
    MailFolder,
    MailFolderSearchPage,
    MailMessage,
    MailMessageExport,
    MailMessagePage,
    MailMessageRecord,
    MailMultiSearchPage,
    MailOrder,
    MailPredicate,
    MailPreview,
    MailPreviewRequest,
    MailQuery,
    MailSearchPage,
    MailSessionHeaders,
    MailThreadFilter,
    MailThreadFilterPage,
    MailThreadFilterRequest,
    MailThreadPage,
    MessageResponse,
    PreviewResponse,
    SearchResponse,
    ThreadFilterResponse,
)
from pyicloud.session import PyiCloudSession

DEFAULT_TIMEOUT = (10.0, 60.0)
MAX_RESULTS = 1000
MAX_SEARCH_FOLDERS = 20
_RESULT_LIMIT_EXCEEDED = "Mail exceeded the requested result limit"
_MESSAGE_LIST = "message/list"
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


class MailError(PyiCloudException):
    """An invalid response, incomplete enumeration, or failed attachment stream."""


class MailWriteRejected(MailError):
    """The server explicitly rejected the one requested flag update."""


class MailWriteUncertain(MailError):
    """A submitted write could not be confirmed; inspect state before retrying."""


_FLAG_OUTCOME_UNKNOWN = (
    "Mail flag update outcome is uncertain; refresh message metadata before "
    "retrying. No automatic retry was performed."
)


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
        if set(response.not_updated) == {message_id} and not response.updated:
            raise MailWriteRejected("Mail rejected the flag update; refresh metadata.")
        raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
    if response.updated is None or set(response.updated) != {message_id}:
        raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
    return response.updated[message_id].modseq


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


def _validate_uid_scan(folder: MailFolder, page_size: int, max_pages: int) -> None:
    """Validate a UID scan before checking mailbox identity or reading metadata."""
    _limit(page_size)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if folder.uid_validity is None or folder.uid_validity < 1:
        raise ValueError(_UID_VALIDITY_REQUIRED)


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


class MailService(BaseService):
    """Bounded Mail reads, UID enumeration, and explicit single-message flags.

    Bounded methods remain snapshots; only explicit iterators opt into paging.
    Reads never mutate mail; only set_read and set_flagged submit flag writes.
    Sending, moves, deletion, HTML rendering, and synchronization are unsupported.
    Read-side authentication failures retain the library's existing exception types.
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        """Validate the discovered endpoint and copy session query parameters."""
        validate_mail_endpoint(service_root)
        super().__init__(service_root.rstrip("/"), session, dict(params))

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
            "geqs/query",
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

    def set_read(self, message: MailMessage, *, read: bool) -> MailFlagUpdate:
        """Set or clear only seen, after a fresh folder-local identity lookup.

        The result acknowledges submission, not an atomic snapshot of later mail
        state. A stale message is not mutated locally. An uncertain write must be
        checked through fresh metadata before any explicit caller retry.
        """
        return self._set_flag(message, "seen", read)

    def set_flagged(self, message: MailMessage, *, flagged: bool) -> MailFlagUpdate:
        """Set or clear only flagged; never replace other flags or retry writes."""
        return self._set_flag(message, "flagged", flagged)

    def _set_flag(
        self, message: MailMessage, flag: MailFlag, enabled: bool
    ) -> MailFlagUpdate:
        """Validate caller inputs and current identity before one mutation."""
        if not isinstance(enabled, bool):
            raise ValueError("The requested flag state must be a boolean")
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
        delta = FlagDelta(set=(flag,)) if enabled else FlagDelta(clear=(flag,))
        request = FlagRequest(
            batchUpdate=(
                FlagOperation(ids=(message.id,), patch=FlagPatch(flags=delta)),
            )
        )
        # Browser intent names describe unread/flagged, not the inverse seen bit.
        setting = enabled if flag == "flagged" else not enabled
        intent: FlagIntent = "setMessageFlag" if setting else "clearMessageFlag"
        counter = self._submit_flag(request, account_id, message.id, intent)
        return MailFlagUpdate(
            message_id=message.id, flag=flag, enabled=enabled, modseq=counter
        )

    def _submit_flag(
        self, request: FlagRequest, account_id: str, message_id: str, intent: FlagIntent
    ) -> int:
        """Do not normalize/replay authentication errors after submitting a write."""
        try:
            response = self.session.request_raw(
                "POST",
                f"{self.service_root}/mailws2/v1/email/set",
                params={**self.params, "clientIntent": intent},
                json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except (PyiCloudException, OSError) as exc:
            raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN) from exc
        try:
            if response.status_code != 200:
                raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN)
            try:
                result = FlagResponse.model_validate(response.json())
            except ValueError as exc:
                raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN) from exc
            return _flag_acknowledgement(result, account_id, message_id)
        finally:
            try:
                response.close()
            except OSError as exc:
                raise MailWriteUncertain(_FLAG_OUTCOME_UNKNOWN) from exc

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
                messages.append(MailMessage(**record.model_dump(), folder=folder))
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
        return MailMessage(**record.model_dump(), folder=current)

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
        _validate_uid_scan(folder, page_size, max_pages)
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
                yield MailMessage(**record.model_dump(), folder=current)
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
            "geqs/query",
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
            messages=[
                MailMessage(**record.model_dump(), folder=current) for record in records
            ],
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
        if response.context.folder != requested_folder or len(response.records) > 1:
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
        if response.context.folder != requested_folder:
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

    def body(self, message: MailMessage) -> MailBody:
        """Read advertised plain-text/HTML parts without marking the message read."""
        parts = [
            part.id
            for part in message.parts
            if not part.is_attachment
            and re.fullmatch(MIME_PART_PATTERN, part.id)
            and part.content_type.split(";", 1)[0].strip().lower()
            in ("text/plain", "text/html")
        ]
        if not parts:
            raise ValueError("Message has no supported text body parts")
        return self._post(
            "message/get",
            MailBodyRequest(
                uid=str(message.uid),
                parts=parts,
                session_headers=MailSessionHeaders(folder=message.folder.name),
            ),
            MailBody,
            keep_nulls=True,
        )

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
        query: dict[str, str | int] = {
            "guid": f"messagepart:{message.folder.name}/{message.uid}-{part.id}",
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
            if expected == "application/octet-stream":
                # Missing contentType metadata defaults to octet-stream here,
                # and the server may then answer with the real type instead of
                # echoing the default, so there is nothing specific to match.
                # Only reject what cannot be attachment bytes: an empty type
                # or an HTML error page served with HTTP 200.
                acceptable = bool(content_type) and content_type != "text/html"
            else:
                # A specifically advertised type must match, unless the server
                # fell back to octet-stream.
                acceptable = content_type in (expected, "application/octet-stream")
            if not acceptable:
                raise MailError("Mail attachment returned an unexpected content type")
            yield self._chunks(response.iter_content(chunk_size=65536))
        finally:
            response.close()

    @staticmethod
    def _chunks(chunks: Iterator[bytes]) -> Iterator[bytes]:
        """Yield nonempty chunks and translate attachment transport failures."""
        try:
            for chunk in chunks:
                if chunk:
                    yield chunk
        except RequestException as exc:
            raise MailError("Mail attachment stream failed") from exc
