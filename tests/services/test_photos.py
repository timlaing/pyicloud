"""PhotoLibrary tests."""

from __future__ import annotations

# pylint: disable=protected-access
# pylint: disable=redefined-outer-name
# pylint: disable=abstract-method
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKLookupResponse,
    CKModifyResponse,
    CKQueryResponse,
    CKRecord,
    CKZoneChangesResponse,
    CKZoneListResponse,
)
from pyicloud.common.cloudkit.client import CloudKitApiError
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.photos import (
    PRIMARY_ZONE,
    AlbumContainer,
    AlbumTypeEnum,
    BasePhotoAlbum,
    BasePhotoLibrary,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    PhotoAlbum,
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    PhotosServiceException,
    PhotoStreamLibrary,
    SharedPhotoStreamAlbum,
    SmartAlbumEnum,
    SmartPhotoAlbum,
)
from pyicloud.services.photos_cloudkit.mappers import (
    record_change_tag,
    record_field_value,
)
from pyicloud.services.photos_cloudkit.queries import parent_filter, smart_album_filter

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
BROWSER_MUTATION_FIXTURE_DIR = FIXTURE_DIR / "photos_browser_mutations"
ALBUM_CREATE_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_album_create_response.json").read_text(encoding="utf-8")
)
ALBUM_RENAME_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_album_rename_response.json").read_text(encoding="utf-8")
)
INDEXING_NOT_FINISHED_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_indexing_not_finished_response.json").read_text(
        encoding="utf-8"
    )
)
ZONES_LIST_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_zones_list_response.json").read_text(encoding="utf-8")
)
SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_private_zones_response.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_SHARED_ZONES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_shared_zones_response.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_all_photos_query_core.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_ALL_PHOTOS_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_all_photos_response.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_FAVORITES_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_favorites_query_core.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_FAVORITES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_favorites_response.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_ZONE_CHANGES_REQUEST = json.loads(
    (FIXTURE_DIR / "photos_shared_library_zone_changes_request.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_ZONE_CHANGES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_zone_changes_response.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_UNFAVORITE_REQUEST = json.loads(
    (FIXTURE_DIR / "photos_shared_library_unfavorite_request.json").read_text(
        encoding="utf-8"
    )
)
SHARED_LIBRARY_UNFAVORITE_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_shared_library_unfavorite_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ZONE_CHANGES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_zone_changes_response.json").read_text(encoding="utf-8")
)
ALL_PHOTOS_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_all_photos_query_core.json").read_text(encoding="utf-8")
)
ALL_PHOTOS_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_all_photos_response.json").read_text(encoding="utf-8")
)
RECENTLY_ADDED_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_recently_added_query_core.json").read_text(encoding="utf-8")
)
RECENTLY_ADDED_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_recently_added_response.json").read_text(encoding="utf-8")
)
FAVORITES_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_favorites_query_core.json").read_text(encoding="utf-8")
)
FAVORITES_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_favorites_response.json").read_text(encoding="utf-8")
)
MISSING_COUNTERPARTS_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_missing_counterparts_response.json").read_text(
        encoding="utf-8"
    )
)
ALBUM_MEMBERSHIP_QUERY_CORE = json.loads(
    (FIXTURE_DIR / "photos_album_membership_query_core.json").read_text(
        encoding="utf-8"
    )
)
ALBUM_MEMBERSHIP_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_album_membership_response.json").read_text(encoding="utf-8")
)
LIVE_PHOTO_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_live_photo_response.json").read_text(encoding="utf-8")
)
VIDEO_ONLY_RESPONSE = json.loads(
    (FIXTURE_DIR / "photos_video_only_response.json").read_text(encoding="utf-8")
)
BROWSER_ALBUM_CREATE_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_create_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_CREATE_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_create_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_RENAME_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_rename_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_RENAME_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_rename_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_ADD_PHOTO_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_add_photo_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_ADD_PHOTO_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_add_photo_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_REMOVE_PHOTO_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_remove_photo_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_REMOVE_PHOTO_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_remove_photo_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_PHOTO_DELETE_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "photo_delete_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_PHOTO_DELETE_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "photo_delete_response.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_DELETE_REQUEST = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_delete_request.json").read_text(
        encoding="utf-8"
    )
)
BROWSER_ALBUM_DELETE_RESPONSE = json.loads(
    (BROWSER_MUTATION_FIXTURE_DIR / "album_delete_response.json").read_text(
        encoding="utf-8"
    )
)


def _ck_record(
    record_type: str,
    record_name: str,
    fields: dict[str, Any] | None = None,
    **extra: Any,
) -> CKRecord:
    raw = {
        "recordName": record_name,
        "recordType": record_type,
        "fields": fields or {},
        **extra,
    }
    return CKRecord.model_validate(raw)


def _last_posted_json(mock_post: MagicMock) -> dict[str, Any]:
    return mock_post.call_args.kwargs["json"]


def _payload_filter_map(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        item["fieldName"]: item["fieldValue"]["value"]
        for item in payload["query"]["filterBy"]
    }


def _indexing_ready_response(sync_token: str = "sync-token") -> CKQueryResponse:
    return CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken=sync_token,
    )


def test_photo_library_initialization(mock_photos_service: MagicMock) -> None:
    """Tests initialization of PhotoLibrary."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
    ]
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    assert library.zone_id == {"zoneName": "PrimarySync"}
    assert library.url == ("https://example.com/records/query?dsid=12345")


def test_photo_library_indexing_not_finished(mock_photos_service: MagicMock) -> None:
    """Tests exception when indexing is not finished."""
    mock_photos_service.session.post.return_value.json.return_value = (
        INDEXING_NOT_FINISHED_RESPONSE
    )
    with pytest.raises(PyiCloudServiceNotActivatedException):
        PhotoLibrary(
            service=mock_photos_service,
            zone_id={"zoneName": "PrimarySync"},
            upload_url="https://upload.example.com",
        )


def test_photo_library_sync_cursor_uses_zones_list_fixture(
    mock_photos_service: MagicMock,
) -> None:
    """Raw sync-cursor discovery should use the tracked zones/list fixture."""

    mock_photos_service.session.post.return_value.json.return_value = (
        ZONES_LIST_RESPONSE
    )
    library = PhotoLibrary.__new__(PhotoLibrary)
    library.service = mock_photos_service
    library._zone_id = {
        "zoneName": "PrimarySync",
        "ownerRecordName": "OWNER_RECORD_NAME_001",
        "zoneType": "REGULAR_CUSTOM_ZONE",
    }
    library.zone_id = library._zone_id
    library._client = None
    library._current_sync_token = None

    assert library.sync_cursor() == "SYNC_TOKEN_101"
    mock_photos_service.session.post.assert_called_once_with(
        "https://example.com/zones/list?dsid=12345",
        json={},
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_library_iter_changes_uses_zone_changes_fixture() -> None:
    """Tracked zone-change fixtures should map into PhotoChangeEvent objects."""

    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="SYNC_TOKEN_100",
    )
    mock_client.iter_changes.return_value = iter(
        CKZoneChangesResponse.model_validate(BROWSER_ZONE_CHANGES_RESPONSE).zones
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )
    library = PhotoLibrary(
        service=service,
        zone_id={
            "zoneName": "PrimarySync",
            "ownerRecordName": "OWNER_RECORD_NAME_001",
            "zoneType": "REGULAR_CUSTOM_ZONE",
        },
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    events = list(library.iter_changes(since="SYNC_TOKEN_102"))

    assert [event.kind for event in events] == ["updated", "deleted"]
    assert events[0].record_name == "ASSET_RECORD_ID_101"
    assert events[0].record_type == "CPLAsset"
    assert events[0].deleted is False
    assert events[0].modified == datetime.fromtimestamp(
        1775666233042 / 1000, tz=timezone.utc
    )
    assert events[1].record_name == "ALBUM_RECORD_ID_999"
    assert events[1].record_type is None
    assert events[1].deleted is True
    assert events[1].modified is None
    assert library.current_sync_token == "SYNC_TOKEN_103"


def test_fetch_folders(mock_photos_service: MagicMock) -> None:
    """Tests the _fetch_folders method."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder1",
                            "recordChangeTag": "tag1",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMQ=="},
                                "isDeleted": {"value": False},
                            },
                        }
                    ]
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    library.SMART_ALBUMS = {}
    albums: AlbumContainer = library.albums

    assert len(albums) == 1
    assert albums[0].name == "folder1"


def test_get_albums(mock_photos_service: MagicMock) -> None:
    """Tests the _get_albums method."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder1",
                            "recordChangeTag": "tag1",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMQ=="},
                                "isDeleted": {"value": False},
                            },
                        },
                        {
                            "recordName": "1111-1111-1111-1111",
                            "recordChangeTag": "tag2",
                            "fields": {
                                "albumNameEnc": {"value": "QWxidW0gTmFtZSAy"},
                                "isDeleted": {"value": False},
                            },
                        },
                    ]
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    albums: AlbumContainer = library.albums
    assert SmartAlbumEnum.ALL_PHOTOS in albums
    assert "folder1" in albums
    assert albums["folder1"].name == "folder1"
    assert albums["Album Name 2"].id == "1111-1111-1111-1111"
    assert albums.index(1).id == "Time-lapse"
    assert albums.get("Nonexistent Album") is None
    assert albums[0] == next(iter(albums))
    with pytest.raises(KeyError):
        _ = albums["Album Name 3"]

    with pytest.raises(IndexError):
        _ = albums.index(100)


def test_upload_file_success(mock_photos_service: MagicMock) -> None:
    """Tests the upload_file method for successful upload."""
    mock_photos_service.params = {"dsid": "12345"}
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "uploaded_photo",
                            "recordChangeTag": "tag1",
                            "recordType": "CPLAsset",
                            "fields": {
                                "masterRef": {"value": {"recordName": "uploaded_photo"}}
                            },
                        },
                        {
                            "recordType": "CPLMaster",
                            "recordName": "uploaded_photo",
                        },
                    ]
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )

    with patch("builtins.open", mock_open(read_data=b"file_content")) as mock_file:
        asset: PhotoAsset | None = library.upload_file("test_photo.jpg")

    assert asset is not None
    assert asset.id == "uploaded_photo"
    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload?dsid=12345&filename=test_photo.jpg",
        data=mock_file.return_value,
    )


def test_upload_file_with_errors(mock_photos_service: MagicMock) -> None:
    """Tests the upload_file method when the response contains errors."""
    mock_photos_service.params = {"dsid": "12345"}
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "errors": [
                        {
                            "code": "UPLOAD_ERROR",
                            "message": "Upload failed",
                        },
                    ],
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )

    with patch("builtins.open", mock_open(read_data=b"file_content")) as mock_file:
        with pytest.raises(PyiCloudAPIResponseException) as exc_info:
            library.upload_file("test_photo.jpg")

    assert "UPLOAD_ERROR" in str(exc_info.value)
    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload?dsid=12345&filename=test_photo.jpg",
        data=mock_file.return_value,
    )


def test_upload_file_no_records(mock_photos_service: MagicMock) -> None:
    """Tests the upload_file method when no records are returned."""
    mock_photos_service.params = {"dsid": "12345"}
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [],
                }
            )
        ),
    ]
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )

    with patch("builtins.open", mock_open(read_data=b"file_content")) as mock_file:
        result: PhotoAsset | None = library.upload_file("test_photo.jpg")
        assert result is None

    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload?dsid=12345&filename=test_photo.jpg",
        data=mock_file.return_value,
    )


def test_upload_file_success_typed_client() -> None:
    """Tests upload_file delegates to the typed Photos client when available."""
    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.upload_file.return_value = {
        "records": [
            {
                "recordName": "uploaded_photo",
                "recordChangeTag": "tag1",
                "recordType": "CPLAsset",
                "fields": {
                    "masterRef": {"value": {"recordName": "uploaded_photo"}},
                    "assetDate": {"value": 1700000000000},
                    "addedDate": {"value": 1700000000000},
                },
                "zoneID": {"zoneName": "PrimarySync"},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "uploaded_photo",
                "recordChangeTag": "tag2",
                "fields": {
                    "filenameEnc": {
                        "value": base64.b64encode(b"uploaded_photo.jpg").decode("utf-8")
                    }
                },
                "zoneID": {"zoneName": "PrimarySync"},
            },
        ]
    }
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    asset = library.upload_file("test_photo.jpg")

    assert asset is not None
    assert asset.id == "uploaded_photo"
    mock_client.upload_file.assert_called_once_with("test_photo.jpg", dsid="12345")


def test_upload_file_typed_client_hydrates_skeletal_records() -> None:
    """Tests upload_file performs a lookup when upload returns skeletal records."""
    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.upload_file.return_value = {
        "records": [
            {
                "recordType": "CPLMaster",
                "recordName": "master123",
            },
            {
                "recordType": "CPLAsset",
                "recordName": "asset123",
            },
        ]
    }
    mock_client.lookup.return_value = CKLookupResponse(
        records=[
            _ck_record(
                "CPLMaster",
                "master123",
                {
                    "filenameEnc": {
                        "type": "STRING",
                        "value": base64.b64encode(b"uploaded_photo.jpg").decode(
                            "utf-8"
                        ),
                    }
                },
                recordChangeTag="master-tag",
            ),
            _ck_record(
                "CPLAsset",
                "asset123",
                {
                    "masterRef": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": "master123",
                            "action": "NONE",
                        },
                    },
                    "assetDate": {"type": "TIMESTAMP", "value": 1700000000000},
                    "addedDate": {"type": "TIMESTAMP", "value": 1700000000000},
                },
                recordChangeTag="asset-tag",
            ),
        ],
        syncToken="sync-token",
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    asset = library.upload_file("test_photo.jpg")

    assert asset is not None
    assert asset.id == "master123"
    assert asset.filename == "uploaded_photo.jpg"
    mock_client.lookup.assert_called_once()
    assert mock_client.lookup.call_args.kwargs["record_names"] == [
        "master123",
        "asset123",
    ]
    assert mock_client.lookup.call_args.kwargs["zone_id"].zoneName == "PrimarySync"
    assert "filenameEnc" in mock_client.lookup.call_args.kwargs["desired_keys"]


def test_upload_file_typed_client_hydrates_duplicate_upload_records() -> None:
    """Tests duplicate uploads still resolve to a usable PhotoAsset."""

    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.upload_file.return_value = {
        "isDuplicate": True,
        "records": [
            {
                "recordType": "CPLMaster",
                "recordName": "master123",
            },
            {
                "recordType": "CPLAsset",
                "recordName": "asset123",
            },
        ],
    }
    mock_client.lookup.return_value = CKLookupResponse(
        records=[
            _ck_record(
                "CPLMaster",
                "master123",
                {
                    "filenameEnc": {
                        "type": "STRING",
                        "value": base64.b64encode(b"existing_photo.jpg").decode(
                            "utf-8"
                        ),
                    }
                },
                recordChangeTag="master-tag",
            ),
            _ck_record(
                "CPLAsset",
                "asset123",
                {
                    "masterRef": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": "master123",
                            "action": "NONE",
                        },
                    },
                    "assetDate": {"type": "TIMESTAMP", "value": 1700000000000},
                    "addedDate": {"type": "TIMESTAMP", "value": 1700000000000},
                },
                recordChangeTag="asset-tag",
            ),
        ],
        syncToken="sync-token",
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    asset = library.upload_file("test_photo.jpg")

    assert asset is not None
    assert asset.id == "master123"
    assert asset.filename == "existing_photo.jpg"
    mock_client.lookup.assert_called_once()


def test_upload_file_typed_client_raises_api_response_exception() -> None:
    """Tests typed upload errors are normalized to the public exception type."""
    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.upload_file.side_effect = CloudKitApiError(
        "UPLOAD_ERROR: Upload failed"
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    with pytest.raises(PyiCloudAPIResponseException, match="UPLOAD_ERROR"):
        library.upload_file("test_photo.jpg")


def test_fetch_folders_multiple_pages(mock_photos_service: MagicMock) -> None:
    """Tests _fetch_folders with multiple pages of results."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        },
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder1",
                            "recordChangeTag": "tag1",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMQ=="},
                                "isDeleted": {"value": False},
                            },
                        }
                    ],
                    "continuationMarker": "marker1",
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder2",
                            "recordChangeTag": "tag2",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMg=="},
                                "isDeleted": {"value": False},
                            },
                        }
                    ]
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    library.SMART_ALBUMS = {}
    albums: AlbumContainer = library.albums
    assert len(albums) == 2
    assert albums[0].name == "folder1"
    assert albums[1].name == "folder2"
    mock_photos_service.session.post.assert_called()


