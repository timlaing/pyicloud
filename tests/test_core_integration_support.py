"""Standalone core metadata/correlation regressions, with no MCP imports."""

# Synthetic setup intentionally inspects core metadata and query processing.
# pylint: disable=protected-access

import base64
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord, CKZoneListResponse
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.drive import DriveService
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.constants import (
    AlbumTypeEnum,
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
)
from pyicloud.services.photos.exceptions import PhotosServiceException
from pyicloud.services.photos.queries import parent_filter
from pyicloud.services.photos.service import (
    AlbumContainer,
    PhotoAlbum,
    PhotoAlbumFolder,
    PhotoAlbumRecordState,
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    private_photo_zone,
)
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


def objects() -> tuple[Any, PhotoLibrary, PhotoAlbum, PhotoAsset]:
    """Typed fake private core objects; no protocol server or transport."""
    service = MagicMock()
    service.session = object()
    service.params = {"dsid": "123"}
    service.service_endpoint = "https://photos.example.test"
    client = MagicMock(spec=PhotosCloudKitClient)
    library = PhotoLibrary(
        service, client=client, zone_id={"zoneName": "Z"}, check_indexing=False
    )
    album = PhotoAlbum(
        library,
        name="Synthetic",
        record_id="A",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        zone_id=library.zone_id,
        query_filters=[parent_filter("A")],
        record=PhotoAlbumRecordState(record_change_tag="R1"),
    )
    master = CKRecord.model_validate({
        "recordName": "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    record = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "zoneID": library.zone_id,
        "fields": {},
    })
    return client, library, album, PhotoAsset(service, master, record)


def test_route_and_album_metadata_are_read_only() -> None:
    """Integration reads cannot redirect mutation routes or fabricate revisions."""
    drive = DriveService("https://drive.test", "https://docs.test", MagicMock(), {})
    assert drive.document_root == "https://docs.test"
    with pytest.raises(AttributeError):
        object.__setattr__(drive, "document_root", "https://other.test")
    client, _library, album, _photo = objects()
    assert album.record_change_tag == "R1"
    assert album.parent_id is None
    assert album.object_type == ObjectTypeEnum.CONTAINER
    zone = album.zone_id
    zone["zoneName"] = "OTHER"
    assert album.zone_id == {"zoneName": "Z"}
    with pytest.raises(AttributeError):
        object.__setattr__(album, "record_change_tag", "OTHER")
    client.query.assert_not_called()


def test_album_folder_cannot_accept_photo_relations() -> None:
    """A top-level folder is not a photo container even with container metadata."""
    client, library, _album, photo = objects()
    folder = PhotoAlbumFolder(
        library,
        name="Folder",
        record_id="F",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.CONTAINER,
        direction=DirectionEnum.ASCENDING,
        zone_id=library.zone_id,
        query_filters=[parent_filter("F")],
        record=PhotoAlbumRecordState(record_change_tag="R1"),
    )
    assert folder.parent_id is None
    assert not folder.can_add_photo_correlated(photo)
    with pytest.raises(PhotosServiceException, match="unavailable"):
        folder.contains_photo_scoped(photo)
    with pytest.raises(PhotosServiceException, match="unavailable"):
        folder.add_photo_correlated(photo)
    client.query.assert_not_called()
    client.modify.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "equivalent",
        "integer",
        "foreign",
        "endpoint",
        "missing",
        "blank",
        "boolean",
        "zero",
        "params",
        "route_missing",
    ],
)
def test_photo_relation_provenance_is_account_and_endpoint_bound(case: str) -> None:
    """Passive guards reject foreign services but allow equivalent same-account ones."""
    client, _library, album, photo = objects()
    origin = MagicMock()
    origin.params = {"dsid": "123"}
    origin.service_endpoint = album.service.service_endpoint
    if case == "integer":
        origin.params["dsid"] = 123
    elif case == "foreign":
        origin.params["dsid"] = "456"
    elif case == "endpoint":
        origin.service_endpoint = "https://other.test"
    elif case == "missing":
        origin.params = {}
    elif case == "blank":
        origin.params["dsid"] = " "
    elif case == "boolean":
        origin.params["dsid"] = True
    elif case == "zero":
        origin.params["dsid"] = 0
    elif case == "params":
        origin.params = None
    elif case == "route_missing":
        cast(Any, album.service).service_endpoint = None
    candidate = PhotoAsset(origin, photo.master_record, photo.asset_record)
    allowed = case in {"equivalent", "integer"}
    assert album.can_add_photo_correlated(candidate) is allowed
    if not allowed:
        with pytest.raises(PhotosServiceException, match="unavailable"):
            album.contains_photo_scoped(candidate)
        with pytest.raises(PhotosServiceException, match="unavailable"):
            album.add_photo_correlated(candidate)
    client.query.assert_not_called()
    client.modify.assert_not_called()


