"""Typed domain models for the modern Photos CloudKit service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pyicloud.exceptions import PyiCloudException


class PhotosServiceException(PyiCloudException):
    """Photo service exception."""

    def __init__(
        self,
        *args,
        photo: "PhotoAsset | None" = None,
        album: "BasePhotoAlbum | None" = None,
    ) -> None:
        super().__init__(*args)
        self.photo = photo
        self.album = album


@dataclass(slots=True)
class PhotoResource:
    """A downloadable photo/video resource variant."""

    key: str
    filename: str
    url: Optional[str]
    size: Optional[int]
    type: Optional[str]
    checksum: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        """Return a compatibility dict for legacy callers/tests."""

        return {
            "filename": self.filename,
            "url": self.url,
            "size": self.size,
            "type": self.type,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True)
class PhotoChangeEvent:
    """A zone change event surfaced by ``icloud photos changes``."""

    kind: str
    record_name: str
    record_type: Optional[str]
    deleted: bool
    modified: Optional[datetime]


# Import-only type hints to avoid circular imports at runtime.
if False:  # pragma: no cover
    from .service import BasePhotoAlbum, PhotoAsset
