"""High-level Notes data transfer objects."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import computed_field

from pyicloud.common.models import FrozenServiceModel

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from ..service import NotesService


class NoteSummary(FrozenServiceModel):
    """Lightweight metadata returned by list/search APIs."""

    id: str
    title: str | None
    snippet: str | None
    modified_at: datetime | None
    folder_id: str | None
    folder_name: str | None
    is_deleted: bool
    is_locked: bool


class Attachment(FrozenServiceModel):
    """Metadata for a note attachment."""

    id: str
    filename: str | None
    uti: str | None
    size: int | None
    download_url: str | None
    preview_url: str | None
    thumbnail_url: str | None

    def save_to(self, directory: str, *, service: NotesService) -> str:
        """Download the attachment to ``directory`` using the provided service."""

        return service.download_attachment_to(self, directory)

    def stream(
        self, *, service: NotesService, chunk_size: int = 65_536
    ) -> Iterator[bytes]:
        """Yield the attachment bytes in chunks using the provided service."""

        yield from service.stream_attachment(self, chunk_size=chunk_size)


class Note(NoteSummary):
    """Full note payload returned by ``NotesService.get``."""

    text: str | None
    html: str | None = None
    attachments: list[Attachment] | None

    @computed_field
    @property
    def has_attachments(self) -> bool | None:
        """Return ``True``/``False`` when attachments were loaded, otherwise
        ``None``."""
        if self.attachments is None:
            return None
        return bool(self.attachments)


class NoteFolder(FrozenServiceModel):
    """Metadata for a Notes folder."""

    id: str
    name: str | None
    has_subfolders: bool | None
    count: int | None  # not always available


class ChangeEvent(FrozenServiceModel):
    """A change to a note reported by the sync stream."""

    type: Literal["updated", "deleted"]
    note: NoteSummary
