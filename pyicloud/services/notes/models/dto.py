"""High-level Notes data transfer objects."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterator, List, Literal, Optional

from pydantic import computed_field

from pyicloud.common.models import FrozenServiceModel

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from ..service import NotesService


class NoteSummary(FrozenServiceModel):
    """Lightweight metadata returned by list/search APIs."""

    id: str
    title: Optional[str]
    snippet: Optional[str]
    modified_at: Optional[datetime]
    folder_id: Optional[str]
    folder_name: Optional[str]
    is_deleted: bool
    is_locked: bool


class Attachment(FrozenServiceModel):
    """Metadata for a note attachment."""

    id: str
    filename: Optional[str]
    uti: Optional[str]
    size: Optional[int]
    download_url: Optional[str]
    preview_url: Optional[str]
    thumbnail_url: Optional[str]

    def save_to(self, directory: str, *, service: "NotesService") -> str:
        """Download the attachment to ``directory`` using the provided service."""

        return service._download_attachment_to(self, directory)

    def stream(
        self, *, service: "NotesService", chunk_size: int = 65_536
    ) -> Iterator[bytes]:
        """Yield the attachment bytes in chunks using the provided service."""

        yield from service._stream_attachment(self, chunk_size=chunk_size)


class Note(NoteSummary):
    """Full note payload returned by ``NotesService.get``."""

    text: Optional[str]
    html: Optional[str] = None
    attachments: Optional[List[Attachment]]

    @computed_field
    @property
    def has_attachments(self) -> Optional[bool]:
        """Return ``True``/``False`` when attachments were loaded, otherwise ``None``."""
        if self.attachments is None:
            return None
        return bool(self.attachments)


class NoteFolder(FrozenServiceModel):
    id: str
    name: Optional[str]
    has_subfolders: Optional[bool]
    count: Optional[int]  # not always available


class ChangeEvent(FrozenServiceModel):
    type: Literal["updated", "deleted"]
    note: NoteSummary