@pytest.mark.parametrize(
    "case", ["exact", "wrong_id", "wrong_name", "wrong_type", "missing_tag", "multiple"]
)
def test_typed_album_creation_requires_exact_acknowledgement(case: str) -> None:
    """Existing creation payload is preserved; an unrelated response is uncertain."""
    client, library, _album, _photo = objects()

    def response(**kwargs: Any) -> CKModifyResponse:
        sent = kwargs["operations"][0].record
        data = sent.model_dump(mode="json")
        data["recordChangeTag"] = None if case == "missing_tag" else "R1"
        if case == "wrong_id":
            data["recordName"] = "OTHER"
        if case == "wrong_type":
            data["recordType"] = "OTHER"
        if case == "wrong_name":
            data["fields"]["albumNameEnc"]["value"] = base64.b64encode(
                b"OTHER"
            ).decode()
        record = CKRecord.model_validate(data)
        return CKModifyResponse(
            records=[record, record] if case == "multiple" else [record]
        )

    client.modify.side_effect = response
    if case == "exact":
        created = library.create_album_correlated("New")
        assert created is not None
        assert created.name == "New"
        assert isinstance(created, PhotoAlbum)
        assert not isinstance(created, PhotoAlbumFolder)
        submitted = client.modify.call_args.kwargs["operations"][0].record
        assert submitted.fields.get_value("albumType") == AlbumTypeEnum.ALBUM.value
        with patch.object(library, "_get_albums", return_value=AlbumContainer()):
            assert not library.server_albums()
    else:
        with pytest.raises(PhotosServiceException, match="uncertain"):
            library.create_album_correlated("New")
    client.modify.assert_called_once()


@pytest.mark.parametrize(
    "case",
    ["present", "absent", "duplicate", "untyped", "wrong_master", "wrong_id", "mixed"],
)
def test_membership_is_one_scoped_query_without_full_album_fallback(case: str) -> None:
    """Absence is bounded and cannot turn into a paginated full-album scan."""
    client, _library, album, photo = objects()
    found: Any = photo
    if case in {"wrong_id", "mixed"}:
        record = photo.asset_record.model_dump(mode="json")
        record["recordName"] = "OTHER"
        found = PhotoAsset(
            album.service, photo.master_record, CKRecord.model_validate(record)
        )
    if case == "untyped":
        found = object()
    if case == "wrong_master":
        found = PhotoAsset(
            album.service,
            CKRecord.model_validate({
                "recordName": "OTHER",
                "recordType": "CPLMaster",
                "fields": {},
            }),
            photo.asset_record,
        )
    candidates = (
        [] if case == "absent" else [photo, photo] if case == "duplicate" else [found]
    )
    if case == "mixed":
        candidates = [photo, found]
    client.query.return_value.records = []
    client.query.return_value.syncToken = None
    with (
        patch.object(album, "_process_photo_list_response", return_value=candidates),
        patch.object(
            album, "get", side_effect=AssertionError("No full-album fallback")
        ),
    ):
        if case in {"present", "absent"}:
            assert album.contains_photo_scoped(photo) is (case == "present")
        else:
            with pytest.raises(PhotosServiceException):
                album.contains_photo_scoped(photo)
    client.query.assert_called_once()


