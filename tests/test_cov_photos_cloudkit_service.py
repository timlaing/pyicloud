"""Extra branch coverage for the modern Photos CloudKit service (issue #394).

These tests only exercise branches the existing photos suites leave untouched.
They deliberately drive small, self-contained seams -- abstract bodies,
fallback paths, and delegation shims -- rather than full request flows.
"""

from __future__ import annotations

# The entire module exercises private seams by design.
# pylint: disable=protected-access
import base64
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import (
    CKQueryResponse,
    CKRecord,
    CKTombstoneRecord,
    CKZoneID,
)
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.photos_cloudkit.constants import (
    PRIMARY_ZONE,
    AlbumTypeEnum,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
    SmartAlbumEnum,
)
from pyicloud.services.photos_cloudkit.models import (
    PhotoResource,
    PhotosServiceException,
)
from pyicloud.services.photos_cloudkit.service import (
    AlbumContainer,
    BasePhotoAlbum,
    BasePhotoLibrary,
    PhotoAlbum,
    PhotoAlbumFolder,
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
)
from pyicloud.services.photos_cloudkit.sync import PhotoSyncOptions


def _ck_record(
    record_type: str,
    record_name: str,
    fields: dict[str, Any] | None = None,
    **extra: Any,
) -> CKRecord:
    """Build a typed CloudKit record from a raw dict."""

    return CKRecord.model_validate({
        "recordName": record_name,
        "recordType": record_type,
        "fields": fields or {},
        **extra,
    })


def _b64(text: str) -> str:
    """Return the base64 representation used by encrypted text fields."""

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _set(obj: Any, name: str, value: Any) -> None:
    """Assign an attribute without tripping ruff's constant-setattr rule."""

    setattr(obj, name, value)


def _raw_library(
    *,
    client: Any = None,
    token: str | None = None,
    session: Any = None,
    zone_id: dict[str, str] | None = None,
) -> Any:
    """Build a ``PhotoLibrary`` without running its querying constructor."""

    library: Any = PhotoLibrary.__new__(PhotoLibrary)
    library.service = SimpleNamespace(
        service_endpoint="https://example.com",
        params={"dsid": "12345"},
        session=session if session is not None else object(),
    )
    library._client = client
    library._current_sync_token = token
    library.zone_id = dict(zone_id or PRIMARY_ZONE)
    library._albums = None
    library._pending_albums = {}
    library._indexing_state = "FINISHED"
    library.url = "https://example.com/records/query?dsid=12345"
    library.scope = "private"
    library.asset_type = PhotoAsset
    return library


def _bare_service(*, session: Any = None) -> Any:
    """Build a ``PhotosService`` skeleton for delegation/discovery tests."""

    service: Any = PhotosService.__new__(PhotosService)
    service.service_endpoint = "https://example.com"
    service._BaseService__params = {"dsid": "12345"}
    service._BaseService__session = session if session is not None else object()
    service._private_client = MagicMock()
    service._shared_client = MagicMock()
    service._root_library = SimpleNamespace(current_sync_token=None)
    service._shared_library = None
    service._libraries = None
    service._upload_hydration_timeout = 1.0
    service._upload_hydration_interval = 0.1
    return service


def _asset(
    master_fields: dict[str, Any] | None = None,
    asset_fields: dict[str, Any] | None = None,
    *,
    service: Any = None,
    library: Any = None,
) -> PhotoAsset:
    """Build a ``PhotoAsset`` from separate master/asset field maps."""

    if service is None:
        service = SimpleNamespace(
            service_endpoint="https://example.com",
            params={"dsid": "12345"},
            session=object(),
        )
    master = _ck_record("CPLMaster", "master-1", master_fields)
    asset_record = _ck_record("CPLAsset", "asset-1", asset_fields)
    return PhotoAsset(service, master, asset_record, library=library)


def _asset_from_dict(asset_fields: dict[str, Any]) -> PhotoAsset:
    """Build an asset whose backing record is a plain, unvalidated dict."""

    service = SimpleNamespace(
        service_endpoint="https://example.com",
        params={"dsid": "12345"},
        session=object(),
    )
    master = _ck_record("CPLMaster", "master-1", {})
    record: dict[str, Any] = {
        "recordName": "asset-1",
        "recordType": "CPLAsset",
        "fields": asset_fields,
    }
    return PhotoAsset(cast(Any, service), master, cast(Any, record))


