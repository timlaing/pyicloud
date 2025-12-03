"""PhotoLibrary tests."""

# pylint: disable=protected-access
import base64
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

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
    album = BasePhotoAlbum(
        library=mock_photo_library,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
        page_size=50,
        direction=DirectionEnum.ASCENDING,
    )
    assert album.name == "Test Album"
    assert album.service == mock_photo_library.service
    assert album.page_size == 50


def test_base_photo_album_parse_response() -> None:
    """Tests the _parse_response method."""
    library = BasePhotoLibrary(
        service=MagicMock(),
        asset_type=PhotoAsset,
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
    asset_records, master_records = library.parse_asset_response(response)
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


def test_base_photo_album_len(mock_photos_service: MagicMock) -> None:
    """Tests the __len__ method."""
    album = BasePhotoAlbum(
        library=mock_photos_service,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
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


def test_base_photo_album_str() -> None:
    """Tests the __str__ method."""
    album = BasePhotoAlbum(
        library=MagicMock(),
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
    )
    assert str(album) == "Test Album"


def test_base_photo_album_repr() -> None:
    """Tests the __repr__ method."""
    album = BasePhotoAlbum(
        library=MagicMock(),
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
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
    libraries: dict[str, BasePhotoLibrary] = photos_service.libraries
    assert "root" in libraries
    assert "shared" in libraries
    assert "CustomZone" in libraries
    assert isinstance(libraries["root"], PhotoLibrary)
    assert isinstance(libraries["shared"], PhotoStreamLibrary)
    assert isinstance(libraries["CustomZone"], PhotoLibrary)
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
    mock_photo_library.service.session.post.return_value.json.return_value = {
        "records": [
            {
                "recordChangeTag": "new_tag",
                "fields": {"recordModificationDate": {"value": "2023-02-01T00:00:00Z"}},
            }
        ]
    }
    album.rename("Another Name")
    assert album._record_change_tag == "new_tag"
    assert album._record_modification_date == "2023-02-01T00:00:00Z"


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


def test_photo_album_upload_success(mock_photos_service: MagicMock) -> None:
    """Tests successful photo upload to album."""
    mock_photo_library: MagicMock = MagicMock(spec=PhotoLibrary)
    mock_photo_asset = MagicMock()
    mock_photo_asset.id = "photo123"
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
                        "itemId": {"value": "photo123"},
                        "position": {"value": 1024},
                        "containerId": {"value": "album123"},
                    },
                    "recordType": "CPLContainerRelation",
                    "recordName": "photo123-IN-album123",
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
        MagicMock(
            json=MagicMock(
                return_value={
                    "records": [
                        {
                            "recordName": "album123",
                            "recordChangeTag": "tag123",
                            "fields": {
                                "albumNameEnc": {
                                    "value": base64.b64encode(b"My Album").decode(
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
