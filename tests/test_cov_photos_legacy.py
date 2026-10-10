"""Coverage-focused unit tests for the legacy Photos service internals.

These tests exercise the raw CloudKit-backed classes in
``pyicloud.services.photos_legacy`` directly. Network access is always mocked
through ``MagicMock`` sessions and every file-system touch is intercepted.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.photos_legacy import (
    PRIMARY_ZONE,
    AlbumContainer,
    AlbumTypeEnum,
    BasePhotoAlbum,
    BasePhotoLibrary,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    PhotoAlbum,
    PhotoAlbumFolder,
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    PhotosServiceException,
    PhotoStreamAsset,
    SmartAlbumEnum,
    SmartPhotoAlbum,
    _valid_modify_records,
)

# pylint: disable=protected-access

EPOCH = datetime.fromtimestamp(0, timezone.utc)
ASSET_EPOCH = datetime.fromtimestamp(1_700_000_000, timezone.utc)
ENDPOINT = "https://example.com/endpoint"
QUERY_URL = "https://example.com/records/query?dsid=12345"


def _b64(text: str) -> str:
    """B64."""
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def _mock_service() -> MagicMock:
    """Mock service."""
    service = MagicMock()
    service.service_endpoint = ENDPOINT
    service.params = {"dsid": "12345"}
    service.session = MagicMock()
    return service


def _master_record(
    record_name: str = "master-1",
    *,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Master record."""
    merged: dict[str, Any] = {
        "itemType": {"value": "public.jpeg"},
        "filenameEnc": {"value": _b64("IMG_0001.JPG")},
        "resOriginalRes": {
            "value": {"size": 12345, "downloadURL": "https://cdn.local/original"}
        },
        "resOriginalWidth": {"value": 4032},
        "resOriginalHeight": {"value": 3024},
        "resOriginalFileType": {"value": "public.jpeg"},
        "resJPEGMedRes": {
            "value": {"size": 456, "downloadURL": "https://cdn.local/med"}
        },
        "resJPEGMedWidth": {"value": 1024},
        "resJPEGMedHeight": {"value": 768},
        "resJPEGMedFileType": {"value": "public.jpeg"},
    }
    if fields:
        merged.update(fields)
    return {
        "recordName": record_name,
        "recordType": "CPLMaster",
        "recordChangeTag": "master-tag",
        "zoneID": {"zoneName": "PrimarySync"},
        "fields": merged,
    }