def _typed_zone(
    name: str,
    *,
    deleted: bool = False,
    sync_token: str | None = None,
) -> Any:
    """Build a zone object shaped like the typed client response."""

    return SimpleNamespace(
        deleted=deleted,
        syncToken=sync_token,
        zoneID=CKZoneID(zoneName=name),
    )


class _PagedAlbum(BasePhotoAlbum):
    """Album whose page lookups come from a fixed offset->photos mapping."""

    def __init__(
        self,
        library: Any,
        pages: dict[int, list[Any]],
        *,
        list_type: ListTypeEnum,
        direction: DirectionEnum,
        page_size: int = 100,
    ) -> None:
        super().__init__(
            library=library,
            name="Paged",
            list_type=list_type,
            direction=direction,
            page_size=page_size,
        )
        self._pages = pages

    @property
    def fullname(self) -> str:
        """Return the album full name."""

        return "Paged"

    @property
    def id(self) -> str:
        """Return the album identifier."""

        return "paged"

    def _get_len(self) -> int:
        """Return a stable length for offset calculations."""

        return 0

    def _get_payload(
        self, offset: int, page_size: int, direction: DirectionEnum
    ) -> dict[str, Any]:
        """Return an empty legacy payload."""

        return {}

    def _get_url(self) -> str:
        """Return the query URL."""

        return "https://example.com/query"

    def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
        """Return an empty lookup payload."""

        return {}

    def _get_photos_at(
        self, index: int, direction: DirectionEnum, page_size: int
    ) -> Generator[PhotoAsset]:
        """Yield the configured page for the given offset."""

        yield from self._pages.get(index, [])


# ---------------------------------------------------------------------------
# AlbumContainer
# ---------------------------------------------------------------------------


def test_album_container_remove_drops_album(mock_photo_album: BasePhotoAlbum) -> None:
    """Removing an album also rebuilds the positional index."""

    container = AlbumContainer([mock_photo_album])
    container.remove(mock_photo_album.id)
    assert len(container) == 0


# ---------------------------------------------------------------------------
# BasePhotoLibrary
# ---------------------------------------------------------------------------


def test_base_photo_library_builds_typed_client() -> None:
    """A non-mock service with an endpoint constructs a typed client."""

    class Library(BasePhotoLibrary):
        """Concrete library used to reach the typed-client branch."""

        def _get_albums(self) -> AlbumContainer:
            """Return no albums."""

            return AlbumContainer()

        def _ensure_indexing_ready(self) -> None:
            """Skip the indexing handshake."""

    service = cast(
        Any,
        SimpleNamespace(
            service_endpoint="https://example.com",
            params={"dsid": "12345"},
            session=object(),
        ),
    )
    library = Library(service)
    assert library._client is not None


def test_ensure_indexing_ready_falls_back_to_finished(
    mock_photos_service: Any,
    mock_photo_library: BasePhotoLibrary,
) -> None:
    """A legacy response without records still resolves to FINISHED."""

    mock_photos_service.session.post.return_value.json.return_value = {}
    mock_photo_library._indexing_state = None
    mock_photo_library._ensure_indexing_ready()
    assert mock_photo_library.indexing_state == "FINISHED"


def test_refresh_and_cache_albums(
    mock_photo_library: BasePhotoLibrary,
    mock_photo_album: BasePhotoAlbum,
) -> None:
    """Album refresh, caching, and removal keep the container in sync."""

    _set(mock_photo_library, "_get_albums", MagicMock(return_value=AlbumContainer()))
    refreshed = mock_photo_library.refresh_albums()
    assert isinstance(refreshed, AlbumContainer)

    mock_photo_library._albums = AlbumContainer()
    mock_photo_library._cache_created_album(cast(PhotoAlbum, mock_photo_album))
    assert mock_photo_library._albums.get(mock_photo_album.id) is mock_photo_album

    mock_photo_library.remove_cached_album(mock_photo_album.id)
    assert mock_photo_library._albums.get(mock_photo_album.id) is None


