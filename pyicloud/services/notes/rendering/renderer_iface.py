"""
Transport-agnostic renderer interface for Apple Notes.

Defines the minimal datasource seam (`NoteDataSource`) that the renderer
requires to resolve:
  - the UTI of an embedded attachment (by identifier), and
  - the mergeable table bytes (gzipped) for table attachments.

Optional richer datasource capabilities (if present) may include:
  - get_primary_asset_url(identifier)
  - get_thumbnail_url(identifier)
  - get_title(identifier)

The renderer never performs I/O; it only calls this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class NoteDataSource(Protocol):
    """Minimal attachment datasource required by the renderer."""

    def get_attachment_uti(self, identifier: str) -> str | None: ...

    def get_mergeable_gz(self, identifier: str) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Lightweight reference created while walking AttributeRuns."""

    identifier: str | None = None
    uti_hint: str | None = None

    def resolved_uti(self, datasource: NoteDataSource | None) -> str | None:
        if self.uti_hint:
            return self.uti_hint
        if datasource and self.identifier:
            return datasource.get_attachment_uti(self.identifier)
        return None