def test_fetch_folders_skips_deleted_folders(mock_photos_service: MagicMock) -> None:
    """Tests _fetch_folders skips folders marked as deleted."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        },
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "continuationMarker": "marker1",
                    "records": [
                        {
                            "recordName": "folder1",
                            "recordChangeTag": "tag1",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMQ=="},
                                "isDeleted": {"value": True},
                            },
                        },
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder2",
                            "recordChangeTag": "tag2",
                            "fields": {
                                "albumNameEnc": {"value": "Zm9sZGVyMg=="},
                                "isDeleted": {"value": False},
                            },
                        },
                    ]
                },
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    library.SMART_ALBUMS = {}
    albums: AlbumContainer = library.albums

    assert len(albums) == 1
    assert albums[0].name == "folder2"
    mock_photos_service.session.post.assert_called()


def test_fetch_folders_no_records(mock_photos_service: MagicMock) -> None:
    """Tests _fetch_folders when no records are returned."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        },
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [],
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    library.SMART_ALBUMS = {}
    albums: AlbumContainer = library.albums

    assert len(albums) == 0
    mock_photos_service.session.post.assert_called()


def test_fetch_folders_handles_missing_fields(mock_photos_service: MagicMock) -> None:
    """Tests _fetch_folders handles records with missing fields."""
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        },
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "folder1",
                            "fields": {
                                "isDeleted": {"value": False},
                            },
                        }
                    ]
                }
            )
        ),
    ]

    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    library.SMART_ALBUMS = {}
    albums: AlbumContainer = library.albums

    assert len(albums) == 0
    mock_photos_service.session.post.assert_called()


def test_base_photo_album_initialization(mock_photo_library: MagicMock) -> None:
    """Tests initialization of BasePhotoAlbum."""

    class MyPhotoAlbum(BasePhotoAlbum):
        """Mock BasePhotoAlbum subclass for testing."""

        def _get_len(self) -> int:
            return 0

        def _get_payload(
            self, offset: int, page_size: int, direction: DirectionEnum
        ) -> dict[str, Any]:
            return {}

        def _get_url(self) -> str:
            return "https://example.com/test_album"

        def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
            return {}

        @property
        def fullname(self) -> str:
            return "Test Album"

        @property
        def id(self) -> str:
            return "test_album"

    album = MyPhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
        page_size=50,
        direction=DirectionEnum.ASCENDING,
    )
    assert album.name == "Test Album"
    assert album.service == mock_photo_library.service
    assert album.page_size == 50


def test_base_photo_album_added_descending_photos_use_recent_window_paging(
    mock_photo_library: MagicMock,
) -> None:
    """Added-date feeds should page newest-first without relying on count lookup."""

    class MyPhotoAlbum(BasePhotoAlbum):
        """Mock album with recently-added index semantics."""

        def _get_len(self) -> int:
            return 0

        def _get_photos_at(
            self,
            index: int,
            direction: DirectionEnum,
            page_size: int,
        ):
            assert direction == DirectionEnum.DESCENDING
            assert page_size == 3
            windows = {
                2: ["photo-2", "photo-1", "photo-0"],
                5: ["photo-4", "photo-3"],
            }
            for photo_id in windows.get(index, []):
                yield SimpleNamespace(id=photo_id)

        def _get_payload(
            self, offset: int, page_size: int, direction: DirectionEnum
        ) -> dict[str, Any]:
            return {}

        def _get_url(self) -> str:
            return "https://example.com/test_album"

        def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
            return {}

        @property
        def fullname(self) -> str:
            return "Recently Added"

        @property
        def id(self) -> str:
            return "recent"

    album = MyPhotoAlbum(
        library=mock_photo_library,
        name="Recently Added",
        list_type=ListTypeEnum.ADDED,
        page_size=3,
        direction=DirectionEnum.DESCENDING,
    )

    assert [photo.id for photo in album.photos] == [
        "photo-0",
        "photo-1",
        "photo-2",
        "photo-3",
        "photo-4",
    ]


def test_base_photo_album_parse_response(mock_photo_library: MagicMock) -> None:
    """Tests the _parse_response method."""
    response = {
        "records": [
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "master1"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "master1",
            },
        ]
    }
    asset_records, master_records = mock_photo_library.parse_asset_response(response)
    assert "master1" in asset_records
    assert len(master_records) == 1
    assert master_records[0]["recordName"] == "master1"


def test_base_photo_album_get_photos_at(mock_photo_library: MagicMock) -> None:
    """Tests the _get_photos_at method."""
    mock_photo_library.service.session.post.return_value.json.side_effect = [
        {
            "records": [
                {
                    "recordType": "CPLAsset",
                    "fields": {"masterRef": {"value": {"recordName": "master1"}}},
                },
                {
                    "recordType": "CPLMaster",
                    "recordName": "master1",
                },
            ]
        },
        {
            "records": [],
        },
    ]
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
        obj_type=ObjectTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        page_size=10,
        record_id="album1",
        url="https://example.com/records/query?dsid=12345",
    )
    photos = list(album.photos)
    assert len(photos) == 1
    mock_photo_library.service.session.post.assert_called()


def test_all_photos_feed_uses_default_index_and_fixture_response(
    mock_photo_library: MagicMock,
) -> None:
    """The Library smart album should use the all-photos index and parse fixture data."""

    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.service.session.post.return_value.json.return_value = (
        ALL_PHOTOS_RESPONSE
    )
    album = SmartPhotoAlbum(
        library=mock_photo_library,
        name=SmartAlbumEnum.ALL_PHOTOS,
        obj_type=ObjectTypeEnum.ALL,
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.DESCENDING,
        client=MagicMock(),
        zone_id=PRIMARY_ZONE,
    )

    photos = list(album._get_photos_at(0, DirectionEnum.DESCENDING, 1))

    posted = _last_posted_json(mock_photo_library.service.session.post)
    assert posted["query"]["recordType"] == ALL_PHOTOS_QUERY_CORE["recordType"]
    assert posted["resultsLimit"] == ALL_PHOTOS_QUERY_CORE["resultsLimit"]
    assert _payload_filter_map(posted) == ALL_PHOTOS_QUERY_CORE["filters"]
    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_201"
    assert photos[0].filename == "all_photo.jpg"


def test_recently_added_feed_uses_added_index_and_fixture_response(
    mock_photo_library: MagicMock,
) -> None:
    """The recently-added feed should use the added-date index and parse fixture data."""

    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.service.session.post.return_value.json.return_value = (
        RECENTLY_ADDED_RESPONSE
    )
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Recently Added",
        record_id="Recently Added",
        obj_type=ObjectTypeEnum.ALL,
        list_type=ListTypeEnum.ADDED,
        direction=DirectionEnum.DESCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id=PRIMARY_ZONE,
    )

    photos = list(album._get_photos_at(0, DirectionEnum.DESCENDING, 1))

    posted = _last_posted_json(mock_photo_library.service.session.post)
    assert posted["query"]["recordType"] == RECENTLY_ADDED_QUERY_CORE["recordType"]
    assert posted["resultsLimit"] == RECENTLY_ADDED_QUERY_CORE["resultsLimit"]
    assert _payload_filter_map(posted) == RECENTLY_ADDED_QUERY_CORE["filters"]
    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_202"
    assert photos[0].filename == "recent_added.jpg"


def test_favorites_feed_uses_smart_album_filter_and_fixture_response(
    mock_photo_library: MagicMock,
) -> None:
    """Favorite smart albums should project the raw smartAlbum selector as well."""

    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.service.session.post.return_value.json.return_value = (
        FAVORITES_RESPONSE
    )
    album = SmartPhotoAlbum(
        library=mock_photo_library,
        name=SmartAlbumEnum.FAVORITES,
        obj_type=ObjectTypeEnum.FAVORITE,
        list_type=ListTypeEnum.SMART_ALBUM,
        direction=DirectionEnum.ASCENDING,
        client=MagicMock(),
        zone_id=PRIMARY_ZONE,
        query_filters=[smart_album_filter("FAVORITE")],
    )

    photos = list(album._get_photos_at(0, DirectionEnum.ASCENDING, 1))

    posted = _last_posted_json(mock_photo_library.service.session.post)
    assert posted["query"]["recordType"] == FAVORITES_QUERY_CORE["recordType"]
    assert posted["resultsLimit"] == FAVORITES_QUERY_CORE["resultsLimit"]
    assert _payload_filter_map(posted) == FAVORITES_QUERY_CORE["filters"]
    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_203"
    assert photos[0].filename == "favorite_photo.jpg"


def test_process_photo_list_response_skips_missing_counterparts_fixture(
    mock_photo_library: MagicMock,
) -> None:
    """Only matched master/asset pairs should materialize into PhotoAsset objects."""

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    photos = list(album._process_photo_list_response(MISSING_COUNTERPARTS_RESPONSE))

    assert len(photos) == 1
    assert photos[0].id == "MASTER_MATCHED_001"
    assert photos[0].filename == "matched_photo.jpg"


def test_process_photo_list_response_maps_live_photo_fixture(
    mock_photo_library: MagicMock,
) -> None:
    """Fixture-backed live photos should expose paired movie resources."""

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    photos = list(album._process_photo_list_response(LIVE_PHOTO_RESPONSE))

    assert len(photos) == 1
    photo = photos[0]
    assert photo.id == "MASTER_RECORD_ID_204"
    assert photo.filename == "live_photo.HEIC"
    assert photo.item_type == "image"
    assert photo.is_live_photo is True
    assert photo.versions["original_video"]["filename"] == "live_photo.MOV"
    assert (
        photo.versions["original_video"]["url"] == "https://example.com/live_photo.mov"
    )


def test_process_photo_list_response_maps_video_only_fixture(
    mock_photo_library: MagicMock,
) -> None:
    """Fixture-backed movie assets should map as video-only resources."""

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    photos = list(album._process_photo_list_response(VIDEO_ONLY_RESPONSE))

    assert len(photos) == 1
    photo = photos[0]
    assert photo.id == "MASTER_RECORD_ID_205"
    assert photo.filename == "video_only.MOV"
    assert photo.item_type == "movie"
    assert photo.is_live_photo is False
    assert "original_video" not in photo.versions
    assert photo.versions["original"]["url"] == "https://example.com/video_only.mov"
    assert photo.versions["thumb"]["filename"] == "video_only.MOV"


def test_album_membership_feed_uses_container_relation_fixture(
    mock_photo_library: MagicMock,
) -> None:
    """Album membership reads should use the container-relation index."""

    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.service.session.post.return_value.json.return_value = (
        ALBUM_MEMBERSHIP_RESPONSE
    )
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Fixture Album",
        record_id="ALBUM_RECORD_ID_301",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        query_filters=[parent_filter("ALBUM_RECORD_ID_301")],
        zone_id=PRIMARY_ZONE,
    )

    photos = list(album._get_photos_at(0, DirectionEnum.ASCENDING, 1))

    posted = _last_posted_json(mock_photo_library.service.session.post)
    assert posted["query"]["recordType"] == ALBUM_MEMBERSHIP_QUERY_CORE["recordType"]
    assert posted["resultsLimit"] == ALBUM_MEMBERSHIP_QUERY_CORE["resultsLimit"]
    assert _payload_filter_map(posted) == ALBUM_MEMBERSHIP_QUERY_CORE["filters"]
    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_206"
    assert photos[0].filename == "album_membership.jpg"


def test_base_photo_album_len(mock_photo_album) -> None:
    """Tests the __len__ method."""
    mock_photo_album._get_len = MagicMock(return_value=42)
    assert len(mock_photo_album) == 42
    mock_photo_album._get_len.assert_called_once()


def test_base_photo_album_iter(mock_photo_library: MagicMock) -> None:
    """Tests the __iter__ method."""
    mock_photo_library.service.session.post.return_value.json.side_effect = [
        {
            "records": [
                {
                    "recordType": "CPLAsset",
                    "fields": {"masterRef": {"value": {"recordName": "master1"}}},
                },
                {
                    "recordType": "CPLMaster",
                    "recordName": "master1",
                },
            ]
        },
        {
            "records": [],
        },
    ]
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
        obj_type=ObjectTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        page_size=10,
        url="https://example.com/records/query?dsid=12345",
        record_id="album1",
    )
    photos = list(iter(album))
    assert len(photos) == 1
    mock_photo_library.service.session.post.assert_called()


def test_base_photo_album_str(mock_photo_album) -> None:
    """Tests the __str__ method."""
    assert str(mock_photo_album) == "Test Album"


def test_base_photo_album_is_truthy_even_when_empty(mock_photo_album) -> None:
    """Albums should be truthy objects even if their current item count is zero."""

    mock_photo_album._len = 0

    assert bool(mock_photo_album) is True


def test_base_photo_album_repr(mock_photo_album) -> None:
    """Tests the __repr__ method."""
    assert repr(mock_photo_album) == "<MyPhotoAlbum: 'Test Album'>"


