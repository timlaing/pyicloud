"""Fresh source provenance for explicit reply/forward draft creation."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from ._draft_models import DraftStoredReference
from ._draft_uploads import attachment_references
from ._thread_models import MailDraftThreading
from ._threading import source_threading
from .exceptions import MailError
from .models import MailBody, MailDraftAttachment, MailFolder, MailMessage


@dataclass(frozen=True)
class ConversationDraftSource:
    """A fresh parent, its headers and optional original ordinary attachments."""

    message: MailMessage
    threading: MailDraftThreading
    attachments: tuple[MailDraftAttachment, ...]


def prepare_source(
    message: MailMessage,
    mode: Literal["reply", "forward"],
    lookup: Callable[[MailFolder, int], MailMessage | None],
    body: Callable[[MailMessage], MailBody],
    root: str,
    account_id: str,
    *,
    include_attachments: bool = False,
) -> ConversationDraftSource:
    """Bind headers and parts to an exact source; never infer recipients or send."""
    if not isinstance(message, MailMessage) or not isinstance(
        include_attachments, bool
    ):
        raise ValueError(
            "An exact source message and boolean attachment choice are required"
        )
    current = lookup(message.folder, message.uid)
    if current is None or current.id != message.id:
        raise MailError("Reply/forward source identity changed; refresh first")
    source = body(current)
    if source.guid != f"message:{current.folder.name}/{current.uid}":
        raise MailError("Reply/forward body belongs to a different message")
    threading = source_threading(source, mode)
    attachments: list[MailDraftAttachment] = []
    if include_attachments:
        parts = [p for p in current.parts if p.is_attachment]
        if current.has_attachment is not bool(parts):
            raise MailError("Source attachment metadata is incomplete")
        for part in parts:
            if part.filename is None or part.size is None:
                raise MailError("Source attachment name or size is missing")
            guid = f"messagepart:{current.folder.name}/{current.uid}-{part.id}"
            attachments.append(
                MailDraftAttachment(
                    account_id=account_id,
                    service_root=root,
                    source_message=current,
                    reference=DraftStoredReference(
                        guid=guid,
                        name=part.filename,
                        datatype=part.content_type,
                        size=part.size,
                        contentID=None,
                        mailDropExpirationDate=None,
                        isMailDropThumbnail=False,
                        url=root.rstrip("/")
                        + "/mailws2/v1/message/part?"
                        + urlencode({
                            "guid": guid,
                            "type": part.content_type,
                            "name": part.filename,
                            "size": part.size,
                        }),
                    ),
                )
            )
        attachment_references(tuple(attachments), root, account_id)
    return ConversationDraftSource(current, threading, tuple(attachments))
