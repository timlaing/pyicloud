"""Photo service."""

import base64
import json
import logging
import os
from abc import abstractmethod
from datetime import datetime, timezone
from enum import IntEnum, unique
from typing import Any, Generator, Iterable, Iterator, Optional, cast
from urllib.parse import urlencode

from requests import Response

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession

_LOGGER: logging.Logger = logging.getLogger(__name__)


@unique
class AlbumTypeEnum(IntEnum):
    """Album types"""

    ALBUM = 0
    FOLDER = 3
    SMART_ALBUM = 6


class SmartAlbumEnum:
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


class DirectionEnum:
    """Direction names."""

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class ListTypeEnum:
    """List type names."""

    DEFAULT: str = "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted"
    DELETED: str = "CPLAssetAndMasterDeletedByExpungedDate"
    HIDDEN: str = "CPLAssetAndMasterHiddenByAssetDate"
    SMART_ALBUM: str = "CPLAssetAndMasterInSmartAlbumByAssetDate"
    STACK: str = "CPLBurstStackAssetAndMasterByAssetDate"


class ObjectTypeEnum:
    """Object type names."""

    ALL: str = "CPLAssetByAssetDateWithoutHiddenOrDeleted"
    BURST: str = "CPLAssetBurstStackAssetByAssetDate"
    DELETED: str = "CPLAssetDeletedByExpungedDate"
    FAVORITE: str = "CPLAssetInSmartAlbumByAssetDate:Favorite"
    HIDDEN: str = "CPLAssetHiddenByAssetDate"
    LIVE: str = "CPLAssetInSmartAlbumByAssetDate:Live"
    PANORAMA: str = "CPLAssetInSmartAlbumByAssetDate:Panorama"
    SCREENSHOT: str = "CPLAssetInSmartAlbumByAssetDate:Screenshot"
    SLOMO: str = "CPLAssetInSmartAlbumByAssetDate:Slomo"
    TIMELASPE: str = "CPLAssetInSmartAlbumByAssetDate:Timelapse"
    VIDEO: str = "CPLAssetInSmartAlbumByAssetDate:Video"


# The primary zone for the user's photo library
PRIMARY_ZONE: dict[str, str] = {
    "zoneName": "PrimarySync",
    "zoneType": "REGULAR_CUSTOM_ZONE",
}


class AlbumContainer(Iterable):
    """Container for photo albums.
    This provides a way to access all the albums in the library.
    """

    def __init__(self, albums: dict[str, "BasePhotoAlbum"]) -> None:
        self._albums: dict[str, "BasePhotoAlbum"] = albums

    def __len__(self) -> int:
        return len(self._albums)

    def __getitem__(self, name) -> "BasePhotoAlbum":
        if name in self._albums:
            return self._albums[name]

        for album in self._albums.values():
            if name == album.fullname:
                return album

        raise KeyError(f"Photo album does not exist: {name}")

    def __iter__(self) -> Iterator[str]:
        return self._albums.__iter__()

    def values(self):
        """Returns the values of the albums."""
        return self._albums.values()