def test_photos_service_initialization(mock_photos_service: MagicMock) -> None:
    """Tests initialization of PhotosService."""
    mock_photos_service.session.post.return_value.json.return_value = {
        "records": [
            {
                "fields": {
                    "state": {"value": "FINISHED"},
                },
            }
        ]
    }
    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )
    assert photos_service.service_endpoint == (
        "https://example.com/database/1/com.apple.photos.cloud/production/private"
    )
    assert isinstance(photos_service._root_library, PhotoLibrary)
    assert isinstance(photos_service._shared_library, PhotoStreamLibrary)
    assert photos_service.params["remapEnums"] is True
    assert photos_service.params["getCurrentSyncToken"] is True


def test_photos_service_libraries(mock_photos_service: MagicMock) -> None:
    """Tests the libraries property."""
    mock_photos_service.session.post.return_value.json.side_effect = [
        {
            "records": [
                {
                    "fields": {
                        "state": {"value": "FINISHED"},
                    },
                }
            ]
        },
        {
            "zones": [
                {
                    "zoneID": {
                        "zoneName": "PrimarySync",
                        "zoneType": "REGULAR_CUSTOM_ZONE",
                    },
                    "deleted": False,
                    "syncToken": "root-sync-token",
                },
                {"zoneID": {"zoneName": "CustomZone"}, "deleted": False},
            ]
        },
        {
            "records": [
                {
                    "fields": {
                        "state": {"value": "FINISHED"},
                    },
                }
            ]
        },
    ]
    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )
    libraries: dict[str, BasePhotoLibrary] = photos_service.libraries
    assert "root" in libraries
    assert "shared" in libraries
    assert "CustomZone" in libraries
    assert "PrimarySync" not in libraries
    assert isinstance(libraries["root"], PhotoLibrary)
    assert isinstance(libraries["shared"], PhotoStreamLibrary)
    assert isinstance(libraries["CustomZone"], PhotoLibrary)
    assert libraries["root"].current_sync_token == "root-sync-token"
    mock_photos_service.session.post.assert_called_with(
        url=(
            "https://example.com/database/1/com.apple.photos.cloud/production/private/records/query"
            "?dsid=12345&remapEnums=True&getCurrentSyncToken=True"
        ),
        json={
            "query": {"recordType": "CheckIndexingState"},
            "zoneID": {"zoneName": "CustomZone"},
        },
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photos_service_libraries_cached(mock_photos_service: MagicMock) -> None:
    """Tests that libraries are cached after the first access."""
    mock_photos_service.session.post.return_value.json.return_value = {
        "records": [
            {
                "fields": {
                    "state": {"value": "FINISHED"},
                },
            }
        ]
    }
    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )
    mock_libraries = {"cached": MagicMock(spec=PhotoLibrary)}
    photos_service._libraries = mock_libraries  # type: ignore
    libraries: dict[str, BasePhotoLibrary] = photos_service.libraries
    assert libraries == mock_libraries
    mock_photos_service.session.post.assert_called_once()


def test_photos_service_libraries_classify_shared_sync_zone_raw_path(
    mock_photos_service: MagicMock,
) -> None:
    """Raw zones/list fallback should surface SharedSync zones as Shared Library entries."""

    shared_zone = SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE["zones"][0]["zoneID"]
    mock_photos_service.session.post.return_value.json.side_effect = [
        {
            "records": [
                {
                    "fields": {
                        "state": {"value": "FINISHED"},
                    },
                }
            ]
        },
        SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE,
        {
            "records": [
                {
                    "fields": {
                        "state": {"value": "FINISHED"},
                    },
                }
            ]
        },
    ]

    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )

    libraries: dict[str, BasePhotoLibrary] = photos_service.libraries
    shared_key = "shared:SharedSync-6E1C0494-1BF4-4928-BD07-3FD81633193E"

    assert shared_key in libraries
    assert "SharedSync-6E1C0494-1BF4-4928-BD07-3FD81633193E" not in libraries
    assert libraries[shared_key].scope == "shared-library"
    assert libraries["root"].current_sync_token == "SYNC_TOKEN_001"
    mock_photos_service.session.post.assert_called_with(
        url=(
            "https://example.com/database/1/com.apple.photos.cloud/production/private/records/query"
            "?dsid=12345&remapEnums=True&getCurrentSyncToken=True"
        ),
        json={
            "query": {"recordType": "CheckIndexingState"},
            "zoneID": shared_zone,
        },
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photos_service_libraries_classify_shared_sync_zone_typed_path() -> None:
    """Typed zones/list discovery should reuse private SharedSync zones."""

    def _mark_indexing_ready(instance: PhotoLibrary) -> None:
        instance._indexing_state = "FINISHED"

    with patch.object(
        PhotoLibrary,
        "_ensure_indexing_ready",
        autospec=True,
        side_effect=_mark_indexing_ready,
    ):
        photos_service = PhotosService(
            service_root="https://example.com",
            session=object(),
            params={"dsid": "12345"},
            upload_url="https://upload.example.com",
            shared_streams_url="https://shared.example.com",
        )
        photos_service._private_client.zones_list = MagicMock(
            return_value=CKZoneListResponse.model_validate(
                SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE
            )
        )
        photos_service._shared_client.zones_list = MagicMock(
            return_value=CKZoneListResponse.model_validate(
                SHARED_LIBRARY_SHARED_ZONES_RESPONSE
            )
        )

        libraries = photos_service.libraries

    shared_key = "shared:SharedSync-6E1C0494-1BF4-4928-BD07-3FD81633193E"
    assert shared_key in libraries
    assert "SharedSync-6E1C0494-1BF4-4928-BD07-3FD81633193E" not in libraries
    assert libraries[shared_key].scope == "shared-library"
    assert libraries[shared_key]._client is photos_service._private_client
    assert libraries["root"].current_sync_token == "SYNC_TOKEN_001"
    photos_service._private_client.zones_list.assert_called_once()
    photos_service._shared_client.zones_list.assert_called_once()


def test_shared_library_all_photos_feed_uses_captured_fixture() -> None:
    """Shared Library all-photos reads should target the SharedSync zone."""

    shared_zone = SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE["zones"][0]["zoneID"]
    service = MagicMock()
    service.service_endpoint = "https://example.com/endpoint"
    service.params = {"dsid": "12345"}
    service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={"records": [{"fields": {"state": {"value": "FINISHED"}}}]}
            )
        ),
        MagicMock(json=MagicMock(return_value=SHARED_LIBRARY_ALL_PHOTOS_RESPONSE)),
    ]
    library = PhotoLibrary(
        service=service,
        zone_id=shared_zone,
        upload_url="https://upload.example.com",
        scope="shared-library",
    )
    library._fetch_album_records = MagicMock(return_value=[])

    photos = list(
        library.all._get_photos_at(
            SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE["filters"]["startRank"],
            DirectionEnum.DESCENDING,
            SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE["resultsLimit"] // 2,
        )
    )

    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_111"
    assert photos[0].filename == "shared_library_photo.jpg"
    posted = _last_posted_json(service.session.post)
    assert (
        posted["query"]["recordType"]
        == SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE["recordType"]
    )
    assert (
        posted["resultsLimit"] == SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE["resultsLimit"]
    )
    assert (
        _payload_filter_map(posted) == SHARED_LIBRARY_ALL_PHOTOS_QUERY_CORE["filters"]
    )
    assert posted["zoneID"] == shared_zone


def test_shared_library_all_photos_skips_album_record_fetch() -> None:
    """Shared Library should expose only the currently supported smart albums."""

    shared_zone = SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE["zones"][0]["zoneID"]
    service = MagicMock()
    service.service_endpoint = "https://example.com/endpoint"
    service.params = {"dsid": "12345"}
    service.session.post.return_value = MagicMock(
        json=MagicMock(
            return_value={"records": [{"fields": {"state": {"value": "FINISHED"}}}]}
        )
    )
    library = PhotoLibrary(
        service=service,
        zone_id=shared_zone,
        upload_url="https://upload.example.com",
        scope="shared-library",
    )
    library._fetch_album_records = MagicMock(side_effect=AssertionError("unexpected"))

    album_ids = [album.id for album in library.albums]

    assert album_ids == [
        SmartAlbumEnum.ALL_PHOTOS.value,
        SmartAlbumEnum.FAVORITES.value,
    ]
    assert library.all.id == SmartAlbumEnum.ALL_PHOTOS.value
    assert library.albums[SmartAlbumEnum.FAVORITES.value].id == (
        SmartAlbumEnum.FAVORITES.value
    )
    library._fetch_album_records.assert_not_called()


def test_shared_library_favorites_feed_uses_captured_fixture() -> None:
    """Shared Library favorites should use the captured smart-album query shape."""

    shared_zone = SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE["zones"][0]["zoneID"]
    service = MagicMock()
    service.service_endpoint = "https://example.com/endpoint"
    service.params = {"dsid": "12345"}
    service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={"records": [{"fields": {"state": {"value": "FINISHED"}}}]}
            )
        ),
        MagicMock(json=MagicMock(return_value=SHARED_LIBRARY_FAVORITES_RESPONSE)),
    ]
    library = PhotoLibrary(
        service=service,
        zone_id=shared_zone,
        upload_url="https://upload.example.com",
        scope="shared-library",
    )
    library._fetch_album_records = MagicMock(return_value=[])

    album = library.albums[SmartAlbumEnum.FAVORITES.value]
    photos = list(album._get_photos_at(0, album._direction, 1))

    assert album._direction == DirectionEnum.DESCENDING
    assert len(photos) == 1
    assert photos[0].id == "MASTER_RECORD_ID_110"
    assert photos[0].filename == "shared_favorite_photo.jpg"
    posted = _last_posted_json(service.session.post)
    assert (
        posted["query"]["recordType"]
        == SHARED_LIBRARY_FAVORITES_QUERY_CORE["recordType"]
    )
    assert posted["resultsLimit"] == SHARED_LIBRARY_FAVORITES_QUERY_CORE["resultsLimit"]
    assert _payload_filter_map(posted) == SHARED_LIBRARY_FAVORITES_QUERY_CORE["filters"]
    assert posted["zoneID"] == shared_zone


def test_shared_library_iter_changes_uses_captured_zone_fixture() -> None:
    """Shared Library zone changes should map into normal PhotoChangeEvent objects."""

    shared_zone = SHARED_LIBRARY_ZONE_CHANGES_REQUEST["zones"][0]["zoneID"]
    mock_client = MagicMock()
    mock_client.query.return_value = _indexing_ready_response("SYNC_TOKEN_001")
    mock_client.iter_changes.return_value = iter(
        CKZoneChangesResponse.model_validate(SHARED_LIBRARY_ZONE_CHANGES_RESPONSE).zones
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )
    library = PhotoLibrary(
        service=service,
        zone_id=shared_zone,
        client=mock_client,
        upload_url="https://upload.example.com",
        scope="shared-library",
    )

    events = list(
        library.iter_changes(
            since=SHARED_LIBRARY_ZONE_CHANGES_REQUEST["zones"][0]["syncToken"]
        )
    )

    assert [event.record_type for event in events] == [
        "CPLAsset",
        "CPLSharedLibraryQuota",
    ]
    assert events[0].record_name == "ASSET_RECORD_ID_110"
    assert events[0].deleted is False
    assert events[0].modified == datetime.fromtimestamp(
        1775676937952 / 1000, tz=timezone.utc
    )
    assert library.current_sync_token == "SYNC_TOKEN_005"
    zone_req = mock_client.iter_changes.call_args.kwargs["zone_req"]
    assert zone_req.zoneID.zoneName == shared_zone["zoneName"]
    assert (
        zone_req.syncToken
        == SHARED_LIBRARY_ZONE_CHANGES_REQUEST["zones"][0]["syncToken"]
    )


def test_shared_library_all_photo_lookup_falls_back_to_scanning_feed() -> None:
    """Shared Library should fall back to feed scanning when direct lookup misses."""

    shared_zone = SHARED_LIBRARY_PRIVATE_ZONES_RESPONSE["zones"][0]["zoneID"]
    mock_client = MagicMock()
    mock_client.query.side_effect = [
        _indexing_ready_response("SYNC_TOKEN_001"),
        CKQueryResponse(records=[], syncToken="SYNC_TOKEN_002"),
        CKQueryResponse(
            records=[
                _ck_record(
                    "CPLMaster",
                    "MASTER_RECORD_ID_111",
                    {
                        "filenameEnc": {
                            "type": "ENCRYPTED_BYTES",
                            "value": base64.b64encode(
                                b"shared_library_photo.jpg"
                            ).decode("utf-8"),
                        }
                    },
                    zoneID=shared_zone,
                ),
                _ck_record(
                    "CPLAsset",
                    "ASSET_RECORD_ID_111",
                    {
                        "masterRef": {
                            "type": "REFERENCE",
                            "value": {
                                "recordName": "MASTER_RECORD_ID_111",
                                "action": "DELETE_SELF",
                                "zoneID": shared_zone,
                            },
                        },
                        "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
                        "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
                        "isFavorite": {"type": "INT64", "value": 0},
                    },
                    zoneID=shared_zone,
                ),
            ],
            syncToken="SYNC_TOKEN_003",
        ),
    ]
    mock_client.batch_count.return_value = 1
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )
    library = PhotoLibrary(
        service=service,
        zone_id=shared_zone,
        client=mock_client,
        upload_url="https://upload.example.com",
        scope="shared-library",
    )

    result = library.all.get("MASTER_RECORD_ID_111")

    assert result is not None
    assert result.id == "MASTER_RECORD_ID_111"
    assert result.filename == "shared_library_photo.jpg"
    assert mock_client.query.call_count == 3
    lookup_query = mock_client.query.call_args_list[1].kwargs["query"]
    assert lookup_query.filterBy[-1].fieldName == "recordName"
    fallback_query = mock_client.query.call_args_list[2].kwargs["query"]
    filter_names = [item.fieldName for item in fallback_query.filterBy]
    assert filter_names == ["direction", "startRank"]
    assert fallback_query.filterBy[0].fieldValue.value == DirectionEnum.DESCENDING.value
    mock_client.batch_count.assert_called_once()