@pytest.mark.parametrize("wrong_field", [None, "itemId", "containerId"])
def test_relation_acknowledgement_matches_item_and_container(
    wrong_field: str | None,
) -> None:
    """Capability reads do not dispatch; a typed relation must acknowledge exact IDs."""
    client, _library, album, photo = objects()
    assert album.can_add_photo_correlated(photo)
    client.query.assert_not_called()
    record = CKRecord.model_validate({
        "recordName": "P-IN-A",
        "recordType": "CPLContainerRelation",
        "fields": {
            "itemId": {
                "type": "STRING",
                "value": "OTHER" if wrong_field == "itemId" else "P",
            },
            "containerId": {
                "type": "STRING",
                "value": "OTHER" if wrong_field == "containerId" else "A",
            },
        },
    })
    client.modify.return_value = CKModifyResponse(records=[record])
    if wrong_field:
        with pytest.raises(PhotosServiceException, match="uncertain"):
            album.add_photo_correlated(photo)
    else:
        assert album.add_photo_correlated(photo)
    client.modify.assert_called_once()


@pytest.mark.parametrize(
    "case", ["present", "absent", "paired_foreign", "unpaired_foreign", "mixed"]
)
def test_scoped_membership_validates_raw_asset_identities(case: str) -> None:
    """Foreign assets cannot disappear in conversion and become apparent absence."""
    client, _library, album, photo = objects()
    asset = photo.asset_record.model_dump(mode="json")
    asset["fields"]["masterRef"] = {
        "type": "REFERENCE",
        "value": {"recordName": "M", "action": "NONE"},
    }
    if case in {"paired_foreign", "unpaired_foreign", "mixed"}:
        asset["recordName"] = "OTHER"
    records = [photo.master_record, CKRecord.model_validate(asset)]
    if case == "absent":
        records = []
    elif case == "unpaired_foreign":
        records = records[1:]
    elif case == "mixed":
        expected = {**asset, "recordName": "P"}
        records.append(CKRecord.model_validate(expected))
    client.query.return_value.records = records
    client.query.return_value.syncToken = None
    with patch.object(
        album, "get", side_effect=AssertionError("No full-album fallback")
    ):
        if case in {"present", "absent"}:
            assert album.contains_photo_scoped(photo) is (case == "present")
        else:
            with pytest.raises(
                PhotosServiceException, match="different asset identity"
            ):
                album.contains_photo_scoped(photo)
    client.query.assert_called_once()
    client.modify.assert_not_called()


def test_primary_owner_is_observed_not_guessed_and_snapshot_is_passive() -> None:
    """Owned PrimarySync may match; foreign/shared/changed routes may not."""
    service = PhotosService(
        "https://photos.test",
        cast(PyiCloudSession, object()),
        {"dsid": "123"},
        None,
        None,
        photos_upload_url="https://upload.test",
    )
    zone = load_fixture("photos_zones_list_response.json")["zones"][0]["zoneID"]
    service._root_library = PhotoLibrary(
        service,
        zone_id={"zoneName": "PrimarySync"},
        client=MagicMock(spec=PhotosCloudKitClient),
        check_indexing=False,
    )
    with (
        patch.object(PhotoLibrary, "_ensure_indexing_ready"),
        patch.object(
            service.private_client,
            "zones_list",
            return_value=CKZoneListResponse.model_validate(
                load_fixture("photos_zones_list_response.json")
            ),
        ),
        patch.object(
            service._shared_client,
            "zones_list",
            return_value=CKZoneListResponse(zones=[]),
        ),
    ):
        assert service.libraries["root"] is service._root_library
    with patch.object(service.private_client, "zones_list") as inventory:
        assert private_photo_zone(service, zone) == {"zoneName": "PrimarySync"}
        assert (
            private_photo_zone(service, {**zone, "ownerRecordName": "FOREIGN"}) is None
        )
        assert (
            private_photo_zone(service, {**zone, "zoneName": "SharedSync-other"})
            is None
        )
        snapshot = service.private_primary_zone
        assert snapshot is not None
        snapshot["ownerRecordName"] = "FOREIGN"
        assert service.private_primary_zone == zone
        service.params["dsid"] = "456"
        assert service.private_primary_zone is None
        assert private_photo_zone(service, zone) is None
        inventory.assert_not_called()
    assert service.upload_root == "https://upload.test"