def _asset_record(
    record_name: str = "asset-1",
    *,
    master_name: str = "master-1",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Asset record."""
    merged: dict[str, Any] = {
        "assetDate": {"value": 1_700_000_000_000},
        "addedDate": {"value": 1_700_000_000_000},
        "masterRef": {"value": {"recordName": master_name}},
    }
    if fields:
        merged.update(fields)
    return {
        "recordName": record_name,
        "recordType": "CPLAsset",
        "recordChangeTag": "asset-tag",
        "zoneID": {"zoneName": "PrimarySync"},
        "fields": merged,
    }


def _album_record(
    record_name: str = "album-1",
    *,
    name: str = "Album",
    album_type: AlbumTypeEnum = AlbumTypeEnum.ALBUM,
    sort_ascending: int = 1,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Album record."""
    fields: dict[str, Any] = {
        "albumNameEnc": {"value": _b64(name)},
        "albumType": {"value": album_type.value},
        "sortAscending": {"value": sort_ascending},
        "recordChangeTag": {"value": "record-tag"},
    }
    if extra_fields:
        fields.update(extra_fields)
    return {
        "recordName": record_name,
        "recordType": "CPLAlbum",
        "recordChangeTag": "tag",
        "zoneID": {"zoneName": "PrimarySync"},
        "fields": fields,
    }


def _library_stub(service: Any | None = None) -> PhotoLibrary:
    """Library stub."""
    library = PhotoLibrary.__new__(PhotoLibrary)
    library.service = service if service is not None else _mock_service()
    library.asset_type = PhotoAsset
    library._albums = AlbumContainer()
    library._upload_url = "https://upload.example.com"
    library.zone_id = {"zoneName": "PrimarySync", "zoneType": "REGULAR_CUSTOM_ZONE"}
    library.url = QUERY_URL
    return library


def _album(library: Any, **overrides: Any) -> PhotoAlbum:
    """Album."""
    params: dict[str, Any] = {
        "name": "Album",
        "record_id": "album-1",
        "obj_type": ObjectTypeEnum.CONTAINER,
        "list_type": ListTypeEnum.CONTAINER,
        "direction": DirectionEnum.ASCENDING,
        "url": QUERY_URL,
        "record_change_tag": "album-tag",
    }
    params.update(overrides)
    return PhotoAlbum(library=library, **params)


def _photos_service() -> PhotosService:
    """Photos service."""
    return PhotosService(
        service_root="https://example.com",
        session=MagicMock(),
        params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        shared_streams_url="https://shared.example.com",
    )


class _ConcreteLibrary(BasePhotoLibrary):
    def __init__(self) -> None:
        """Init."""
        super().__init__(service=MagicMock(), asset_type=PhotoAsset)

    def _get_albums(self) -> AlbumContainer:
        """Get albums."""
        return AlbumContainer()


# ---------------------------------------------------------------------------
# _valid_modify_records
# ---------------------------------------------------------------------------


def test_valid_modify_records_empty_on_errors() -> None:
    """Test valid modify records empty on errors."""
    assert not _valid_modify_records({"errors": [{"reason": "boom"}]})


def test_valid_modify_records_skips_non_dict_records() -> None:
    """Test valid modify records skips non dict records."""
    assert _valid_modify_records({"records": ["nope", {"recordName": "a"}]}) == [
        {"recordName": "a"}
    ]


# ---------------------------------------------------------------------------
# AlbumContainer
# ---------------------------------------------------------------------------


def test_album_container_accessors() -> None:
    """Test album container accessors."""
    first: Any = MagicMock(spec=BasePhotoAlbum)
    first.id = "id-1"
    first.fullname = "First"
    second: Any = MagicMock(spec=BasePhotoAlbum)
    second.id = "id-2"
    second.fullname = "Second"

    container = AlbumContainer([first, second])

    assert len(container) == 2
    assert container[0] is first
    assert container["id-2"] is second
    assert container["Second"] is second
    assert list(iter(container)) == [first, second]
    assert "Second" in container
    assert "Missing" not in container
    assert container.get("id-1") is first
    assert container.get("nope") is None
    assert container.index(1) is second

    with pytest.raises(KeyError, match="Photo album does not exist"):
        _ = container["missing"]

    with pytest.raises(IndexError, match="Photo album index out of range"):
        container.index(5)


def test_album_container_append() -> None:
    """Test album container append."""
    container = AlbumContainer()
    assert len(container) == 0

    album: Any = MagicMock(spec=BasePhotoAlbum)
    album.id = "new-id"
    album.fullname = "New"
    container.append(album)

    assert container.index(0) is album


# ---------------------------------------------------------------------------
# BasePhotoLibrary
# ---------------------------------------------------------------------------


def test_base_photo_library_get_albums_is_abstract() -> None:
    """Test base photo library get albums is abstract."""
    library = _ConcreteLibrary()
    with pytest.raises(NotImplementedError):
        BasePhotoLibrary._get_albums(library)


def test_base_photo_library_parse_asset_response_branches() -> None:
    """Test base photo library parse asset response branches."""
    library = _ConcreteLibrary()

    records_not_list: Any = {"records": "nope"}
    non_dict_records: Any = {"records": ["nope"]}
    non_dict_fields: Any = {"records": [{"recordType": "CPLAsset", "fields": "nope"}]}
    non_dict_master_ref: Any = {
        "records": [{"recordType": "CPLAsset", "fields": {"masterRef": "nope"}}]
    }
    non_dict_master_value: Any = {
        "records": [{"recordType": "CPLAsset", "fields": {"masterRef": {"value": "x"}}}]
    }

    assert library.parse_asset_response(records_not_list) == ({}, [])
    assert library.parse_asset_response(non_dict_records) == ({}, [])
    assert library.parse_asset_response(non_dict_fields) == ({}, [])
    assert library.parse_asset_response(non_dict_master_ref) == ({}, [])
    assert library.parse_asset_response(non_dict_master_value) == ({}, [])


def test_base_photo_library_parse_asset_response_success() -> None:
    """Test base photo library parse asset response success."""
    library = _ConcreteLibrary()
    response = {
        "records": [
            {"recordType": "CPLMaster", "recordName": "master-1", "fields": {}},
            {
                "recordType": "CPLAsset",
                "recordName": "asset-1",
                "fields": {"masterRef": {"value": {"recordName": "master-1"}}},
            },
        ]
    }

    assets, masters = library.parse_asset_response(response)

    assert list(assets) == ["master-1"]
    assert [record["recordName"] for record in masters] == ["master-1"]


# ---------------------------------------------------------------------------
# PhotoLibrary.__init__ indexing guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"records": ["not-a-dict"]},
        {"records": [{"fields": "not-a-dict"}]},
        {"records": [{"fields": {"state": "not-a-dict"}}]},
    ],
)
def test_photo_library_init_requires_finished_indexing(
    payload: dict[str, Any],
) -> None:
    """Test photo library init requires finished indexing."""
    service = _mock_service()
    service.session.post.return_value.json.return_value = payload

    with pytest.raises(PyiCloudServiceNotActivatedException):
        PhotoLibrary(service=service, zone_id=dict(PRIMARY_ZONE))