def test_private_library_all_photo_lookup_falls_back_to_scanning_feed() -> None:
    """Private Library lookups should also fall back to feed scanning when needed."""

    mock_client = MagicMock()
    mock_client.query.side_effect = [
        _indexing_ready_response("SYNC_TOKEN_001"),
        CKQueryResponse(records=[], syncToken="SYNC_TOKEN_002"),
        CKQueryResponse(records=[], syncToken="SYNC_TOKEN_003"),
        CKQueryResponse(
            records=[
                _ck_record(
                    "CPLMaster",
                    "MASTER_RECORD_ID_211",
                    {
                        "filenameEnc": {
                            "type": "ENCRYPTED_BYTES",
                            "value": base64.b64encode(
                                b"private_library_photo.jpg"
                            ).decode("utf-8"),
                        }
                    },
                    zoneID=PRIMARY_ZONE,
                ),
                _ck_record(
                    "CPLAsset",
                    "ASSET_RECORD_ID_211",
                    {
                        "masterRef": {
                            "type": "REFERENCE",
                            "value": {
                                "recordName": "MASTER_RECORD_ID_211",
                                "action": "DELETE_SELF",
                                "zoneID": PRIMARY_ZONE,
                            },
                        },
                        "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
                        "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
                        "isFavorite": {"type": "INT64", "value": 0},
                    },
                    zoneID=PRIMARY_ZONE,
                ),
            ],
            syncToken="SYNC_TOKEN_004",
        ),
    ]
    mock_client.batch_count.return_value = 1
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )
    library = PhotoLibrary(
        service=service,
        zone_id=PRIMARY_ZONE,
        client=mock_client,
        upload_url="https://upload.example.com",
        scope="private",
    )

    result = library.all.get("MASTER_RECORD_ID_211")

    assert result is not None
    assert result.id == "MASTER_RECORD_ID_211"
    assert result.filename == "private_library_photo.jpg"
    assert mock_client.query.call_count == 4
    lookup_query = mock_client.query.call_args_list[2].kwargs["query"]
    assert lookup_query.filterBy[-1].fieldName == "recordName"
    fallback_query = mock_client.query.call_args_list[3].kwargs["query"]
    filter_names = [item.fieldName for item in fallback_query.filterBy]
    assert filter_names == ["direction", "startRank"]
    assert fallback_query.filterBy[0].fieldValue.value == DirectionEnum.DESCENDING.value
    mock_client.batch_count.assert_called_once()


def test_photos_service_albums(mock_photos_service: MagicMock) -> None:
    """Tests the albums property."""
    mock_photos_service.session.post.return_value.json.return_value = {
        "records": [
            {
                "fields": {
                    "state": {"value": "FINISHED"},
                },
            }
        ]
    }
    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )
    albums: AlbumContainer = photos_service.albums
    assert isinstance(albums, AlbumContainer)
    assert SmartAlbumEnum.ALL_PHOTOS in albums
    mock_photos_service.session.post.assert_called()


def test_photos_service_shared_streams(mock_photos_service: MagicMock) -> None:
    """Tests the shared_streams property."""
    mock_photos_service.session.post.return_value.json.side_effect = [
        {
            "records": [
                {
                    "fields": {
                        "state": {"value": "FINISHED"},
                    },
                }
            ]
        },
        {
            "albums": [
                {
                    "albumlocation": "https://shared.example.com/album/",
                    "albumctag": "ctag",
                    "albumguid": "guid",
                    "ownerdsid": "owner",
                    "attributes": {
                        "name": "Shared Album",
                        "creationDate": "1234567890",
                        "allowcontributions": True,
                        "ispublic": False,
                    },
                    "sharingtype": "owned",
                    "iswebuploadsupported": True,
                }
            ]
        },
    ]
    photos_service = PhotosService(
        service_root="https://example.com",
        session=mock_photos_service.session,
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )
    shared_streams: AlbumContainer = photos_service.shared_streams
    assert isinstance(shared_streams, AlbumContainer)
    assert "Shared Album" in shared_streams
    assert isinstance(shared_streams.find("Shared Album"), SharedPhotoStreamAlbum)
    mock_photos_service.session.post.assert_called()


def test_photos_service_upload_root_library() -> None:
    """Tests service-level uploads delegate to the root library by default."""

    photos_service = PhotosService.__new__(PhotosService)
    root_library = MagicMock(spec=PhotoLibrary)
    root_library.upload_file.return_value = MagicMock(spec=PhotoAsset)
    photos_service._root_library = root_library

    result = photos_service.upload("/path/to/photo.jpg")

    assert result == root_library.upload_file.return_value
    root_library.upload_file.assert_called_once_with("/path/to/photo.jpg")


def test_photos_service_upload_named_album() -> None:
    """Tests service-level uploads can target a named album."""

    photos_service = PhotosService.__new__(PhotosService)
    root_library = MagicMock(spec=PhotoLibrary)
    target_album = MagicMock(spec=PhotoAlbum)
    target_album.upload.return_value = MagicMock(spec=PhotoAsset)
    root_library.albums.find.return_value = target_album
    photos_service._root_library = root_library

    result = photos_service.upload("/path/to/photo.jpg", album="Favorites")

    assert result == target_album.upload.return_value
    root_library.albums.find.assert_called_once_with("Favorites")
    root_library.refresh_albums.assert_not_called()
    target_album.upload.assert_called_once_with("/path/to/photo.jpg")


def test_photos_service_upload_named_album_refreshes_after_cache_miss() -> None:
    """Tests service-level named album uploads retry against a refreshed album view."""

    photos_service = PhotosService.__new__(PhotosService)
    root_library = MagicMock(spec=PhotoLibrary)
    stale_albums = MagicMock()
    stale_albums.find.return_value = None
    refreshed_albums = MagicMock()
    target_album = MagicMock(spec=PhotoAlbum)
    target_album.upload.return_value = MagicMock(spec=PhotoAsset)
    refreshed_albums.find.return_value = target_album
    root_library.albums = stale_albums
    root_library.refresh_albums.return_value = refreshed_albums
    photos_service._root_library = root_library

    result = photos_service.upload("/path/to/photo.jpg", album="Favorites")

    assert result == target_album.upload.return_value
    stale_albums.find.assert_called_once_with("Favorites")
    root_library.refresh_albums.assert_called_once_with()
    refreshed_albums.find.assert_called_once_with("Favorites")
    target_album.upload.assert_called_once_with("/path/to/photo.jpg")


def test_photos_service_upload_album_object() -> None:
    """Tests service-level uploads accept an album object directly."""

    photos_service = PhotosService.__new__(PhotosService)
    root_library = MagicMock(spec=PhotoLibrary)
    target_album = MagicMock(spec=PhotoAlbum)
    target_album.upload.return_value = MagicMock(spec=PhotoAsset)
    photos_service._root_library = root_library

    result = photos_service.upload("/path/to/photo.jpg", album=target_album)

    assert result == target_album.upload.return_value
    root_library.upload_file.assert_not_called()
    target_album.upload.assert_called_once_with("/path/to/photo.jpg")


def test_photos_service_upload_missing_album_raises() -> None:
    """Tests service-level uploads fail clearly for an unknown album name."""

    photos_service = PhotosService.__new__(PhotosService)
    root_library = MagicMock(spec=PhotoLibrary)
    root_library.albums.find.return_value = None
    refreshed_albums = MagicMock()
    refreshed_albums.find.return_value = None
    root_library.refresh_albums.return_value = refreshed_albums
    photos_service._root_library = root_library

    with pytest.raises(PhotosServiceException, match="No album matched 'Missing'"):
        photos_service.upload("/path/to/photo.jpg", album="Missing")
    root_library.refresh_albums.assert_called_once_with()


def test_photo_album_initialization(mock_photo_library: MagicMock) -> None:
    """Tests initialization of PhotoAlbum."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        query_filter=[
            {
                "fieldName": "test",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": "test"},
            }
        ],
        zone_id={"zoneName": "TestZone"},
        page_size=50,
        parent_id="parent123",
        record_change_tag="tag123",
        record_modification_date="2023-01-01T00:00:00Z",
    )

    assert album.name == "Test Album"
    assert album.id == "album123"
    assert album._record_id == "album123"
    assert album._obj_type == ObjectTypeEnum.CONTAINER
    assert album._list_type == ListTypeEnum.CONTAINER
    assert album._direction == DirectionEnum.ASCENDING
    assert album._url == "https://example.com/records/query?dsid=12345"
    assert album._parent_id == "parent123"
    assert album._record_change_tag == "tag123"
    assert album._record_modification_date == "2023-01-01T00:00:00Z"
    assert album._zone_id == {"zoneName": "TestZone"}


def test_photo_album_initialization_default_zone(mock_photo_library: MagicMock) -> None:
    """Tests PhotoAlbum initialization with default zone."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    assert album._zone_id == PRIMARY_ZONE


def test_photo_album_fullname_no_parent(mock_photo_library: MagicMock) -> None:
    """Tests fullname property when album has no parent."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Root Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    assert album.fullname == "Root Album"


def test_photo_album_fullname_with_parent() -> None:
    """Tests fullname property when album has a parent."""
    mock_photo_library: MagicMock = MagicMock(spec=PhotoLibrary)
    parent_album = MagicMock()
    parent_album.fullname = "Parent Album"

    mock_albums = MagicMock()
    mock_albums.__getitem__.return_value = parent_album
    mock_photo_library.albums = mock_albums

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Child Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        parent_id="parent123",
    )

    assert album.fullname == "Parent Album/Child Album"
    mock_albums.__getitem__.assert_called_once_with("parent123")


def test_photo_album_rename_success(mock_photos_service: MagicMock) -> None:
    """Tests successful album renaming."""
    mock_photo_library: MagicMock = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = mock_photos_service
    mock_photo_library.service.session.post.return_value = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Old Name",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        record_change_tag="tag123",
        zone_id={"zoneName": "TestZone"},
    )

    album.rename("New Name")

    assert album._name == "New Name"

    expected_data = {
        "atomic": True,
        "zoneID": {"zoneName": "TestZone"},
        "operations": [
            {
                "operationType": "update",
                "record": {
                    "recordName": "album123",
                    "recordType": "CPLAlbum",
                    "recordChangeTag": "tag123",
                    "fields": {
                        "albumNameEnc": {
                            "value": base64.b64encode(
                                "New Name".encode("utf-8")
                            ).decode("utf-8"),
                        },
                    },
                },
            }
        ],
    }

    mock_photo_library.service.session.post.assert_called_once_with(
        "https://example.com/endpoint/records/modify?dsid=12345",
        json=expected_data,
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )

    # Verify that if the server returns updated tags, they are stored
    mock_photo_library.service.session.post.return_value.json.return_value = (
        ALBUM_RENAME_RESPONSE
    )
    album.rename("Another Name")
    assert album._record_change_tag == "new_tag"
    assert album._record_modification_date == "2023-02-01T00:00:00Z"


def test_photo_album_rename_uses_browser_response_user_modification_date() -> None:
    """Browser rename fixtures use userModificationDate rather than recordModificationDate."""

    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}
    mock_photo_library.service.session.post.return_value = MagicMock(
        json=MagicMock(return_value=BROWSER_ALBUM_RENAME_RESPONSE)
    )

    request_record = BROWSER_ALBUM_RENAME_REQUEST["operations"][0]["record"]
    album = PhotoAlbum(
        library=mock_photo_library,
        name="ALBUM_NAME_ENC_001",
        record_id=request_record["recordName"],
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        record_change_tag=request_record["recordChangeTag"],
        zone_id=BROWSER_ALBUM_RENAME_REQUEST["zoneID"],
    )

    album.rename("ALBUM_NAME_ENC_028")

    posted = _last_posted_json(mock_photo_library.service.session.post)
    assert posted["atomic"] is BROWSER_ALBUM_RENAME_REQUEST["atomic"]
    assert posted["zoneID"] == BROWSER_ALBUM_RENAME_REQUEST["zoneID"]
    assert posted["operations"][0]["operationType"] == "update"
    assert (
        posted["operations"][0]["record"]["recordName"] == request_record["recordName"]
    )
    assert (
        posted["operations"][0]["record"]["recordChangeTag"]
        == request_record["recordChangeTag"]
    )
    assert (
        posted["operations"][0]["record"]["recordType"] == request_record["recordType"]
    )
    assert (
        posted["operations"][0]["record"]["fields"]["albumNameEnc"]["value"]
        == request_record["fields"]["albumNameEnc"]["value"]
    )
    assert "userModificationDate" not in posted["operations"][0]["record"]["fields"]
    assert "userModificationDate" in request_record["fields"]
    assert album._record_change_tag == "RECORD_CHANGE_TAG_263"
    assert album._record_modification_date == 1775666024305


def test_photo_album_rename_same_name(mock_photo_library: MagicMock) -> None:
    """Tests that renaming to the same name does nothing."""
    mock_photo_library.service.session.post.return_value = MagicMock()

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Same Name",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    album.rename("Same Name")

    assert album._name == "Same Name"
    mock_photo_library.service.session.post.assert_not_called()


def test_photo_album_delete_success(mock_photo_library: MagicMock) -> None:
    """Tests successful album deletion."""
    mock_photo_library.service.session.post.return_value = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        record_change_tag="tag123",
        zone_id={"zoneName": "TestZone"},
    )

    result = album.delete()

    assert result is True

    expected_data = {
        "atomic": True,
        "zoneID": {"zoneName": "TestZone"},
        "operations": [
            {
                "operationType": "update",
                "record": {
                    "recordName": "album123",
                    "recordChangeTag": "tag123",
                    "recordType": "CPLAlbum",
                    "fields": {
                        "isDeleted": {"value": 1},
                    },
                },
            }
        ],
    }

    mock_photo_library.service.session.post.assert_called_once_with(
        "https://example.com/endpoint/records/modify?dsid=12345",
        json=expected_data,
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_delete_matches_browser_request_fixture() -> None:
    """Album deletion should match the browser's CloudKit write shape."""

    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}
    mock_photo_library.service.session.post.return_value = MagicMock()

    request_record = BROWSER_ALBUM_DELETE_REQUEST["operations"][0]["record"]
    album = PhotoAlbum(
        library=mock_photo_library,
        name="ALBUM_NAME_ENC_028",
        record_id=request_record["recordName"],
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        record_change_tag=request_record["recordChangeTag"],
        zone_id=BROWSER_ALBUM_DELETE_REQUEST["zoneID"],
    )

    assert album.delete() is True

    assert _last_posted_json(mock_photo_library.service.session.post) == (
        BROWSER_ALBUM_DELETE_REQUEST
    )
    assert (
        BROWSER_ALBUM_DELETE_RESPONSE["records"][0]["fields"]["isDeleted"]["value"] == 1
    )


