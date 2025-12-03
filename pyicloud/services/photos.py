"""Photo service."""

import base64
import logging
import os
from abc import abstractmethod
from datetime import datetime, timezone
from enum import Enum, IntEnum, unique
from typing import Any, Generator, Iterable, Iterator, Optional, cast
from urllib.parse import urlencode

from requests import Response

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession

_LOGGER: logging.Logger = logging.getLogger(__name__)


class PhotosServiceException(PyiCloudException):
    """Photo service exception."""

    def __init__(
        self,
        *args,
        photo: "PhotoAsset|None" = None,
        album: "BasePhotoAlbum|None" = None,
    ) -> None:
        super().__init__(*args)
        self.photo: "PhotoAsset|None" = photo
        self.album: "BasePhotoAlbum|None" = album


@unique
class AlbumTypeEnum(IntEnum):
    """Album types"""

    ALBUM = 0
    FOLDER = 3
    SMART_ALBUM = 6


class SmartAlbumEnum(str, Enum):
    """Smart albums names."""

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
    """Direction names."""

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class ListTypeEnum(str, Enum):
    """List type names."""

    DEFAULT = "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted"
    DELETED = "CPLAssetAndMasterDeletedByExpungedDate"
    HIDDEN = "CPLAssetAndMasterHiddenByAssetDate"
    SMART_ALBUM = "CPLAssetAndMasterInSmartAlbumByAssetDate"
    STACK = "CPLBurstStackAssetAndMasterByAssetDate"
    CONTAINER = "CPLContainerRelationLiveByAssetDate"
    SHARED_STREAM = "sharedstream"


class ObjectTypeEnum(str, Enum):
    """Object type names."""

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


# The primary zone for the user's photo library
PRIMARY_ZONE: dict[str, str] = {
    "zoneName": "PrimarySync",
    "zoneType": "REGULAR_CUSTOM_ZONE",
}


class AlbumContainer(Iterable):
    """
    Container for photo albums.
    This provides a way to access all the albums in the library.
    """

    def __init__(self, albums: list["BasePhotoAlbum"] | None = None) -> None:
        if albums is not None:
            self._albums: dict[str, "BasePhotoAlbum"] = {
                album.id: album for album in albums
            }
        else:
            self._albums = {}

        self._index: list[str] = list(self._albums.keys())

    def __len__(self) -> int:
        return len(self._albums)

    def __getitem__(self, key: str | int) -> "BasePhotoAlbum":
        """Returns the album for the given id."""
        if isinstance(key, int):
            return self._albums[self._index[key]]
        if key in self._albums:
            return self._albums[key]
        album: BasePhotoAlbum | None = self.find(key)
        if album is not None:
            return album
        raise KeyError(f"Photo album does not exist: {key}")

    def __iter__(self) -> Iterator["BasePhotoAlbum"]:
        return iter(self._albums.values())

    def __contains__(self, name: str) -> bool:
        """Checks if an album exists in the container."""
        return self.find(name) is not None

    def find(self, name: str) -> Optional["BasePhotoAlbum"]:
        """Finds an album by name, returns None if not found."""
        for album in self._albums.values():
            if name == album.fullname:
                return album
        return None

    def get(
        self, key: str, default: "BasePhotoAlbum | None" = None
    ) -> "BasePhotoAlbum | None":
        """Returns the album for the given key, or default if not found."""
        return self._albums.get(key, default)

    def append(self, album: "BasePhotoAlbum") -> None:
        """Appends an album to the container."""
        self._albums[album.id] = album
        self._index: list[str] = list(self._albums.keys())

    def index(self, idx: int) -> "BasePhotoAlbum":
        """Returns the album at the given index."""
        if idx < 0 or idx >= len(self._index):
            raise IndexError("Photo album index out of range")
        return self._albums[self._index[idx]]