# ---------------------------------------------------------------------------
# PhotoLibrary._fetch_records / _convert_record_to_album / _get_albums
# ---------------------------------------------------------------------------


def test_photo_library_fetch_records_expands_folders() -> None:
    """Test photo library fetch records expands folders."""
    service = _mock_service()
    folder = _album_record(
        "folder-1",
        name="Folder",
        album_type=AlbumTypeEnum.FOLDER,
    )
    album = _album_record("album-1", name="Album")
    child = _album_record("child-1", name="Child")
    service.session.post.side_effect = [
        MagicMock(json=MagicMock(return_value={"records": [folder, album]})),
        MagicMock(json=MagicMock(return_value={"records": [child]})),
    ]

    library = _library_stub(service)

    records = library._fetch_records()

    assert [record["recordName"] for record in records] == [
        "folder-1",
        "album-1",
        "child-1",
    ]
    assert service.session.post.call_count == 2


def test_photo_library_fetch_records_follows_continuation_marker() -> None:
    """Test photo library fetch records follows continuation marker."""
    service = _mock_service()
    first = _album_record("album-1", name="One")
    second = _album_record("album-2", name="Two")
    service.session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={"records": [first], "continuationMarker": "marker"}
            )
        ),
        MagicMock(json=MagicMock(return_value={"records": [second]})),
    ]

    library = _library_stub(service)

    records = library._fetch_records()

    assert [record["recordName"] for record in records] == ["album-1", "album-2"]


def test_photo_library_convert_record_to_album_folder() -> None:
    """Test photo library convert record to album folder."""
    library = _library_stub()
    record = _album_record(
        "folder-1",
        name="Vacation",
        album_type=AlbumTypeEnum.FOLDER,
        extra_fields={
            "sortAscending": {"value": 0},
            "recordModificationDate": {"value": "2026-01-01T00:00:00Z"},
            "parentId": {"value": "parent-1"},
        },
    )

    album = library._convert_record_to_album(record)

    assert album is not None
    assert isinstance(album, PhotoAlbumFolder)
    assert album.name == "Vacation"
    assert album.fullname == "Vacation"
    assert album._direction == DirectionEnum.DESCENDING
    assert album._parent_id == "parent-1"
    assert album._record_modification_date == "2026-01-01T00:00:00Z"


def test_photo_library_convert_record_to_album_album() -> None:
    """Test photo library convert record to album album."""
    library = _library_stub()
    record = _album_record("album-1", name="Plain", extra_fields={})

    album = library._convert_record_to_album(record)

    assert album is not None
    assert not isinstance(album, PhotoAlbumFolder)
    assert album._direction == DirectionEnum.ASCENDING


def test_photo_library_get_albums_builds_smart_and_custom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test photo library get albums builds smart and custom."""
    library = _library_stub()
    record = _album_record("album-1", name="Custom")
    monkeypatch.setattr(library, "_fetch_records", lambda parent_id=None: [record])

    albums = library._get_albums()

    assert len(albums) == len(PhotoLibrary.SMART_ALBUMS) + 1
    assert albums.find("Custom") is not None


# ---------------------------------------------------------------------------
# PhotoLibrary.create_album / upload_file
# ---------------------------------------------------------------------------


def test_photo_library_create_album_returns_album() -> None:
    """Test photo library create album returns album."""
    service = _mock_service()
    record = _album_record("album-1", name="Created")
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"records": [record]})
    )

    library = _library_stub(service)

    album = library.create_album("Created")

    assert album is not None
    assert album.name == "Created"
    assert album.id == "album-1"


def test_photo_library_create_album_returns_none_for_empty_records() -> None:
    """Test photo library create album returns none for empty records."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"records": []})
    )

    library = _library_stub(service)

    assert library.create_album("Created") is None


def test_photo_library_create_album_wraps_api_error() -> None:
    """Test photo library create album wraps api error."""
    service = _mock_service()
    service.session.post.side_effect = PyiCloudAPIResponseException("boom")

    library = _library_stub(service)

    with pytest.raises(PhotosServiceException, match="Failed to create album"):
        library.create_album("Created")