def test_get_albums_abstract_body(mock_photo_library: BasePhotoLibrary) -> None:
    """The base album getter is abstract and raises when called directly."""

    with pytest.raises(NotImplementedError):
        BasePhotoLibrary._get_albums(mock_photo_library)


# ---------------------------------------------------------------------------
# BasePhotoAlbum seams
# ---------------------------------------------------------------------------


def test_album_name_setter_and_abstract_operations(
    mock_photo_album: BasePhotoAlbum,
) -> None:
    """The name setter only renames on change; base rename/delete raise."""

    mock_photo_album.name = "Test Album"
    with pytest.raises(NotImplementedError):
        mock_photo_album.name = "Other"
    with pytest.raises(NotImplementedError):
        mock_photo_album.delete()


def test_abstract_property_bodies_raise(mock_photo_album: BasePhotoAlbum) -> None:
    """Calling the concrete slots of abstract properties raises."""

    fullname_get = cast(Any, BasePhotoAlbum.fullname).fget
    assert fullname_get is not None
    with pytest.raises(NotImplementedError):
        fullname_get(mock_photo_album)

    album_id_get = cast(Any, BasePhotoAlbum.id).fget
    assert album_id_get is not None
    with pytest.raises(NotImplementedError):
        album_id_get(mock_photo_album)

    with pytest.raises(NotImplementedError):
        BasePhotoAlbum._get_len(mock_photo_album)


def test_base_query_filters_returns_empty(mock_photo_album: BasePhotoAlbum) -> None:
    """The default query filters are empty regardless of paging."""

    assert (
        mock_photo_album._query_filters(offset=0, direction=DirectionEnum.ASCENDING)
        == []
    )


def test_base_get_photo_payload_builds_query(
    mock_photo_album: BasePhotoAlbum,
) -> None:
    """The base lookup payload includes the record-name filter."""

    _set(mock_photo_album, "_zone_id", PRIMARY_ZONE)
    payload = BasePhotoAlbum._get_photo_payload(mock_photo_album, "photo-1")
    assert payload


def test_base_get_url_uses_endpoint_and_raises_without(
    mock_photo_album: BasePhotoAlbum,
) -> None:
    """The base URL prefers the endpoint and raises when it is absent."""

    endpoint_service = SimpleNamespace(
        service_endpoint="https://example.com", params={"dsid": "12345"}
    )
    _set(mock_photo_album, "_library", SimpleNamespace(service=endpoint_service))
    assert BasePhotoAlbum._get_url(mock_photo_album).startswith("https://")

    _set(mock_photo_album, "_library", SimpleNamespace(service=SimpleNamespace()))
    with pytest.raises(AttributeError):
        BasePhotoAlbum._get_url(mock_photo_album)


def test_iter_added_desc_deduplicates_and_stops(
    mock_photo_library: BasePhotoLibrary,
) -> None:
    """The recently-added pager deduplicates and stops on an empty window."""

    first = SimpleNamespace(id="a")
    second = SimpleNamespace(id="b")
    album = _PagedAlbum(
        mock_photo_library,
        {1: [first, first, second], 3: []},
        list_type=ListTypeEnum.ADDED,
        direction=DirectionEnum.DESCENDING,
        page_size=2,
    )
    assert [photo.id for photo in album._iter_added_desc_photos()] == ["b", "a"]


def test_photos_paging_ascending_and_descending(
    mock_photo_library: BasePhotoLibrary,
) -> None:
    """Photos paging advances offsets in both directions and dedupes."""

    photo = SimpleNamespace(id="a")
    ascending = _PagedAlbum(
        mock_photo_library,
        {0: [photo, photo], 2: []},
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        page_size=2,
    )
    assert [item.id for item in ascending.photos] == ["a"]

    descending = _PagedAlbum(
        mock_photo_library,
        {-1: [photo], -2: []},
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.DESCENDING,
        page_size=2,
    )
    assert [item.id for item in descending.photos] == ["a"]