class BasePhotoLibrary:
    """Represents a library in the user's photos.

    This provides access to all the albums as well as the photos.
    """

    def __init__(
        self,
        service: "PhotosService",
        asset_type: type["PhotoAsset"],
        upload_url: Optional[str] = None,
    ) -> None:
        self.service: PhotosService = service
        self.asset_type: type[PhotoAsset] = asset_type
        self._albums: Optional[AlbumContainer] = None
        self._upload_url: Optional[str] = upload_url

    @abstractmethod
    def _get_albums(self) -> AlbumContainer:
        """Returns the photo albums."""
        raise NotImplementedError

    @property
    def albums(self) -> AlbumContainer:
        """Returns the photo albums."""
        if self._albums is None:
            self._albums = self._get_albums()
        return self._albums

    def parse_asset_response(
        self, response: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Parses the asset response."""
        asset_records: dict[str, dict[str, Any]] = {}
        master_records: list[dict[str, Any]] = []
        for rec in response["records"]:
            if rec["recordType"] == "CPLAsset":
                master_id: str = rec["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_id] = rec
            elif rec["recordType"] == "CPLMaster":
                master_records.append(rec)
        return (asset_records, master_records)


class PhotoLibrary(BasePhotoLibrary):
    """Represents the user's primary photo libraries."""

    SMART_ALBUMS: dict[SmartAlbumEnum, dict[str, Any]] = {
        SmartAlbumEnum.ALL_PHOTOS: {
            "obj_type": ObjectTypeEnum.ALL,
            "list_type": ListTypeEnum.DEFAULT,
            "direction": DirectionEnum.DESCENDING,
            "query_filter": None,
        },
        SmartAlbumEnum.TIME_LAPSE: {
            "obj_type": ObjectTypeEnum.TIMELAPSE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "TIMELAPSE"},
                }
            ],
        },
        SmartAlbumEnum.VIDEOS: {
            "obj_type": ObjectTypeEnum.VIDEO,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "VIDEO"},
                }
            ],
        },
        SmartAlbumEnum.SLO_MO: {
            "obj_type": ObjectTypeEnum.SLOMO,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SLOMO"},
                }
            ],
        },
        SmartAlbumEnum.BURSTS: {
            "obj_type": ObjectTypeEnum.BURST,
            "list_type": ListTypeEnum.STACK,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
        SmartAlbumEnum.FAVORITES: {
            "obj_type": ObjectTypeEnum.FAVORITE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "FAVORITE"},
                }
            ],
        },
        SmartAlbumEnum.PANORAMAS: {
            "obj_type": ObjectTypeEnum.PANORAMA,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "PANORAMA"},
                }
            ],
        },
        SmartAlbumEnum.SCREENSHOTS: {
            "obj_type": ObjectTypeEnum.SCREENSHOT,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SCREENSHOT"},
                }
            ],
        },
        SmartAlbumEnum.LIVE: {
            "obj_type": ObjectTypeEnum.LIVE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "LIVE"},
                }
            ],
        },
        SmartAlbumEnum.RECENTLY_DELETED: {
            "obj_type": ObjectTypeEnum.DELETED,
            "list_type": ListTypeEnum.DELETED,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
        SmartAlbumEnum.HIDDEN: {
            "obj_type": ObjectTypeEnum.HIDDEN,
            "list_type": ListTypeEnum.HIDDEN,
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
    }

    def __init__(
        self,
        service: "PhotosService",
        zone_id: dict[str, str],
        upload_url: Optional[str] = None,
    ) -> None:
        super().__init__(service, asset_type=PhotoAsset, upload_url=upload_url)
        self.zone_id: dict[str, str] = zone_id

        self.url: str = (
            f"{self.service.service_endpoint}"
            f"/records/query?{urlencode(self.service.params)}"
        )
        request: Response = self.service.session.post(
            url=self.url,
            json={
                "query": {
                    "recordType": "CheckIndexingState",
                },
                "zoneID": self.zone_id,
            },
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()
        indexing_state: str = response["records"][0]["fields"]["state"]["value"]
        if indexing_state != "FINISHED":
            _LOGGER.debug("iCloud Photo Library not finished indexing")
            raise PyiCloudServiceNotActivatedException(
                "iCloud Photo Library not finished indexing. "
                "Please try again in a few minutes."
            )

    def _fetch_records(self, parent_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetches records."""
        query: dict[str, Any] = {
            "query": {
                "recordType": "CPLAlbumByPositionLive",
            },
            "zoneID": self.zone_id,
        }

        if parent_id:
            query["query"]["filterBy"] = [
                {
                    "fieldName": "parentId",
                    "comparator": "EQUALS",
                    "fieldValue": {
                        "value": parent_id,
                        "type": "STRING",
                    },
                }
            ]

        request: Response = self.service.session.post(
            url=self.url,
            json=query,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, list[dict[str, Any]]] = request.json()
        records: list[dict[str, Any]] = response["records"]

        while "continuationMarker" in response:
            query["continuationMarker"] = response["continuationMarker"]

            request: Response = self.service.session.post(
                url=self.url,
                json=query,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response = request.json()
            records.extend(response["records"])

        for record in records.copy():
            if (
                record["fields"].get("albumType")
                and record["fields"]["albumType"]["value"] == AlbumTypeEnum.FOLDER.value
            ):
                records.extend(self._fetch_records(parent_id=record["recordName"]))

        return records

    def _convert_record_to_album(
        self, record: dict[str, Any]
    ) -> Optional["PhotoAlbum"]:
        """Converts a record to a photo album."""
        if (
            # Skipping albums having null name, that can happen sometime
            "albumNameEnc" not in record["fields"]
            or (
                record["fields"].get("isDeleted")
                and record["fields"]["isDeleted"]["value"]
            )
        ):
            return None

        record_id: str = record["recordName"]
        album_name: str = base64.b64decode(
            record["fields"]["albumNameEnc"]["value"]
        ).decode("utf-8")

        query_filter: list[dict[str, Any]] = [
            {
                "fieldName": "parentId",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": record_id},
            }
        ]

        parent_id: Optional[str] = record["fields"].get("parentId", {}).get("value")

        album_type: type[PhotoAlbum] = PhotoAlbum

        if (
            record["fields"].get("albumType")
            and record["fields"]["albumType"]["value"] == AlbumTypeEnum.FOLDER.value
        ):
            album_type = PhotoAlbumFolder

        direction: DirectionEnum = DirectionEnum.ASCENDING
        if record["fields"].get("sortAscending", {}).get("value", 1) != 1:
            direction = DirectionEnum.DESCENDING

        record_modification_date = (
            record["fields"].get("recordModificationDate", {}).get("value", None)
        )

        return album_type(
            library=self,
            name=album_name,
            record_id=record_id,
            list_type=ListTypeEnum.CONTAINER,
            obj_type=ObjectTypeEnum.CONTAINER,
            direction=direction,
            url=self.url,
            query_filter=query_filter,
            zone_id=record.get("zoneID", self.zone_id),
            parent_id=parent_id,
            record_change_tag=record["recordChangeTag"],
            record_modification_date=record_modification_date,
        )

    def _get_albums(self) -> AlbumContainer:
        """Returns photo albums."""
        albums = AlbumContainer(
            [
                SmartPhotoAlbum(
                    library=self,
                    name=name,
                    zone_id=self.zone_id,
                    url=self.url,
                    **props,
                )
                for (name, props) in self.SMART_ALBUMS.items()
            ]
        )

        for record in self._fetch_records():
            album: PhotoAlbum | None = self._convert_record_to_album(record)
            if album is not None:
                albums.append(album)

        return albums

    def create_album(
        self, name: str, album_type: AlbumTypeEnum = AlbumTypeEnum.ALBUM
    ) -> Optional["PhotoAlbum"]:
        """Creates a new album, returns the request response."""
        data: dict[str, Any] = {
            "operations": [
                {
                    "operationType": "create",
                    "record": {
                        "recordType": "CPLAlbum",
                        "fields": {
                            "albumNameEnc": {
                                "value": base64.b64encode(name.encode("utf-8")).decode(
                                    "utf-8"
                                ),
                            },
                            "albumType": {
                                "value": album_type.value,
                            },
                            "isDeleted": {
                                "value": 0,
                            },
                            "isExpunged": {
                                "value": 0,
                            },
                            "sortType": {
                                "value": 1,
                            },
                            "sortAscending": {
                                "value": 1,
                            },
                        },
                    },
                }
            ],
            "zoneID": self.zone_id,
            "atomic": True,
        }

        endpoint: str = self.service.service_endpoint
        params: str = urlencode(self.service.params)
        url: str = f"{endpoint}/records/modify?{params}"

        try:
            resp: Response = self.service.session.post(
                url,
                json=data,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )

            payload: dict[str, Any] = resp.json()
            records: list[dict[str, Any]] = payload.get("records", [])
            if not records:
                return None
        except PyiCloudAPIResponseException as ex:
            _LOGGER.error("Failed to create album: %s", ex)
            raise PhotosServiceException("Failed to create album") from ex

        return self._convert_record_to_album(records[0])

    def upload_file(self, path: str) -> Optional["PhotoAsset"]:
        """Upload a photo from path, returns a recordName"""

        filename: str = os.path.basename(path)

        params: dict[str, Any] = self.service.params.copy()
        params["filename"] = filename

        url: str = f"{self._upload_url}/upload?{urlencode(params)}"

        with open(path, "rb") as file_obj:
            response: Response = self.service.session.post(
                url=url,
                data=file_obj,
            )

        json_response: dict[str, Any] = response.json()
        if "errors" in json_response:
            raise PyiCloudAPIResponseException("", json_response["errors"])

        records: dict[Any, dict[str, Any]] = {
            rec["recordType"]: rec for rec in json_response["records"]
        }

        if "CPLMaster" not in records or "CPLAsset" not in records:
            return None

        return self.asset_type(self.service, records["CPLMaster"], records["CPLAsset"])

    @property
    def all(self) -> "PhotoAlbum":
        """Returns the All Photos album."""
        return cast(PhotoAlbum, self.albums[SmartAlbumEnum.ALL_PHOTOS])


class PhotoStreamLibrary(BasePhotoLibrary):
    """Represents a shared photo library."""

    def __init__(
        self,
        service: "PhotosService",
        shared_streams_url: str,
    ) -> None:
        super().__init__(service, asset_type=PhotoStreamAsset, upload_url=None)
        self.shared_streams_url: str = shared_streams_url

    def _get_albums(self) -> AlbumContainer:
        """Returns albums."""
        albums: AlbumContainer = AlbumContainer()
        url: str = f"{self.shared_streams_url}?{urlencode(self.service.params)}"
        request: Response = self.service.session.post(
            url, json={}, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response: dict[str, list] = request.json()
        for album in response["albums"]:
            shared_stream = SharedPhotoStreamAlbum(
                library=self,
                name=album["attributes"]["name"],
                album_location=album["albumlocation"],
                album_ctag=album["albumctag"],
                album_guid=album["albumguid"],
                owner_dsid=album["ownerdsid"],
                creation_date=album["attributes"]["creationDate"],
                sharing_type=album["sharingtype"],
                allow_contributions=album["attributes"]["allowcontributions"],
                is_public=album["attributes"]["ispublic"],
                is_web_upload_supported=album["iswebuploadsupported"],
                public_url=album.get("publicurl", None),
            )
            albums.append(shared_stream)
        return albums


class PhotosService(BaseService):
    """The 'Photos' iCloud service.

    This also acts as a way to access the user's primary library."""

    def __init__(
        self,
        service_root: str,
        session: PyiCloudSession,
        params: dict[str, Any],
        upload_url: str,
        shared_streams_url: str,
    ) -> None:
        BaseService.__init__(
            self,
            service_root=service_root,
            session=session,
            params=params,
        )
        self.service_endpoint: str = (
            f"{self.service_root}/database/1/com.apple.photos.cloud/production/private"
        )

        self._libraries: Optional[dict[str, BasePhotoLibrary]] = None

        self.params.update({"remapEnums": True, "getCurrentSyncToken": True})
        self._photo_assets: dict = {}

        self._root_library: PhotoLibrary = PhotoLibrary(
            self,
            PRIMARY_ZONE,
            upload_url=upload_url,
        )

        self._shared_library: PhotoStreamLibrary = PhotoStreamLibrary(
            self,
            shared_streams_url=(
                f"{shared_streams_url}/{self.params['dsid']}"
                "/sharedstreams/webgetalbumslist"
            ),
        )

    @property
    def libraries(self) -> dict[str, BasePhotoLibrary]:
        """Returns photo libraries."""
        if not self._libraries:
            url: str = f"{self.service_endpoint}/changes/database"

            request: Response = self.session.post(
                url, data="{}", headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
            )
            response: dict[str, Any] = request.json()
            zones: list[dict[str, Any]] = response["zones"]

            libraries: dict[str, BasePhotoLibrary] = {
                "root": self._root_library,
                "shared": self._shared_library,
            }
            for zone in zones:
                if not zone.get("deleted"):
                    zone_name: str = zone["zoneID"]["zoneName"]
                    libraries[zone_name] = PhotoLibrary(self, zone["zoneID"])

            self._libraries = libraries

        return self._libraries

    @property
    def all(self) -> "PhotoAlbum":
        """Returns the primary photo library."""
        return self._root_library.all

    @property
    def albums(self) -> AlbumContainer:
        """Returns the standard photo albums."""
        return self._root_library.albums

    @property
    def shared_streams(self) -> AlbumContainer:
        """Returns the shared photo albums."""
        return self._shared_library.albums

    def create_album(
        self, name: str, album_type: AlbumTypeEnum = AlbumTypeEnum.ALBUM
    ) -> Optional["PhotoAlbum"]:
        """Creates a new album in the primary photo library."""
        return self._root_library.create_album(name, album_type)


class BasePhotoAlbum:
    """An abstract photo album."""

    def __init__(
        self,
        library: BasePhotoLibrary,
        name: str,
        list_type: ListTypeEnum,
        page_size: int = 100,
        direction: DirectionEnum = DirectionEnum.ASCENDING,
    ) -> None:
        self._name: str = name
        self._library: BasePhotoLibrary = library
        self._page_size: int = page_size
        self._direction: DirectionEnum = direction
        self._list_type: ListTypeEnum = list_type
        self._len: Optional[int] = None

    @property
    @abstractmethod
    def fullname(self) -> str:
        """Gets the full name of the album including path"""
        raise NotImplementedError

    @property
    def page_size(self) -> int:
        """Gets the page size."""
        return self._page_size if self._page_size < 100 else 100

    @property
    def service(self) -> PhotosService:
        """Get the Photo service"""
        return self._library.service

    def _get_photos_at(
        self, index: int, direction: DirectionEnum, page_size: int
    ) -> Generator["PhotoAsset", None, None]:
        offset: int = max(0, index)

        response: Response = self.service.session.post(
            url=self._get_url(),
            json=self._get_payload(
                offset=offset,
                page_size=page_size
                * 2,  # Fetch double the page size to cater for master and asset records
                direction=direction,
            ),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        json_response: dict[str, list[dict[str, Any]]] = response.json()
        asset_records: dict[str, Any]
        master_records: list[dict[str, Any]]
        asset_records, master_records = self._library.parse_asset_response(
            json_response
        )
        for master_record in master_records:
            record_name: str = master_record["recordName"]
            asset_record = asset_records.get(record_name)
            if not asset_record:
                _LOGGER.debug(
                    "No asset record found for master record: %s", record_name
                )
                continue
            yield self._library.asset_type(self.service, master_record, asset_record)

    def photo(self, index) -> "PhotoAsset":
        """Returns a photo at the given index."""
        return next(self._get_photos_at(index, self._direction, 1))

    @property
    def title(self) -> str:
        """Gets the album title."""
        return self.name

    @property
    def name(self) -> str:
        """Gets the album name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        """Sets the album name."""
        if self._name != value:
            self.rename(value)

    def rename(self, value: str) -> None:
        """Renames the album."""
        raise NotImplementedError("Album name is read-only")

    def delete(self) -> bool:
        """Deletes the album."""
        raise NotImplementedError("Album delete is not implemented")

    @property
    def photos(self) -> Generator["PhotoAsset", None, None]:
        """Returns the album photos."""
        self._len = None
        if self._direction == DirectionEnum.DESCENDING:
            offset: int = len(self) - 1
        else:
            offset = 0

        photos_ids: set[str] = set()

        while True:
            num_results = 0
            for photo in self._get_photos_at(offset, self._direction, self.page_size):
                num_results += 1
                if photo.id in photos_ids:
                    _LOGGER.debug("Duplicate photo found: %s, skipping", photo.id)
                    continue
                photos_ids.add(photo.id)
                yield photo
            if num_results < self.page_size:
                _LOGGER.debug("Less than page size returned: %d", num_results)
            if (
                num_results < self.page_size // 2
            ):  # If less than half the page size is returned, we assume we're done
                break
            if self._direction == DirectionEnum.DESCENDING:
                offset = offset - num_results
            else:
                offset = offset + num_results

    @property
    @abstractmethod
    def id(self) -> str:
        """Gets the album id."""
        raise NotImplementedError

    @abstractmethod
    def _get_payload(
        self, offset: int, page_size: int, direction: DirectionEnum
    ) -> dict[str, Any]:
        """Returns the payload for the photo list request."""
        raise NotImplementedError

    @abstractmethod
    def _get_url(self) -> str:
        """Returns the URL for the photo list request."""
        raise NotImplementedError

    @abstractmethod
    def _get_len(self) -> int:
        """Returns the number of photos in the album."""
        raise NotImplementedError

    def __iter__(self) -> Generator["PhotoAsset", None, None]:
        return self.photos

    def __len__(self) -> int:
        if self._len is None:
            self._len = self._get_len()
        return self._len

    def __str__(self) -> str:
        return self.title

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: '{self}'>"


class PhotoAlbum(BasePhotoAlbum):
    """A photo album."""

    def __init__(
        self,
        library: PhotoLibrary,
        name: str,
        record_id: str,
        obj_type: ObjectTypeEnum,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        url: str,
        query_filter: Optional[list[dict[str, Any]]] = None,
        zone_id: Optional[dict[str, str]] = None,
        page_size: int = 100,
        parent_id: Optional[str] = None,
        record_change_tag: Optional[str] = None,
        record_modification_date: Optional[str] = None,
    ) -> None:
        super().__init__(
            library=library,
            name=name,
            list_type=list_type,
            page_size=page_size,
            direction=direction,
        )

        self._record_id: str = record_id
        self._obj_type: ObjectTypeEnum = obj_type
        self._query_filter: Optional[list[dict[str, Any]]] = query_filter
        self._url: str = url
        self._parent_id: Optional[str] = parent_id
        self._record_change_tag: Optional[str] = record_change_tag
        self._record_modification_date: Optional[str] = record_modification_date

        if zone_id:
            self._zone_id: dict[str, str] = zone_id
        else:
            self._zone_id = PRIMARY_ZONE

    @property
    def id(self) -> str:
        """Gets the album id."""
        return self._record_id

    @property
    def fullname(self) -> str:
        if self._parent_id is not None:
            return f"{self._library.albums[self._parent_id].fullname}/{self.name}"

        return self.name

    def rename(self, value: str) -> None:
        """Renames the album."""
        if self._name == value:
            return

        data: dict[str, Any] = {
            "atomic": True,
            "zoneID": self._zone_id,
            "operations": [
                {
                    "operationType": "update",
                    "record": {
                        "recordName": self._record_id,
                        "recordType": "CPLAlbum",
                        "recordChangeTag": self._record_change_tag,
                        "fields": {
                            "albumNameEnc": {
                                "value": base64.b64encode(value.encode("utf-8")).decode(
                                    "utf-8"
                                ),
                            },
                        },
                    },
                }
            ],
        }
        url: str = (
            f"{self.service.service_endpoint}/records/modify"
            f"?{urlencode(self.service.params)}"
        )

        response: Response = self.service.session.post(
            url,
            json=data,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        payload: dict[str, Any] = response.json()
        if payload.get("records"):
            latest: dict[str, Any] = payload["records"][0]
            self._record_change_tag = latest.get(
                "recordChangeTag", self._record_change_tag
            )
            fields: dict[str, Any] = latest.get("fields", {})
            self._record_modification_date = fields.get(
                "recordModificationDate", {}
            ).get("value", self._record_modification_date)

        self._name = value

    def delete(self) -> bool:
        """Deletes the album."""
        data: dict[str, Any] = {
            "atomic": True,
            "zoneID": self._zone_id,
            "operations": [
                {
                    "operationType": "update",
                    "record": {
                        "recordName": self._record_id,
                        "recordChangeTag": self._record_change_tag,
                        "recordType": "CPLAlbum",
                        "fields": {
                            "isDeleted": {"value": 1},
                        },
                    },
                }
            ],
        }
        url: str = (
            f"{self.service.service_endpoint}/records/modify"
            f"?{urlencode(self.service.params)}"
        )

        try:
            response: Response = self.service.session.post(
                url,
                json=data,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            payload: dict[str, Any] = response.json()
            self._record_change_tag = payload["records"][0].get(
                "recordChangeTag", self._record_change_tag
            )
            self._record_modification_date = (
                payload["records"][0]
                .get("fields", {})
                .get("recordModificationDate", {})
                .get("value", self._record_modification_date)
            )
        except PyiCloudAPIResponseException as ex:
            _LOGGER.error("Failed to delete photo from album: %s", ex)
            raise PhotosServiceException(
                "Failed to delete photo from album", album=self
            ) from ex

        return True

    def add_photo(self, photo: "PhotoAsset") -> bool:
        """Adds an existing photo to the album."""

        data: dict[str, Any] = {
            "atomic": True,
            "zoneID": self._zone_id,
            "operations": [
                {
                    "operationType": "create",
                    "record": {
                        "fields": {
                            "itemId": {"value": photo.id},
                            "position": {"value": 1024},
                            "containerId": {"value": self._record_id},
                        },
                        "recordType": "CPLContainerRelation",
                        "recordName": f"{photo.id}-IN-{self._record_id}",
                    },
                }
            ],
        }
        url: str = (
            f"{self.service.service_endpoint}/records/modify"
            f"?{urlencode(self.service.params)}"
        )

        try:
            response: Response = self.service.session.post(
                url,
                json=data,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )

            payload: dict[str, Any] = response.json()
            self._record_change_tag = payload["records"][0].get(
                "recordChangeTag", self._record_change_tag
            )
            self._record_modification_date = (
                payload["records"][0]
                .get("fields", {})
                .get("recordModificationDate", {})
                .get("value", self._record_modification_date)
            )
        except PyiCloudAPIResponseException as ex:
            _LOGGER.error("Failed to add photo to album: %s", ex)
            return False

        return True

    def upload(self, path) -> Optional["PhotoAsset"]:
        """Uploads a photo to the album."""
        if not isinstance(self._library, PhotoLibrary):
            return None
        photo_asset: PhotoAsset | None = self._library.upload_file(path)

        if photo_asset is None:
            return None

        if not self.add_photo(photo_asset):
            _LOGGER.error("Failed to add photo to album")
            raise PhotosServiceException(
                "Failed to add photo to album",
                album=self,
                photo=photo_asset,
            )

        return photo_asset

    @property
    def _get_container_id(self) -> str:
        """Returns the container ID."""
        return f"{self._obj_type.value}:{self._record_id}"

    def _get_len(self) -> int:
        url: str = (
            f"{self.service.service_endpoint}/internal/records/query/batch"
            f"?{urlencode(self.service.params)}"
        )
        request: Response = self.service.session.post(
            url,
            json={
                "batch": [
                    {
                        "resultsLimit": 1,
                        "query": {
                            "recordType": "HyperionIndexCountLookup",
                            "filterBy": {
                                "fieldName": "indexCountID",
                                "comparator": "IN",
                                "fieldValue": {
                                    "type": "STRING_LIST",
                                    "value": [self._get_container_id],
                                },
                            },
                        },
                        "zoneWide": True,
                        "zoneID": self._zone_id,
                    }
                ]
            },
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()

        return response["batch"][0]["records"][0]["fields"]["itemCount"]["value"]

    def _get_payload(
        self, offset: int, page_size: int, direction: DirectionEnum
    ) -> dict[str, Any]:
        return self._list_query_gen(
            offset,
            self._list_type,
            direction,
            page_size,
            self._query_filter,
        )

    def _get_url(self) -> str:
        return self._url

    def _list_query_gen(
        self,
        offset: int,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        num_results: int,
        query_filter=None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "query": {
                "recordType": list_type.value,
                "filterBy": [
                    {
                        "fieldName": "direction",
                        "comparator": "EQUALS",
                        "fieldValue": {"type": "STRING", "value": direction.value},
                    },
                    {
                        "fieldName": "startRank",
                        "comparator": "EQUALS",
                        "fieldValue": {"type": "INT64", "value": offset},
                    },
                ],
            },
            "resultsLimit": num_results,
            "desiredKeys": [
                "resJPEGFullWidth",
                "resJPEGFullHeight",
                "resJPEGFullFileType",
                "resJPEGFullFingerprint",
                "resJPEGFullRes",
                "resJPEGLargeWidth",
                "resJPEGLargeHeight",
                "resJPEGLargeFileType",
                "resJPEGLargeFingerprint",
                "resJPEGLargeRes",
                "resJPEGMedWidth",
                "resJPEGMedHeight",
                "resJPEGMedFileType",
                "resJPEGMedFingerprint",
                "resJPEGMedRes",
                "resJPEGThumbWidth",
                "resJPEGThumbHeight",
                "resJPEGThumbFileType",
                "resJPEGThumbFingerprint",
                "resJPEGThumbRes",
                "resVidFullWidth",
                "resVidFullHeight",
                "resVidFullFileType",
                "resVidFullFingerprint",
                "resVidFullRes",
                "resVidMedWidth",
                "resVidMedHeight",
                "resVidMedFileType",
                "resVidMedFingerprint",
                "resVidMedRes",
                "resVidSmallWidth",
                "resVidSmallHeight",
                "resVidSmallFileType",
                "resVidSmallFingerprint",
                "resVidSmallRes",
                "resSidecarWidth",
                "resSidecarHeight",
                "resSidecarFileType",
                "resSidecarFingerprint",
                "resSidecarRes",
                "itemType",
                "dataClassType",
                "filenameEnc",
                "originalOrientation",
                "resOriginalWidth",
                "resOriginalHeight",
                "resOriginalFileType",
                "resOriginalFingerprint",
                "resOriginalRes",
                "resOriginalAltWidth",
                "resOriginalAltHeight",
                "resOriginalAltFileType",
                "resOriginalAltFingerprint",
                "resOriginalAltRes",
                "resOriginalVidComplWidth",
                "resOriginalVidComplHeight",
                "resOriginalVidComplFileType",
                "resOriginalVidComplFingerprint",
                "resOriginalVidComplRes",
                "isDeleted",
                "isExpunged",
                "dateExpunged",
                "remappedRef",
                "recordName",
                "recordType",
                "recordChangeTag",
                "masterRef",
                "adjustmentRenderType",
                "assetDate",
                "addedDate",
                "isFavorite",
                "isHidden",
                "orientation",
                "duration",
                "assetSubtype",
                "assetSubtypeV2",
                "assetHDRType",
                "burstFlags",
                "burstFlagsExt",
                "burstId",
                "captionEnc",
                "locationEnc",
                "locationV2Enc",
                "locationLatitude",
                "locationLongitude",
                "adjustmentType",
                "timeZoneOffset",
                "vidComplDurValue",
                "vidComplDurScale",
                "vidComplDispValue",
                "vidComplDispScale",
                "vidComplVisibilityState",
                "customRenderedValue",
                "containerId",
                "itemId",
                "position",
                "isKeyAsset",
            ],
            "zoneID": self._zone_id,
        }

        if query_filter:
            query["query"]["filterBy"].extend(query_filter)

        return query


class PhotoAlbumFolder(PhotoAlbum):
    """A Photo Album Folder."""

    def upload(self, path) -> Optional["PhotoAsset"]:
        """Uploads a photo to the album."""
        # Folders do not support uploads
        return None


class SmartPhotoAlbum(PhotoAlbum):
    """A Smart Photo Album."""

    def __init__(
        self,
        library: PhotoLibrary,
        name: SmartAlbumEnum,
        obj_type: ObjectTypeEnum,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        url: str,
        query_filter: Optional[list[dict[str, Any]]] = None,
        zone_id: Optional[dict[str, str]] = None,
        page_size: int = 100,
        parent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            library=library,
            name=name.value,
            record_id=name.value,
            obj_type=obj_type,
            list_type=list_type,
            direction=direction,
            url=url,
            query_filter=query_filter,
            zone_id=zone_id,
            page_size=page_size,
            parent_id=parent_id,
        )

    @property
    def id(self) -> str:
        """Gets the album id."""
        return self.name

    def upload(self, path) -> Optional["PhotoAsset"]:
        """Uploads a photo to the album."""
        # Smart albums do not support uploads
        return None

    @property
    def fullname(self) -> str:
        """Gets the full name of the album including path"""
        return self.name

    @property
    def _get_container_id(self) -> str:
        """Gets the container ID."""
        return f"{self._obj_type.value}"


class SharedPhotoStreamAlbum(BasePhotoAlbum):
    """A Shared Stream Photo Album."""

    def __init__(
        self,
        library: BasePhotoLibrary,
        name: str,
        album_location: str,
        album_ctag: str,
        album_guid: str,
        owner_dsid: str,
        creation_date: str,
        sharing_type: str = "owned",
        allow_contributions: bool = False,
        is_public: bool = False,
        is_web_upload_supported: bool = False,
        public_url: Optional[str] = None,
        page_size: int = 100,
    ) -> None:
        super().__init__(
            library=library,
            name=name,
            list_type=ListTypeEnum.SHARED_STREAM,
            page_size=page_size,
        )

        self._album_location: str = album_location
        self._album_ctag: str = album_ctag
        self._album_guid: str = album_guid
        self._owner_dsid: str = owner_dsid
        try:
            self.creation_date: datetime = datetime.fromtimestamp(
                int(creation_date) / 1000.0, timezone.utc
            )
        except ValueError:
            self.creation_date = datetime.fromtimestamp(0, timezone.utc)

        # Read only properties
        self._sharing_type: str = sharing_type
        self._allow_contributions: bool = allow_contributions
        self._is_public: bool = is_public
        self._is_web_upload_supported: bool = is_web_upload_supported
        self._public_url: Optional[str] = public_url

    @property
    def id(self) -> str:
        """Gets the album id."""
        return self._album_guid

    @property
    def fullname(self) -> str:
        return self.name

    @property
    def sharing_type(self) -> str:
        """Gets the sharing type."""
        return self._sharing_type

    @property
    def allow_contributions(self) -> bool:
        """Gets if contributions are allowed."""
        return self._allow_contributions

    @property
    def is_public(self) -> bool:
        """Gets if the album is public."""
        return self._is_public

    @property
    def is_web_upload_supported(self) -> bool:
        """Gets if web uploads are supported."""
        return self._is_web_upload_supported

    @property
    def public_url(self) -> Optional[str]:
        """Gets the public URL."""
        return self._public_url

    def _get_payload(
        self, offset: int, page_size: int, direction: DirectionEnum
    ) -> dict[str, Any]:
        return {
            "albumguid": self._album_guid,
            "albumctag": self._album_ctag,
            "limit": str(min(offset + page_size, len(self))),
            "offset": str(offset),
        }

    def _get_url(self) -> str:
        return f"{self._album_location}webgetassets?{urlencode(self.service.params)}"

    def _get_len(self) -> int:
        url: str = (
            f"{self._album_location}webgetassetcount?{urlencode(self.service.params)}"
        )
        request: Response = self.service.session.post(
            url,
            json={
                "albumguid": self._album_guid,
            },
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()

        return response["albumassetcount"]

    def delete(self) -> bool:
        """Deletes the album."""
        # Shared albums cannot be deleted
        return False

    def rename(self, value: str) -> None:
        """Renames the album."""
        # Shared albums cannot be renamed
        return None


class PhotoAsset:
    """A photo."""

    def __init__(
        self,
        service: PhotosService,
        master_record: dict[str, Any],
        asset_record: dict[str, Any],
    ) -> None:
        self._service: PhotosService = service
        self._master_record: dict[str, Any] = master_record
        self._asset_record: dict[str, Any] = asset_record

        self._versions: Optional[dict[str, dict[str, Any]]] = None

    ITEM_TYPES: dict[str, str] = {
        "public.heic": "image",
        "public.jpeg": "image",
        "public.png": "image",
        "com.apple.quicktime-movie": "movie",
    }

    FILE_TYPE_EXTENSIONS: dict[str, str] = {
        "public.heic": ".HEIC",
        "public.jpeg": ".JPG",
        "public.png": ".PNG",
        "com.apple.quicktime-movie": ".MOV",
    }

    PHOTO_VERSION_LOOKUP: dict[str, str] = {
        "original": "resOriginal",
        "medium": "resJPEGMed",
        "thumb": "resJPEGThumb",
        "original_video": "resOriginalVidCompl",
        "medium_video": "resVidMed",
        "thumb_video": "resVidSmall",
    }

    VIDEO_VERSION_LOOKUP: dict[str, str] = {
        "original": "resOriginal",
        "medium": "resVidMed",
        "thumb": "resVidSmall",
    }

    @property
    def id(self) -> str:
        """Gets the photo id."""
        return self._master_record["recordName"]

    @property
    def filename(self) -> str:
        """Gets the photo file name."""
        return base64.b64decode(
            self._master_record["fields"]["filenameEnc"]["value"]
        ).decode("utf-8")

    @property
    def size(self):
        """Gets the photo size."""
        return self._master_record["fields"]["resOriginalRes"]["value"]["size"]

    @property
    def created(self) -> datetime:
        """Gets the photo created date."""
        return self.asset_date

    @property
    def asset_date(self) -> datetime:
        """Gets the photo asset date."""
        try:
            return datetime.fromtimestamp(
                self._asset_record["fields"]["assetDate"]["value"] / 1000.0,
                timezone.utc,
            )
        except KeyError:
            return datetime.fromtimestamp(0, timezone.utc)

    @property
    def added_date(self) -> datetime:
        """Gets the photo added date."""
        return datetime.fromtimestamp(
            self._asset_record["fields"]["addedDate"]["value"] / 1000.0, timezone.utc
        )

    @property
    def dimensions(self):
        """Gets the photo dimensions."""
        return (
            self._master_record["fields"]["resOriginalWidth"]["value"],
            self._master_record["fields"]["resOriginalHeight"]["value"],
        )

    @property
    def item_type(self) -> str:
        """Gets the photo item type."""
        item_type: str = ""
        try:
            item_type = self._master_record["fields"]["itemType"]["value"]
        except KeyError:
            try:
                item_type = self._master_record["fields"]["resOriginalFileType"][
                    "value"
                ]
            except KeyError:
                # Both fields missing; fall back to filename extension or default to "movie".
                pass
        if item_type in self.ITEM_TYPES:
            return self.ITEM_TYPES[item_type]
        if self.filename.lower().endswith((".heic", ".png", ".jpg", ".jpeg")):
            return "image"
        return "movie"

    @property
    def is_live_photo(self) -> bool:
        """Check if the photo is a live photo."""
        return (
            self.item_type == "image"
            and "resOriginalVidComplFileType" in self._master_record["fields"]
        )

    @property
    def versions(self) -> dict[str, dict[str, Any]]:
        """Gets the photo versions."""
        if not self._versions:
            self._versions = {}
            if self.item_type == "movie":
                typed_version_lookup: dict[str, str] = self.VIDEO_VERSION_LOOKUP
            else:
                typed_version_lookup = self.PHOTO_VERSION_LOOKUP

            for key, prefix in typed_version_lookup.items():
                if f"{prefix}Res" in self._master_record["fields"]:
                    self._versions[key] = self._get_photo_version(prefix)

        return self._versions

    def _get_photo_version(self, prefix: str) -> dict[str, Any]:
        version: dict[str, Any] = {}
        fields: dict[str, dict[str, Any]] = self._master_record["fields"]
        width_entry: Optional[dict[str, Any]] = fields.get(f"{prefix}Width")
        if width_entry:
            version["width"] = width_entry["value"]
        else:
            version["width"] = None

        height_entry: Optional[dict[str, Any]] = fields.get(f"{prefix}Height")
        if height_entry:
            version["height"] = height_entry["value"]
        else:
            version["height"] = None

        size_entry: Optional[dict[str, Any]] = fields.get(f"{prefix}Res")
        if size_entry:
            version["size"] = size_entry["value"]["size"]
            version["url"] = size_entry["value"]["downloadURL"]
        else:
            version["size"] = None
            version["url"] = None

        type_entry: Optional[dict[str, Any]] = fields.get(f"{prefix}FileType")
        if type_entry:
            version["type"] = type_entry["value"]
        else:
            version["type"] = None

        # Default to the master filename.
        version["filename"] = self.filename
        # For live photos, the video version has a different filename.
        if self.is_live_photo:
            version_type: Optional[str] = version.get("type")
            # Check if the current version is the video component of the live photo.
            if version_type and self.ITEM_TYPES.get(version_type, None) == "movie":
                # Create the video filename from the image filename.
                # e.g. IMG_1234.HEIC -> IMG_1234.MOV
                filename_base, _ = os.path.splitext(self.filename)
                extension: str = self.FILE_TYPE_EXTENSIONS.get(version_type, ".MOV")
                live_photo_video_filename: str = f"{filename_base}{extension}"
                version["filename"] = live_photo_video_filename

        return version

    def download(self, version="original", **kwargs) -> Optional[bytes]:
        """Returns the photo file."""
        if version not in self.versions:
            return None

        response: Response = self._service.session.get(
            self.versions[version]["url"],
            stream=True,
            **kwargs,
        )
        return response.raw.read()

    def delete(self) -> bool:
        """Deletes the photo."""
        endpoint: str = self._service.service_endpoint
        params: str = urlencode(self._service.params)
        url: str = f"{endpoint}/records/modify?{params}"

        resp: Response = self._service.session.post(
            url,
            json={
                "operations": [
                    {
                        "operationType": "update",
                        "record": {
                            "recordName": self._asset_record["recordName"],
                            "recordType": self._asset_record["recordType"],
                            "recordChangeTag": self._asset_record.get(
                                "recordChangeTag",
                                self._master_record.get("recordChangeTag"),
                            ),
                            "fields": {"isDeleted": {"value": 1}},
                        },
                    }
                ],
                "zoneID": self._asset_record["zoneID"],
                "atomic": True,
            },
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        return resp.status_code == 200

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: id={self.id}>"


class PhotoStreamAsset(PhotoAsset):
    """A Shared Stream Photo Asset"""

    @property
    def like_count(self) -> int:
        """Gets the photo like count."""
        return (
            self._asset_record.get("pluginFields", {})
            .get("likeCount", {})
            .get("value", 0)
        )

    @property
    def liked(self) -> bool:
        """Gets if the photo is liked."""
        return bool(
            self._asset_record.get("pluginFields", {})
            .get("likedByCaller", {})
            .get("value", False)
        )