class BasePhotoLibrary:
    """Represents a library in the user's photos.

    This provides access to all the albums as well as the photos.
    """

    def __init__(
        self,
        service: "PhotosService",
        upload_url: Optional[str] = None,
    ) -> None:
        self.service: PhotosService = service
        self._albums: Optional[AlbumContainer] = None
        self._upload_url: Optional[str] = upload_url

    @abstractmethod
    def _get_albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns the photo albums."""
        raise NotImplementedError

    @property
    def albums(self) -> AlbumContainer:
        """Returns the photo albums."""
        if self._albums is None:
            self._albums = AlbumContainer(self._get_albums())
        return self._albums


class PhotoLibrary(BasePhotoLibrary):
    """Represents the user's primary photo libraries."""

    SMART_ALBUMS: dict[str, dict[str, Any]] = {
        SmartAlbumEnum.ALL_PHOTOS: {
            "obj_type": ObjectTypeEnum.ALL,
            "list_type": ListTypeEnum.DEFAULT,
            "direction": DirectionEnum.DESCENDING,
            "query_filter": None,
        },
        SmartAlbumEnum.TIME_LAPSE: {
            "obj_type": ObjectTypeEnum.TIMELASPE,
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
        super().__init__(service, upload_url)
        self.zone_id: dict[str, str] = zone_id

        self.url: str = f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        json_data: str = json.dumps(
            {
                "query": {
                    "recordType": "CheckIndexingState",
                },
                "zoneID": self.zone_id,
            }
        )
        request: Response = self.service.session.post(
            url=self.url,
            data=json_data,
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
            data=json.dumps(query),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, list[dict[str, Any]]] = request.json()
        records: list[dict[str, Any]] = response["records"]

        while "continuationMarker" in response:
            query["continuationMarker"] = response["continuationMarker"]

            request: Response = self.service.session.post(
                url=self.url,
                data=json.dumps(query),
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

    def _get_albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns photo albums."""
        albums: dict[str, "BasePhotoAlbum"] = {
            name: PhotoAlbum(
                library=self,
                name=name,
                zone_id=self.zone_id,
                url=self.url,
                **props,
            )
            for (name, props) in self.SMART_ALBUMS.items()
        }

        for record in self._fetch_records():
            if (
                # Skipping albums having null name, that can happen sometime
                "albumNameEnc" not in record["fields"]
                or (
                    record["fields"].get("albumType")
                    and record["fields"]["albumType"]["value"]
                    == AlbumTypeEnum.FOLDER.value
                )
                or (
                    record["fields"].get("isDeleted")
                    and record["fields"]["isDeleted"]["value"]
                )
            ):
                continue

            record_id: str = record["recordName"]
            obj_type: str = f"CPLContainerRelationNotDeletedByAssetDate:{record_id}"
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

            photo_album = PhotoAlbum(
                library=self,
                name=album_name,
                list_type="CPLContainerRelationLiveByAssetDate",
                obj_type=obj_type,
                direction=DirectionEnum.ASCENDING,
                url=self.url,
                query_filter=query_filter,
                zone_id=self.zone_id,
                parent_id=parent_id,
            )
            albums[record_id] = photo_album

        return albums

    def upload_file(self, path: str) -> dict[str, Any]:
        """Upload a photo from path, returns a recordName"""

        filename: str = os.path.basename(path)
        url: str = f"{self._upload_url}/upload"

        with open(path, "rb") as file_obj:
            request: Response = self.service.session.post(
                url=url,
                data=file_obj.read(),
                params={
                    "filename": filename,
                    "dsid": self.service.params["dsid"],
                },
            )

        if "errors" in request.json():
            raise PyiCloudAPIResponseException("", request.json()["errors"])

        return [
            x["recordName"]
            for x in request.json()["records"]
            if x["recordType"] == "CPLAsset"
        ][0]

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
        super().__init__(service)
        self.shared_streams_url: str = shared_streams_url

    def _get_albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns albums."""
        albums: dict[str, BasePhotoAlbum] = {}
        url: str = f"{self.shared_streams_url}?{urlencode(self.service.params)}"
        json_data: str = json.dumps({})
        request: Response = self.service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
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
            albums[album["attributes"]["name"]] = shared_stream
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
            shared_streams_url=f"{shared_streams_url}/{self.params['dsid']}/sharedstreams/webgetalbumslist",
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


class BasePhotoAlbum:
    """An abstract photo album."""

    def __init__(
        self,
        library: BasePhotoLibrary,
        name: str,
        list_type: str,
        asset_type: type["PhotoAsset"],
        page_size: int = 100,
        direction: str = DirectionEnum.ASCENDING,
    ) -> None:
        self.name: str = name
        self._library: BasePhotoLibrary = library
        self._page_size: int = page_size
        self.direction: str = direction
        self.list_type: str = list_type
        self.asset_type: type[PhotoAsset] = asset_type
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

    def _parse_response(
        self, response: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        asset_records: dict[str, dict[str, Any]] = {}
        master_records: list[dict[str, Any]] = []
        for rec in response["records"]:
            if rec["recordType"] == "CPLAsset":
                master_id: str = rec["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_id] = rec
            elif rec["recordType"] == "CPLMaster":
                master_records.append(rec)
        return asset_records, master_records

    def _get_photos_at(
        self, index: int, direction: str, page_size: int
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
        asset_records, master_records = self._parse_response(json_response)
        for master_record in master_records:
            record_name: str = master_record["recordName"]
            yield self.asset_type(
                self.service, master_record, asset_records[record_name]
            )

    def photo(self, index) -> Generator["PhotoAsset", None, None]:
        """Returns a photo at the given index."""
        return self._get_photos_at(index, self.direction, 1)

    @property
    def title(self) -> str:
        """Gets the album name."""
        return self.name

    @property
    def photos(self) -> Generator["PhotoAsset", None, None]:
        """Returns the album photos."""
        self._len = None
        if self.direction == DirectionEnum.DESCENDING:
            offset: int = len(self) - 1
        else:
            offset = 0

        photos_ids: set[str] = set()

        while True:
            num_results = 0
            for photo in self._get_photos_at(offset, self.direction, self.page_size):
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
            if self.direction == DirectionEnum.DESCENDING:
                offset = offset - num_results
            else:
                offset = offset + num_results

    @abstractmethod
    def _get_payload(
        self, offset: int, page_size: int, direction: str
    ) -> dict[str, str]:
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
        library: BasePhotoLibrary,
        name: str,
        list_type: str,
        obj_type: str,
        direction: str,
        url: str,
        query_filter: Optional[list[dict[str, Any]]] = None,
        zone_id: Optional[dict[str, str]] = None,
        page_size: int = 100,
        parent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            library=library,
            name=name,
            list_type=list_type,
            page_size=page_size,
            direction=direction,
            asset_type=PhotoAsset,
        )

        self.obj_type: str = obj_type
        self.query_filter: Optional[list[dict[str, Any]]] = query_filter
        self.url: str = url
        self._parent_id: Optional[str] = parent_id

        if zone_id:
            self.zone_id: dict[str, str] = zone_id
        else:
            self.zone_id = PRIMARY_ZONE

    @property
    def fullname(self) -> str:
        if self._parent_id is not None:
            return f"{self._library.albums[self._parent_id].fullname}/{self.name}"

        return self.name

    def _get_len(self) -> int:
        url: str = f"{self.service.service_endpoint}/internal/records/query/batch?{urlencode(self.service.params)}"
        request: Response = self.service.session.post(
            url,
            data=json.dumps(
                {
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
                                        "value": [self.obj_type],
                                    },
                                },
                            },
                            "zoneWide": True,
                            "zoneID": self.zone_id,
                        }
                    ]
                }
            ),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()

        return response["batch"][0]["records"][0]["fields"]["itemCount"]["value"]

    def _get_payload(
        self, offset: int, page_size: int, direction: str
    ) -> dict[str, str]:
        return self._list_query_gen(
            offset,
            self.list_type,
            direction,
            page_size,
            self.query_filter,
        )

    def _get_url(self) -> str:
        return self.url

    def _list_query_gen(
        self,
        offset: int,
        list_type: str,
        direction: str,
        num_results: int,
        query_filter=None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "query": {
                "recordType": list_type,
                "filterBy": [
                    {
                        "fieldName": "direction",
                        "comparator": "EQUALS",
                        "fieldValue": {"type": "STRING", "value": direction},
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
            "zoneID": self.zone_id,
        }

        if query_filter:
            query["query"]["filterBy"].extend(query_filter)

        return query


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
            list_type="sharedstream",
            page_size=page_size,
            asset_type=PhotoStreamAsset,
        )

        self._album_location: str = album_location
        self._album_ctag: str = album_ctag
        self.album_guid: str = album_guid
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
        self, offset: int, page_size: int, direction: str
    ) -> dict[str, str]:
        return {
            "albumguid": self.album_guid,
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
            data=json.dumps({"albumguid": self.album_guid}),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()

        return response["albumassetcount"]


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

    PHOTO_VERSION_LOOKUP: dict[str, str] = {
        "original": "resOriginal",
        "medium": "resJPEGMed",
        "thumb": "resJPEGThumb",
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
        try:
            item_type: str = self._master_record["fields"]["itemType"]["value"]
        except KeyError:
            try:
                item_type = self._master_record["fields"]["resOriginalFileType"][
                    "value"
                ]
            except KeyError:
                return "image"
        if item_type in self.ITEM_TYPES:
            return self.ITEM_TYPES[item_type]
        if self.filename.lower().endswith((".heic", ".png", ".jpg", ".jpeg")):
            return "image"
        return "movie"

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
        version: dict = {"filename": self.filename}
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

        return version

    def download(self, version="original", **kwargs) -> Optional[Response]:
        """Returns the photo file."""
        if version not in self.versions:
            return None

        return self._service.session.get(
            self.versions[version]["url"], stream=True, **kwargs
        )

    def delete(self) -> Response:
        """Deletes the photo."""
        data: dict[str, Any] = {
            "operations": [
                {
                    "operationType": "update",
                    "record": {
                        "recordName": self._asset_record["recordName"],
                        "recordType": self._asset_record["recordType"],
                        "recordChangeTag": self._master_record["recordChangeTag"],
                        "fields": {"isDeleted": {"value": 1}},
                    },
                }
            ],
            "zoneID": self._asset_record["zoneID"],
            "atomic": True,
        }

        endpoint: str = self._service.service_endpoint
        params: str = urlencode(self._service.params)
        url: str = f"{endpoint}/records/modify?{params}"

        return self._service.session.post(
            url, data=json.dumps(data), headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )

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
