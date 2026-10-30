"""Public Photos service facade.

Private-library Photos features and the currently supported Shared Library
surface are backed by the modern CloudKit service. Shared Library coverage is
currently limited to library-scoped reads plus the safe smart albums
``Library`` and ``Favorites``. Legacy Shared Albums / shared streams remain
available through the separate shared-stream adapter while broader Shared
Library album and mixed-view coverage remains deferred.
"""

from __future__ import annotations

from .constants import (
    PRIMARY_ZONE,
    UPLOAD_HYDRATION_INTERVAL,
    UPLOAD_HYDRATION_TIMEOUT,
    AlbumTypeEnum,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    SmartAlbumEnum,
)
from .exceptions import (
    PhotosApiError,
    PhotosAuthError,
    PhotosRateLimited,
    PhotosServiceException,
    PhotosZoneNotFoundError,
)
from .mappers import record_change_tag
from .models import (
    PhotoChangeEvent,
    PhotoResource,
    PhotosPutAssetResult,
    PhotosUploadResponse,
    SmartAlbumSpec,
)
from .service import (
    AlbumContainer,
    BasePhotoAlbum,
    BasePhotoLibrary,
    PhotoAlbum,
    PhotoAlbumFolder,
    PhotoAlbumRecordState,
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    SmartPhotoAlbum,
)
from .shared_streams import (
    PhotoStreamAsset,
    PhotoStreamLibrary,
    SharedPhotoStreamAlbum,
)
from .state import (
    PhotoSyncState,
    SQLitePhotoSyncState,
    SyncedPhotoResource,
    create_photo_sync_state,
)
from .sync import (
    PhotoSyncItem,
    PhotoSyncOptions,
    PhotoSyncResult,
    run_photo_sync,
    watch_photo_sync,
)

__all__ = [
    "PRIMARY_ZONE",
    "UPLOAD_HYDRATION_INTERVAL",
    "UPLOAD_HYDRATION_TIMEOUT",
    "AlbumContainer",
    "AlbumTypeEnum",
    "BasePhotoAlbum",
    "BasePhotoLibrary",
    "DirectionEnum",
    "ListTypeEnum",
    "ObjectTypeEnum",
    "PhotoAlbum",
    "PhotoAlbumFolder",
    "PhotoAlbumRecordState",
    "PhotoAsset",
    "PhotoChangeEvent",
    "PhotoLibrary",
    "PhotoResource",
    "PhotoStreamAsset",
    "PhotoStreamLibrary",
    "PhotoSyncItem",
    "PhotoSyncOptions",
    "PhotoSyncResult",
    "PhotoSyncState",
    "PhotosApiError",
    "PhotosAuthError",
    "PhotosPutAssetResult",
    "PhotosRateLimited",
    "PhotosService",
    "PhotosServiceException",
    "PhotosUploadResponse",
    "PhotosZoneNotFoundError",
    "SQLitePhotoSyncState",
    "SharedPhotoStreamAlbum",
    "SmartAlbumEnum",
    "SmartAlbumSpec",
    "SmartPhotoAlbum",
    "SyncedPhotoResource",
    "create_photo_sync_state",
    "record_change_tag",
    "run_photo_sync",
    "watch_photo_sync",
]