def test_photo_album_add_photo_success(mock_photo_library: MagicMock) -> None:
    """Tests successful album membership creation via the raw request path."""

    mock_photo_library.service.session.post.return_value = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}
    photo = MagicMock(spec=PhotoAsset)
    photo.id = "master123"
    photo.asset_id = "asset123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    assert album.add_photo(photo) is True

    expected_data = {
        "atomic": True,
        "zoneID": {"zoneName": "TestZone"},
        "operations": [
            {
                "operationType": "create",
                "record": {
                    "recordName": "asset123-IN-album123",
                    "recordType": "CPLContainerRelation",
                    "fields": {
                        "itemId": {"value": "asset123"},
                        "position": {"value": 1024},
                        "containerId": {"value": "album123"},
                    },
                },
            }
        ],
    }

    mock_photo_library.service.session.post.assert_called_once_with(
        "https://example.com/endpoint/records/modify?dsid=12345",
        json=expected_data,
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_add_photo_matches_browser_request_fixture() -> None:
    """Album membership creation should match the browser's relation payload."""

    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}
    mock_photo_library.service.session.post.return_value = MagicMock()
    photo = MagicMock(spec=PhotoAsset)
    photo.id = "MASTER_RECORD_ID_031"
    photo.asset_id = BROWSER_ALBUM_ADD_PHOTO_REQUEST["operations"][0]["record"][
        "fields"
    ]["itemId"]["value"]

    album = PhotoAlbum(
        library=mock_photo_library,
        name="ALBUM_NAME_ENC_028",
        record_id=BROWSER_ALBUM_ADD_PHOTO_REQUEST["operations"][0]["record"]["fields"][
            "containerId"
        ]["value"],
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id=BROWSER_ALBUM_ADD_PHOTO_REQUEST["zoneID"],
    )

    assert album.add_photo(photo) is True

    assert _last_posted_json(mock_photo_library.service.session.post) == (
        BROWSER_ALBUM_ADD_PHOTO_REQUEST
    )
    assert (
        BROWSER_ALBUM_ADD_PHOTO_RESPONSE["records"][0]["recordType"]
        == "CPLContainerRelation"
    )


def test_browser_album_remove_photo_fixture_represents_force_delete_relation() -> None:
    """The browser remove-from-album flow deletes the relation record, not the asset."""

    request_operation = BROWSER_ALBUM_REMOVE_PHOTO_REQUEST["operations"][0]
    response_record = BROWSER_ALBUM_REMOVE_PHOTO_RESPONSE["records"][0]

    assert request_operation["operationType"] == "forceDelete"
    assert request_operation["record"]["recordName"] == response_record["recordName"]
    assert response_record["deleted"] is True


def test_photo_album_rename_success_typed_client() -> None:
    """Tests album renaming via the typed CloudKit client path."""
    mock_client = MagicMock()
    mock_client.modify.return_value = CKModifyResponse(
        records=[
            _ck_record(
                "CPLAlbum",
                "album123",
                {
                    "recordModificationDate": {
                        "type": "STRING",
                        "value": "2023-02-01T00:00:00Z",
                    }
                },
                recordChangeTag="new_tag",
            )
        ],
        syncToken="sync-token",
    )
    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = SimpleNamespace(session=object())
    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.asset_type = PhotoAsset

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Old Name",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        client=mock_client,
        zone_id={"zoneName": "TestZone"},
        record_change_tag="tag123",
    )

    album.rename("New Name")

    assert album.name == "New Name"
    assert album._record_change_tag == "new_tag"
    assert album._record_modification_date == "2023-02-01T00:00:00Z"
    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "update"
    assert op.record.recordName == "album123"
    assert op.record.fields.get_value("albumNameEnc") == b"New Name"
    assert mock_client.modify.call_args.kwargs["zone_id"].zoneName == "TestZone"
    assert mock_client.modify.call_args.kwargs["atomic"] is True


def test_photo_album_delete_success_typed_client() -> None:
    """Tests album deletion via the typed CloudKit client path."""
    mock_client = MagicMock()
    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = SimpleNamespace(session=object())

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        client=mock_client,
        zone_id={"zoneName": "TestZone"},
        record_change_tag="tag123",
    )

    assert album.delete() is True

    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "update"
    assert op.record.recordName == "album123"
    assert op.record.fields.get_value("isDeleted") == 1
    assert mock_client.modify.call_args.kwargs["zone_id"].zoneName == "TestZone"


def test_photo_album_add_photo_success_typed_client() -> None:
    """Tests adding a photo to an album via the typed CloudKit client path."""
    mock_client = MagicMock()
    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = SimpleNamespace(session=object())

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        client=mock_client,
        zone_id={"zoneName": "TestZone"},
    )
    photo = SimpleNamespace(id="master123", asset_id="asset123")

    assert album.add_photo(photo) is True

    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "create"
    assert op.record.recordName == "asset123-IN-album123"
    assert op.record.recordType == "CPLContainerRelation"
    assert op.record.fields.get_value("itemId") == "asset123"
    assert op.record.fields.get_value("containerId") == "album123"


def test_photo_album_upload_success(mock_photos_service: MagicMock) -> None:
    """Tests successful photo upload to album."""
    mock_photo_library: MagicMock = MagicMock(spec=PhotoLibrary)
    mock_photo_asset = MagicMock()
    mock_photo_asset.id = "master123"
    mock_photo_asset.asset_id = "asset123"
    mock_photo_library.service = mock_photos_service
    mock_photo_library.upload_file.return_value = mock_photo_asset
    mock_photo_library.service.session.post.return_value = MagicMock()
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    result = album.upload("/path/to/photo.jpg")

    assert result == mock_photo_asset
    mock_photo_library.upload_file.assert_called_once_with("/path/to/photo.jpg")

    expected_data = {
        "atomic": True,
        "zoneID": {"zoneName": "TestZone"},
        "operations": [
            {
                "operationType": "create",
                "record": {
                    "fields": {
                        "itemId": {"value": "asset123"},
                        "position": {"value": 1024},
                        "containerId": {"value": "album123"},
                    },
                    "recordType": "CPLContainerRelation",
                    "recordName": "asset123-IN-album123",
                },
            }
        ],
    }

    mock_photo_library.service.session.post.assert_called_with(
        "https://example.com/endpoint/records/modify?dsid=12345",
        json=expected_data,
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_upload_not_photo_library() -> None:
    """Tests upload when library is not a PhotoLibrary instance."""
    mock_library = MagicMock(spec=BasePhotoLibrary)

    album = PhotoAlbum(
        library=mock_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    result = album.upload("/path/to/photo.jpg")

    assert result is None


def test_photo_album_upload_upload_file_returns_none() -> None:
    """Tests upload when upload_file returns None."""
    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.upload_file.return_value = None
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    result: PhotoAsset | None = album.upload("/path/to/photo.jpg")

    mock_photo_library.upload_file.assert_called_once_with("/path/to/photo.jpg")
    assert result is None


def test_photo_album_get_container_id(mock_photo_library: MagicMock) -> None:
    """Tests _get_container_id property."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    container_id = album._get_container_id

    assert container_id == f"{ObjectTypeEnum.CONTAINER.value}:album123"


def test_photo_album_get_len(mock_photo_library: MagicMock) -> None:
    """Tests _get_len method."""
    mock_response = {"batch": [{"records": [{"fields": {"itemCount": {"value": 42}}}]}]}
    mock_photo_library.service.session.post.return_value.json.return_value = (
        mock_response
    )
    mock_photo_library.service.service_endpoint = "https://example.com/endpoint"
    mock_photo_library.service.params = {"dsid": "12345"}

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    length = album._get_len()

    assert length == 42

    expected_json = {
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
                            "value": [f"{ObjectTypeEnum.CONTAINER.value}:album123"],
                        },
                    },
                },
                "zoneWide": True,
                "zoneID": {"zoneName": "TestZone"},
            }
        ]
    }

    mock_photo_library.service.session.post.assert_called_once_with(
        "https://example.com/endpoint/internal/records/query/batch?dsid=12345",
        json=expected_json,
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_get_payload(mock_photo_library: MagicMock) -> None:
    """Tests _get_payload method."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        query_filter=[
            {
                "fieldName": "test",
                "comparator": "EQUALS",
                "fieldValue": {"type": "STRING", "value": "test"},
            }
        ],
        zone_id={"zoneName": "TestZone"},
    )

    payload = album._get_payload(
        offset=10, page_size=20, direction=DirectionEnum.DESCENDING
    )

    expected_payload = {
        "query": {
            "recordType": ListTypeEnum.CONTAINER.value,
            "filterBy": [
                {
                    "fieldName": "direction",
                    "comparator": "EQUALS",
                    "fieldValue": {
                        "type": "STRING",
                        "value": DirectionEnum.DESCENDING.value,
                    },
                },
                {
                    "fieldName": "startRank",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "INT64", "value": 10},
                },
                {
                    "fieldName": "test",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "test"},
                },
            ],
        },
        "resultsLimit": 20,
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
        "zoneID": {"zoneName": "TestZone"},
    }

    assert payload == expected_payload


def test_photo_album_get_photo_payload_uses_minimum_album_lookup_limit(
    mock_photo_library: MagicMock,
) -> None:
    """Album lookups should request at least three records from CloudKit."""

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    payload = album._get_photo_payload("photo123")

    assert payload["resultsLimit"] == 3
    assert _payload_filter_map(payload)["recordName"] == "photo123"


def test_photo_album_get_payload_no_query_filter(mock_photo_library: MagicMock) -> None:
    """Tests _get_payload method without query filter."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    payload: dict[str, Any] = album._get_payload(
        offset=5, page_size=10, direction=DirectionEnum.ASCENDING
    )

    # Verify that only the default filterBy entries are present
    assert len(payload["query"]["filterBy"]) == 2
    assert payload["query"]["filterBy"][0]["fieldName"] == "direction"
    assert payload["query"]["filterBy"][1]["fieldName"] == "startRank"


def test_photo_album_get_url(mock_photo_library: MagicMock) -> None:
    """Tests _get_url method."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    url = album._get_url()

    assert url == "https://example.com/records/query?dsid=12345"


def test_photo_album_list_query_gen_with_filter(mock_photo_library: MagicMock) -> None:
    """Tests _list_query_gen method with query filter."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    query_filter = [
        {
            "fieldName": "custom",
            "comparator": "EQUALS",
            "fieldValue": {"type": "STRING", "value": "value"},
        }
    ]

    query = album._list_query_gen(
        offset=0,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        num_results=50,
        query_filter=query_filter,
    )

    # Verify that query filter is added to the filterBy array
    assert len(query["query"]["filterBy"]) == 3
    assert query["query"]["filterBy"][2] == query_filter[0]


def test_photo_album_list_query_gen_without_filter(
    mock_photo_library: MagicMock,
) -> None:
    """Tests _list_query_gen method without query filter."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
        zone_id={"zoneName": "TestZone"},
    )

    query = album._list_query_gen(
        offset=0,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        num_results=50,
        query_filter=None,
    )

    # Verify that only default filterBy entries are present
    assert len(query["query"]["filterBy"]) == 2
    assert query["query"]["filterBy"][0]["fieldName"] == "direction"
    assert query["query"]["filterBy"][1]["fieldName"] == "startRank"


def test_photo_asset_properties_and_methods() -> None:
    """Test PhotoAsset properties and methods."""

    # Prepare mock data for master and asset records
    filename = "test_photo.JPG"
    encoded_filename: str = base64.b64encode(filename.encode("utf-8")).decode("utf-8")
    now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    master_record: dict[str, Any] = {
        "recordName": "photo_id_123",
        "fields": {
            "filenameEnc": {"value": encoded_filename},
            "resOriginalRes": {
                "value": {
                    "size": 123456,
                    "downloadURL": "http://example.com/photo.jpg",
                }
            },
            "resOriginalWidth": {"value": 1920},
            "resOriginalHeight": {"value": 1080},
            "itemType": {"value": "public.jpeg"},
            "resOriginalFileType": {"value": "public.jpeg"},
            "resJPEGThumbRes": {
                "value": {
                    "size": 1234,
                    "downloadURL": "http://example.com/thumb.jpg",
                }
            },
            "resJPEGThumbWidth": {"value": 100},
            "resJPEGThumbHeight": {"value": 50},
            "resJPEGThumbFileType": {"value": "public.jpeg"},
        },
        "recordChangeTag": "tag1",
    }
    asset_record: dict[str, Any] = {
        "fields": {
            "assetDate": {"value": now},
            "addedDate": {"value": now},
        },
        "recordName": "photo_id_123",
        "recordType": "CPLAsset",
        "zoneID": {"zoneName": "PrimarySync"},
    }

    mock_service = MagicMock()
    mock_service.service_endpoint = "https://example.com"
    mock_service.params = {"dsid": "12345"}
    mock_service.session.get.return_value = MagicMock(
        json=MagicMock(return_value={}),
        raw=MagicMock(read=MagicMock(return_value=b"response")),
    )
    mock_service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={}), status_code=200
    )

    asset = PhotoAsset(mock_service, master_record, asset_record)

    # Test id
    assert asset.id == "photo_id_123"
    # Test filename
    assert asset.filename == filename
    # Test size
    assert asset.size == 123456
    # Test created and asset_date
    assert isinstance(asset.created, datetime)
    assert isinstance(asset.asset_date, datetime)
    # Test added_date
    assert isinstance(asset.added_date, datetime)
    # Test dimensions
    assert asset.dimensions == (1920, 1080)
    # Test item_type
    assert asset.item_type == "image"
    # Test is_live_photo (should be False)
    assert asset.is_live_photo is False
    # Test versions
    versions: dict[str, dict[str, Any]] = asset.versions
    assert "original" in versions
    assert "thumb" in versions
    assert versions["original"]["filename"] == filename
    assert versions["original"]["url"] == "http://example.com/photo.jpg"
    assert versions["thumb"]["url"] == "http://example.com/thumb.jpg"
    # Test download returns the mocked response
    assert asset.download(version="original") == b"response"
    # Test download with invalid version returns None
    assert asset.download(version="nonexistent") is None
    # Test delete returns a mocked response
    resp: bool = asset.delete()
    assert resp is True
    # Test __repr__
    assert repr(asset) == "<PhotoAsset: id=photo_id_123>"


def test_photo_asset_delete_success_typed_client() -> None:
    """Tests photo deletion via the typed CloudKit client path."""
    mock_client = MagicMock()
    service = SimpleNamespace(session=object(), _private_client=mock_client)
    master_record = _ck_record(
        "CPLMaster",
        "photo_id_123",
        {},
        recordChangeTag="master-tag",
        zoneID={"zoneName": "PrimarySync"},
    )
    asset_record = _ck_record(
        "CPLAsset",
        "photo_id_123",
        {
            "assetDate": {"value": 1700000000000},
            "addedDate": {"value": 1700000000000},
        },
        recordChangeTag="asset-tag",
        zoneID={"zoneName": "PrimarySync"},
    )

    asset = PhotoAsset(service, master_record, asset_record)

    assert asset.delete() is True

    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "update"
    assert op.record.recordName == "photo_id_123"
    assert op.record.recordChangeTag == "asset-tag"
    assert op.record.fields.get_value("isDeleted") == 1
    assert mock_client.modify.call_args.kwargs["zone_id"].zoneName == "PrimarySync"