@pytest.mark.parametrize(
    "name", [None, "", "  ", "bad\nname", "bad\x7fname", "é" * 2049]
)
def test_correlated_creation_rejects_invalid_names_before_dispatch(name: Any) -> None:
    """The name limit counts UTF-8 bytes and excludes controls and blank names."""
    client, library, _album, _photo = objects()
    with pytest.raises(ValueError, match="album name"):
        library.create_album_correlated(name)
    client.modify.assert_not_called()


def test_correlated_creation_accepts_byte_limit_and_preserves_wire_name() -> None:
    """The largest permitted multibyte name is sent without trimming or recoding."""
    client, library, _album, _photo = objects()
    name = "é" * 2048

    def response(**kwargs: Any) -> CKModifyResponse:
        data = kwargs["operations"][0].record.model_dump(mode="json")
        assert (
            data["fields"]["albumNameEnc"]["value"]
            == base64.b64encode(name.encode("utf-8")).decode()
        )
        data["fields"]["albumNameEnc"]["type"] = "STRING"
        data["recordChangeTag"] = "R1"
        return CKModifyResponse(records=[CKRecord.model_validate(data)])

    client.modify.side_effect = response
    created = library.create_album_correlated(name)
    assert created is not None
    assert created.name == name
    client.modify.assert_called_once()


def test_creation_preserves_unicode_name_from_encrypted_bytes_acknowledgement() -> None:
    """The acknowledged album name survives decoding of its typed byte field."""
    client, library, _album, _photo = objects()

    def response(**kwargs: Any) -> CKModifyResponse:
        data = kwargs["operations"][0].record.model_dump(mode="json")
        data["recordChangeTag"] = "R1"
        return CKModifyResponse(records=[CKRecord.model_validate(data)])

    client.modify.side_effect = response
    created = library.create_album_correlated("é")
    assert created is not None
    assert created.name == "é"
    client.modify.assert_called_once()


@pytest.mark.parametrize(
    "case", ["shared", "owner", "empty_zone", "mock_session", "client"]
)
def test_correlated_creation_unavailable_without_private_typed_route(case: str) -> None:
    """Capability checks and failed creates never fall back to REST."""
    client, library, _album, _photo = objects()
    if case == "shared":
        library.scope = "shared-library"
    elif case == "owner":
        library.zone_id["ownerRecordName"] = "owner"
    elif case == "empty_zone":
        library.zone_id["zoneName"] = ""
    elif case == "mock_session":
        mock_session = MagicMock()
        cast(Any, library.service).session = mock_session
    else:
        library._client = None
    assert not library.correlated_album_creation_available
    with pytest.raises(PhotosServiceException, match="unavailable"):
        library.create_album_correlated("New")
    client.modify.assert_not_called()
    if case == "mock_session":
        mock_session.post.assert_not_called()


@pytest.mark.parametrize("tag", ["", " ", " R1", "R1 ", "null", "NuLl"])
def test_creation_rejects_invalid_acknowledgement_revision(tag: str) -> None:
    """An acknowledgement without an exact usable revision is uncertain."""
    client, library, _album, _photo = objects()

    def response(**kwargs: Any) -> CKModifyResponse:
        data = kwargs["operations"][0].record.model_dump(mode="json")
        data["recordChangeTag"] = tag
        return CKModifyResponse(records=[CKRecord.model_validate(data)])

    client.modify.side_effect = response
    with pytest.raises(PhotosServiceException, match="uncertain"):
        library.create_album_correlated("New")
    client.modify.assert_called_once()


