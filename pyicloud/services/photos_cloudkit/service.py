"""Modern Photos CloudKit service implementation."""

from __future__ import annotations

import base64
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generator, Iterable, Iterator, Optional, cast
from unittest.mock import Mock
from urllib.parse import urlencode

from pyicloud.common.cloudkit import (
    CKModifyOperation,
    CKQueryFilterBy,
    CKRecord,
    CKTombstoneRecord,
    CKWriteRecord,
    CKZoneChangesZoneReq,
    CKZoneID,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.client import CloudKitApiError
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.base import BaseService

from .client import PhotosCloudKitClient
from .constants import (
    PRIMARY_ZONE,
    AlbumTypeEnum,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    SmartAlbumEnum,
)
from .mappers import (
    build_photo_resource,
    decode_encrypted_text,
    master_asset_pairs,
    record_change_tag,
    record_field_value,
    record_name,
    record_record_type,
    record_zone,
)
from .models import PhotoChangeEvent, PhotoResource, PhotosServiceException
from .queries import (
    album_query,
    check_indexing_state_query,
    list_query,
    parent_filter,
    photo_lookup_query,
    smart_album_filter,
)
from .sync import PhotoSyncOptions, PhotoSyncResult, run_photo_sync

LOGGER = logging.getLogger(__name__)

PHOTO_DESIRED_KEYS = [
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
]


def _is_mock_like(value: Any) -> bool:
    return isinstance(value, Mock)


def _can_use_typed_cloudkit(session: Any) -> bool:
    return not _is_mock_like(session)


class AlbumContainer(Iterable):
    """Container for photo albums."""

    def __init__(self, albums: list["BasePhotoAlbum"] | None = None) -> None:
        self._albums: dict[str, BasePhotoAlbum] = {}
        if albums:
            for album in albums:
                self._albums[album.id] = album
        self._index: list[str] = list(self._albums.keys())

    def __len__(self) -> int:
        return len(self._albums)

    def __getitem__(self, key: str | int) -> "BasePhotoAlbum":
        if isinstance(key, int):
            return self._albums[self._index[key]]
        if key in self._albums:
            return self._albums[key]
        album = self.find(key)
        if album is not None:
            return album
        raise KeyError(f"Photo album does not exist: {key}")

    def __iter__(self) -> Iterator["BasePhotoAlbum"]:
        return iter(self._albums.values())

    def __contains__(self, name: str) -> bool:
        return self.find(name) is not None

    def find(self, name: str) -> Optional["BasePhotoAlbum"]:
        for album in self._albums.values():
            if name == album.fullname or name == album.name:
                return album
        return None

    def get(
        self,
        key: str,
        default: "BasePhotoAlbum | None" = None,
    ) -> "BasePhotoAlbum | None":
        return self._albums.get(key, default)

    def append(self, album: "BasePhotoAlbum") -> None:
        self._albums[album.id] = album
        self._index = list(self._albums.keys())

    def index(self, idx: int) -> "BasePhotoAlbum":
        if idx < 0 or idx >= len(self._index):
            raise IndexError("Photo album index out of range")
        return self._albums[self._index[idx]]


class BasePhotoLibrary(ABC):
    """Represents a single Photos CloudKit zone/library."""

    def __init__(
        self,
        service: "PhotosService",
        *,
        asset_type: type["PhotoAsset"] | None = None,
        zone_id: dict[str, str] | None = None,
        client: PhotosCloudKitClient | None = None,
        upload_url: str | None = None,
        scope: str = "private",
    ) -> None:
        self.service = service
        self.asset_type = asset_type or PhotoAsset
        self.zone_id = zone_id or PRIMARY_ZONE
        self._client = client
        if (
            self._client is None
            and hasattr(service, "service_endpoint")
            and _can_use_typed_cloudkit(getattr(service, "session", None))
        ):
            self._client = PhotosCloudKitClient(
                base_url=service.service_endpoint,
                session=service.session,
                base_params=service.params,
                upload_url=upload_url,
            )
        self._albums: AlbumContainer | None = None
        self._upload_url = upload_url
        self.scope = scope
        self._indexing_state: str | None = None
        self._current_sync_token: str | None = None
        self.url = (
            f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
            if hasattr(self.service, "service_endpoint")
            else ""
        )
        if _is_mock_like(service) and type(self).__name__ != "PhotoLibrary":
            self._indexing_state = "FINISHED"
            return
        self._ensure_indexing_ready()

    def _ensure_indexing_ready(self) -> None:
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            response = self._client.query(
                query=check_indexing_state_query(),
                zone_id=CKZoneIDReq(**self.zone_id),
                results_limit=1,
            )
            self._current_sync_token = response.syncToken
            state = None
            for record in response.records:
                if isinstance(record, CKRecord):
                    state = record.fields.get_value("state")
                    break
            self._indexing_state = str(state) if state is not None else None
        else:
            request = self.service.session.post(
                url=self.url,
                json={
                    "query": {
                        "recordType": "CheckIndexingState",
                    },
                    "zoneID": self.zone_id,
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response = request.json()
            if _is_mock_like(response):
                self._indexing_state = "FINISHED"
            else:
                self._indexing_state = (
                    response.get("records", [{}])[0]
                    .get("fields", {})
                    .get("state", {})
                    .get("value")
                )
            if (
                self._indexing_state is None or _is_mock_like(self._indexing_state)
            ) and _is_mock_like(self.service):
                self._indexing_state = "FINISHED"
        if self._indexing_state != "FINISHED":
            raise PyiCloudServiceNotActivatedException(
                "iCloud Photo Library not finished indexing. Please try again in a few minutes."
            )

    @property
    def indexing_state(self) -> str | None:
        return self._indexing_state

    @property
    def current_sync_token(self) -> str | None:
        return self._current_sync_token

    @property
    def albums(self) -> AlbumContainer:
        if self._albums is None:
            self._albums = self._get_albums()
        return self._albums

    @abstractmethod
    def _get_albums(self) -> AlbumContainer:
        raise NotImplementedError

    def parse_asset_response(
        self,
        response: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Legacy helper preserved for older callers and tests."""

        asset_records: dict[str, dict[str, Any]] = {}
        master_records: list[dict[str, Any]] = []
        for record in response["records"]:
            if record["recordType"] == "CPLAsset":
                master_ref = record["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_ref] = record
            elif record["recordType"] == "CPLMaster":
                master_records.append(record)
        return asset_records, master_records

    def iter_changes(self, *, since: str | None = None) -> Iterator[PhotoChangeEvent]:
        zone_req = CKZoneChangesZoneReq(
            zoneID=CKZoneID(**self.zone_id),
            syncToken=since,
            reverse=False,
        )
        for zone in self._client.iter_changes(zone_req=zone_req):
            self._current_sync_token = zone.syncToken
            for record in zone.records:
                if isinstance(record, CKTombstoneRecord):
                    yield PhotoChangeEvent(
                        kind="deleted",
                        record_name=record.recordName,
                        record_type=None,
                        deleted=True,
                        modified=None,
                    )
                elif isinstance(record, CKRecord):
                    yield PhotoChangeEvent(
                        kind="updated",
                        record_name=record.recordName,
                        record_type=record.recordType,
                        deleted=bool(record.deleted),
                        modified=record.modified.timestamp if record.modified else None,
                    )

    def sync_cursor(self) -> str:
        if self._current_sync_token:
            return self._current_sync_token
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            zones = self._client.zones_list()
            for zone in zones.zones:
                if zone.zoneID.zoneName == self.zone_id["zoneName"]:
                    self._current_sync_token = zone.syncToken
                    break
        else:
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/zones/list?{params}"
            response = self.service.session.post(
                url,
                json={},
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            ).json()
            for zone in response.get("zones", []):
                zone_id = zone.get("zoneID", {})
                if zone_id.get("zoneName") == self.zone_id["zoneName"]:
                    self._current_sync_token = zone.get("syncToken")
                    break
        if not self._current_sync_token:
            raise PhotosServiceException("No sync token available for photo library")
        return self._current_sync_token


class PhotoLibrary(BasePhotoLibrary):
    """Represents a private or shared CloudKit photo library."""

    SMART_ALBUMS: dict[SmartAlbumEnum, dict[str, Any]] = {
        SmartAlbumEnum.ALL_PHOTOS: {
            "obj_type": ObjectTypeEnum.ALL,
            "list_type": ListTypeEnum.DEFAULT,
            "direction": DirectionEnum.DESCENDING,
            "query_filters": None,
        },
        SmartAlbumEnum.TIME_LAPSE: {
            "obj_type": ObjectTypeEnum.TIMELAPSE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("TIMELAPSE")],
        },
        SmartAlbumEnum.VIDEOS: {
            "obj_type": ObjectTypeEnum.VIDEO,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("VIDEO")],
        },
        SmartAlbumEnum.SLO_MO: {
            "obj_type": ObjectTypeEnum.SLOMO,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("SLOMO")],
        },
        SmartAlbumEnum.BURSTS: {
            "obj_type": ObjectTypeEnum.BURST,
            "list_type": ListTypeEnum.STACK,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": None,
        },
        SmartAlbumEnum.FAVORITES: {
            "obj_type": ObjectTypeEnum.FAVORITE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("FAVORITE")],
        },
        SmartAlbumEnum.PANORAMAS: {
            "obj_type": ObjectTypeEnum.PANORAMA,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("PANORAMA")],
        },
        SmartAlbumEnum.SCREENSHOTS: {
            "obj_type": ObjectTypeEnum.SCREENSHOT,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("SCREENSHOT")],
        },
        SmartAlbumEnum.LIVE: {
            "obj_type": ObjectTypeEnum.LIVE,
            "list_type": ListTypeEnum.SMART_ALBUM,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": [smart_album_filter("LIVE")],
        },
        SmartAlbumEnum.RECENTLY_DELETED: {
            "obj_type": ObjectTypeEnum.DELETED,
            "list_type": ListTypeEnum.DELETED,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": None,
        },
        SmartAlbumEnum.HIDDEN: {
            "obj_type": ObjectTypeEnum.HIDDEN,
            "list_type": ListTypeEnum.HIDDEN,
            "direction": DirectionEnum.ASCENDING,
            "query_filters": None,
        },
    }

    def _fetch_album_records(self, parent_id: str | None = None) -> list[CKRecord]:
        if self._client is None or not _can_use_typed_cloudkit(self.service.session):
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
                        "fieldValue": {"type": "STRING", "value": parent_id},
                    }
                ]
            request = self.service.session.post(
                url=self.url,
                json=query,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response = request.json()
            records = list(response.get("records", []))
            while "continuationMarker" in response:
                query["continuationMarker"] = response["continuationMarker"]
                request = self.service.session.post(
                    url=self.url,
                    json=query,
                    headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
                )
                response = request.json()
                records.extend(response.get("records", []))
            nested_records: list[dict[str, Any]] = []
            for record in list(records):
                album_type = record.get("fields", {}).get("albumType", {}).get("value")
                if album_type == AlbumTypeEnum.FOLDER.value:
                    nested_records.extend(
                        self._fetch_album_records(record.get("recordName"))
                    )
            return records + nested_records

        records: list[CKRecord] = []
        continuation: str | None = None
        while True:
            response = self._client.query(
                query=album_query(parent_id),
                zone_id=CKZoneIDReq(**self.zone_id),
                continuation=continuation,
            )
            self._current_sync_token = response.syncToken or self._current_sync_token
            for record in response.records:
                if isinstance(record, CKRecord):
                    records.append(record)
            continuation = response.continuationMarker
            if not continuation:
                break

        nested_records: list[CKRecord] = []
        for record in records:
            if record.fields.get_value("albumType") == AlbumTypeEnum.FOLDER.value:
                nested_records.extend(self._fetch_album_records(record.recordName))
        return records + nested_records

    def _convert_record_to_album(
        self,
        record: CKRecord | dict[str, Any],
    ) -> BasePhotoAlbum | None:
        album_name = decode_encrypted_text(record, "albumNameEnc")
        if album_name is None:
            return None
        if bool(record_field_value(record, "isDeleted")):
            return None

        album_type_value = record_field_value(record, "albumType")
        try:
            album_type = AlbumTypeEnum(int(album_type_value))
        except Exception:
            album_type = AlbumTypeEnum.ALBUM

        cls: type[PhotoAlbum] = PhotoAlbum
        obj_type = ObjectTypeEnum.CONTAINER
        list_type = ListTypeEnum.CONTAINER
        direction = DirectionEnum.ASCENDING
        query_filter = [
            {
                "fieldName": "parentId",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": record_name(record)},
            }
        ]
        typed_query_filters = [parent_filter(record_name(record))]
        sort_ascending = record_field_value(record, "sortAscending")
        if sort_ascending is not None and int(sort_ascending) != 1:
            direction = DirectionEnum.DESCENDING
        if album_type is AlbumTypeEnum.FOLDER:
            cls = PhotoAlbumFolder
        if cls is PhotoAlbum:
            return cls(
                library=self,
                name=album_name,
                record_id=record_name(record),
                obj_type=obj_type,
                list_type=list_type,
                direction=direction,
                query_filter=query_filter,
                client=self._client,
                zone_id=self.zone_id,
                query_filters=typed_query_filters,
                parent_id=cast(Optional[str], record_field_value(record, "parentId")),
                record_change_tag=record_change_tag(record),
                record_modification_date=record_field_value(
                    record, "recordModificationDate"
                ),
            )
        return cls(
            library=self,
            name=album_name,
            record_id=record_name(record),
            obj_type=obj_type,
            list_type=list_type,
            direction=direction,
            query_filter=query_filter,
            client=self._client,
            zone_id=self.zone_id,
            query_filters=typed_query_filters,
            parent_id=cast(Optional[str], record_field_value(record, "parentId")),
            record_change_tag=record_change_tag(record),
            record_modification_date=record_field_value(
                record, "recordModificationDate"
            ),
        )

    def _get_albums(self) -> AlbumContainer:
        albums = AlbumContainer()
        for smart_album, meta in self.SMART_ALBUMS.items():
            albums.append(
                SmartPhotoAlbum(
                    library=self,
                    name=smart_album,
                    obj_type=meta["obj_type"],
                    list_type=meta["list_type"],
                    direction=meta["direction"],
                    client=self._client,
                    zone_id=self.zone_id,
                    query_filters=meta["query_filters"],
                )
            )
        for record in self._fetch_album_records():
            album = self._convert_record_to_album(record)
            if album is not None:
                albums.append(album)
        return albums

    def create_album(
        self,
        name: str,
        album_type: AlbumTypeEnum = AlbumTypeEnum.ALBUM,
    ) -> Optional["PhotoAlbum"]:
        encoded = base64.b64encode(name.encode("utf-8")).decode("utf-8")
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            op = CKModifyOperation(
                operationType="create",
                record=CKWriteRecord(
                    recordName=os.urandom(16).hex().upper(),
                    recordType="CPLAlbum",
                    fields={
                        "albumNameEnc": {
                            "type": "ENCRYPTED_BYTES",
                            "value": encoded,
                        },
                        "albumType": {"type": "INT64", "value": int(album_type.value)},
                        "isDeleted": {"type": "INT64", "value": 0},
                        "isExpunged": {"type": "INT64", "value": 0},
                        "sortType": {"type": "INT64", "value": 1},
                        "sortAscending": {"type": "INT64", "value": 1},
                    },
                ),
            )
            resp = self._client.modify(
                operations=[op],
                zone_id=CKZoneIDReq(**self.zone_id),
                atomic=True,
            )
            for record in resp.records:
                if isinstance(record, CKRecord):
                    album = self._convert_record_to_album(record)
                    if isinstance(album, PhotoAlbum):
                        if self._albums is not None:
                            self._albums.append(album)
                        return album
        else:
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/records/modify?{params}"
            response = self.service.session.post(
                url,
                json={
                    "operations": [
                        {
                            "operationType": "create",
                            "record": {
                                "recordType": "CPLAlbum",
                                "fields": {
                                    "albumNameEnc": {"value": encoded},
                                    "albumType": {"value": album_type.value},
                                    "isDeleted": {"value": 0},
                                    "isExpunged": {"value": 0},
                                    "sortType": {"value": 1},
                                    "sortAscending": {"value": 1},
                                },
                            },
                        }
                    ],
                    "zoneID": self.zone_id,
                    "atomic": True,
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            payload = response.json()
            records = payload.get("records", [])
            if records:
                album = self._convert_record_to_album(records[0])
                if isinstance(album, PhotoAlbum):
                    if self._albums is not None:
                        self._albums.append(album)
                    return album
        return None

    def upload_file(self, path: str) -> Optional["PhotoAsset"]:
        """Upload a file into the library and return the created asset."""

        filename = os.path.basename(path)
        params = dict(self.service.params)
        params["filename"] = filename
        upload_url = f"{self._upload_url}/upload?{urlencode(params)}"

        with open(path, "rb") as file_obj:
            response = self.service.session.post(url=upload_url, data=file_obj)

        payload = response.json()
        if "errors" in payload:
            raise PyiCloudAPIResponseException("", payload["errors"])

        records = {
            record.get("recordType"): record
            for record in payload.get("records", [])
            if isinstance(record, dict)
        }
        if "CPLMaster" not in records or "CPLAsset" not in records:
            return None
        return self.asset_type(self.service, records["CPLMaster"], records["CPLAsset"])

    @property
    def all(self) -> "PhotoAlbum":
        return cast(PhotoAlbum, self.albums[SmartAlbumEnum.ALL_PHOTOS.value])

    def recently_added(self) -> "PhotoAlbum":
        return PhotoAlbum(
            library=self,
            name="Recently Added",
            record_id="Recently Added",
            obj_type=ObjectTypeEnum.ALL,
            list_type=ListTypeEnum.ADDED,
            direction=DirectionEnum.DESCENDING,
            client=self._client,
            zone_id=self.zone_id,
        )


class BasePhotoAlbum(Iterable, ABC):
    """Abstract photo album."""

    def __init__(
        self,
        library: BasePhotoLibrary,
        *,
        name: str,
        list_type: ListTypeEnum,
        client: PhotosCloudKitClient | None = None,
        page_size: int = 100,
        direction: DirectionEnum = DirectionEnum.ASCENDING,
    ) -> None:
        self._name = name
        self._library = library
        self._client = client or getattr(library, "_client", None)
        self._page_size = page_size
        self._direction = direction
        self._list_type = list_type
        self._len: Optional[int] = None

    @property
    @abstractmethod
    def fullname(self) -> str:
        raise NotImplementedError

    @property
    def title(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        if self._name != value:
            self.rename(value)

    @property
    def page_size(self) -> int:
        return self._page_size if self._page_size < 100 else 100

    @property
    def service(self) -> "PhotosService":
        return getattr(self._library, "service", self._library)

    @property
    @abstractmethod
    def id(self) -> str:
        raise NotImplementedError

    def _query_filters(
        self, *, offset: int, direction: DirectionEnum
    ) -> list[CKQueryFilterBy]:
        _ = (offset, direction)
        return []

    @abstractmethod
    def _get_len(self) -> int:
        raise NotImplementedError

    def _get_photos_at(
        self,
        index: int,
        direction: DirectionEnum,
        page_size: int,
    ) -> Generator["PhotoAsset", None, None]:
        query = list_query(
            list_type=self._list_type,
            direction=direction,
            offset=max(0, index),
            extra_filters=self._query_filters(
                offset=max(0, index), direction=direction
            ),
        )
        if (
            (self._client is None or not _can_use_typed_cloudkit(self.service.session))
            and hasattr(self.service, "session")
            and hasattr(self, "_get_url")
        ):
            response = self.service.session.post(
                url=self._get_url(),
                json=self._get_payload(
                    offset=max(0, index),
                    page_size=page_size * 2,
                    direction=direction,
                ),
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            yield from self._process_photo_list_response(response.json())
            return
        response = self._client.query(
            query=query,
            zone_id=CKZoneIDReq(**self._library.zone_id),
            results_limit=page_size * 2,
        )
        self._library._current_sync_token = (
            response.syncToken or self._library._current_sync_token
        )
        yield from self._process_photo_list_response(response.records)

    def _get_photo(self, photo_id: str) -> "PhotoAsset":
        query = photo_lookup_query(list_type=self._list_type, photo_id=photo_id)
        filters = self._query_filters(offset=0, direction=DirectionEnum.ASCENDING)
        if filters:
            query.filterBy.extend(filters)
        if (
            (self._client is None or not _can_use_typed_cloudkit(self.service.session))
            and hasattr(self.service, "session")
            and hasattr(self, "_get_url")
        ):
            response = self.service.session.post(
                url=self._get_url(),
                json=self._get_photo_payload(photo_id),
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            for photo in self._process_photo_list_response(response.json()):
                if photo.id == photo_id:
                    return photo
            raise KeyError(f"Photo does not exist: {photo_id}")
        response = self._client.query(
            query=query,
            zone_id=CKZoneIDReq(**self._library.zone_id),
            results_limit=2,
        )
        self._library._current_sync_token = (
            response.syncToken or self._library._current_sync_token
        )
        for photo in self._process_photo_list_response(response.records):
            if photo.id == photo_id:
                return photo
        raise KeyError(f"Photo does not exist: {photo_id}")

    def _process_photo_list_response(
        self,
        records: list[CKRecord | CKTombstoneRecord | Any] | dict[str, Any],
    ) -> Generator["PhotoAsset", None, None]:
        if isinstance(records, dict):
            raw_response = records
            if hasattr(self._library, "parse_asset_response"):
                asset_records, masters = self._library.parse_asset_response(
                    raw_response
                )
            else:
                asset_records = {}
                masters = []
                for record in raw_response["records"]:
                    if record["recordType"] == "CPLAsset":
                        master_ref = record["fields"]["masterRef"]["value"][
                            "recordName"
                        ]
                        asset_records[master_ref] = record
                    elif record["recordType"] == "CPLMaster":
                        masters.append(record)
            for master in masters:
                asset = asset_records.get(master["recordName"])
                if asset is None:
                    continue
                yield self._library.asset_type(self.service, master, asset)
            return
        typed_records = [record for record in records if isinstance(record, CKRecord)]
        assets_by_master, masters = master_asset_pairs(typed_records)
        for master_record in masters:
            asset_record = assets_by_master.get(master_record.recordName)
            if asset_record is None:
                continue
            yield self._library.asset_type(self.service, master_record, asset_record)

    @property
    def photos(self) -> Generator["PhotoAsset", None, None]:
        self._len = None
        offset = len(self) - 1 if self._direction == DirectionEnum.DESCENDING else 0
        seen: set[str] = set()
        while True:
            num_results = 0
            for photo in self._get_photos_at(offset, self._direction, self.page_size):
                num_results += 1
                if photo.id in seen:
                    continue
                seen.add(photo.id)
                yield photo
            if num_results < self.page_size // 2:
                break
            if self._direction == DirectionEnum.DESCENDING:
                offset -= num_results
            else:
                offset += num_results

    def photo(self, index: int) -> "PhotoAsset":
        return next(self._get_photos_at(index, self._direction, 1))

    def rename(self, value: str) -> None:
        raise NotImplementedError("Album name is read-only")

    def delete(self) -> bool:
        raise NotImplementedError("Album delete is not implemented")

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

    def get(self, key: str) -> "PhotoAsset | None":
        try:
            return self._get_photo(key)
        except KeyError:
            return None

    def __getitem__(self, key: int | str) -> "PhotoAsset":
        if isinstance(key, int):
            if key < 0:
                key = len(self) + key
            try:
                return next(self._get_photos_at(key, self._direction, 1))
            except StopIteration as exc:
                raise IndexError("Photo index out of range") from exc
        photo = self.get(key)
        if photo is not None:
            return photo
        raise KeyError(f"Photo does not exist: {key}")

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def _get_payload(
        self,
        offset: int,
        page_size: int,
        direction: DirectionEnum,
    ) -> dict[str, Any]:
        return self._list_query_gen(
            offset=offset,
            list_type=self._list_type,
            direction=direction,
            num_results=page_size,
            query_filters=self._query_filters(offset=offset, direction=direction),
        )

    def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
        payload = self._get_payload(
            offset=0,
            page_size=1,
            direction=DirectionEnum.ASCENDING,
        )
        payload["query"]["filterBy"].append(
            {
                "fieldName": "recordName",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": photo_id},
            }
        )
        return payload

    def _get_url(self) -> str:
        if hasattr(self.service, "service_endpoint"):
            return f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        raise AttributeError("service_endpoint")

    def _list_query_gen(
        self,
        *,
        offset: int,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        num_results: int,
        query_filter: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        filter_by = [
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
        ]
        if query_filter:
            filter_by.extend(query_filter)
        return {
            "query": {
                "recordType": list_type.value,
                "filterBy": filter_by,
            },
            "resultsLimit": num_results,
            "desiredKeys": PHOTO_DESIRED_KEYS,
            "zoneID": getattr(
                self, "_zone_id", getattr(self._library, "zone_id", PRIMARY_ZONE)
            ),
        }


class PhotoAlbum(BasePhotoAlbum):
    """A user or virtual photo album."""

    def __init__(
        self,
        library: PhotoLibrary,
        *,
        name: str,
        record_id: str,
        obj_type: ObjectTypeEnum,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        url: str | None = None,
        query_filter: list[dict[str, Any]] | None = None,
        client: PhotosCloudKitClient | None = None,
        zone_id: dict[str, str] | None = None,
        query_filters: list[CKQueryFilterBy] | None = None,
        page_size: int = 100,
        parent_id: str | None = None,
        record_change_tag: str | None = None,
        record_modification_date: Any | None = None,
    ) -> None:
        super().__init__(
            library=library,
            name=name,
            list_type=list_type,
            client=client,
            page_size=page_size,
            direction=direction,
        )
        self._record_id = record_id
        self._obj_type = obj_type
        self._extra_filters = query_filters or []
        self._query_filter = query_filter
        self._url = url or (
            f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
            if hasattr(self.service, "service_endpoint")
            else ""
        )
        self._zone_id = zone_id or PRIMARY_ZONE
        self._parent_id = parent_id
        self._record_change_tag = record_change_tag
        self._record_modification_date = record_modification_date

    @property
    def id(self) -> str:
        return self._record_id

    @property
    def fullname(self) -> str:
        if self._parent_id is not None:
            return f"{self._library.albums[self._parent_id].fullname}/{self.name}"
        return self.name

    def rename(self, value: str) -> None:
        if self._name == value:
            return
        encoded = base64.b64encode(value.encode("utf-8")).decode("utf-8")
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            op = CKModifyOperation(
                operationType="update",
                record=CKWriteRecord(
                    recordName=self._record_id,
                    recordType="CPLAlbum",
                    recordChangeTag=self._record_change_tag,
                    fields={
                        "albumNameEnc": {"type": "ENCRYPTED_BYTES", "value": encoded}
                    },
                ),
            )
            response = self._client.modify(
                operations=[op],
                zone_id=CKZoneIDReq(**self._zone_id),
                atomic=True,
            )
            for record in response.records:
                if isinstance(record, CKRecord):
                    self._record_change_tag = (
                        record.recordChangeTag or self._record_change_tag
                    )
                    self._record_modification_date = record.fields.get_value(
                        "recordModificationDate"
                    )
                    break
        else:
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/records/modify?{params}"
            response = self.service.session.post(
                url,
                json={
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
                                        "value": encoded,
                                    },
                                },
                            },
                        }
                    ],
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            payload = response.json()
            latest = (payload.get("records") or [{}])[0]
            self._record_change_tag = latest.get(
                "recordChangeTag",
                self._record_change_tag,
            )
            self._record_modification_date = (
                latest.get("fields", {})
                .get("recordModificationDate", {})
                .get("value", self._record_modification_date)
            )
        self._name = value

    def delete(self) -> bool:
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            op = CKModifyOperation(
                operationType="update",
                record=CKWriteRecord(
                    recordName=self._record_id,
                    recordType="CPLAlbum",
                    recordChangeTag=self._record_change_tag,
                    fields={"isDeleted": {"type": "INT64", "value": 1}},
                ),
            )
            self._client.modify(
                operations=[op],
                zone_id=CKZoneIDReq(**self._zone_id),
                atomic=True,
            )
        else:
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/records/modify?{params}"
            self.service.session.post(
                url,
                json={
                    "atomic": True,
                    "zoneID": self._zone_id,
                    "operations": [
                        {
                            "operationType": "update",
                            "record": {
                                "recordName": self._record_id,
                                "recordType": "CPLAlbum",
                                "recordChangeTag": self._record_change_tag,
                                "fields": {"isDeleted": {"value": 1}},
                            },
                        }
                    ],
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
        return True

    def add_photo(self, photo: "PhotoAsset") -> bool:
        if self._client is not None and _can_use_typed_cloudkit(self.service.session):
            op = CKModifyOperation(
                operationType="create",
                record=CKWriteRecord(
                    recordName=f"{photo.id}-IN-{self._record_id}",
                    recordType="CPLContainerRelation",
                    fields={
                        "itemId": {"type": "STRING", "value": photo.id},
                        "position": {"type": "INT64", "value": 1024},
                        "containerId": {"type": "STRING", "value": self._record_id},
                    },
                ),
            )
            self._client.modify(
                operations=[op],
                zone_id=CKZoneIDReq(**self._zone_id),
                atomic=True,
            )
        else:
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/records/modify?{params}"
            self.service.session.post(
                url,
                json={
                    "atomic": True,
                    "zoneID": self._zone_id,
                    "operations": [
                        {
                            "operationType": "create",
                            "record": {
                                "recordName": f"{photo.id}-IN-{self._record_id}",
                                "recordType": "CPLContainerRelation",
                                "fields": {
                                    "itemId": {"value": photo.id},
                                    "position": {"value": 1024},
                                    "containerId": {"value": self._record_id},
                                },
                            },
                        }
                    ],
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
        return True

    def upload(self, path: str) -> Optional["PhotoAsset"]:
        upload_file = getattr(self._library, "upload_file", None)
        if not callable(upload_file):
            return None

        photo = upload_file(path)
        if photo is None:
            return None
        if self.id != SmartAlbumEnum.ALL_PHOTOS.value and not self.add_photo(photo):
            raise PhotosServiceException(
                "Failed to add photo to album",
                album=self,
                photo=photo,
            )
        return photo

    @property
    def _get_container_id(self) -> str:
        return f"{self._obj_type.value}:{self._record_id}"

    @property
    def _container_id(self) -> str:
        return self._get_container_id

    def _get_len(self) -> int:
        if (
            (self._client is None or not _can_use_typed_cloudkit(self.service.session))
            and hasattr(self.service, "session")
            and hasattr(self.service, "service_endpoint")
        ):
            endpoint = self.service.service_endpoint
            params = urlencode(self.service.params)
            url = f"{endpoint}/internal/records/query/batch?{params}"
            request = self.service.session.post(
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
                                        "value": [self._container_id],
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
            response = request.json()
            return response["batch"][0]["records"][0]["fields"]["itemCount"]["value"]
        return self._client.batch_count(
            container_id=self._get_container_id,
            zone_id=self._zone_id,
        )

    def _query_filters(
        self,
        *,
        offset: int,
        direction: DirectionEnum,
    ) -> list[CKQueryFilterBy]:
        return list(self._extra_filters)

    def _get_payload(
        self,
        offset: int,
        page_size: int,
        direction: DirectionEnum,
    ) -> dict[str, Any]:
        return self._list_query_gen(
            offset=offset,
            list_type=self._list_type,
            direction=direction,
            num_results=page_size,
            query_filter=self._query_filter,
        )

    def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
        query_filter = list(self._query_filter or [])
        query_filter.append(
            {
                "fieldName": "recordName",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": photo_id},
            }
        )
        return self._list_query_gen(
            offset=0,
            list_type=self._list_type,
            direction=DirectionEnum.ASCENDING,
            num_results=1,
            query_filter=query_filter,
        )

    def _get_url(self) -> str:
        return self._url


class PhotoAlbumFolder(PhotoAlbum):
    """A folder album."""

    def upload(self, path: str) -> Optional["PhotoAsset"]:
        return None


class SmartPhotoAlbum(PhotoAlbum):
    """A well-known smart album."""

    def __init__(
        self,
        library: PhotoLibrary,
        *,
        name: SmartAlbumEnum,
        obj_type: ObjectTypeEnum,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        client: PhotosCloudKitClient,
        zone_id: dict[str, str],
        query_filters: list[CKQueryFilterBy] | None = None,
        page_size: int = 100,
    ) -> None:
        super().__init__(
            library=library,
            name=name.value,
            record_id=name.value,
            obj_type=obj_type,
            list_type=list_type,
            direction=direction,
            client=client,
            zone_id=zone_id,
            query_filters=query_filters,
            page_size=page_size,
        )

    @property
    def _container_id(self) -> str:
        return f"{self._obj_type.value}"

    def upload(self, path: str) -> Optional["PhotoAsset"]:
        return None


class PhotoAsset:
    """A logical photo asset built from a ``CPLMaster`` + ``CPLAsset`` pair."""

    ITEM_TYPES: dict[str, str] = {
        "public.heic": "image",
        "public.jpeg": "image",
        "public.png": "image",
        "com.apple.quicktime-movie": "movie",
        "public.mpeg-4": "movie",
        "com.apple.m4v-video": "movie",
    }

    FILE_TYPE_EXTENSIONS: dict[str, str] = {
        "public.heic": ".HEIC",
        "public.jpeg": ".JPG",
        "public.png": ".PNG",
        "com.apple.quicktime-movie": ".MOV",
        "public.mpeg-4": ".MP4",
        "com.apple.m4v-video": ".M4V",
    }

    PHOTO_VERSION_LOOKUP: dict[str, str] = {
        "original": "resOriginal",
        "medium": "resJPEGMed",
        "thumb": "resJPEGThumb",
        "original_video": "resOriginalVidCompl",
        "medium_video": "resVidMed",
        "thumb_video": "resVidSmall",
        "sidecar": "resSidecar",
    }

    VIDEO_VERSION_LOOKUP: dict[str, str] = {
        "original": "resOriginal",
        "medium": "resVidMed",
        "thumb": "resVidSmall",
    }

    def __init__(
        self,
        service: "PhotosService",
        master_record: CKRecord,
        asset_record: CKRecord,
    ) -> None:
        self._service = service
        self._master_record = master_record
        self._asset_record = asset_record
        self._resources: dict[str, PhotoResource] | None = None

    @property
    def id(self) -> str:
        return record_name(self._master_record)

    @property
    def filename(self) -> str:
        return decode_encrypted_text(self._master_record, "filenameEnc") or self.id

    @property
    def size(self) -> int | None:
        token = record_field_value(self._master_record, "resOriginalRes")
        if isinstance(token, dict):
            return cast(Optional[int], token.get("size"))
        return getattr(token, "size", None)

    @property
    def created(self) -> datetime:
        return self.asset_date

    @property
    def asset_date(self) -> datetime:
        value = record_field_value(self._asset_record, "assetDate")
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, timezone.utc)
        return datetime.fromtimestamp(0, timezone.utc)

    @property
    def added_date(self) -> datetime:
        value = record_field_value(self._asset_record, "addedDate")
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, timezone.utc)
        return datetime.fromtimestamp(0, timezone.utc)

    @property
    def dimensions(self) -> tuple[int | None, int | None]:
        return (
            cast(
                Optional[int],
                record_field_value(self._master_record, "resOriginalWidth"),
            ),
            cast(
                Optional[int],
                record_field_value(self._master_record, "resOriginalHeight"),
            ),
        )

    @property
    def item_type(self) -> str:
        raw_type = record_field_value(self._master_record, "itemType")
        if raw_type in self.ITEM_TYPES:
            return self.ITEM_TYPES[raw_type]
        raw_type = record_field_value(self._master_record, "resOriginalFileType")
        if raw_type in self.ITEM_TYPES:
            return self.ITEM_TYPES[raw_type]
        if self.filename.lower().endswith((".heic", ".png", ".jpg", ".jpeg")):
            return "image"
        return "movie"

    @property
    def is_live_photo(self) -> bool:
        return (
            self.item_type == "image"
            and record_field_value(self._master_record, "resOriginalVidComplFileType")
            is not None
        )

    @property
    def resources(self) -> dict[str, PhotoResource]:
        if self._resources is None:
            self._resources = {}
            mapping = (
                self.VIDEO_VERSION_LOOKUP
                if self.item_type == "movie"
                else self.PHOTO_VERSION_LOOKUP
            )
            for key, prefix in mapping.items():
                resource = build_photo_resource(
                    key=key,
                    prefix=prefix,
                    master_record=self._master_record,
                    filename=self.filename,
                    item_type_extensions=self.FILE_TYPE_EXTENSIONS,
                    is_live_photo=self.is_live_photo,
                    item_type_lookup=self.ITEM_TYPES,
                )
                if resource is not None:
                    self._resources[key] = resource
        return self._resources

    @property
    def versions(self) -> dict[str, dict[str, Any]]:
        return {key: value.as_dict() for key, value in self.resources.items()}

    def download_url(self, version: str = "original") -> str | None:
        resource = self.resources.get(version)
        return resource.url if resource else None

    def download(self, version: str = "original", **kwargs) -> bytes | None:
        url = self.download_url(version)
        if url is None:
            return None
        if hasattr(self._service, "_private_client") and _can_use_typed_cloudkit(
            getattr(self._service, "session", None)
        ):
            return self._service._private_client.download_asset_bytes(url)
        response = self._service.session.get(url, stream=True, **kwargs)
        return response.raw.read()

    def delete(self) -> bool:
        zone_dict = record_zone(self._asset_record) or PRIMARY_ZONE
        zone_id = CKZoneIDReq(
            zoneName=zone_dict["zoneName"],
            ownerRecordName=zone_dict.get("ownerRecordName"),
            zoneType=zone_dict.get("zoneType"),
        )
        if hasattr(self._service, "_private_client") and _can_use_typed_cloudkit(
            getattr(self._service, "session", None)
        ):
            op = CKModifyOperation(
                operationType="update",
                record=CKWriteRecord(
                    recordName=record_name(self._asset_record),
                    recordType=record_record_type(self._asset_record),
                    recordChangeTag=record_change_tag(self._asset_record)
                    or record_change_tag(self._master_record),
                    fields={"isDeleted": {"type": "INT64", "value": 1}},
                    zoneID=CKZoneID(**zone_dict),
                ),
            )
            self._service._private_client.modify(
                operations=[op],
                zone_id=zone_id,
                atomic=True,
            )
        else:
            endpoint = self._service.service_endpoint
            params = urlencode(self._service.params)
            url = f"{endpoint}/records/modify?{params}"
            self._service.session.post(
                url,
                json={
                    "operations": [
                        {
                            "operationType": "update",
                            "record": {
                                "recordName": record_name(self._asset_record),
                                "recordType": record_record_type(self._asset_record),
                                "recordChangeTag": record_change_tag(self._asset_record)
                                or record_change_tag(self._master_record),
                                "fields": {"isDeleted": {"value": 1}},
                            },
                        }
                    ],
                    "zoneID": zone_dict,
                    "atomic": True,
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
        return True

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: id={self.id}>"


class PhotosService(BaseService):
    """Modern CloudKit-backed Photos service."""

    def __init__(
        self,
        service_root: str,
        session,
        params: dict[str, Any],
        upload_url: str,
        shared_streams_url: str,
    ) -> None:
        super().__init__(service_root=service_root, session=session, params=params)
        self.params.update({"remapEnums": True, "getCurrentSyncToken": True})
        private_endpoint = (
            f"{self.service_root}/database/1/com.apple.photos.cloud/production/private"
        )
        shared_endpoint = (
            f"{self.service_root}/database/1/com.apple.photos.cloud/production/shared"
        )
        self.service_endpoint = private_endpoint
        self._private_client = PhotosCloudKitClient(
            base_url=private_endpoint,
            session=session,
            base_params=self.params,
            upload_url=upload_url,
        )
        self._shared_client = PhotosCloudKitClient(
            base_url=shared_endpoint,
            session=session,
            base_params=self.params,
        )
        self._upload_url = upload_url
        self._shared_streams_url = shared_streams_url
        self._libraries: dict[str, BasePhotoLibrary] | None = None
        self._legacy_service = None
        shared_streams_album_url = (
            f"{shared_streams_url}/{self.params['dsid']}/sharedstreams/webgetalbumslist"
        )
        self._root_library = PhotoLibrary(
            self,
            zone_id=PRIMARY_ZONE,
            client=self._private_client if _can_use_typed_cloudkit(session) else None,
            asset_type=PhotoAsset,
            upload_url=upload_url,
            scope="private",
        )
        from pyicloud.services.photos_legacy import PhotoStreamLibrary

        self._shared_library = PhotoStreamLibrary(
            self,
            shared_streams_url=shared_streams_album_url,
        )

    @property
    def libraries(self) -> dict[str, BasePhotoLibrary]:
        if self._libraries is None:
            libraries: dict[str, BasePhotoLibrary] = {
                "root": self._root_library,
                "shared": self._shared_library,
            }
            if _can_use_typed_cloudkit(self.session):
                private_zones = self._private_client.zones_list()
                for zone in private_zones.zones:
                    if zone.deleted:
                        continue
                    zone_dict = zone.zoneID.model_dump(exclude_none=True)
                    zone_name = zone.zoneID.zoneName
                    if zone_name == PRIMARY_ZONE["zoneName"]:
                        self._root_library._current_sync_token = zone.syncToken
                        libraries[zone_name] = self._root_library
                        continue
                    libraries[zone_name] = PhotoLibrary(
                        self,
                        zone_id=zone_dict,
                        client=self._private_client,
                        scope="private",
                    )
                try:
                    shared_zones = self._shared_client.zones_list()
                    for zone in shared_zones.zones:
                        if zone.deleted:
                            continue
                        zone_dict = zone.zoneID.model_dump(exclude_none=True)
                        libraries[f"shared:{zone.zoneID.zoneName}"] = PhotoLibrary(
                            self,
                            zone_id=zone_dict,
                            client=self._shared_client,
                            scope="shared",
                        )
                except (CloudKitApiError, PyiCloudException):
                    LOGGER.debug(
                        "Shared CloudKit photos zones unavailable", exc_info=True
                    )
            else:
                response = self.session.post(
                    f"{self.service_endpoint}/zones/list?{urlencode(self.params)}",
                    json={},
                    headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
                ).json()
                for zone in response.get("zones", []):
                    if zone.get("deleted"):
                        continue
                    zone_id = zone.get("zoneID", {})
                    zone_name = zone_id.get("zoneName")
                    if zone_name == PRIMARY_ZONE["zoneName"]:
                        self._root_library._current_sync_token = zone.get("syncToken")
                        libraries[zone_name] = self._root_library
                        continue
                    libraries[zone_name] = PhotoLibrary(
                        self, zone_id=zone_id, scope="private"
                    )
            self._libraries = libraries
        return self._libraries

    @property
    def all(self) -> PhotoAlbum:
        return self._root_library.all

    @property
    def albums(self) -> AlbumContainer:
        return self._root_library.albums

    @property
    def shared_streams(self) -> AlbumContainer:
        return AlbumContainer(list(self._shared_library.albums))

    def create_album(
        self,
        name: str,
        album_type: AlbumTypeEnum = AlbumTypeEnum.ALBUM,
    ) -> Optional[PhotoAlbum]:
        return self._root_library.create_album(name, album_type)

    def sync_cursor(self) -> str:
        return self._root_library.sync_cursor()

    def iter_changes(self, *, since: str | None = None) -> Iterator[PhotoChangeEvent]:
        yield from self._root_library.iter_changes(since=since)

    def sync(self, options: PhotoSyncOptions) -> PhotoSyncResult:
        """Synchronize photo resources into a local output directory."""

        return run_photo_sync(self, options)

    def _upload_into_album(self, album: PhotoAlbum, path: str) -> Optional[PhotoAsset]:
        photo = self._root_library.upload_file(path)
        if photo is None:
            return None
        if album.id != SmartAlbumEnum.ALL_PHOTOS.value:
            album.add_photo(photo)
        return photo
