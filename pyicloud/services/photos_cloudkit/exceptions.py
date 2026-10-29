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


class PhotosAuthError(PhotosServiceException):
    """Auth/PCS/cookie issues (401/403) reaching the Photos container."""


class PhotosRateLimited(PhotosServiceException):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PhotosApiError(PhotosServiceException):
    """Catch-all Photos API/transport error."""

    def __init__(self, message: str, payload: object | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class PhotosZoneNotFoundError(PhotosApiError):
    """The Photos zone does not exist for this account.

    Apple answers ``ZONE_NOT_FOUND`` when the requested zone is missing. The
    Photos client surfaces it like every other service's client so callers can
    report "Photos not available" instead of a generic failure. It derives from
    ``PhotosApiError`` (and therefore from ``PhotosServiceException``), so
    existing handlers keep catching it.
    """

    def __init__(
        self,
        message: str,
        payload: object | None = None,
        *,
        zone_name: str | None = None,
    ) -> None:
        super().__init__(message, payload)
        self.zone_name = zone_name
