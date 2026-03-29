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
from typing import Optional, Protocol


class NoteDataSource(Protocol):
    """Minimal attachment datasource required by the renderer."""

    def get_attachment_uti(self, identifier: str) -> Optional[str]: ...

    def get_mergeable_gz(self, identifier: str) -> Optional[bytes]: ...


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Lightweight reference created while walking AttributeRuns."""

    identifier: Optional[str] = None
    uti_hint: Optional[str] = None

    def resolved_uti(self, datasource: Optional[NoteDataSource]) -> Optional[str]:
        if self.uti_hint:
            return self.uti_hint
        if datasource and self.identifier:
            return datasource.get_attachment_uti(self.identifier)
        return None
