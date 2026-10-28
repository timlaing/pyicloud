"""Bounded ordinary draft uploads, with no redirects, remote fetches or retries."""

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from pyicloud.exceptions import PyiCloudException
from pyicloud.session import PyiCloudSession

from ._draft_models import (
    DraftAttachmentReference,
    DraftStoredReference,
    DraftUploadResponse,
)
from .exceptions import MailError, MailWriteUncertain
from .models import MailDraftAttachment

MAX_DRAFT_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_TIMEOUT = (10.0, 60.0)
UPLOAD_UNCERTAIN = (
    "Mail attachment upload outcome is uncertain; a cached upload may exist. "
    "No automatic retry or cleanup was performed. No draft was saved or sent."
)


def validate_upload_input(data: bytes, filename: str, content_type: str) -> None:
    """Validate the local size limit, basename and bare MIME type before I/O."""
    if not isinstance(data, bytes) or len(data) > MAX_DRAFT_UPLOAD_BYTES:
        raise ValueError("Attachment data must be bytes, at most 25 MiB")
    if (
        not isinstance(filename, str)
        or not filename
        or filename in (".", "..")
        or any(c in filename for c in ("/", "\\"))
        or any(ord(c) < 32 or ord(c) == 127 for c in filename)
    ):
        raise ValueError("Attachment filename must be a control-free basename")
    try:
        filename.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Attachment filename must be valid Unicode") from exc
    if not isinstance(content_type, str) or not re.fullmatch(
        r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", content_type
    ):
        raise ValueError("Attachment content type must be a bare MIME type")


def checked_part_url(
    root: str,
    url: str,
    guid: str,
    filename: str,
    content_type: str,
    *,
    size: int | None = None,
) -> str:
    """Validate a part reference, then rebuild it using only the discovered root."""
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise MailError("Invalid attachment part URL")
    source, target = urlsplit(root), urlsplit(url)
    path = source.path.rstrip("/") + "/mailws2/v1/message/part"
    if (
        target.username
        or target.password
        or target.fragment
        or target.path != path
        or (not target.scheme and bool(target.netloc))
        or (
            target.scheme
            and (
                target.scheme != source.scheme
                or target.hostname != source.hostname
                or (target.port or 443) != (source.port or 443)
            )
        )
    ):
        raise MailError("Attachment URL is outside the discovered part endpoint")
    expected = {
        "guid": [guid],
        "name": [filename],
        "type": [content_type],
    }
    expected["code" if size is None else "size"] = [
        "base64" if size is None else str(size)
    ]
    if parse_qs(target.query, keep_blank_values=True, strict_parsing=True) != expected:
        raise MailError("Attachment URL does not match its advertised metadata")
    return (
        root.rstrip("/")
        + "/mailws2/v1/message/part?"
        + urlencode({key: value[0] for key, value in expected.items()})
    )


def attachment_references(
    attachments: tuple[MailDraftAttachment, ...], root: str, account_id: str
) -> list[DraftAttachmentReference | DraftStoredReference]:
    """Recheck persisted handles before sending any part reference to Apple."""
    if not isinstance(attachments, tuple):
        raise ValueError("Attachments must be a tuple of uploaded handles")
    references: list[DraftAttachmentReference | DraftStoredReference] = []
    for attachment in attachments:
        if (
            not isinstance(attachment, MailDraftAttachment)
            or attachment.account_id != account_id
            or attachment.service_root != root
        ):
            raise ValueError("Attachment belongs to a different account or gateway")
        if not isinstance(
            attachment.reference, (DraftAttachmentReference, DraftStoredReference)
        ):
            raise ValueError("Invalid attachment reference")
        reference = type(attachment.reference).model_validate(
            attachment.reference.model_dump(by_alias=True), by_alias=True, by_name=False
        )
        validate_upload_input(b"", reference.name, reference.datatype)
        if (
            isinstance(reference, DraftAttachmentReference)
            and reference.size > MAX_DRAFT_UPLOAD_BYTES
        ):
            raise ValueError("Attachment handle exceeds the ordinary upload limit")
        if isinstance(reference, DraftAttachmentReference) and (
            reference.content_id != reference.guid
            or attachment.source_message is not None
        ):
            raise ValueError("Attachment content identity does not match its part")
        if isinstance(reference, DraftStoredReference):
            source = attachment.source_message
            if source is None:
                raise ValueError("Stored attachments require their source message")
            matches = [
                p
                for p in source.parts
                if reference.guid
                == f"messagepart:{source.folder.name}/{source.uid}-{p.id}"
                and p.is_attachment
                and p.filename == reference.name
                and p.content_type == reference.datatype
                and p.size == reference.size
            ]
            if len(matches) != 1:
                raise ValueError("Stored attachment does not match its source metadata")
        url = checked_part_url(
            root,
            reference.url,
            reference.guid,
            reference.name,
            reference.datatype,
            size=reference.size
            if isinstance(reference, DraftStoredReference)
            else None,
        )
        if any(
            r.guid == reference.guid or r.name == reference.name for r in references
        ):
            raise ValueError("Duplicate attachment identity or filename")
        references.append(reference.model_copy(update={"url": url}))
    return references


def upload_attachment(
    session: PyiCloudSession,
    root: str,
    params: Mapping[str, Any],
    data: bytes,
    filename: str,
    content_type: str,
) -> MailDraftAttachment:
    """Upload raw bytes exactly once and verify the echoed upload and part identity."""
    validate_upload_input(data, filename, content_type)
    account_id = params.get("dsid")
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("A discovered account identifier is required for uploads")
    upload_id = str(uuid4())
    content_type = content_type.lower()
    query = dict(params)
    query.pop("clientIntent", None)
    query.update({
        "X-id": upload_id,
        "X-type": content_type,
        "X-size": str(len(data)),
        "X-name": filename,
    })
    try:
        response = session.request_raw(
            "POST",
            root.rstrip("/") + "/mailws2/v1/message/part",
            params=query,
            data=data,
            headers={"Content-Type": content_type},
            timeout=UPLOAD_TIMEOUT,
            allow_redirects=False,
        )
    except (PyiCloudException, OSError) as exc:
        raise MailWriteUncertain(UPLOAD_UNCERTAIN) from exc
    try:
        try:
            if response.status_code != 200:
                raise MailError("Upload did not return a successful response")
            result = DraftUploadResponse.model_validate(
                response.json(), by_alias=True, by_name=False
            )
            part = result.message_part
            if (
                result.uid != upload_id
                or part.name != filename
                or part.size != len(data)
                or part.datatype != content_type
            ):
                raise MailError("Upload response does not match the submitted file")
            url = checked_part_url(root, part.url, part.guid, filename, content_type)
            return MailDraftAttachment(
                account_id=account_id,
                service_root=root,
                reference=DraftAttachmentReference(
                    guid=part.guid,
                    name=part.name,
                    url=url,
                    contentID=part.guid,
                    size=part.size,
                    mailDropExpirationDate=None,
                    isMailDropThumbnail=False,
                    datatype=part.datatype,
                ),
            )
        except (PyiCloudException, OSError, ValueError) as exc:
            raise MailWriteUncertain(UPLOAD_UNCERTAIN) from exc
    finally:
        try:
            response.close()
        except OSError as exc:
            raise MailWriteUncertain(UPLOAD_UNCERTAIN) from exc