def test_creation_accepts_string_field_in_acknowledgement() -> None:
    """A STRING acknowledgement with the same wire name is also correlated."""
    client, library, _album, _photo = objects()

    def response(**kwargs: Any) -> CKModifyResponse:
        data = kwargs["operations"][0].record.model_dump(mode="json")
        data["fields"]["albumNameEnc"]["type"] = "STRING"
        data["recordChangeTag"] = "R1"
        return CKModifyResponse(records=[CKRecord.model_validate(data)])

    client.modify.side_effect = response
    created = library.create_album_correlated("New")
    assert created is not None
    assert created.name == "New"
    client.modify.assert_called_once()


def test_server_albums_excludes_pending_and_does_not_replace_cache() -> None:
    """A fresh server observation is distinct from the cached pending album."""
    _client, library, album, _photo = objects()
    library._cache_created_album(album)
    with patch.object(library, "_fetch_album_records", return_value=[]):
        cached = library.albums
        assert cached.get(album.id) is album
        observed = library.server_albums()
    assert observed.get(album.id) is None
    assert library.albums is cached
    assert library.albums.get(album.id) is album


@pytest.mark.parametrize(
    "case",
    ["wrong_id", "wrong_type", "wrong_item", "wrong_container", "empty", "multiple"],
)
def test_relation_rejects_uncorrelated_acknowledgements(case: str) -> None:
    """A successful response must identify exactly the submitted relation."""
    client, _library, album, photo = objects()
    data: dict[str, Any] = {
        "recordName": "P-IN-A",
        "recordType": "CPLContainerRelation",
        "fields": {
            "itemId": {"type": "STRING", "value": "P"},
            "containerId": {"type": "STRING", "value": "A"},
        },
    }
    if case == "wrong_id":
        data["recordName"] = "OTHER"
    elif case == "wrong_type":
        data["recordType"] = "CPLAlbum"
    elif case == "wrong_item":
        data["fields"]["itemId"]["value"] = "OTHER"
    elif case == "wrong_container":
        data["fields"]["containerId"]["value"] = "OTHER"
    record = CKRecord.model_validate(data)
    records = (
        [] if case == "empty" else [record, record] if case == "multiple" else [record]
    )
    client.modify.return_value = CKModifyResponse(records=[*records])
    with pytest.raises(PhotosServiceException, match="uncertain"):
        album.add_photo_correlated(photo)
    kwargs = client.modify.call_args.kwargs
    assert kwargs["atomic"] is True
    assert kwargs["zone_id"].model_dump(exclude_none=True) == {"zoneName": "Z"}
    sent = kwargs["operations"][0].model_dump(mode="json", exclude_none=True)
    assert sent["operationType"] == "create"
    assert sent["record"]["recordName"] == "P-IN-A"
    assert sent["record"]["fields"]["itemId"]["value"] == "P"
    assert sent["record"]["fields"]["containerId"]["value"] == "A"
    client.modify.assert_called_once()


def test_scoped_membership_query_has_exact_filters_and_updates_token() -> None:
    """A membership read uses both exact asset and album filters in the right zone."""
    client, library, album, photo = objects()
    client.query.return_value.records = []
    client.query.return_value.syncToken = "new-token"
    with patch.object(album, "_process_photo_list_response", return_value=[]):
        assert not album.contains_photo_scoped(photo)
    kwargs = client.query.call_args.kwargs
    query = kwargs["query"].model_dump(mode="json", exclude_none=True)
    assert query["recordType"] == ListTypeEnum.CONTAINER.value
    filters = {f["fieldName"]: f["fieldValue"]["value"] for f in query["filterBy"]}
    assert filters == {
        "direction": "ASCENDING",
        "startRank": 0,
        "recordName": "P",
        "parentId": "A",
    }
    assert kwargs["zone_id"].model_dump(exclude_none=True) == {"zoneName": "Z"}
    assert kwargs["results_limit"] > 0
    assert library.current_sync_token == "new-token"
    client.query.assert_called_once()


