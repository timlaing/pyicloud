"""Exceptions raised by the Photos CloudKit service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyicloud.exceptions import PyiCloudException

if TYPE_CHECKING:
    from .service import BasePhotoAlbum, PhotoAsset


class PhotosServiceException(PyiCloudException):
    """Photo service exception."""

    def __init__(
        self,
        *args: Any,
        photo: PhotoAsset | None = None,
        album: BasePhotoAlbum | None = None,
    ) -> None:
        super().__init__(*args)
        self.photo = photo
        self.album = album