def test_photo_asset_delete_success_raw_request_payload() -> None:
    """Tests photo deletion via the raw request path uses the expected modify payload."""

    master_record = {
        "recordName": "photo_id_123",
        "recordType": "CPLMaster",
        "recordChangeTag": "master-tag",
        "zoneID": {"zoneName": "PrimarySync"},
        "fields": {},
    }
    asset_record = {
        "fields": {
            "assetDate": {"value": 1700000000000},
            "addedDate": {"value": 1700000000000},
        },
        "recordName": "photo_id_123",
        "recordType": "CPLAsset",
        "recordChangeTag": "asset-tag",
        "zoneID": {"zoneName": "PrimarySync"},
    }
    mock_service = MagicMock()
    mock_service.service_endpoint = "https://example.com"
    mock_service.params = {"dsid": "12345"}
    mock_service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={}),
        status_code=200,
    )

    asset = PhotoAsset(mock_service, master_record, asset_record)

    assert asset.delete() is True
    mock_service.session.post.assert_called_once_with(
        "https://example.com/records/modify?dsid=12345",
        json={
            "atomic": True,
            "zoneID": {"zoneName": "PrimarySync"},
            "operations": [
                {
                    "operationType": "update",
                    "record": {
                        "recordName": "photo_id_123",
                        "recordType": "CPLAsset",
                        "recordChangeTag": "asset-tag",
                        "fields": {"isDeleted": {"value": 1}},
                    },
                }
            ],
        },
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_asset_delete_matches_browser_request_fixture() -> None:
    """Photo deletion should match the browser's library-delete payload."""

    request_record = BROWSER_PHOTO_DELETE_REQUEST["operations"][0]["record"]
    master_record_name = BROWSER_PHOTO_DELETE_RESPONSE["records"][0]["fields"][
        "masterRef"
    ]["value"]["recordName"]
    asset_record = {
        "recordName": request_record["recordName"],
        "recordType": request_record["recordType"],
        "recordChangeTag": request_record["recordChangeTag"],
        "zoneID": BROWSER_PHOTO_DELETE_REQUEST["zoneID"],
        "fields": {
            "assetDate": BROWSER_PHOTO_DELETE_RESPONSE["records"][0]["fields"][
                "assetDate"
            ],
            "addedDate": BROWSER_PHOTO_DELETE_RESPONSE["records"][0]["fields"][
                "addedDate"
            ],
        },
    }
    master_record = {
        "recordName": master_record_name,
        "recordType": "CPLMaster",
        "recordChangeTag": "MASTER_RECORD_CHANGE_TAG_001",
        "zoneID": BROWSER_PHOTO_DELETE_REQUEST["zoneID"],
        "fields": {},
    }
    mock_service = MagicMock()
    mock_service.service_endpoint = "https://example.com"
    mock_service.params = {"dsid": "12345"}
    mock_service.session.post.return_value = MagicMock(
        json=MagicMock(return_value=BROWSER_PHOTO_DELETE_RESPONSE),
        status_code=200,
    )

    asset = PhotoAsset(mock_service, master_record, asset_record)

    assert asset.delete() is True
    assert _last_posted_json(mock_service.session.post) == BROWSER_PHOTO_DELETE_REQUEST
    assert (
        BROWSER_PHOTO_DELETE_RESPONSE["records"][0]["fields"]["isDeleted"]["value"] == 1
    )
    assert (
        BROWSER_PHOTO_DELETE_RESPONSE["records"][0]["fields"]["masterRef"]["value"][
            "action"
        ]
        == "DELETE_SELF"
    )


def test_photo_asset_unfavorite_matches_shared_library_browser_fixture() -> None:
    """Shared Library unfavorite should match the captured browser request exactly."""

    master_record = SHARED_LIBRARY_FAVORITES_RESPONSE["records"][1]
    asset_record = SHARED_LIBRARY_FAVORITES_RESPONSE["records"][0]
    mock_service = MagicMock()
    mock_service.service_endpoint = "https://example.com"
    mock_service.params = {"dsid": "12345"}
    mock_service.session.post.return_value = MagicMock(
        json=MagicMock(return_value=SHARED_LIBRARY_UNFAVORITE_RESPONSE),
        status_code=200,
    )

    asset = PhotoAsset(mock_service, master_record, asset_record)

    assert asset.unfavorite() is True
    assert _last_posted_json(mock_service.session.post) == (
        SHARED_LIBRARY_UNFAVORITE_REQUEST
    )
    assert record_field_value(asset._asset_record, "isFavorite") == 0
    assert record_change_tag(asset._asset_record) == "RECORD_CHANGE_TAG_309"


def test_photo_asset_favorite_uses_symmetric_shared_library_payload() -> None:
    """Shared Library favorite should reuse the captured unfavorite request shape."""

    expected_request = json.loads(json.dumps(SHARED_LIBRARY_UNFAVORITE_REQUEST))
    expected_request["operations"][0]["record"]["fields"]["isFavorite"]["value"] = 1
    favorite_response = json.loads(json.dumps(SHARED_LIBRARY_UNFAVORITE_RESPONSE))
    favorite_response["records"][0]["recordChangeTag"] = "RECORD_CHANGE_TAG_310"
    favorite_response["records"][0]["fields"]["isFavorite"]["value"] = 1

    master_record = SHARED_LIBRARY_FAVORITES_RESPONSE["records"][1]
    asset_record = SHARED_LIBRARY_FAVORITES_RESPONSE["records"][0]
    mock_service = MagicMock()
    mock_service.service_endpoint = "https://example.com"
    mock_service.params = {"dsid": "12345"}
    mock_service.session.post.return_value = MagicMock(
        json=MagicMock(return_value=favorite_response),
        status_code=200,
    )

    asset = PhotoAsset(mock_service, master_record, asset_record)

    assert asset.favorite() is True
    assert _last_posted_json(mock_service.session.post) == expected_request
    assert record_field_value(asset._asset_record, "isFavorite") == 1
    assert record_change_tag(asset._asset_record) == "RECORD_CHANGE_TAG_310"


def test_photo_asset_set_favorite_success_typed_client() -> None:
    """Typed favorite mutations should target the asset zone and update the local record."""

    mock_client = MagicMock()
    service = SimpleNamespace(session=object(), _private_client=mock_client)
    master_record = _ck_record(
        "CPLMaster",
        "MASTER_RECORD_ID_110",
        {
            "filenameEnc": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(b"shared_favorite_photo.jpg").decode("utf-8"),
            }
        },
        recordChangeTag="RECORD_CHANGE_TAG_302",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    asset_record = _ck_record(
        "CPLAsset",
        "ASSET_RECORD_ID_110",
        {
            "masterRef": {
                "type": "REFERENCE",
                "value": {
                    "recordName": "MASTER_RECORD_ID_110",
                    "action": "DELETE_SELF",
                    "zoneID": SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
                },
            },
            "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
            "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
            "isFavorite": {"type": "INT64", "value": 0},
        },
        recordChangeTag="RECORD_CHANGE_TAG_309",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    mock_client.modify.return_value = CKModifyResponse(
        records=[
            _ck_record(
                "CPLAsset",
                "ASSET_RECORD_ID_110",
                {
                    "masterRef": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": "MASTER_RECORD_ID_110",
                            "action": "DELETE_SELF",
                            "zoneID": SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
                        },
                    },
                    "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
                    "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
                    "isFavorite": {"type": "INT64", "value": 1},
                },
                recordChangeTag="RECORD_CHANGE_TAG_311",
                zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
            )
        ],
        syncToken="SYNC_TOKEN_006",
    )

    asset = PhotoAsset(service, master_record, asset_record)

    assert asset.favorite() is True
    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "update"
    assert op.record.recordName == "ASSET_RECORD_ID_110"
    assert op.record.fields.get_value("isFavorite") == 1
    assert mock_client.modify.call_args.kwargs["zone_id"].zoneName == (
        "SharedSync-6E1C0494-1BF4-4928-BD07-3FD81633193E"
    )
    assert record_field_value(asset._asset_record, "isFavorite") == 1
    assert record_change_tag(asset._asset_record) == "RECORD_CHANGE_TAG_311"