@pytest.mark.parametrize(
    "filters",
    [[], [parent_filter("OTHER")], [parent_filter("A"), parent_filter("OTHER")]],
)
def test_membership_rejects_unverified_filters_before_query(filters: list[Any]) -> None:
    """Missing, wrong or additional selectors cannot broaden membership lookup."""
    client, _library, album, photo = objects()
    album._extra_filters = filters
    with pytest.raises(PhotosServiceException, match="unavailable"):
        album.contains_photo_scoped(photo)
    client.query.assert_not_called()


@pytest.mark.parametrize(
    "zone",
    [
        None,
        {},
        {"zoneName": ""},
        {"zoneName": 7},
        {"zoneName": "Z", "zoneType": "OTHER"},
        {"zoneName": "SharedSync-test"},
        {"zoneName": "Z", "ownerRecordName": "owner"},
    ],
)
def test_private_zone_rejects_unverified_identities(zone: Any) -> None:
    """Only ownerless private zones or observed primary owners are accepted."""
    service = MagicMock()
    service.private_primary_zone = {
        "zoneName": "PrimarySync",
        "ownerRecordName": "owner",
    }
    assert private_photo_zone(service, zone) is None


@pytest.mark.parametrize("case", ["parent", "shared", "asset_zone", "object_type"])
def test_correlated_relation_rejects_non_private_container_context(case: str) -> None:
    """Nested, shared, mismatched-zone and virtual albums cannot dispatch relations."""
    client, library, album, photo = objects()
    if case == "parent":
        album._parent_id = "PARENT"
    elif case == "shared":
        library.scope = "shared-library"
    elif case == "asset_zone":
        data = photo.asset_record.model_dump(mode="json")
        data["zoneID"] = {"zoneName": "OTHER"}
        photo = PhotoAsset(
            album.service, photo.master_record, CKRecord.model_validate(data)
        )
    else:
        album._obj_type = ObjectTypeEnum.FAVORITE
    assert not album.can_add_photo_correlated(photo)
    with pytest.raises(PhotosServiceException, match="unavailable"):
        album.add_photo_correlated(photo)
    with pytest.raises(PhotosServiceException, match="unavailable"):
        album.contains_photo_scoped(photo)
    client.query.assert_not_called()
    client.modify.assert_not_called()


@pytest.mark.parametrize("case", ["duplicate", "deleted", "failure"])
def test_primary_zone_discovery_clears_stale_owner(case: str) -> None:
    """A new ambiguous, deleted or failed inventory invalidates prior ownership."""
    service = PhotosService(
        "https://photos.test",
        cast(PyiCloudSession, object()),
        {"dsid": "123"},
        None,
        None,
    )
    zone = {"zoneName": "PrimarySync", "ownerRecordName": "owner"}
    service._private_primary_zone = zone.copy()
    service._private_primary_binding = ("123", service.service_endpoint)
    root = PhotoLibrary(
        service, zone_id={"zoneName": "PrimarySync"}, check_indexing=False
    )
    entries: list[dict[str, Any]] = [{"zoneID": zone}]
    if case == "duplicate":
        entries.append({"zoneID": zone})
    elif case == "deleted":
        entries[0]["deleted"] = True
    result = CKZoneListResponse.model_validate({"zones": entries})
    with (
        patch.object(service, "_get_root_library", return_value=root),
        patch.object(service, "_discover_shared_typed_zones", return_value=True),
        patch.object(service.private_client, "zones_list") as inventory,
    ):
        if case == "failure":
            inventory.side_effect = PyiCloudException("unavailable")
        else:
            inventory.return_value = result
        assert service._discover_typed_zones({}) is (case != "failure")
        assert service.private_primary_zone is None
        assert private_photo_zone(service, zone) is None
