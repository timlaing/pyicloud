"""Domain models for note attachments and body content."""

from __future__ import annotations

from pydantic import Field

from pyicloud.common.models import FrozenServiceModel


class AttachmentId(FrozenServiceModel):
    identifier: str
    type_uti: str | None = None


class NoteBody(FrozenServiceModel):
    bytes: bytes
    text: str | None = None
    attachment_ids: list[AttachmentId] = Field(default_factory=list)
