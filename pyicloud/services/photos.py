"""Photo service."""

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Generator, Optional
from urllib.parse import urlencode

from requests import Response

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class PhotoLibrary:
    """Represents a library in the user's photos.

    This provides access to all the albums as well as the photos.
    """

    SMART_FOLDERS = {
        "All Photos": {
            "obj_type": "CPLAssetByAssetDateWithoutHiddenOrDeleted",
            "list_type": "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Time-lapse": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Timelapse",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "TIMELAPSE"},
                }
            ],
        },
        "Videos": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Video",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "VIDEO"},
                }
            ],
        },
        "Slo-mo": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Slomo",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SLOMO"},
                }
            ],
        },
        "Bursts": {
            "obj_type": "CPLAssetBurstStackAssetByAssetDate",
            "list_type": "CPLBurstStackAssetAndMasterByAssetDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Favorites": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Favorite",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "FAVORITE"},
                }
            ],
        },
        "Panoramas": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Panorama",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "PANORAMA"},
                }
            ],
        },
        "Screenshots": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Screenshot",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SCREENSHOT"},
                }
            ],
        },
        "Live": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Live",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "LIVE"},
                }
            ],
        },
        "Recently Deleted": {
            "obj_type": "CPLAssetDeletedByExpungedDate",
            "list_type": "CPLAssetAndMasterDeletedByExpungedDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Hidden": {
            "obj_type": "CPLAssetHiddenByAssetDate",
            "list_type": "CPLAssetAndMasterHiddenByAssetDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
    }

    def __init__(
        self,
        service: "PhotosService",
        zone_id: dict[str, str],
        upload_url: Optional[str] = None,
    ) -> None:
        self.service: PhotosService = service
        self._upload_url: Optional[str] = upload_url
        self.zone_id: dict[str, str] = zone_id
        self._albums: Optional[dict[str, PhotoAlbum]] = None

        url: str = f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        json_data: str = json.dumps(
            {"query": {"recordType": "CheckIndexingState"}, "zoneID": self.zone_id}
        )
        request: Response = self.service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response: dict[str, Any] = request.json()
        indexing_state: str = response["records"][0]["fields"]["state"]["value"]
        if indexing_state != "FINISHED":
            raise PyiCloudServiceNotActivatedException(
                "iCloud Photo Library not finished indexing. "
                "Please try again in a few minutes."
            )

    @property
    def albums(self) -> dict[str, "PhotoAlbum"]:
        """Returns photo albums."""
        if not self._albums:
            self._albums = {
                name: PhotoAlbum(self.service, name, zone_id=self.zone_id, **props)
                for (name, props) in self.SMART_FOLDERS.items()
            }

            for folder in self._fetch_folders():
                # Skiping albums having null name, that can happen sometime
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
                    self.service,
                    folder_name,
                    "CPLContainerRelationLiveByAssetDate",
                    folder_obj_type,
                    "ASCENDING",
                    query_filter,
                    zone_id=self.zone_id,
                )
                self._albums[folder_name] = album

        return self._albums

    def _fetch_folders(self) -> list[dict[str, Any]]:
        url: str = f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        json_data: str = json.dumps(
            {"query": {"recordType": "CPLAlbumByPositionLive"}, "zoneID": self.zone_id}
        )

        request: Response = self.service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response: dict[str, list[dict[str, Any]]] = request.json()

        return response["records"]

    @property
    def all(self) -> "PhotoAlbum":
        """Returns all photos."""
        return self.albums["All Photos"]

    def upload_file(self, path):
        """Upload a photo from path, returns a recordName"""

        filename: str = os.path.basename(path)
        url: str = f"{self._upload_url}/upload"

        with open(path, "rb") as file_obj:
            request: Response = self.service.session.post(
                url,
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


class PhotosService(PhotoLibrary, BaseService):
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
        self._shared_streams_url: str = shared_streams_url
        self.shared_streams_url: str = f"{self._shared_streams_url}/{self.params['dsid']}/sharedstreams/webgetalbumslist"
        self._shared_streams: Optional[dict[str, SharedStream]] = None

        self._libraries: Optional[dict[str, PhotoLibrary]] = None

        self.params.update({"remapEnums": True, "getCurrentSyncToken": True})

        self._photo_assets: dict = {}

        super().__init__(
            service=self,
            upload_url=upload_url,
            zone_id={"zoneName": "PrimarySync"},
        )

    @property
    def shared_streams(self) -> dict[str, "SharedStream"]:
        """Returns shared streams."""
        if not self._shared_streams:
            self._shared_streams = dict()
            url: str = f"{self.shared_streams_url}?{urlencode(self.service.params)}"
            json_data: str = json.dumps({})
            request: Response = self.service.session.post(
                url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
            )
            response: dict[str, list] = request.json()
            for album in response["albums"]:
                shared_stream = SharedStream(
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
                self._shared_streams[album["attributes"]["name"]] = shared_stream
        return self._shared_streams

    @property
    def libraries(self) -> dict[str, PhotoLibrary]:
        """Returns photo libraries."""
        if not self._libraries:
            url: str = f"{self.service_endpoint}/changes/database"

            request: Response = self.session.post(
                url, data="{}", headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
            )
            response: dict[str, Any] = request.json()
            zones: list[dict[str, Any]] = response["zones"]

            libraries: dict[str, PhotoLibrary] = {}
            for zone in zones:
                if not zone.get("deleted"):
                    zone_name: str = zone["zoneID"]["zoneName"]
                    libraries[zone_name] = PhotoLibrary(self, zone["zoneID"])

            self._libraries = libraries

        return self._libraries


class PhotoAlbum:
    """A photo album."""

    def __init__(
        self,
        service: PhotosService,
        name: str,
        list_type: str,
        obj_type: str,
        direction: str,
        query_filter: Optional[list[dict[str, Any]]] = None,
        page_size: int = 100,
        zone_id: Optional[dict[str, str]] = None,
    ) -> None:
        self.name: str = name
        self.service: PhotosService = service
        self.list_type: str = list_type
        self.obj_type: str = obj_type
        self.direction: str = direction
        self.query_filter: Optional[list[dict[str, Any]]] = query_filter
        self.page_size: int = page_size

        if zone_id:
            self.zone_id: dict[str, str] = zone_id
        else:
            self.zone_id = {"zoneName": "PrimarySync"}

        self._len: Optional[int] = None

    @property
    def title(self) -> str:
        """Gets the album name."""
        return self.name

    def __iter__(self) -> Generator["PhotoAsset", None, None]:
        return self.photos

    def __len__(self) -> int:
        if self._len is None:
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

            self._len = response["batch"][0]["records"][0]["fields"]["itemCount"][
                "value"
            ]

        return self._len if self._len else 0

    @property
    def photos(self) -> Generator["PhotoAsset", None, None]:
        """Returns the album photos."""
        if self.direction == "DESCENDING":
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
            if self.direction == "DESCENDING":
                offset = offset - num_results
            else:
                offset = offset + num_results

    def photo(self, index) -> Generator["PhotoAsset", None, None]:
        """Returns a photo at the given index."""
        return self._get_photos_at(index, self.direction, 2)

    def _get_photos_at(
        self, index: int, direction: str, page_size=100
    ) -> Generator["PhotoAsset", None, None]:
        offset: int = len(self) - index - 1 if direction == "DESCENDING" else index

        url: str = f"{self.service.service_endpoint}/records/query?" + urlencode(
            self.service.params
        )
        request: Response = self.service.session.post(
            url,
            data=json.dumps(
                self._list_query_gen(
                    offset, self.list_type, direction, page_size, self.query_filter
                )
            ),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, list[dict[str, Any]]] = request.json()

        asset_records, master_records = self._parse_response(response)

        for master_record in master_records:
            record_name: str = master_record["recordName"]
            yield PhotoAsset(self.service, master_record, asset_records[record_name])

    def _parse_response(
        self, response: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        asset_records = {}
        master_records = []
        for rec in response["records"]:
            if rec["recordType"] == "CPLAsset":
                master_id = rec["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_id] = rec
            elif rec["recordType"] == "CPLMaster":
                master_records.append(rec)
        return asset_records, master_records

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

    def __str__(self) -> str:
        return self.title

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: '{self}'>"


class SharedStream:
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
        self.name: str = name
        self.service: PhotosService = service
        self.page_size: int = page_size
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
        self._photos: Optional[Generator[PhotoStreamAsset, None, None]] = None

        self._len: Optional[int] = None

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

    @property
    def photos(self) -> Generator["PhotoStreamAsset", None, None]:
        """Returns the album photos."""
        offset: int = 0
        while True:
            num_results = 0
            for photo in self._get_photos_at(offset, self.page_size):
                num_results += 1
                yield photo
            if num_results == 0:
                break
            offset = offset + num_results

    def _parse_response(
        self, response: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Parses the response."""
        asset_records = {}
        master_records = []
        for rec in response["records"]:
            if rec["recordType"] == "CPLAsset":
                master_id = rec["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_id] = rec
            elif rec["recordType"] == "CPLMaster":
                master_records.append(rec)
        return asset_records, master_records

    def _get_photos_at(
        self, offset, page_size=100
    ) -> Generator["PhotoStreamAsset", None, None]:
        """Returns the photos at the given offset."""
        url: str = (
            f"{self._album_location}webgetassets?{urlencode(self.service.params)}"
        )
        limit: int = min(offset + page_size, len(self))
        payload: dict[str, str] = {
            "albumguid": self.album_guid,
            "albumctag": self._album_ctag,
            "limit": str(limit),
            "offset": str(offset),
        }
        response: Response = self.service.session.post(
            url,
            data=json.dumps(payload),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        json_response: dict[str, Any] = response.json()
        asset_records, master_records = self._parse_response(json_response)

        for master_record in master_records:
            record_name: str = master_record["recordName"]
            yield PhotoStreamAsset(
                self.service, master_record, asset_records[record_name]
            )

    def __iter__(self) -> Generator["PhotoStreamAsset", None, None]:
        return self.photos

    def __len__(self) -> int:
        if self._len is None:
            url: str = f"{self._album_location}webgetassetcount?{urlencode(self.service.params)}"
            request: Response = self.service.session.post(
                url,
                data=json.dumps({"albumguid": self.album_guid}),
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response: dict[str, Any] = request.json()

            self._len = response["albumassetcount"]

        return self._len if self._len else 0

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: '{self}'>"


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