def test_photo_by_position(mock_photo_library: BasePhotoLibrary) -> None:
    """Positional access returns the first entry of the requested page."""

    photo = cast(PhotoAsset, SimpleNamespace(id="a"))
    album = _PagedAlbum(
        mock_photo_library,
        {0: [photo]},
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
    )
    assert album.photo(0) is photo


def test_parse_asset_response_skips_unknown_types(
    mock_photo_library: BasePhotoLibrary,
) -> None:
    """Records that are neither assets nor masters are ignored."""

    response: dict[str, list[dict[str, Any]]] = {
        "records": [
            {
                "recordType": "CPLAsset",
                "fields": {"masterRef": {"value": {"recordName": "m1"}}},
            },
            {"recordType": "Unknown"},
        ]
    }
    assets, masters = mock_photo_library.parse_asset_response(response)
    assert "m1" in assets
    assert masters == []


# ---------------------------------------------------------------------------
# PhotoLibrary helpers
# ---------------------------------------------------------------------------


def test_recently_added_builds_virtual_album() -> None:
    """Recently Added returns a descending virtual album."""

    album = _raw_library().recently_added()
    assert isinstance(album, PhotoAlbum)
    assert album.name == "Recently Added"


def test_convert_record_to_album_folder_descending() -> None:
    """A folder record maps to a descending folder album."""

    record = _ck_record(
        "CPLAlbum",
        "folder-1",
        {
            "albumNameEnc": {"type": "STRING", "value": _b64("Folder")},
            "albumType": {"type": "INT64", "value": AlbumTypeEnum.FOLDER.value},
            "sortAscending": {"type": "INT64", "value": 0},
        },
    )
    album = _raw_library()._convert_record_to_album(record)
    assert isinstance(album, PhotoAlbumFolder)


def test_fetch_album_records_legacy_recurses_into_folders() -> None:
    """A legacy folder record triggers a nested fetch."""

    folder = {
        "recordName": "folder-1",
        "fields": {"albumType": {"value": AlbumTypeEnum.FOLDER.value}},
    }
    session = MagicMock()
    session.post.side_effect = [
        MagicMock(json=MagicMock(return_value={"records": [folder]})),
        MagicMock(json=MagicMock(return_value={"records": []})),
    ]
    library = _raw_library(client=None, session=session)
    records = library._fetch_album_records()
    assert records == [folder]


def test_fetch_album_records_typed_pages_and_nests() -> None:
    """The typed fetch pages, skips non-records, and expands folders."""

    client = MagicMock()
    leaf = _ck_record("CPLAlbum", "leaf-1", {})
    folder = _ck_record(
        "CPLAlbum",
        "folder-1",
        {"albumType": {"type": "INT64", "value": AlbumTypeEnum.FOLDER.value}},
    )
    tombstone = CKTombstoneRecord(recordName="gone", deleted=True)
    client.query.side_effect = [
        CKQueryResponse(
            records=[leaf, tombstone], continuationMarker="next", syncToken="s1"
        ),
        CKQueryResponse(records=[folder], continuationMarker=None),
        CKQueryResponse(records=[], continuationMarker=None),
    ]
    library = _raw_library(client=client, session=object())
    records = library._fetch_album_records()
    assert leaf in records
    assert folder in records


def test_sync_cursor_returns_existing_token() -> None:
    """A cached sync token short-circuits discovery."""

    assert _raw_library(token="existing").sync_cursor() == "existing"


def test_sync_cursor_typed_match() -> None:
    """The typed zone list resolves a matching zone's token."""

    client = MagicMock()
    client.zones_list.return_value = SimpleNamespace(
        zones=[
            _typed_zone("Other"),
            _typed_zone(PRIMARY_ZONE["zoneName"], sync_token="typed"),
        ]
    )
    assert _raw_library(client=client, session=object()).sync_cursor() == "typed"


def test_sync_cursor_typed_missing_token() -> None:
    """No matching typed zone raises a photos service error."""

    client = MagicMock()
    client.zones_list.return_value = SimpleNamespace(zones=[])
    library = _raw_library(client=client, session=object())
    with pytest.raises(PhotosServiceException):
        library.sync_cursor()