def test_photo_library_upload_file_returns_asset() -> None:
    """Test photo library upload file returns asset."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"records": [_master_record(), _asset_record()]})
    )

    library = _library_stub(service)

    with patch("builtins.open", mock_open(read_data=b"data")):
        result = library.upload_file("/tmp/python-test-results/photo.jpg")

    assert isinstance(result, PhotoAsset)


def test_photo_library_upload_file_raises_on_errors() -> None:
    """Test photo library upload file raises on errors."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"errors": [{"reason": "nope"}]})
    )

    library = _library_stub(service)

    with (
        patch("builtins.open", mock_open(read_data=b"data")),
        pytest.raises(PyiCloudAPIResponseException),
    ):
        library.upload_file("/tmp/python-test-results/photo.jpg")


def test_photo_library_upload_file_returns_none_for_missing_records() -> None:
    """Test photo library upload file returns none for missing records."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"records": [_master_record()]})
    )

    library = _library_stub(service)

    with patch("builtins.open", mock_open(read_data=b"data")):
        result = library.upload_file("/tmp/python-test-results/photo.jpg")

    assert result is None


# ---------------------------------------------------------------------------
# PhotosService lazy accessors
# ---------------------------------------------------------------------------


def test_photos_service_get_root_library_returns_cached() -> None:
    """Test photos service get root library returns cached."""
    service = _photos_service()
    cached = MagicMock(spec=PhotoLibrary)
    service._root_library = cached

    assert service._get_root_library() is cached


def test_photos_service_libraries_caches_and_skips_deleted() -> None:
    """Test photos service libraries caches and skips deleted."""
    service = _photos_service()
    service._root_library = MagicMock(spec=PhotoLibrary)
    session = cast(MagicMock, service.session)
    session.post.side_effect = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "zones": [
                        {"zoneID": {"zoneName": "CustomZone"}},
                        {"zoneID": {"zoneName": "DeadZone"}, "deleted": True},
                    ]
                }
            )
        ),
        MagicMock(
            json=MagicMock(
                return_value={"records": [{"fields": {"state": {"value": "FINISHED"}}}]}
            )
        ),
    ]

    libraries = service.libraries

    assert "CustomZone" in libraries
    assert "DeadZone" not in libraries
    assert service.libraries is libraries


def test_photos_service_albums_and_create_album_delegate() -> None:
    """Test photos service albums and create album delegate."""
    service = _photos_service()
    root = MagicMock(spec=PhotoLibrary)
    service._root_library = root

    assert service.albums is root.albums

    service.create_album("Name", AlbumTypeEnum.ALBUM)

    root.create_album.assert_called_once_with("Name", AlbumTypeEnum.ALBUM)


# ---------------------------------------------------------------------------
# BasePhotoAlbum behavior
# ---------------------------------------------------------------------------


def test_base_photo_album_abstract_members_raise() -> None:
    """Test base photo album abstract members raise."""
    album = _album(MagicMock(spec=PhotoLibrary))
    library = _library_stub()

    id_getter = BasePhotoAlbum.__dict__["id"].fget
    assert id_getter is not None
    fullname_getter = BasePhotoAlbum.__dict__["fullname"].fget
    assert fullname_getter is not None

    with pytest.raises(NotImplementedError):
        id_getter(album)
    with pytest.raises(NotImplementedError):
        fullname_getter(album)
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum.rename(album, "x")
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum.delete(album)
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum._get_payload(album, 0, 1, DirectionEnum.ASCENDING)
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum._get_photo_payload(album, "photo")
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum._get_url(album)
    with pytest.raises(NotImplementedError):
        BasePhotoAlbum._get_len(album)
    with pytest.raises(NotImplementedError):
        BasePhotoLibrary._get_albums(library)


def test_base_photo_album_get_photos_at_and_get_photo() -> None:
    """Test base photo album get photos at and get photo."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        json=MagicMock(return_value={"records": [_master_record(), _asset_record()]})
    )
    album = _album(_library_stub(service))

    photos = list(album._get_photos_at(0, DirectionEnum.ASCENDING, 10))

    assert [photo.id for photo in photos] == ["asset-1"]

    found = album._get_photo("asset-1")

    assert found.id == "asset-1"

    with pytest.raises(KeyError, match="Photo does not exist"):
        album._get_photo("missing")


