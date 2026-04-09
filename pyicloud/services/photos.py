"""Public Photos service facade.

Private-library Photos features and the currently supported Shared Library
surface are backed by the modern CloudKit service. Shared Library coverage is
currently limited to library-scoped reads plus the safe smart albums
``Library`` and ``Favorites``. Legacy Shared Albums / shared streams remain
available through the separate shared-stream adapter while broader Shared
Library album and mixed-view coverage remains deferred.
"""

from __future__ import annotations

from pyicloud.services.photos_cloudkit import (
    PRIMARY_ZONE,
    AlbumContainer,
    AlbumTypeEnum,
    BasePhotoAlbum,
    BasePhotoLibrary,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    PhotoAlbum,
    PhotoAlbumFolder,
    PhotoAsset,
    PhotoChangeEvent,
    PhotoLibrary,
    PhotoResource,
    PhotosService,
    PhotosServiceException,
    PhotoSyncItem,
    PhotoSyncOptions,
    PhotoSyncResult,
    PhotoSyncState,
    SmartAlbumEnum,
    SmartPhotoAlbum,
    SQLitePhotoSyncState,
    SyncedPhotoResource,
    create_photo_sync_state,
    run_photo_sync,
    watch_photo_sync,
)
from pyicloud.services.photos_legacy import (
    PhotoStreamAsset,
    PhotoStreamLibrary,
    SharedPhotoStreamAlbum,
)

__all__ = [
    "AlbumContainer",
    "AlbumTypeEnum",
    "BasePhotoAlbum",
    "BasePhotoLibrary",
    "DirectionEnum",
    "ListTypeEnum",
    "ObjectTypeEnum",
    "PhotoAlbum",
    "PhotoAlbumFolder",
    "PhotoAsset",
    "PhotoChangeEvent",
    "PhotoLibrary",
    "PhotoSyncItem",
    "PhotoSyncOptions",
    "PhotoSyncResult",
    "PhotoSyncState",
    "PhotoResource",
    "PhotoStreamAsset",
    "PhotoStreamLibrary",
    "PhotosService",
    "PhotosServiceException",
    "PRIMARY_ZONE",
    "SQLitePhotoSyncState",
    "SharedPhotoStreamAlbum",
    "SmartAlbumEnum",
    "SmartPhotoAlbum",
    "SyncedPhotoResource",
    "create_photo_sync_state",
    "run_photo_sync",
    "watch_photo_sync",
]