def test_photo_asset_set_favorite_refreshes_shared_library_state() -> None:
    """Shared Library favorite writes should refresh the asset state after modify."""

    mock_client = MagicMock()
    service = SimpleNamespace(session=object(), _private_client=mock_client)
    master_record = _ck_record(
        "CPLMaster",
        "MASTER_RECORD_ID_110",
        {
            "filenameEnc": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(b"shared_favorite_photo.jpg").decode("utf-8"),
            }
        },
        recordChangeTag="RECORD_CHANGE_TAG_302",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    asset_record = _ck_record(
        "CPLAsset",
        "ASSET_RECORD_ID_110",
        {
            "masterRef": {
                "type": "REFERENCE",
                "value": {
                    "recordName": "MASTER_RECORD_ID_110",
                    "action": "DELETE_SELF",
                    "zoneID": SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
                },
            },
            "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
            "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
            "isFavorite": {"type": "INT64", "value": 1},
        },
        recordChangeTag="RECORD_CHANGE_TAG_309",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    mock_client.modify.return_value = CKModifyResponse(
        records=[], syncToken="SYNC_TOKEN_006"
    )

    asset = PhotoAsset(service, master_record, asset_record)
    refreshed_library = MagicMock(spec=PhotoLibrary)
    refreshed_library.scope = "shared-library"
    refreshed_asset = PhotoAsset(
        service,
        master_record,
        _ck_record(
            "CPLAsset",
            "ASSET_RECORD_ID_110",
            {
                "masterRef": {
                    "type": "REFERENCE",
                    "value": {
                        "recordName": "MASTER_RECORD_ID_110",
                        "action": "DELETE_SELF",
                        "zoneID": SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
                    },
                },
                "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
                "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
                "isFavorite": {"type": "INT64", "value": 0},
            },
            recordChangeTag="RECORD_CHANGE_TAG_312",
            zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
        ),
    )
    refreshed_library.all.get.return_value = refreshed_asset
    asset._library = refreshed_library

    assert asset.unfavorite() is True
    refreshed_library.all.get.assert_called_once_with("MASTER_RECORD_ID_110")
    assert record_field_value(asset._asset_record, "isFavorite") == 0
    assert record_change_tag(asset._asset_record) == "RECORD_CHANGE_TAG_312"


def test_photo_asset_set_favorite_raises_on_record_error() -> None:
    """Per-record CloudKit errors should surface when the server state does not change."""

    mock_client = MagicMock()
    service = SimpleNamespace(session=object(), _private_client=mock_client)
    master_record = _ck_record(
        "CPLMaster",
        "MASTER_RECORD_ID_110",
        {
            "filenameEnc": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(b"shared_favorite_photo.jpg").decode("utf-8"),
            }
        },
        recordChangeTag="RECORD_CHANGE_TAG_302",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    asset_record = _ck_record(
        "CPLAsset",
        "ASSET_RECORD_ID_110",
        {
            "masterRef": {
                "type": "REFERENCE",
                "value": {
                    "recordName": "MASTER_RECORD_ID_110",
                    "action": "DELETE_SELF",
                    "zoneID": SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
                },
            },
            "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
            "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
            "isFavorite": {"type": "INT64", "value": 1},
        },
        recordChangeTag="RECORD_CHANGE_TAG_309",
        zoneID=SHARED_LIBRARY_UNFAVORITE_REQUEST["zoneID"],
    )
    mock_client.modify.return_value = CKModifyResponse(
        records=[
            CKErrorItem(
                serverErrorCode="SERVER_RECORD_CHANGED",
                reason="stale tag",
                recordName="ASSET_RECORD_ID_110",
            )
        ],
        syncToken="SYNC_TOKEN_006",
    )

    asset = PhotoAsset(service, master_record, asset_record)

    with pytest.raises(PhotosServiceException, match="SERVER_RECORD_CHANGED"):
        asset.unfavorite()


def test_photo_asset_is_live_photo_true() -> None:
    """Test PhotoAsset is_live_photo property for live photo."""
    master_record: dict[str, Any] = {
        "recordName": "photo_id_456",
        "fields": {
            "filenameEnc": {
                "value": base64.b64encode(b"IMG_0001.HEIC").decode("utf-8")
            },
            "resOriginalRes": {
                "value": {
                    "size": 123456,
                    "downloadURL": "http://example.com/photo.heic",
                }
            },
            "resOriginalWidth": {"value": 4032},
            "resOriginalHeight": {"value": 3024},
            "itemType": {"value": "public.heic"},
            "resOriginalFileType": {"value": "public.heic"},
            "resOriginalVidComplFileType": {"value": "com.apple.quicktime-movie"},
            "resVidSmallRes": {
                "value": {
                    "size": 1000,
                    "downloadURL": "http://example.com/video.mov",
                }
            },
            "resVidSmallFileType": {"value": "com.apple.quicktime-movie"},
        },
        "recordChangeTag": "tag2",
    }
    asset_record: dict[str, Any] = {
        "fields": {
            "assetDate": {"value": 1700000000000},
            "addedDate": {"value": 1700000000000},
        },
        "recordName": "photo_id_456",
        "recordType": "CPLAsset",
        "zoneID": {"zoneName": "PrimarySync"},
    }
    mock_service = MagicMock()
    asset = PhotoAsset(mock_service, master_record, asset_record)
    assert asset.is_live_photo is True
    # The thumb_video version filename should end with .MOV
    thumb_video = asset.versions.get("thumb_video")
    if thumb_video:
        assert thumb_video["filename"].endswith(".MOV")


@pytest.mark.parametrize(
    "master_fields,expected_type,filename",
    [
        # itemType present and recognized
        (
            {
                "itemType": {"value": "public.jpeg"},
                "filenameEnc": {
                    "value": base64.b64encode(b"photo.JPG").decode("utf-8")
                },
            },
            "image",
            "photo.JPG",
        ),
        (
            {
                "itemType": {"value": "public.heic"},
                "filenameEnc": {
                    "value": base64.b64encode(b"photo.HEIC").decode("utf-8")
                },
            },
            "image",
            "photo.HEIC",
        ),
        (
            {
                "itemType": {"value": "com.apple.quicktime-movie"},
                "filenameEnc": {
                    "value": base64.b64encode(b"movie.MOV").decode("utf-8")
                },
            },
            "movie",
            "movie.MOV",
        ),
        # itemType missing, resOriginalFileType present and recognized
        (
            {
                "resOriginalFileType": {"value": "public.png"},
                "filenameEnc": {"value": base64.b64encode(b"img.PNG").decode("utf-8")},
            },
            "image",
            "img.PNG",
        ),
        # itemType and resOriginalFileType missing, fallback to filename extension
        (
            {
                "filenameEnc": {
                    "value": base64.b64encode(b"fallback.JPG").decode("utf-8")
                }
            },
            "image",
            "fallback.JPG",
        ),
        (
            {
                "filenameEnc": {
                    "value": base64.b64encode(b"fallback.HEIC").decode("utf-8")
                }
            },
            "image",
            "fallback.HEIC",
        ),
        # itemType and resOriginalFileType missing, filename not image, fallback to movie
        (
            {
                "filenameEnc": {
                    "value": base64.b64encode(b"fallback.avi").decode("utf-8")
                }
            },
            "movie",
            "fallback.avi",
        ),
        # itemType present but not recognized, fallback to filename extension
        (
            {
                "itemType": {"value": "unknown.type"},
                "filenameEnc": {
                    "value": base64.b64encode(b"photo.JPG").decode("utf-8")
                },
            },
            "image",
            "photo.JPG",
        ),
        (
            {
                "itemType": {"value": "unknown.type"},
                "filenameEnc": {
                    "value": base64.b64encode(b"video.avi").decode("utf-8")
                },
            },
            "movie",
            "video.avi",
        ),
    ],
)
def test_photo_asset_item_type(
    master_fields: dict[str, Any], expected_type: str, filename: str
) -> None:
    """Test PhotoAsset item_type property with various scenarios."""
    asset_record: dict[str, Any] = {
        "fields": {
            "assetDate": {"value": 1700000000000},
            "addedDate": {"value": 1700000000000},
        },
        "recordName": "photo_id_test",
        "recordType": "CPLAsset",
        "zoneID": {"zoneName": "PrimarySync"},
    }
    master_record: dict[str, Any] = {
        "recordName": "photo_id_test",
        "fields": master_fields,
        "recordChangeTag": "tag",
    }
    mock_service = MagicMock()
    asset = PhotoAsset(mock_service, master_record, asset_record)
    assert asset.filename == filename
    assert asset.item_type == expected_type


def test_shared_photo_stream_album_properties() -> None:
    """Test SharedPhotoStreamAlbum properties and methods."""

    # Setup test data
    name = "Shared Album"
    album_location = "https://shared.example.com/album/"
    album_ctag = "ctag"
    album_guid = "guid"
    owner_dsid = "owner"
    creation_date = str(
        int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    )
    sharing_type = "owned"
    allow_contributions = True
    is_public = False
    is_web_upload_supported = True
    public_url = "https://shared.example.com/public/album"
    page_size = 50

    mock_library = MagicMock()
    album = SharedPhotoStreamAlbum(
        library=mock_library,
        name=name,
        album_location=album_location,
        album_ctag=album_ctag,
        album_guid=album_guid,
        owner_dsid=owner_dsid,
        creation_date=creation_date,
        sharing_type=sharing_type,
        allow_contributions=allow_contributions,
        is_public=is_public,
        is_web_upload_supported=is_web_upload_supported,
        public_url=public_url,
        page_size=page_size,
    )

    assert album.id == album_guid
    assert album.fullname == name
    assert album.sharing_type == sharing_type
    assert album.allow_contributions is allow_contributions
    assert album.is_public is is_public
    assert album.is_web_upload_supported is is_web_upload_supported
    assert album.public_url == public_url
    assert isinstance(album.creation_date, datetime)
    assert album._album_location == album_location
    assert album._album_ctag == album_ctag
    assert album._album_guid == album_guid
    assert album._owner_dsid == owner_dsid


def test_shared_photo_stream_album_get_payload_and_url_and_len(
    mock_photos_service: MagicMock,
) -> None:
    """Test SharedPhotoStreamAlbum _get_payload, _get_url, and _get_len."""
    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = mock_photos_service
    mock_photos_service.params = {"dsid": "12345"}
    mock_photos_service.session.post.return_value.json.return_value = {
        "albumassetcount": 7
    }
    mock_album = SharedPhotoStreamAlbum(
        library=mock_library,
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
    )

    # Test _get_payload
    payload = mock_album._get_payload(
        offset=2, page_size=5, direction=DirectionEnum.ASCENDING
    )
    assert payload["albumguid"] == "guid"
    assert payload["albumctag"] == "ctag"
    assert payload["offset"] == "2"
    # limit should be offset+page_size or len(self), whichever is smaller
    # Since __len__ is not set, it will call _get_len, which returns 7
    assert payload["limit"] == str(min(2 + 5, 7))

    # Test _get_url
    url = mock_album._get_url()
    assert url.startswith("https://shared.example.com/album/webgetassets?")

    # Test _get_len
    length = mock_album._get_len()
    assert length == 7
    mock_photos_service.session.post.assert_called_with(
        "https://shared.example.com/album/webgetassetcount?dsid=12345",
        json={"albumguid": "guid"},
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_shared_photo_stream_album_delete_and_rename_are_noops() -> None:
    """Test that delete returns False and rename returns None for SharedPhotoStreamAlbum."""
    album = SharedPhotoStreamAlbum(
        library=MagicMock(),
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
    )
    assert album.delete() is False
    assert album.rename("New Name") is None


def test_create_album_success(mock_photos_service: MagicMock) -> None:
    """Tests successful creation of an album."""
    # Mock the POST response for indexing state
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(json=MagicMock(return_value=ALBUM_CREATE_RESPONSE)),
    ]
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    album: PhotoAlbum | None = library.create_album("My Album")
    assert album is not None
    assert album.name == "My Album"
    assert album.id == "album123"
    # Check that the correct POST was made for album creation
    expected_data = {
        "operations": [
            {
                "operationType": "create",
                "record": {
                    "recordType": "CPLAlbum",
                    "fields": {
                        "albumNameEnc": {
                            "value": base64.b64encode(
                                "My Album".encode("utf-8")
                            ).decode("utf-8"),
                        },
                        "albumType": {"value": AlbumTypeEnum.ALBUM.value},
                        "isDeleted": {"value": 0},
                        "isExpunged": {"value": 0},
                        "sortType": {"value": 1},
                        "sortAscending": {"value": 1},
                    },
                },
            }
        ],
        "zoneID": {"zoneName": "PrimarySync"},
        "atomic": True,
    }
    # The albumType value may be an enum, so just check the call was made
    assert mock_photos_service.session.post.call_count == 2
    args, kwargs = mock_photos_service.session.post.call_args
    assert "records/modify" in args[0]
    assert (
        kwargs["json"]["operations"][0]["record"]["fields"]["albumNameEnc"]["value"]
        == expected_data["operations"][0]["record"]["fields"]["albumNameEnc"]["value"]
    )
    assert kwargs["json"]["operations"][0]["record"]["fields"]["position"]["value"] > 0


def test_create_album_browser_fixture_matches_core_request_fields() -> None:
    """Browser album-create fixtures should match the core raw payload shape."""

    mock_photos_service = MagicMock()
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [{"fields": {"state": {"value": "FINISHED"}}}],
                }
            )
        ),
        MagicMock(json=MagicMock(return_value=BROWSER_ALBUM_CREATE_RESPONSE)),
    ]
    mock_photos_service.service_endpoint = "https://example.com/endpoint"
    mock_photos_service.params = {"dsid": "12345"}
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id=BROWSER_ALBUM_CREATE_REQUEST["zoneID"],
        upload_url="https://upload.example.com",
    )

    album = library.create_album("ALBUM_NAME_ENC_001")

    assert album is not None
    posted = _last_posted_json(mock_photos_service.session.post)
    request_record = posted["operations"][0]["record"]
    browser_record = BROWSER_ALBUM_CREATE_REQUEST["operations"][0]["record"]
    assert posted["atomic"] is BROWSER_ALBUM_CREATE_REQUEST["atomic"]
    assert posted["zoneID"] == BROWSER_ALBUM_CREATE_REQUEST["zoneID"]
    assert posted["operations"][0]["operationType"] == "create"
    assert request_record["recordType"] == browser_record["recordType"]
    for field_name in (
        "albumNameEnc",
        "albumType",
        "isDeleted",
        "isExpunged",
        "sortAscending",
        "sortType",
    ):
        assert (
            request_record["fields"][field_name] == browser_record["fields"][field_name]
        )
    assert "position" in request_record["fields"]
    assert "position" in browser_record["fields"]
    assert request_record["fields"]["position"]["value"] > 0
    assert BROWSER_ALBUM_CREATE_RESPONSE["records"][0]["recordType"] == "CPLAlbum"


def test_create_album_returns_none_on_invalid_response(
    mock_photos_service: MagicMock,
) -> None:
    """Tests create_album returns None if _convert_record_to_album returns None."""
    # Mock the POST response for indexing state
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "album123",
                            "recordChangeTag": "tag123",
                            "fields": {
                                # Missing albumNameEnc triggers None return
                                "isDeleted": {"value": False},
                            },
                        }
                    ]
                }
            )
        ),
    ]
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )
    album: PhotoAlbum | None = library.create_album("NoNameAlbum")
    assert album is None


def test_create_album_with_custom_album_type(mock_photos_service: MagicMock) -> None:
    """Tests create_album with a custom album_type."""
    # Mock the POST response for indexing state
    mock_photos_service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "fields": {
                                "state": {"value": "FINISHED"},
                            },
                        }
                    ],
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "album456",
                            "recordChangeTag": "tag456",
                            "fields": {
                                "albumNameEnc": {
                                    "value": base64.b64encode(b"Custom Album").decode(
                                        "utf-8"
                                    )
                                },
                                "isDeleted": {"value": False},
                            },
                        }
                    ]
                }
            )
        ),
    ]
    library = PhotoLibrary(
        service=mock_photos_service,
        zone_id={"zoneName": "PrimarySync"},
        upload_url="https://upload.example.com",
    )

    album: PhotoAlbum | None = library.create_album(
        "Custom Album", album_type=AlbumTypeEnum.ALBUM
    )
    assert album is not None
    assert album.name == "Custom Album"
    assert album.id == "album456"


def test_create_album_success_typed_client() -> None:
    """Tests album creation via the typed CloudKit client path."""
    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.modify.return_value = CKModifyResponse(
        records=[
            _ck_record(
                "CPLAlbum",
                "album123",
                {
                    "albumNameEnc": {
                        "type": "STRING",
                        "value": base64.b64encode(b"My Album").decode("utf-8"),
                    },
                    "isDeleted": {"type": "INT64", "value": 0},
                },
                recordChangeTag="tag123",
            )
        ],
        syncToken="sync-token",
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )

    album = library.create_album("My Album")

    assert album is not None
    assert album.name == "My Album"
    assert album.id == "album123"
    op = mock_client.modify.call_args.kwargs["operations"][0]
    assert op.operationType == "create"
    assert op.record.fields.get_value("position") > 0


def test_create_album_success_typed_client_populates_uncached_album_list() -> None:
    """Tests newly created albums become discoverable immediately when the cache was cold."""

    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CheckIndexingState",
                "indexing",
                {"state": {"type": "STRING", "value": "FINISHED"}},
            )
        ],
        syncToken="sync-token",
    )
    mock_client.modify.return_value = CKModifyResponse(
        records=[
            _ck_record(
                "CPLAlbum",
                "album123",
                {
                    "albumNameEnc": {
                        "type": "STRING",
                        "value": base64.b64encode(b"My Album").decode("utf-8"),
                    },
                    "isDeleted": {"type": "INT64", "value": 0},
                },
                recordChangeTag="tag123",
            )
        ],
        syncToken="sync-token",
    )
    service = SimpleNamespace(
        session=object(),
        service_endpoint="https://example.com/endpoint",
        params={"dsid": "12345"},
    )

    library = PhotoLibrary(
        service=service,
        zone_id={"zoneName": "PrimarySync"},
        client=mock_client,
        upload_url="https://upload.example.com",
    )
    library._get_albums = MagicMock(return_value=AlbumContainer())

    album = library.create_album("My Album")

    assert album is not None
    assert library._get_albums.call_count == 0
    found = library.albums.find("My Album")
    assert library._get_albums.call_count == 1
    assert found is album


def test_shared_photo_stream_album_get_photo_success(
    mock_photos_service: MagicMock,
    mock_photo_library: MagicMock,
) -> None:
    """Test SharedPhotoStreamAlbum _get_photo method with successful photo lookup."""
    mock_photos_service.params = {"dsid": "12345"}

    # Mock photo assets
    mock_photo1 = MagicMock(spec=PhotoAsset)
    mock_photo1.id = "photo1"
    mock_photo2 = MagicMock(spec=PhotoAsset)
    mock_photo2.id = "photo2"
    mock_photo3 = MagicMock(spec=PhotoAsset)
    mock_photo3.id = "photo3"

    album = SharedPhotoStreamAlbum(
        library=mock_photo_library,
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
        page_size=2,
    )

    # Mock _get_photos_at to return photos in pages
    album._get_photos_at = MagicMock(
        side_effect=[
            iter([mock_photo1, mock_photo2]),  # First page
            iter([mock_photo3]),  # Second page (photo found here)
        ]
    )

    result = album._get_photo("photo3")

    assert result == mock_photo3
    # Verify _get_photos_at was called twice with correct offsets
    assert album._get_photos_at.call_count == 2
    album._get_photos_at.assert_any_call(0, DirectionEnum.ASCENDING, 2)
    album._get_photos_at.assert_any_call(2, DirectionEnum.ASCENDING, 2)


def test_shared_photo_stream_album_get_photo_not_found(
    mock_photos_service: MagicMock,
) -> None:
    """Test SharedPhotoStreamAlbum _get_photo method when photo is not found."""
    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = mock_photos_service
    mock_photos_service.params = {"dsid": "12345"}

    # Mock photo assets
    mock_photo1 = MagicMock(spec=PhotoAsset)
    mock_photo1.id = "photo1"
    mock_photo2 = MagicMock(spec=PhotoAsset)
    mock_photo2.id = "photo2"

    album = SharedPhotoStreamAlbum(
        library=mock_library,
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
        page_size=3,
    )

    # Mock _get_photos_at to return photos in pages, last page is incomplete
    album._get_photos_at = MagicMock(
        side_effect=[
            iter(
                [mock_photo1, mock_photo2]
            ),  # First page (2 photos, less than page_size)
        ]
    )

    with pytest.raises(KeyError, match="Photo does not exist: nonexistent"):
        album._get_photo("nonexistent")

    # Verify _get_photos_at was called once
    album._get_photos_at.assert_called_once_with(0, DirectionEnum.ASCENDING, 3)


def test_shared_photo_stream_album_get_photo_found_in_first_page(
    mock_photos_service: MagicMock,
) -> None:
    """Test SharedPhotoStreamAlbum _get_photo method when photo is found in first page."""
    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = mock_photos_service
    mock_photos_service.params = {"dsid": "12345"}

    # Mock photo assets
    mock_photo1 = MagicMock(spec=PhotoAsset)
    mock_photo1.id = "target_photo"
    mock_photo2 = MagicMock(spec=PhotoAsset)
    mock_photo2.id = "photo2"

    album = SharedPhotoStreamAlbum(
        library=mock_library,
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
        page_size=2,
    )

    # Mock _get_photos_at to return target photo in first page
    album._get_photos_at = MagicMock(
        side_effect=[
            iter([mock_photo1, mock_photo2]),  # First page contains target
        ]
    )

    result = album._get_photo("target_photo")

    assert result == mock_photo1
    # Verify _get_photos_at was called only once
    album._get_photos_at.assert_called_once_with(0, DirectionEnum.ASCENDING, 2)


