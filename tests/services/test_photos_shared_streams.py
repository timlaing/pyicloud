"""Tests for the shared_streams module."""

# pylint: disable=protected-access

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.services.photos.constants import DirectionEnum
from pyicloud.services.photos.shared_streams import (
    PhotoStreamAsset,
    PhotoStreamLibrary,
    SharedPhotoStreamAlbum,
)


class MockService:
    """Mock PhotosService for testing."""

    def __init__(self) -> None:
        self.params = {"dsid": "12345"}
        self.session = MagicMock()
        self.service_endpoint = "https://example.com"


def test_photo_stream_library_get_albums() -> None:
    """Test PhotoStreamLibrary._get_albums fetches and parses albums."""
    mock_service = MockService()
    mock_service.session.post.return_value.json.return_value = {
        "albums": [
            {
                "attributes": {
                    "name": "Test Album",
                    "creationDate": "1609459200000",
                    "allowcontributions": True,
                    "ispublic": False,
                },
                "albumlocation": "https://shared.example.com/",
                "albumctag": "ctag123",
                "albumguid": "guid123",
                "ownerdsid": "12345",
                "sharingtype": "owned",
                "iswebuploadsupported": True,
            }
        ]
    }

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    albums = library._get_albums()

    assert len(albums) == 1
    album = albums[0]
    assert isinstance(album, SharedPhotoStreamAlbum)
    assert album.name == "Test Album"
    assert album.id == "guid123"
    assert album.allow_contributions is True
    assert album.is_public is False
    assert album.is_web_upload_supported is True

    mock_service.session.post.assert_called_once()


def test_photo_stream_library_get_albums_empty() -> None:
    """Test PhotoStreamLibrary._get_albums with empty response."""
    mock_service = MockService()
    mock_service.session.post.return_value.json.return_value = {"albums": []}

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    albums = library._get_albums()
    assert len(albums) == 0


def test_shared_photo_stream_album_get_len() -> None:
    """Test SharedPhotoStreamAlbum._get_len fetches count."""
    mock_service = MockService()
    mock_service.session.post.return_value.json.return_value = {"albumassetcount": 42}

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    length = album._get_len()
    assert length == 42

    mock_service.session.post.assert_called_once()


def test_shared_photo_stream_album_get_len_error() -> None:
    """Test SharedPhotoStreamAlbum._get_len handles error response."""
    mock_service = MockService()
    mock_service.session.post.return_value.json.return_value = {}

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    # Should raise KeyError or return 0
    with pytest.raises(KeyError):
        album._get_len()


def test_photo_stream_asset_like_count() -> None:
    """Test PhotoStreamAsset.like_count property."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={
            "pluginFields": {
                "likeCount": {"value": 5},
                "likedByCaller": {"value": True},
            }
        },
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.like_count == 5


def test_photo_stream_asset_like_count_missing() -> None:
    """Test PhotoStreamAsset.like_count with missing pluginFields."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.like_count == 0


def test_photo_stream_asset_liked() -> None:
    """Test PhotoStreamAsset.liked property."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={
            "pluginFields": {
                "likeCount": {"value": 5},
                "likedByCaller": {"value": True},
            }
        },
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.liked is True


def test_photo_stream_asset_liked_false() -> None:
    """Test PhotoStreamAsset.liked property when not liked."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={
            "pluginFields": {
                "likeCount": {"value": 5},
                "likedByCaller": {"value": False},
            }
        },
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.liked is False


def test_photo_stream_asset_asset_date() -> None:
    """Test PhotoStreamAsset.asset_date from originalCreationDate."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={
            "originalCreationDate": 1609459200000,  # 2021-01-01 00:00:00 UTC
        },
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    expected = datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert photo.asset_date == expected


def test_photo_stream_asset_asset_date_missing() -> None:
    """Test PhotoStreamAsset.asset_date with missing originalCreationDate."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    expected = datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert photo.asset_date == expected


def test_photo_stream_asset_size() -> None:
    """Test PhotoStreamAsset.size from resOriginalFileSize."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={
            "resOriginalFileSize": {"value": 1024000},
        },
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.size == 1024000


def test_photo_stream_asset_size_missing() -> None:
    """Test PhotoStreamAsset.size with missing resOriginalFileSize."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={},
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.size == 0