def test_base_photo_album_photo_title_and_name_setter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test base photo album photo title and name setter."""
    album = _album(MagicMock(spec=PhotoLibrary))
    photo: Any = MagicMock(spec=PhotoAsset)
    photo.id = "photo-1"
    monkeypatch.setattr(album, "_get_photos_at", lambda *args: iter([photo]))

    assert album.photo(0) is photo
    assert album.title == "Album"

    rename = MagicMock()
    monkeypatch.setattr(album, "rename", rename)
    album.name = "Album"
    rename.assert_not_called()
    album.name = "Renamed"

    rename.assert_called_once_with("Renamed")


def test_base_photo_album_photos_iterates_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test base photo album photos iterates and dedupes."""
    album = _album(_library_stub(), page_size=2)
    first: Any = MagicMock(spec=PhotoAsset)
    first.id = "p1"
    second: Any = MagicMock(spec=PhotoAsset)
    second.id = "p2"
    third: Any = MagicMock(spec=PhotoAsset)
    third.id = "p3"
    pages: dict[int, list[Any]] = {0: [first, second], 2: [second, third], 4: []}

    def _page(offset: int, _direction: DirectionEnum, _page_size: int) -> Iterator[Any]:
        """Page."""
        return iter(pages.get(offset, []))

    monkeypatch.setattr(album, "_get_photos_at", _page)

    assert list(album.photos) == [first, second, third]


def test_base_photo_album_photos_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test base photo album photos descending."""
    album = _album(
        _library_stub(),
        direction=DirectionEnum.DESCENDING,
        page_size=2,
    )
    photo: Any = MagicMock(spec=PhotoAsset)
    photo.id = "photo-1"
    pages: dict[int, list[Any]] = {3: [photo]}
    monkeypatch.setattr(album, "_get_len", lambda: 4)
    monkeypatch.setattr(
        album,
        "_get_photos_at",
        lambda offset, direction, page_size: iter(pages.get(offset, [])),
    )

    assert list(album.photos) == [photo]


def test_base_photo_album_dunder_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test base photo album dunder helpers."""
    album = _album(MagicMock(spec=PhotoLibrary))
    photo: Any = MagicMock(spec=PhotoAsset)
    photo.id = "photo-1"

    assert album.name == "Album"
    assert str(album) == "Album"
    assert repr(album) == "<PhotoAlbum: 'Album'>"

    calls: list[int] = []

    def _get_len() -> int:
        """Get len."""
        calls.append(1)
        return 7

    monkeypatch.setattr(album, "_get_len", _get_len)
    assert len(album) == 7
    assert len(album) == 7
    assert len(calls) == 1

    iterator = iter(album)
    assert iterator is not None

    monkeypatch.setattr(album, "_get_photos_at", lambda *args: iter([photo]))
    assert album[0] is photo
    monkeypatch.setattr(album, "_get_photos_at", lambda *args: iter([photo]))
    assert album[-1] is photo

    monkeypatch.setattr(album, "_get_photos_at", lambda *args: iter([]))
    with pytest.raises(IndexError, match="Photo index out of range"):
        _ = album[0]

    monkeypatch.setattr(album, "_get_photo", lambda key: photo)
    assert album["photo-1"] is photo
    assert "photo-1" in album

    monkeypatch.setattr(album, "_get_photo", MagicMock(side_effect=KeyError("x")))
    assert album.get("missing") is None
    assert "missing" not in album
    with pytest.raises(KeyError, match="Photo does not exist"):
        _ = album["missing"]


# ---------------------------------------------------------------------------
# PhotoAlbum writes
# ---------------------------------------------------------------------------


def test_photo_album_fullname_without_parent() -> None:
    """Test photo album fullname without parent."""
    album = _album(MagicMock(spec=PhotoLibrary))

    assert album.fullname == "Album"


def test_photo_album_rename_same_name_is_noop() -> None:
    """Test photo album rename same name is noop."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    album = _album(library)

    album.rename("Album")

    library.service.session.post.assert_not_called()


def test_photo_album_rename_updates_metadata() -> None:
    """Test photo album rename updates metadata."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    library.service.session.post.return_value = MagicMock(
        json=MagicMock(
            return_value={
                "records": [
                    {
                        "recordName": "album-1",
                        "recordChangeTag": "new-tag",
                        "fields": {
                            "recordModificationDate": {"value": "2026-02-02T00:00:00Z"}
                        },
                    }
                ]
            }
        )
    )
    album = _album(library)

    album.rename("New Name")

    assert album.name == "New Name"
    assert album._record_change_tag == "new-tag"
    assert album._record_modification_date == "2026-02-02T00:00:00Z"