def test_smart_photo_album_len_uses_smart_container_id() -> None:
    """Typed smart album counts should use the smart-album object key without appending the album name."""

    client = MagicMock()
    client.batch_count.return_value = 135
    service = SimpleNamespace(
        session=SimpleNamespace(),
        params={"dsid": "12345"},
        service_endpoint="https://example.com",
    )
    library = MagicMock(spec=PhotoLibrary)
    library.service = service

    album = SmartPhotoAlbum(
        library=library,
        name=SmartAlbumEnum.FAVORITES,
        obj_type=ObjectTypeEnum.FAVORITE,
        list_type=ListTypeEnum.SMART_ALBUM,
        direction=DirectionEnum.ASCENDING,
        client=client,
        zone_id=PRIMARY_ZONE,
    )

    assert len(album) == 135
    client.batch_count.assert_called_once_with(
        container_id="CPLAssetInSmartAlbumByAssetDate:Favorite",
        zone_id=PRIMARY_ZONE,
    )


def test_smart_photo_album_upload_all_photos_delegates_to_library() -> None:
    """Tests the All Photos smart album delegates uploads to the backing library."""

    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = SimpleNamespace(
        session=object(),
        params={"dsid": "12345"},
        service_endpoint="https://example.com",
    )
    mock_photo_asset = MagicMock(spec=PhotoAsset)
    mock_photo_asset.id = "photo123"
    mock_library.upload_file.return_value = mock_photo_asset

    album = SmartPhotoAlbum(
        library=mock_library,
        name=SmartAlbumEnum.ALL_PHOTOS,
        obj_type=ObjectTypeEnum.ALL,
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        client=MagicMock(),
        zone_id=PRIMARY_ZONE,
    )

    result = album.upload("/path/to/photo.jpg")

    assert result == mock_photo_asset
    mock_library.upload_file.assert_called_once_with("/path/to/photo.jpg")


def test_smart_photo_album_upload_other_smart_album_returns_none() -> None:
    """Tests non-uploadable smart albums keep rejecting uploads."""

    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = SimpleNamespace(
        session=object(),
        params={"dsid": "12345"},
        service_endpoint="https://example.com",
    )

    album = SmartPhotoAlbum(
        library=mock_library,
        name=SmartAlbumEnum.FAVORITES,
        obj_type=ObjectTypeEnum.FAVORITE,
        list_type=ListTypeEnum.SMART_ALBUM,
        direction=DirectionEnum.ASCENDING,
        client=MagicMock(),
        zone_id=PRIMARY_ZONE,
    )

    assert album.upload("/path/to/photo.jpg") is None
    mock_library.upload_file.assert_not_called()


def test_shared_photo_stream_album_get_photo_empty_pages(
    mock_photos_service: MagicMock,
) -> None:
    """Test SharedPhotoStreamAlbum _get_photo method with empty album."""
    mock_library = MagicMock(spec=PhotoLibrary)
    mock_library.service = mock_photos_service
    mock_photos_service.params = {"dsid": "12345"}

    album = SharedPhotoStreamAlbum(
        library=mock_library,
        name="Empty Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
        page_size=10,
    )

    # Mock _get_photos_at to return empty iterator
    album._get_photos_at = MagicMock(
        side_effect=[
            iter([]),  # Empty first page
        ]
    )

    with pytest.raises(KeyError, match="Photo does not exist: any_photo"):
        album._get_photo("any_photo")

    # Verify _get_photos_at was called once
    album._get_photos_at.assert_called_once_with(0, DirectionEnum.ASCENDING, 10)


def test_shared_photo_stream_album_get_photo_payload_not_implemented() -> None:
    """Test SharedPhotoStreamAlbum _get_photo_payload raises NotImplementedError."""
    album = SharedPhotoStreamAlbum(
        library=MagicMock(),
        name="Shared Album",
        album_location="https://shared.example.com/album/",
        album_ctag="ctag",
        album_guid="guid",
        owner_dsid="owner",
        creation_date="1700000000000",
    )

    with pytest.raises(
        NotImplementedError,
        match="_get_photo_payload is not implemented for SharedPhotoStreamAlbum",
    ):
        album._get_photo_payload("photo123")


def test_base_photo_album_get_returns_photo(mock_photo_library: MagicMock) -> None:
    """Tests the get method returns a photo when it exists."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "photo123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to return the photo
    album._get_photo = MagicMock(return_value=mock_photo)

    result = album.get("photo123")

    assert result == mock_photo
    album._get_photo.assert_called_once_with("photo123")


def test_base_photo_album_get_returns_none_when_not_found(
    mock_photo_library: MagicMock,
) -> None:
    """Tests the get method returns None when photo doesn't exist."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to raise KeyError
    album._get_photo = MagicMock(side_effect=KeyError("Photo not found"))

    result = album.get("nonexistent")

    assert result is None
    album._get_photo.assert_called_once_with("nonexistent")


def test_base_photo_album_getitem_with_positive_index(
    mock_photo_library: MagicMock,
) -> None:
    """Tests __getitem__ with positive integer index."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "photo123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photos_at to return the photo
    album._get_photos_at = MagicMock(return_value=iter([mock_photo]))

    result = album[5]

    assert result == mock_photo
    album._get_photos_at.assert_called_once_with(5, DirectionEnum.ASCENDING, 1)


def test_base_photo_album_getitem_with_negative_index(
    mock_photo_library: MagicMock,
) -> None:
    """Tests __getitem__ with negative integer index."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "photo123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock len to return 10
    album._get_len = MagicMock(return_value=10)
    # Mock _get_photos_at to return the photo
    album._get_photos_at = MagicMock(return_value=iter([mock_photo]))

    result = album[-2]  # Should resolve to index 8 (10 + (-2))

    assert result == mock_photo
    album._get_photos_at.assert_called_once_with(8, DirectionEnum.ASCENDING, 1)


def test_base_photo_album_getitem_index_out_of_range(
    mock_photo_library: MagicMock,
) -> None:
    """Tests __getitem__ raises IndexError for out of range index."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photos_at to return empty iterator (StopIteration)
    album._get_photos_at = MagicMock(return_value=iter([]))

    with pytest.raises(IndexError, match="Photo index out of range"):
        _ = album[100]

    album._get_photos_at.assert_called_once_with(100, DirectionEnum.ASCENDING, 1)


def test_base_photo_album_getitem_with_string_key_found(
    mock_photo_library: MagicMock,
) -> None:
    """Tests __getitem__ with string key when photo exists."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "photo123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to return the photo
    album._get_photo = MagicMock(return_value=mock_photo)

    result = album["photo123"]

    assert result == mock_photo
    album._get_photo.assert_called_once_with("photo123")


def test_base_photo_album_getitem_with_string_key_not_found(
    mock_photo_library: MagicMock,
) -> None:
    """Tests __getitem__ with string key when photo doesn't exist."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to raise KeyError
    album._get_photo = MagicMock(side_effect=KeyError("Photo not found"))

    with pytest.raises(KeyError, match="Photo does not exist: nonexistent"):
        _ = album["nonexistent"]

    album._get_photo.assert_called_once_with("nonexistent")


def test_base_photo_album_contains_returns_true(mock_photo_library: MagicMock) -> None:
    """Tests __contains__ returns True when photo exists."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "photo123"

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to return the photo
    album._get_photo = MagicMock(return_value=mock_photo)

    result = "photo123" in album

    assert result is True
    album._get_photo.assert_called_once_with("photo123")


def test_base_photo_album_contains_returns_false(mock_photo_library: MagicMock) -> None:
    """Tests __contains__ returns False when photo doesn't exist."""
    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    # Mock _get_photo to raise KeyError
    album._get_photo = MagicMock(side_effect=KeyError("Photo not found"))

    result = "nonexistent" in album

    assert result is False
    album._get_photo.assert_called_once_with("nonexistent")


def test_photo_album_get_photo_success(mock_photo_library: MagicMock) -> None:
    """Tests _get_photo method when photo is found."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "target_photo"

    mock_photo_library.service.session.post.return_value.json.return_value = {
        "records": [
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "target_photo"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "target_photo",
            },
        ]
    }

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    result = album._get_photo("target_photo")

    assert result.id == "target_photo"
    mock_photo_library.service.session.post.assert_called_once_with(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_photo_payload("target_photo"),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_get_photo_success_typed_client_uses_minimum_lookup_limit() -> None:
    """Album lookups should use Apple's minimum accepted typed-query result size."""

    mock_client = MagicMock()
    mock_client.query.return_value = CKQueryResponse(
        records=[
            _ck_record(
                "CPLMaster",
                "target_photo",
                {
                    "filenameEnc": {
                        "type": "ENCRYPTED_BYTES",
                        "value": base64.b64encode(b"target.jpg").decode("utf-8"),
                    }
                },
                zoneID=PRIMARY_ZONE,
            ),
            _ck_record(
                "CPLAsset",
                "asset_photo",
                {
                    "masterRef": {
                        "type": "REFERENCE",
                        "value": {
                            "recordName": "target_photo",
                            "action": "DELETE_SELF",
                            "zoneID": PRIMARY_ZONE,
                        },
                    },
                    "assetDate": {"type": "TIMESTAMP", "value": 1775652698554},
                    "addedDate": {"type": "TIMESTAMP", "value": 1775652699130},
                    "isFavorite": {"type": "INT64", "value": 0},
                },
                zoneID=PRIMARY_ZONE,
            ),
        ],
        syncToken="sync-token",
    )
    mock_photo_library = MagicMock(spec=PhotoLibrary)
    mock_photo_library.service = SimpleNamespace(session=object())
    mock_photo_library.zone_id = PRIMARY_ZONE
    mock_photo_library.asset_type = PhotoAsset

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        client=mock_client,
        zone_id=PRIMARY_ZONE,
    )

    result = album._get_photo("target_photo")

    assert result.id == "target_photo"
    assert mock_client.query.call_args.kwargs["results_limit"] == 3


def test_photo_album_get_photo_not_found(mock_photo_library: MagicMock) -> None:
    """Tests _get_photo method when photo is not found."""
    mock_photo = MagicMock(spec=PhotoAsset)
    mock_photo.id = "different_photo"

    mock_photo_library.service.session.post.return_value.json.return_value = {
        "records": [
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "different_photo"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "different_photo",
            },
        ]
    }

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    with pytest.raises(KeyError, match="Photo does not exist: target_photo"):
        album._get_photo("target_photo")

    assert mock_photo_library.service.session.post.call_args_list[0] == call(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_photo_payload("target_photo"),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )
    assert mock_photo_library.service.session.post.call_args_list[1] == call(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_payload(
            offset=0,
            page_size=200,
            direction=DirectionEnum.ASCENDING,
        ),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_get_photo_empty_response(mock_photo_library: MagicMock) -> None:
    """Tests _get_photo method when no photos are returned."""
    mock_photo_library.service.session.post.return_value.json.return_value = {
        "records": []
    }

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    with pytest.raises(KeyError, match="Photo does not exist: nonexistent_photo"):
        album._get_photo("nonexistent_photo")

    assert mock_photo_library.service.session.post.call_args_list[0] == call(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_photo_payload("nonexistent_photo"),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )
    assert mock_photo_library.service.session.post.call_args_list[1] == call(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_payload(
            offset=0,
            page_size=200,
            direction=DirectionEnum.ASCENDING,
        ),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_album_get_photo_multiple_photos_found_correct_one(
    mock_photo_library: MagicMock,
) -> None:
    """Tests _get_photo method when multiple photos are returned but correct one is found."""
    mock_photo_library.service.session.post.return_value.json.return_value = {
        "records": [
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "photo1"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "photo1",
            },
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "target_photo"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "target_photo",
            },
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "photo3"}}},
            },
            {
                "recordType": "CPLMaster",
                "recordName": "photo3",
            },
        ]
    }

    album = PhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        record_id="album123",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url="https://example.com/records/query?dsid=12345",
    )

    result = album._get_photo("target_photo")

    assert result.id == "target_photo"
    mock_photo_library.service.session.post.assert_called_once_with(
        url="https://example.com/records/query?dsid=12345",
        json=album._get_photo_payload("target_photo"),
        headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
    )


def test_photo_asset_download_url_existing_version() -> None:
    """Test PhotoAsset download_url property for existing version."""
    master_record: dict[str, Any] = {
        "recordName": "photo_id_123",
        "fields": {
            "filenameEnc": {"value": base64.b64encode(b"test.jpg").decode("utf-8")},
            "resOriginalRes": {
                "value": {
                    "size": 123456,
                    "downloadURL": "http://example.com/original.jpg",
                }
            },
            "resJPEGThumbRes": {
                "value": {
                    "size": 1234,
                    "downloadURL": "http://example.com/thumb.jpg",
                }
            },
            "resOriginalWidth": {"value": 1920},
            "resOriginalHeight": {"value": 1080},
            "resJPEGThumbWidth": {"value": 100},
            "resJPEGThumbHeight": {"value": 50},
            "itemType": {"value": "public.jpeg"},
        },
    }
    asset_record: dict[str, Any] = {
        "fields": {"assetDate": {"value": 1700000000000}},
    }

    mock_service = MagicMock()
    asset = PhotoAsset(mock_service, master_record, asset_record)

    # Test original version
    assert asset.download_url("original") == "http://example.com/original.jpg"
    # Test thumb version
    assert asset.download_url("thumb") == "http://example.com/thumb.jpg"


def test_photo_asset_download_url_nonexistent_version() -> None:
    """Test PhotoAsset download_url property for nonexistent version."""
    master_record: dict[str, Any] = {
        "recordName": "photo_id_123",
        "fields": {
            "filenameEnc": {"value": base64.b64encode(b"test.jpg").decode("utf-8")},
            "resOriginalRes": {
                "value": {
                    "size": 123456,
                    "downloadURL": "http://example.com/original.jpg",
                }
            },
            "itemType": {"value": "public.jpeg"},
        },
    }
    asset_record: dict[str, Any] = {
        "fields": {"assetDate": {"value": 1700000000000}},
    }

    mock_service = MagicMock()
    asset = PhotoAsset(mock_service, master_record, asset_record)

    # Test nonexistent version
    assert asset.download_url("nonexistent") is None


def test_photo_asset_download_url_default_parameter() -> None:
    """Test PhotoAsset download_url property with default parameter."""
    master_record: dict[str, Any] = {
        "recordName": "photo_id_123",
        "fields": {
            "filenameEnc": {"value": base64.b64encode(b"test.jpg").decode("utf-8")},
            "resOriginalRes": {
                "value": {
                    "size": 123456,
                    "downloadURL": "http://example.com/original.jpg",
                }
            },
            "itemType": {"value": "public.jpeg"},
        },
    }
    asset_record: dict[str, Any] = {
        "fields": {"assetDate": {"value": 1700000000000}},
    }

    mock_service = MagicMock()
    asset = PhotoAsset(mock_service, master_record, asset_record)

    # Test default parameter (should be "original")
    assert asset.download_url() == "http://example.com/original.jpg"