def test_photo_stream_asset_size_invalid() -> None:
    """Test PhotoStreamAsset.size with invalid value."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={
            "resOriginalFileSize": {"value": "invalid"},
        },
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.size == 0


def test_shared_photo_stream_album_properties() -> None:
    """Test SharedPhotoStreamAlbum read-only properties."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
        sharing_type="owned",
        allow_contributions=True,
        is_public=False,
        is_web_upload_supported=True,
        public_url="https://public.example.com/album",
    )

    assert album.sharing_type == "owned"
    assert album.allow_contributions is True
    assert album.is_public is False
    assert album.is_web_upload_supported is True
    assert album.public_url == "https://public.example.com/album"


def test_shared_photo_stream_album_get_payload() -> None:
    """Test SharedPhotoStreamAlbum._get_payload."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    payload = album._get_payload(0, 100, DirectionEnum.ASCENDING)
    assert payload == {
        "albumguid": "guid123",
        "albumctag": "ctag123",
        "limit": "100",
        "offset": "0",
    }


def test_shared_photo_stream_album_get_photo_payload() -> None:
    """Test SharedPhotoStreamAlbum._get_photo_payload raises NotImplementedError."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    with pytest.raises(
        NotImplementedError, match="_get_photo_payload is not implemented"
    ):
        album._get_photo_payload("photo123")


def test_shared_photo_stream_album_get_photo() -> None:
    """Test SharedPhotoStreamAlbum._get_photo finds photo by paging."""
    mock_service = MockService()

    # Mock the _get_photos_at method to return photos
    mock_photos = [
        SimpleNamespace(id="photo1", filename="photo1.jpg"),
        SimpleNamespace(id="photo2", filename="photo2.jpg"),
    ]

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    # Patch _get_photos_at to return our mock photos
    with patch.object(album, "_get_photos_at", return_value=iter(mock_photos)):
        photo = album._get_photo("photo2")
        assert photo.id == "photo2"


def test_shared_photo_stream_album_get_photo_not_found() -> None:
    """Test SharedPhotoStreamAlbum._get_photo raises KeyError when not found."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    # Patch _get_photos_at to return empty iterator
    with (
        patch.object(album, "_get_photos_at", return_value=iter([])),
        pytest.raises(KeyError, match="Photo does not exist: photo999"),
    ):
        album._get_photo("photo999")


def test_shared_photo_stream_album_delete() -> None:
    """Test SharedPhotoStreamAlbum.delete returns False."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    assert album.delete() is False


def test_shared_photo_stream_album_rename() -> None:
    """Test SharedPhotoStreamAlbum.rename returns None."""
    mock_service = MockService()

    library = PhotoStreamLibrary(
        service=mock_service,
        shared_streams_url="https://shared.example.com/webgetalbumslist",
    )

    album = SharedPhotoStreamAlbum(
        library=library,
        name="Test Album",
        album_location="https://shared.example.com/",
        album_ctag="ctag123",
        album_guid="guid123",
        owner_dsid="12345",
        creation_date="1609459200000",
    )

    assert album.rename("New Name") is None


def test_photo_stream_asset_asset_date_datetime() -> None:
    """Test PhotoStreamAsset.asset_date with datetime value."""
    mock_service = MockService()

    master_record = SimpleNamespace(
        fields={
            "originalCreationDate": datetime(2021, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        },
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    expected = datetime(2021, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert photo.asset_date == expected


def test_photo_stream_asset_size_with_object() -> None:
    """Test PhotoStreamAsset.size with object that has value attribute."""
    mock_service = MockService()

    @dataclass
    class FakeField:
        """Fake field with value attribute for testing."""

        value: int

    master_record = SimpleNamespace(
        fields={
            "resOriginalFileSize": FakeField(2048000),
        },
        recordName="master123",
    )

    asset_record = SimpleNamespace(
        fields={},
        recordName="asset123",
    )

    photo = PhotoStreamAsset(mock_service, master_record, asset_record)
    assert photo.size == 2048000
