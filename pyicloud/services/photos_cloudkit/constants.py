"""Constants and enums for the modern Photos CloudKit service."""

from __future__ import annotations

from enum import Enum, IntEnum, unique


@unique
class AlbumTypeEnum(IntEnum):
    """Album types used by CloudKit Photos records."""

    ALBUM = 0
    FOLDER = 3
    SMART_ALBUM = 6


class SmartAlbumEnum(str, Enum):
    """Well-known Photos smart album names."""

    ALL_PHOTOS = "Library"
    BURSTS = "Bursts"
    FAVORITES = "Favorites"
    HIDDEN = "Hidden"
    LIVE = "Live"
    PANORAMAS = "Panoramas"
    RECENTLY_DELETED = "Recently Deleted"
    SCREENSHOTS = "Screenshots"
    SLO_MO = "Slo-mo"
    TIME_LAPSE = "Time-lapse"
    VIDEOS = "Videos"


class DirectionEnum(str, Enum):
    """Direction values accepted by Photos CloudKit indexes."""

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class ListTypeEnum(str, Enum):
    """Photos list/index record types."""

    DEFAULT = "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted"
    ADDED = "CPLAssetAndMasterByAddedDate"
    DELETED = "CPLAssetAndMasterDeletedByExpungedDate"
    HIDDEN = "CPLAssetAndMasterHiddenByAssetDate"
    SMART_ALBUM = "CPLAssetAndMasterInSmartAlbumByAssetDate"
    STACK = "CPLBurstStackAssetAndMasterByAssetDate"
    CONTAINER = "CPLContainerRelationLiveByAssetDate"
    CONTAINER_ASSET_DATE = "CPLContainerRelationLiveByAssetDate"
    CONTAINER_POSITION = "CPLContainerRelationLiveByPosition"
    SHARED_STREAM = "sharedstream"


class ObjectTypeEnum(str, Enum):
    """Logical album/object index identifiers."""

    ALL = "CPLAssetByAssetDateWithoutHiddenOrDeleted"
    BURST = "CPLAssetBurstStackAssetByAssetDate"
    DELETED = "CPLAssetDeletedByExpungedDate"
    FAVORITE = "CPLAssetInSmartAlbumByAssetDate:Favorite"
    HIDDEN = "CPLAssetHiddenByAssetDate"
    LIVE = "CPLAssetInSmartAlbumByAssetDate:Live"
    PANORAMA = "CPLAssetInSmartAlbumByAssetDate:Panorama"
    SCREENSHOT = "CPLAssetInSmartAlbumByAssetDate:Screenshot"
    SLOMO = "CPLAssetInSmartAlbumByAssetDate:Slomo"
    TIMELAPSE = "CPLAssetInSmartAlbumByAssetDate:Timelapse"
    VIDEO = "CPLAssetInSmartAlbumByAssetDate:Video"
    CONTAINER = "CPLContainerRelationNotDeletedByAssetDate"


PRIMARY_ZONE: dict[str, str] = {
    "zoneName": "PrimarySync",
    "zoneType": "REGULAR_CUSTOM_ZONE",
}