def test_photo_album_delete_success_updates_metadata() -> None:
    """Test photo album delete success updates metadata."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    library.service.session.post.return_value = MagicMock(
        json=MagicMock(
            return_value={
                "records": [
                    {
                        "recordName": "album-1",
                        "recordChangeTag": "deleted-tag",
                        "fields": {
                            "recordModificationDate": {"value": "2026-03-03T00:00:00Z"}
                        },
                    }
                ]
            }
        )
    )
    album = _album(library)

    assert album.delete() is True
    assert album._record_change_tag == "deleted-tag"
    assert album._record_modification_date == "2026-03-03T00:00:00Z"


def test_photo_album_delete_wraps_api_error() -> None:
    """Test photo album delete wraps api error."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    library.service.session.post.side_effect = PyiCloudAPIResponseException("boom")
    album = _album(library)

    with pytest.raises(PhotosServiceException, match="Failed to delete"):
        album.delete()


def test_photo_album_add_photo_returns_false_on_api_error() -> None:
    """Test photo album add photo returns false on api error."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    library.service.session.post.side_effect = PyiCloudAPIResponseException("boom")
    album = _album(library)
    photo: Any = MagicMock()
    photo.id = "asset-1"

    assert album.add_photo(photo) is False


def test_photo_album_add_photo_updates_matching_record() -> None:
    """Test photo album add photo updates matching record."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = _mock_service()
    library.service.session.post.return_value = MagicMock(
        json=MagicMock(
            return_value={
                "records": [
                    {
                        "recordName": "album-1",
                        "recordChangeTag": "relation-tag",
                        "fields": {
                            "recordModificationDate": {"value": "2026-04-04T00:00:00Z"}
                        },
                    }
                ]
            }
        )
    )
    album = _album(library)
    photo: Any = MagicMock()
    photo.id = "asset-1"

    assert album.add_photo(photo) is True
    assert album._record_change_tag == "relation-tag"
    assert album._record_modification_date == "2026-04-04T00:00:00Z"


def test_photo_album_upload_rejects_non_library() -> None:
    """Test photo album upload rejects non library."""
    album = _album(MagicMock())

    assert album.upload("/path") is None


def test_photo_album_upload_returns_none_when_asset_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test photo album upload returns none when asset missing."""
    library = _library_stub()
    album = _album(library)
    monkeypatch.setattr(library, "upload_file", MagicMock(return_value=None))

    assert album.upload("/path") is None


def test_photo_album_upload_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test photo album upload success."""
    library = _library_stub()
    photo: Any = MagicMock(spec=PhotoAsset)
    album = _album(library)
    monkeypatch.setattr(library, "upload_file", MagicMock(return_value=photo))
    monkeypatch.setattr(album, "add_photo", MagicMock(return_value=True))

    assert album.upload("/path") is photo


