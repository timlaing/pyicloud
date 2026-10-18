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
    MailOrder,
    MailPredicate,
    MailQuery,
    MailSearchPage,
    MailSessionHeaders,
    MessageResponse,
    SearchResponse,
)
from pyicloud.session import PyiCloudSession

DEFAULT_TIMEOUT = (10.0, 60.0)
MAX_RESULTS = 1000
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
    """An invalid Mail response or a failed attachment stream."""


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


class MailService(BaseService):
    """Bounded Mail reads using the discovered mccgateway endpoint.

    Results are snapshots. No implicit paging, mutations, HTML rendering, or
    incremental synchronization is performed. Session authentication failures
    retain the library's existing exception types.
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        """Validate the discovered endpoint and copy session query parameters."""
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
            raise MailError("Mail exceeded the requested result limit")
        for record in response.records:
            if record.mailbox.id != folder.id:
                raise MailError("Mail returned a message from another folder")
            if record.id not in seen:
                messages.append(MailMessage(**record.model_dump(), folder=folder))
                seen.add(record.id)
        return MailMessagePage(messages=messages, limit=limit)

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
            raise MailError("Mail exceeded the requested result limit")
        return MailSearchPage(results=response.records, limit=limit)

    def body(self, message: MailMessage) -> MailBody:
        """Read advertised plain-text/HTML parts without marking the message read."""
        parts = [
            part.id
            for part in message.parts
            if not part.is_attachment
            and re.fullmatch(MIME_PART_PATTERN, part.id)
            and part.content_type.split(";", 1)[0].lower()
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
                response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            )
            expected = part.content_type.split(";", 1)[0].lower()
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