def test_sync_cursor_legacy_match_and_missing() -> None:
    """The legacy zone list resolves tokens and raises when empty."""

    session = MagicMock()
    session.post.return_value.json.return_value = {
        "zones": [
            {"zoneID": {"zoneName": "Other"}},
            {"zoneID": {"zoneName": "PrimarySync"}, "syncToken": "legacy"},
        ]
    }
    assert _raw_library(client=None, session=session).sync_cursor() == "legacy"

    empty_session = MagicMock()
    empty_session.post.return_value.json.return_value = {"zones": []}
    library = _raw_library(client=None, session=empty_session)
    with pytest.raises(PhotosServiceException):
        library.sync_cursor()


def test_iter_changes_skips_non_records() -> None:
    """Change events skip objects that are neither tombstones nor records."""

    client = MagicMock()
    tombstone = CKTombstoneRecord(recordName="gone", deleted=True)
    updated = _ck_record("CPLAsset", "a1", {})
    client.iter_changes.return_value = [
        SimpleNamespace(syncToken="s2", records=[tombstone, object(), updated])
    ]
    events = list(_raw_library(client=client, session=object()).iter_changes())
    assert [event.kind for event in events] == ["deleted", "updated"]


# ---------------------------------------------------------------------------
# PhotoAsset
# ---------------------------------------------------------------------------


def test_asset_ids_and_dates() -> None:
    """Identifiers and datetime-typed dates are returned directly."""

    asset_date = datetime(2026, 4, 21, tzinfo=timezone.utc)
    added_date = datetime(2026, 4, 22, tzinfo=timezone.utc)
    asset = _asset_from_dict({
        "assetDate": {"value": asset_date},
        "addedDate": {"value": added_date},
    })
    assert asset.id == "asset-1"
    assert asset.master_id == "master-1"
    assert asset.asset_id == "asset-1"
    assert asset.asset_date == asset_date
    assert asset.added_date == added_date


def test_asset_dates_fall_back_to_epoch() -> None:
    """Missing dates fall back to the epoch."""

    asset = _asset()
    assert asset.asset_date == datetime.fromtimestamp(0, timezone.utc)
    assert asset.added_date == datetime.fromtimestamp(0, timezone.utc)


def test_asset_size_non_dict_token() -> None:
    """A non-dict token has no size attribute and yields None."""

    asset = _asset({"resOriginalRes": {"type": "STRING", "value": "not-a-dict"}})
    assert asset.size is None


def test_asset_item_type_raw_and_extension() -> None:
    """Raw UTI strings and raw extensions both report an image."""

    raw_uti = _asset(
        {
            "resOriginalFileType": {
                "type": "STRING",
                "value": "com.example.raw-image",
            }
        },
    )
    assert raw_uti.item_type == "image"

    raw_extension = _asset(
        {"filenameEnc": {"type": "STRING", "value": _b64("photo.dng")}},
    )
    assert raw_extension.item_type == "image"


def test_asset_download_uses_typed_private_client() -> None:
    """A typed service downloads through the private client."""

    private_client = SimpleNamespace(
        download_asset_bytes=lambda url: b"payload",
    )
    service = SimpleNamespace(private_client=private_client, session=object())
    asset = _asset(service=service)
    asset._resources = {
        "original": PhotoResource(
            key="original",
            filename="photo.jpg",
            url="https://cdn.example.com/photo.jpg",
            size=1,
            type="image/jpeg",
        )
    }
    assert asset.download() == b"payload"


def test_replace_asset_record_skips_mismatched_ckrecord() -> None:
    """A non-matching typed record is skipped and no fallback is requested."""

    asset = _asset()
    other = _ck_record("CPLAsset", "other-1", {})
    assert asset._replace_asset_record([other]) is False


def test_replace_asset_record_updates_dict_backing() -> None:
    """A dict-backed asset is mutated, preserving an existing type."""

    asset = _asset()
    record: dict[str, Any] = {
        "recordName": "asset-1",
        "recordType": "CPLAsset",
        "fields": {},
    }
    _set(asset, "_asset_record", record)
    result = asset._replace_asset_record(
        [], fallback_field="isFavorite", fallback_value=1
    )
    assert result is False
    assert record["fields"]["isFavorite"] == {"value": 1}

    typed_record: dict[str, Any] = {
        "recordName": "asset-1",
        "recordType": "CPLAsset",
        "fields": {"isFavorite": {"type": "INT64"}},
    }
    _set(asset, "_asset_record", typed_record)
    asset._replace_asset_record([], fallback_field="isFavorite", fallback_value=1)
    assert typed_record["fields"]["isFavorite"] == {"type": "INT64", "value": 1}


