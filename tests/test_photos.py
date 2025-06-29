"""PhotoLibrary tests."""

# pylint: disable=protected-access
import json
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.photos import (
    AlbumContainer,
    BasePhotoAlbum,
    BasePhotoLibrary,
    DirectionEnum,
    PhotoAlbum,
    PhotoLibrary,
    PhotosService,
    PhotoStreamLibrary,
    SharedPhotoStreamAlbum,
    SmartAlbumEnum,
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
    mock_photos_service.session.post.return_value.json.return_value = {
        "records": [
            {
                "fields": {
                    "state": {"value": "NOT_FINISHED"},
                },
            }
        ]
    }
    with pytest.raises(PyiCloudServiceNotActivatedException):
        PhotoLibrary(
            service=mock_photos_service,
            zone_id={"zoneName": "PrimarySync"},
            upload_url="https://upload.example.com",
        )


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
    albums: list[BasePhotoAlbum] = list(library.albums.values())

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
    albums: AlbumContainer = library.albums
    assert SmartAlbumEnum.ALL_PHOTOS in albums
    assert "folder1" in albums
    assert albums["folder1"].name == "folder1"
    assert albums["folder1"].direction == DirectionEnum.ASCENDING


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
                            "recordType": "CPLAsset",
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

    with patch("builtins.open", mock_open(read_data=b"file_content")):
        record_name: dict[str, Any] = library.upload_file("test_photo.jpg")

    assert record_name == "uploaded_photo"
    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload",
        data=b"file_content",
        params={"filename": "test_photo.jpg", "dsid": "12345"},
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

    with patch("builtins.open", mock_open(read_data=b"file_content")):
        with pytest.raises(PyiCloudAPIResponseException) as exc_info:
            library.upload_file("test_photo.jpg")

    assert "UPLOAD_ERROR" in str(exc_info.value)
    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload",
        data=b"file_content",
        params={"filename": "test_photo.jpg", "dsid": "12345"},
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

    with patch("builtins.open", mock_open(read_data=b"file_content")):
        with pytest.raises(IndexError):
            library.upload_file("test_photo.jpg")

    mock_photos_service.session.post.assert_called_with(
        url="https://upload.example.com/upload",
        data=b"file_content",
        params={"filename": "test_photo.jpg", "dsid": "12345"},
    )


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
    albums: list[BasePhotoAlbum] = list(library.albums.values())
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
    albums: list[BasePhotoAlbum] = list(library.albums.values())

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
    album = BasePhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        list_type="CPLAssetAndMasterByAssetDate",
        asset_type=MagicMock,
        page_size=50,
        direction=DirectionEnum.ASCENDING,
    )
    assert album.name == "Test Album"
    assert album.service == mock_photo_library.service
    assert album.page_size == 50
    assert album.direction == DirectionEnum.ASCENDING
    assert album.list_type == "CPLAssetAndMasterByAssetDate"
    assert album.asset_type == MagicMock


def test_base_photo_album_parse_response() -> None:
    """Tests the _parse_response method."""
    album = BasePhotoAlbum(
        library=MagicMock(),
        name="Test Album",
        list_type="CPLAssetAndMasterByAssetDate",
        asset_type=MagicMock,
    )
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
    asset_records, master_records = album._parse_response(response)
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
        list_type="CPLAssetAndMasterByAssetDate",
        obj_type="MagicMock",
        direction=DirectionEnum.ASCENDING,
        page_size=10,
        url="https://example.com/records/query?dsid=12345",
    )
    photos = list(album.photos)
    assert len(photos) == 1
    mock_photo_library.service.session.post.assert_called()


def test_base_photo_album_len(mock_photos_service: MagicMock) -> None:
    """Tests the __len__ method."""
    album = BasePhotoAlbum(
        library=mock_photos_service,
        name="Test Album",
        list_type="CPLAssetAndMasterByAssetDate",
        asset_type=MagicMock,
    )
    album._get_len = MagicMock(return_value=42)
    assert len(album) == 42
    album._get_len.assert_called_once()


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
        list_type="CPLAssetAndMasterByAssetDate",
        obj_type="MagicMock",
        direction=DirectionEnum.ASCENDING,
        page_size=10,
        url="https://example.com/records/query?dsid=12345",
    )
    photos = list(iter(album))
    assert len(photos) == 1
    mock_photo_library.service.session.post.assert_called()


def test_base_photo_album_str() -> None:
    """Tests the __str__ method."""
    album = BasePhotoAlbum(
        library=MagicMock(),
        name="Test Album",
        list_type="CPLAssetAndMasterByAssetDate",
        asset_type=MagicMock,
    )
    assert str(album) == "Test Album"


def test_base_photo_album_repr() -> None:
    """Tests the __repr__ method."""
    album = BasePhotoAlbum(
        library=MagicMock(),
        name="Test Album",
        list_type="CPLAssetAndMasterByAssetDate",
        asset_type=MagicMock,
    )
    assert repr(album) == "<BasePhotoAlbum: 'Test Album'>"


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
    libraries = photos_service.libraries
    assert "root" in libraries
    assert "shared" in libraries
    assert "CustomZone" in libraries
    assert isinstance(libraries["root"], PhotoLibrary)
    assert isinstance(libraries["shared"], PhotoStreamLibrary)
    assert isinstance(libraries["CustomZone"], PhotoLibrary)
    mock_photos_service.session.post.assert_called_with(
        url="https://example.com/database/1/com.apple.photos.cloud/production/private/records/query?dsid=12345&remapEnums=True&getCurrentSyncToken=True",
        data=json.dumps(
            {
                "query": {"recordType": "CheckIndexingState"},
                "zoneID": {"zoneName": "CustomZone"},
            }
        ),
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
    assert isinstance(shared_streams["Shared Album"], SharedPhotoStreamAlbum)
    mock_photos_service.session.post.assert_called()
