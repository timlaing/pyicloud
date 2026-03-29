from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from pyicloud.common.models import FrozenServiceModel


class AttachmentId(FrozenServiceModel):
    identifier: str
    type_uti: Optional[str] = None


class NoteBody(FrozenServiceModel):
    bytes: bytes
    text: Optional[str] = None
    attachment_ids: List[AttachmentId] = Field(default_factory=list)