def test_refresh_from_library_handles_failures() -> None:
    """Library refreshes return False on errors or missing assets."""

    asset = _asset()
    failing: Any = MagicMock()
    failing.all.get.side_effect = RuntimeError("boom")  # pylint: disable=no-member
    _set(asset, "_library", failing)
    assert asset._refresh_from_library() is False

    empty: Any = MagicMock()
    empty.all.get.return_value = None  # pylint: disable=no-member
    _set(asset, "_library", empty)
    assert asset._refresh_from_library() is False


def test_record_errors_reads_dict_records() -> None:
    """Dict records carrying server error codes are formatted."""

    records: list[Any] = [
        {"recordName": "r1", "serverErrorCode": "X", "reason": "bad"},
        {"recordName": "r2", "serverErrorCode": "Y"},
        {"other": 1},
        object(),
    ]
    assert PhotoAsset._record_errors(records) == [
        "r1: X (bad)",
        "r2: Y (no reason provided)",
    ]


def test_set_favorite_raises_when_state_not_applied() -> None:
    """A server response that keeps the old favorite state raises."""

    service = MagicMock()
    service.service_endpoint = "https://example.com"
    service.params = {"dsid": "12345"}
    service.session.post.return_value.json.return_value = {
        "records": [
            {
                "recordName": "asset-1",
                "recordType": "CPLAsset",
                "fields": {"isFavorite": {"value": 0}},
            }
        ]
    }
    asset = _asset({}, {"isFavorite": {"type": "INT64", "value": 0}}, service=service)
    with pytest.raises(PhotosServiceException):
        asset.set_favorite(True)


# ---------------------------------------------------------------------------
# PhotoAlbum upload seams
# ---------------------------------------------------------------------------


def _album_library() -> MagicMock:
    """Return a PhotoLibrary spec mock with a usable service namespace."""

    library = MagicMock(spec=PhotoLibrary)
    library.service = SimpleNamespace(
        service_endpoint="https://example.com",
        params={"dsid": "12345"},
    )
    return library


def test_photo_album_upload_add_photo_failure() -> None:
    """A failed membership add raises a photos service error."""

    library = _album_library()
    library.upload_file.return_value = _asset()
    album = PhotoAlbum(
        library=library,
        name="Album",
        record_id="album-1",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
    )
    _set(album, "add_photo", MagicMock(return_value=False))
    with pytest.raises(PhotosServiceException):
        album.upload("photo.jpg")


def test_relation_item_id_falls_back_to_id() -> None:
    """A photo without a usable asset id falls back to its id."""

    photo = cast(PhotoAsset, SimpleNamespace(id="photo-1"))
    assert PhotoAlbum._relation_item_id(photo) == "photo-1"


def test_album_folder_upload_returns_none() -> None:
    """Folders never accept uploads."""

    folder = PhotoAlbumFolder(
        library=_album_library(),
        name="Folder",
        record_id="folder-1",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
    )
    assert folder.upload("photo.jpg") is None


# ---------------------------------------------------------------------------
# PhotosService
# ---------------------------------------------------------------------------


def test_discover_typed_zones() -> None:
    """Typed discovery covers deleted, primary, custom, and shared zones."""

    service = _bare_service()
    service._private_client.zones_list.return_value = SimpleNamespace(
        zones=[
            _typed_zone("PrimarySync", sync_token="root-token"),
            _typed_zone("Dead", deleted=True),
            _typed_zone("CustomZone"),
            _typed_zone("SharedSync-A"),
        ]
    )
    service._shared_client.zones_list.return_value = SimpleNamespace(
        zones=[
            _typed_zone("DeadShared", deleted=True),
            _typed_zone("SharedSync-A"),
            _typed_zone("SharedSync-B"),
        ]
    )

    libraries: dict[str, Any] = {}
    with patch.object(BasePhotoLibrary, "_ensure_indexing_ready"):
        service._discover_typed_zones(libraries)

    assert service._root_library.current_sync_token == "root-token"
    assert "CustomZone" in libraries
    assert "shared:SharedSync-A" in libraries
    assert "shared:SharedSync-B" in libraries