def test_photo_album_upload_raises_when_membership_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test photo album upload raises when membership fails."""
    library = _library_stub()
    photo: Any = MagicMock(spec=PhotoAsset)
    album = _album(library)
    monkeypatch.setattr(library, "upload_file", MagicMock(return_value=photo))
    monkeypatch.setattr(album, "add_photo", MagicMock(return_value=False))

    with pytest.raises(PhotosServiceException, match="Failed to add photo"):
        album.upload("/path")


# ---------------------------------------------------------------------------
# PhotoAlbum query helpers
# ---------------------------------------------------------------------------


def test_photo_album_query_helpers() -> None:
    """Test photo album query helpers."""
    album = _album(MagicMock())

    assert album._get_url() == QUERY_URL

    payload = album._get_payload(0, 10, DirectionEnum.ASCENDING)
    assert payload["resultsLimit"] == 10
    assert payload["query"]["recordType"] == ListTypeEnum.CONTAINER.value
    assert len(payload["query"]["filterBy"]) == 2

    photo_payload = album._get_photo_payload("asset-1")
    assert any(
        field["fieldName"] == "recordName"
        for field in photo_payload["query"]["filterBy"]
    )

    filtered = album._list_query_gen(
        0,
        ListTypeEnum.CONTAINER,
        DirectionEnum.DESCENDING,
        5,
        [{"fieldName": "containerId"}],
    )
    assert len(filtered["query"]["filterBy"]) == 3


def test_photo_album_folder_upload_returns_none() -> None:
    """Test photo album folder upload returns none."""
    folder = PhotoAlbumFolder(
        library=MagicMock(),
        name="Folder",
        record_id="folder-1",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        url=QUERY_URL,
    )

    assert folder.upload("/path") is None


def test_smart_photo_album_upload_fullname_and_container_id() -> None:
    """Test smart photo album upload fullname and container id."""
    album = SmartPhotoAlbum(
        library=MagicMock(spec=PhotoLibrary),
        name=SmartAlbumEnum.FAVORITES,
        obj_type=ObjectTypeEnum.FAVORITE,
        list_type=ListTypeEnum.SMART_ALBUM,
        direction=DirectionEnum.ASCENDING,
        url=QUERY_URL,
    )

    assert album.upload("/path") is None
    assert album.fullname == SmartAlbumEnum.FAVORITES.value
    assert album._get_container_id == ObjectTypeEnum.FAVORITE.value


# ---------------------------------------------------------------------------
# PhotoAsset
# ---------------------------------------------------------------------------


def test_photo_asset_basic_properties() -> None:
    """Test photo asset basic properties."""
    photo = PhotoAsset(_mock_service(), _master_record(), _asset_record())

    assert photo.id == "asset-1"
    assert photo.master_id == "master-1"
    assert photo.asset_id == "asset-1"
    assert photo.filename == "IMG_0001.JPG"
    assert photo.size == 12345
    assert photo.created == ASSET_EPOCH
    assert photo.asset_date == ASSET_EPOCH
    assert photo.added_date == ASSET_EPOCH
    assert photo.dimensions == (4032, 3024)
    assert photo.item_type == "image"
    assert photo.is_live_photo is False
    assert photo.download_url("original") == "https://cdn.local/original"
    assert photo.download_url("missing") is None
    assert repr(photo) == "<PhotoAsset: id=asset-1>"


def test_photo_asset_versions_include_available_prefixes() -> None:
    """Test photo asset versions include available prefixes."""
    photo = PhotoAsset(_mock_service(), _master_record(), _asset_record())

    versions = photo.versions

    assert set(versions) == {"original", "medium"}
    assert versions["original"] == {
        "width": 4032,
        "height": 3024,
        "size": 12345,
        "url": "https://cdn.local/original",
        "type": "public.jpeg",
        "filename": "IMG_0001.JPG",
    }
    assert photo.versions["medium"]["url"] == "https://cdn.local/med"


def test_photo_asset_version_missing_optional_fields() -> None:
    """Test photo asset version missing optional fields."""
    master = _master_record()
    for field_name in (
        "resOriginalWidth",
        "resOriginalHeight",
        "resOriginalFileType",
    ):
        del master["fields"][field_name]
    photo = PhotoAsset(_mock_service(), master, _asset_record())

    version = photo.versions["original"]

    assert version["width"] is None
    assert version["height"] is None
    assert version["type"] is None


def test_photo_asset_item_type_prefers_item_type_field() -> None:
    """Test photo asset item type prefers item type field."""
    master = _master_record(fields={"itemType": {"value": "public.heic"}})

    assert PhotoAsset(_mock_service(), master, _asset_record()).item_type == "image"


def test_photo_asset_item_type_falls_back_to_file_type() -> None:
    """Test photo asset item type falls back to file type."""
    master = _master_record()
    del master["fields"]["itemType"]

    assert PhotoAsset(_mock_service(), master, _asset_record()).item_type == "image"


def test_photo_asset_item_type_falls_back_to_extension() -> None:
    """Test photo asset item type falls back to extension."""
    master = _master_record(fields={"itemType": {}})
    master["fields"].pop("itemType")
    master["fields"]["filenameEnc"] = {"value": _b64("photo.PNG")}
    master["fields"].pop("resOriginalFileType", None)

    assert PhotoAsset(_mock_service(), master, _asset_record()).item_type == "image"


def test_photo_asset_item_type_defaults_to_movie() -> None:
    """Test photo asset item type defaults to movie."""
    master = _master_record(fields={"itemType": {}})
    master["fields"].pop("itemType")
    master["fields"]["filenameEnc"] = {"value": _b64("clip.mov")}
    master["fields"].pop("resOriginalFileType", None)

    assert PhotoAsset(_mock_service(), master, _asset_record()).item_type == "movie"


def test_photo_asset_live_photo_video_filename() -> None:
    """Test photo asset live photo video filename."""
    master = _master_record(
        fields={
            "resOriginalVidComplFileType": {"value": "com.apple.quicktime-movie"},
            "resOriginalFileType": {"value": "com.apple.quicktime-movie"},
        }
    )
    photo = PhotoAsset(_mock_service(), master, _asset_record())

    assert photo.is_live_photo is True
    assert photo.versions["original"]["filename"] == "IMG_0001.MOV"


def test_photo_asset_movie_versions_use_video_lookup() -> None:
    """Test photo asset movie versions use video lookup."""
    master = _master_record(
        fields={
            "itemType": {"value": "com.apple.quicktime-movie"},
            "filenameEnc": {"value": _b64("clip.MOV")},
            "resVidMedRes": {
                "value": {"size": 5, "downloadURL": "https://cdn.local/vid"}
            },
        }
    )
    photo = PhotoAsset(_mock_service(), master, _asset_record())

    assert photo.item_type == "movie"
    assert photo.versions["medium"]["url"] == "https://cdn.local/vid"


def test_photo_asset_version_missing_optional_fields_defaults() -> None:
    """Test photo asset version missing optional fields defaults."""
    master = _master_record(fields={"itemType": {}})
    master["fields"].pop("itemType")
    master["fields"].pop("resOriginalFileType", None)
    master["fields"].pop("resOriginalWidth", None)
    master["fields"].pop("resOriginalHeight", None)
    master["fields"]["filenameEnc"] = {"value": _b64("photo.jpg")}

    version = PhotoAsset(_mock_service(), master, _asset_record())._get_photo_version(
        "resOriginal"
    )

    assert version["width"] is None
    assert version["height"] is None
    assert version["type"] is None


def test_photo_asset_get_photo_version_missing_resource() -> None:
    """Test photo asset get photo version missing resource."""
    photo = PhotoAsset(_mock_service(), _master_record(), _asset_record())

    version = photo._get_photo_version("resSidecar")

    assert version["size"] is None
    assert version["url"] is None
    assert version["type"] is None


def test_photo_asset_download_returns_bytes() -> None:
    """Test photo asset download returns bytes."""
    service = _mock_service()
    service.session.get.return_value.raw.read.return_value = b"binary"
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.download("original") == b"binary"
    assert photo.download("missing") is None


def test_photo_asset_delete_non_200_returns_false() -> None:
    """Test photo asset delete non 200 returns false."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(status_code=500)
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.delete() is False


