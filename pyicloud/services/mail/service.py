"""Experimental, read-only iCloud Mail HTTP service."""

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
from pyicloud.services.mail.models import (
    MIME_PART_PATTERN,
    FolderResponse,
    MailBody,
    MailBodyRequest,
    MailExpression,
    MailFolder,
    MailMessage,
    MailMessagePage,
    MailMessageRecord,
    MailOrder,
    MailPredicate,
    MailQuery,
    MailSearchPage,
    MailSessionHeaders,
    MailThreadPage,
    MessageResponse,
    SearchResponse,
)
from pyicloud.session import PyiCloudSession

DEFAULT_TIMEOUT = (10.0, 60.0)
MAX_RESULTS = 1000
_RESULT_LIMIT_EXCEEDED = "Mail exceeded the requested result limit"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
SearchField = Literal["all", "subject", "from", "to", "cc", "body"]
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
    """Read-only Mail with bounded reads and explicit UID-based enumeration.

    Bounded methods remain snapshots; only ``iter_messages`` opts into paging.
    No mutations, HTML rendering, or incremental synchronization are performed.
    Session authentication failures retain the library's existing exception types.
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

    def messages(self, folder: MailFolder, *, limit: int = 50) -> MailMessagePage:
        """Read at most limit records, newest first; completeness is unknown."""
        _limit(limit)
        response = self._post(
            "message/list",
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

    def _checked_mailbox(self, folder: MailFolder) -> MailFolder:
        """Reject a disappeared or recreated mailbox before exposing metadata."""
        current = next((item for item in self.folders() if item.id == folder.id), None)
        if current is None or current.uid_validity != folder.uid_validity:
            raise MailError(
                "Mailbox identity changed; refresh folders and restart the operation"
            )
        return current

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
        _limit(page_size)
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 1
        ):
            raise ValueError("max_pages must be a positive integer")
        if folder.uid_validity is None or folder.uid_validity < 1:
            raise ValueError(
                "A positive mailbox UID validity is required; refresh folders"
            )
        current = self._checked_mailbox(folder)
        before: int | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            predicate = _folder_predicate(current)
            if before is not None:
                predicate = predicate.model_copy(
                    update={
                        "and_": [
                            *(predicate.and_ or []),
                            MailPredicate(
                                type="lt",
                                expression=MailExpression(
                                    type="property", property="uid"
                                ),
                                value=before,
                            ),
                        ]
                    }
                )
            response = self._post(
                "message/list",
                MailQuery(
                    domain="email",
                    properties=_HEADER_PROPERTIES,
                    predicate=predicate,
                    limit=page_size,
                    orderby=MailOrder(
                        expressions=[MailExpression(type="property", property="uid")]
                    ),
                ),
                MessageResponse,
            )
            current = self._checked_mailbox(folder)
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
        _limit(limit)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Search text must not be empty")
        if field not in ("all", "subject", "from", "to", "cc", "body"):
            raise ValueError("Unsupported search field")
        fields = ("subject", "to", "from", "cc", "body") if field == "all" else (field,)
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
            raise ValueError(
                "A positive mailbox UID validity is required; refresh folders"
            )
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
            "message/list",
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