def test_discover_typed_zones_ignores_shared_failure() -> None:
    """A failing shared-zone listing is logged, not raised."""

    service = _bare_service()
    service._private_client.zones_list.return_value = SimpleNamespace(zones=[])
    service._shared_client.zones_list.side_effect = PyiCloudException("nope")
    libraries: dict[str, Any] = {}
    with patch.object(BasePhotoLibrary, "_ensure_indexing_ready"):
        service._discover_typed_zones(libraries)
    assert not libraries


def test_discover_legacy_zones_skips_deleted() -> None:
    """Legacy zone discovery skips tombstones and adds custom zones."""

    session = MagicMock()
    session.post.return_value.json.return_value = {
        "zones": [
            {"deleted": True},
            {"zoneID": {"zoneName": "CustomZone"}},
        ]
    }
    service = _bare_service(session=session)
    libraries: dict[str, Any] = {}
    with patch.object(BasePhotoLibrary, "_ensure_indexing_ready"):
        service._discover_legacy_zones(libraries)
    assert "CustomZone" in libraries


def test_photos_service_delegates_simple_calls() -> None:
    """The service proxies album, client, and cursor lookups to the library."""

    service = _bare_service()
    root = MagicMock()
    sentinel = object()
    root.all = sentinel
    root.albums = sentinel
    service._root_library = root

    assert service.all is sentinel
    assert service.albums is sentinel
    assert service.private_client is service._private_client
    assert service.create_album("X") is root.create_album.return_value

    root.sync_cursor.return_value = "cursor"
    assert service.sync_cursor() == "cursor"


def test_shared_streams_returns_container() -> None:
    """Shared streams wrap the legacy library albums in a container."""

    service = _bare_service()
    album = SimpleNamespace(id="a", fullname="A", name="A")
    service._shared_library = SimpleNamespace(albums=[album])
    container = service.shared_streams
    assert isinstance(container, AlbumContainer)
    assert len(container) == 1


def test_photos_service_iter_changes_delegates() -> None:
    """Change events are yielded from the root library."""

    service = _bare_service()
    root = MagicMock()
    events = [object()]
    root.iter_changes.return_value = iter(events)
    service._root_library = root
    assert list(service.iter_changes()) == events


def test_photos_service_sync_delegates() -> None:
    """Sync delegates to the standalone sync runner."""

    service = _bare_service()
    options = PhotoSyncOptions(directory=Path("/tmp/python-test-results/out"))
    with patch("pyicloud.services.photos_cloudkit.service.run_photo_sync") as run:
        run.return_value = "result"
        assert service.sync(options) == "result"


def test_photos_service_watch_delegates() -> None:
    """Watch delegates to the standalone watch runner."""

    service = _bare_service()
    options = PhotoSyncOptions(directory=Path("/tmp/python-test-results/out"))
    with patch("pyicloud.services.photos_cloudkit.service.watch_photo_sync") as watch:
        watch.return_value = iter(["event"])
        assert list(service.watch(options, interval_seconds=5)) == ["event"]


def test_upload_into_album_paths() -> None:
    """Uploads skip misses, the All Photos album, and add otherwise."""

    service = _bare_service()
    root = MagicMock()
    service._root_library = root

    root.upload_file.return_value = None
    assert service._upload_into_album(MagicMock(), "photo.jpg") is None

    photo = object()
    root.upload_file.return_value = photo
    all_photos = MagicMock()
    all_photos.id = SmartAlbumEnum.ALL_PHOTOS.value
    assert service._upload_into_album(all_photos, "photo.jpg") is photo
    all_photos.add_photo.assert_not_called()

    album = MagicMock()
    album.id = "album-1"
    assert service._upload_into_album(album, "photo.jpg") is photo
    album.add_photo.assert_called_once_with(photo)