def test_photo_asset_delete_returns_false_for_payload_errors() -> None:
    """Test photo asset delete returns false for payload errors."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"errors": [{"reason": "nope"}]}),
    )
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.delete() is False


def test_photo_asset_delete_returns_record_flag() -> None:
    """Test photo asset delete returns record flag."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={"records": [{"fields": {"isDeleted": {"value": 1}}}]}
        ),
    )
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.delete() is True


def test_photo_asset_delete_returns_true_without_records() -> None:
    """Test photo asset delete returns true without records."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={}),
    )
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.delete() is True


@pytest.mark.parametrize(
    "records",
    [
        [],
        ["not-a-dict"],
        [{"fields": "not-a-dict"}],
        [{"fields": {}}],
        [{"fields": {"isDeleted": "not-a-dict"}}],
    ],
)
def test_photo_asset_delete_returns_true_for_unrecognized_records(
    records: list[Any],
) -> None:
    """Test photo asset delete returns true for unrecognized records."""
    service = _mock_service()
    service.session.post.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"records": records}),
    )
    photo = PhotoAsset(service, _master_record(), _asset_record())

    assert photo.delete() is True


def test_photo_asset_repr() -> None:
    """Test photo asset repr."""
    photo = PhotoAsset(_mock_service(), _master_record(), _asset_record())

    assert repr(photo) == "<PhotoAsset: id=asset-1>"


# ---------------------------------------------------------------------------
# PhotoStreamAsset
# ---------------------------------------------------------------------------


def test_photo_stream_asset_properties() -> None:
    """Test photo stream asset properties."""
    master = {
        "recordName": "master-1",
        "recordType": "CPLMaster",
        "fields": {
            "originalCreationDate": {"value": 1_700_000_000_000},
            "resOriginalFileSize": {"value": "2048"},
        },
    }
    asset = {
        "recordName": "asset-1",
        "recordType": "CPLAsset",
        "fields": {},
        "pluginFields": {
            "likeCount": {"value": 7},
            "likedByCaller": {"value": True},
        },
    }

    photo = PhotoStreamAsset(_mock_service(), master, asset)

    assert photo.like_count == 7
    assert photo.liked is True
    assert photo.asset_date == ASSET_EPOCH
    assert photo.size == 2048


def test_photo_stream_asset_defaults() -> None:
    """Test photo stream asset defaults."""
    master = {"recordName": "master-1", "fields": {}}
    asset = {"recordName": "asset-1", "fields": {}}

    photo = PhotoStreamAsset(_mock_service(), master, asset)

    assert photo.like_count == 0
    assert photo.liked is False
    assert photo.asset_date == EPOCH
    assert photo.size == 0


def test_photo_stream_asset_size_handles_bad_value() -> None:
    """Test photo stream asset size handles bad value."""
    master = {
        "recordName": "master-1",
        "fields": {"resOriginalFileSize": {"value": "x"}},
    }
    asset = {"recordName": "asset-1", "fields": {}}

    assert PhotoStreamAsset(_mock_service(), master, asset).size == 0
