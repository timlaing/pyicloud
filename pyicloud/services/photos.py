"""Photo service."""

import base64
import json
import logging
import os
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any, Generator, Optional, cast
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


class SmartFolderEnum:
    """Smart folder names."""

    ALL_PHOTOS = "All Photos"
    TIME_LAPSE = "Time-lapse"
    VIDEOS = "Videos"
    SLO_MO = "Slo-mo"
    BURSTS = "Bursts"
    FAVORITES = "Favorites"
    PANORAMAS = "Panoramas"
    SCREENSHOTS = "Screenshots"
    LIVE = "Live"
    RECENTLY_DELETED = "Recently Deleted"
    HIDDEN = "Hidden"


class DirectionEnum:
    """Direction names."""

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


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
        self._albums: Optional[dict[str, BasePhotoAlbum]] = None
        self._upload_url: Optional[str] = upload_url

    @abstractmethod
    def _get_albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns the photo albums."""
        raise NotImplementedError

    @property
    def albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns the photo albums."""
        if self._albums is None:
            self._albums = self._get_albums()
        return self._albums


class PhotoLibrary(BasePhotoLibrary):
    """Represents the user's primary photo libraries."""

    SMART_FOLDERS: dict[str, dict[str, Any]] = {
        SmartFolderEnum.ALL_PHOTOS: {
            "obj_type": "CPLAssetByAssetDateWithoutHiddenOrDeleted",
            "list_type": "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
        SmartFolderEnum.TIME_LAPSE: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Timelapse",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "TIMELAPSE"},
                }
            ],
        },
        SmartFolderEnum.VIDEOS: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Video",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "VIDEO"},
                }
            ],
        },
        SmartFolderEnum.SLO_MO: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Slomo",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SLOMO"},
                }
            ],
        },
        SmartFolderEnum.BURSTS: {
            "obj_type": "CPLAssetBurstStackAssetByAssetDate",
            "list_type": "CPLBurstStackAssetAndMasterByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
        SmartFolderEnum.FAVORITES: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Favorite",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "FAVORITE"},
                }
            ],
        },
        SmartFolderEnum.PANORAMAS: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Panorama",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "PANORAMA"},
                }
            ],
        },
        SmartFolderEnum.SCREENSHOTS: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Screenshot",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SCREENSHOT"},
                }
            ],
        },
        SmartFolderEnum.LIVE: {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Live",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "LIVE"},
                }
            ],
        },
        SmartFolderEnum.RECENTLY_DELETED: {
            "obj_type": "CPLAssetDeletedByExpungedDate",
            "list_type": "CPLAssetAndMasterDeletedByExpungedDate",
            "direction": DirectionEnum.ASCENDING,
            "query_filter": None,
        },
        SmartFolderEnum.HIDDEN: {
            "obj_type": "CPLAssetHiddenByAssetDate",
            "list_type": "CPLAssetAndMasterHiddenByAssetDate",
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
                "query": {"recordType": "CheckIndexingState"},
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

    def _fetch_folders(self) -> list[dict[str, Any]]:
        """Fetches folders."""
        query: dict[str, Any] = {
            "query": {"recordType": "CPLAlbumByPositionLive"},
            "zoneID": self.zone_id,
        }

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

        return records

    def _get_albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns photo albums."""
        albums: dict[str, BasePhotoAlbum] = {
            name: PhotoAlbum(
                service=self.service,
                name=name,
                zone_id=self.zone_id,
                url=self.url,
                **props,
            )
            for (name, props) in self.SMART_FOLDERS.items()
        }

        for folder in self._fetch_folders():
            # Skipping albums having null name, that can happen sometime
            if "albumNameEnc" not in folder["fields"]:
                continue

            if folder["recordName"] in (
                "----Root-Folder----",
                "----Project-Root-Folder----",
            ) or (
                folder["fields"].get("isDeleted")
                and folder["fields"]["isDeleted"]["value"]
            ):
                continue

            folder_id: str = folder["recordName"]
            folder_obj_type: str = (
                f"CPLContainerRelationNotDeletedByAssetDate:{folder_id}"
            )
            folder_name: str = base64.b64decode(
                folder["fields"]["albumNameEnc"]["value"]
            ).decode("utf-8")
            query_filter: list[dict[str, Any]] = [
                {
                    "fieldName": "parentId",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": folder_id},
                }
            ]

            album = PhotoAlbum(
                service=self.service,
                name=folder_name,
                list_type="CPLContainerRelationLiveByAssetDate",
                obj_type=folder_obj_type,
                direction=DirectionEnum.ASCENDING,
                url=self.url,
                query_filter=query_filter,
                zone_id=self.zone_id,
            )
            albums[folder_name] = album

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
        return cast(PhotoAlbum, self.albums[SmartFolderEnum.ALL_PHOTOS])


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
                service=self.service,
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
            {"zoneName": "PrimarySync"},
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
    def albums(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns the standard photo albums."""
        return self._root_library.albums

    @property
    def shared_streams(self) -> dict[str, "BasePhotoAlbum"]:
        """Returns the shared photo albums."""
        return self._shared_library.albums


class BasePhotoAlbum:
    """An abstract photo album."""

    def __init__(
        self,
        service: PhotosService,
        name: str,
        list_type: str,
        asset_type: type["PhotoAsset"],
        page_size: int = 100,
        direction: str = DirectionEnum.ASCENDING,
    ) -> None:
        self.name: str = name
        self.service: PhotosService = service
        self.page_size: int = page_size
        self.direction: str = direction
        self.list_type: str = list_type
        self.asset_type: type[PhotoAsset] = asset_type
        self._len: Optional[int] = None

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
        self, index: int, direction: str, page_size=100
    ) -> Generator["PhotoAsset", None, None]:
        offset: int = (
            len(self) - index - 1 if direction == DirectionEnum.DESCENDING else index
        )

        response: Response = self.service.session.post(
            url=self._get_url(),
            data=json.dumps(
                self._get_payload(
                    offset=offset,
                    page_size=page_size,
                    direction=direction,
                )
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
        return self._get_photos_at(index, self.direction, 2)

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

        while True:
            num_results = 0
            for photo in self._get_photos_at(
                offset, self.direction, self.page_size * 2
            ):
                num_results += 1
                yield photo
            if num_results == 0:
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
        service: PhotosService,
        name: str,
        list_type: str,
        obj_type: str,
        direction: str,
        url: str,
        query_filter: Optional[list[dict[str, Any]]] = None,
        zone_id: Optional[dict[str, str]] = None,
        page_size: int = 100,
    ) -> None:
        super().__init__(
            service=service,
            name=name,
            list_type=list_type,
            page_size=page_size,
            direction=direction,
            asset_type=PhotoAsset,
        )

        self.obj_type: str = obj_type
        self.query_filter: Optional[list[dict[str, Any]]] = query_filter
        self.url: str = url

        if zone_id:
            self.zone_id: dict[str, str] = zone_id
        else:
            self.zone_id = {"zoneName": "PrimarySync"}

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
                                "filterBy": {
                                    "fieldName": "indexCountID",
                                    "fieldValue": {
                                        "type": "STRING_LIST",
                                        "value": [self.obj_type],
                                    },
                                    "comparator": "IN",
                                },
                                "recordType": "HyperionIndexCountLookup",
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
                "filterBy": [
                    {
                        "fieldName": "startRank",
                        "fieldValue": {"type": "INT64", "value": offset},
                        "comparator": "EQUALS",
                    },
                    {
                        "fieldName": "direction",
                        "fieldValue": {"type": "STRING", "value": direction},
                        "comparator": "EQUALS",
                    },
                ],
                "recordType": list_type,
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
        service: PhotosService,
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
            service=service,
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
        json_data: str = (
            '{"operations":[{'
            '"operationType":"update",'
            '"record":{'
            '"recordName":"%s",'
            '"recordType":"%s",'
            '"recordChangeTag":"%s",'
            '"fields":{"isDeleted":{"value":1}'
            "}}}],"
            '"zoneID":{'
            '"zoneName":"PrimarySync"'
            '},"atomic":true}'
            % (
                self._asset_record["recordName"],
                self._asset_record["recordType"],
                self._master_record["recordChangeTag"],
            )
        )

        endpoint: str = self._service.service_endpoint
        params: str = urlencode(self._service.params)
        url: str = f"{endpoint}/records/modify?{params}"

        return self._service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
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
