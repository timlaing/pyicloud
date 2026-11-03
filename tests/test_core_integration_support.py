"""Standalone core metadata/correlation regressions, with no MCP imports."""

# Synthetic setup intentionally inspects core metadata and query processing.
# pylint: disable=protected-access

import base64
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord, CKZoneListResponse
from pyicloud.services.drive import DriveService
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.constants import (
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


@pytest.mark.parametrize("wrong", [False, True])
def test_relation_acknowledgement_matches_item_and_container(wrong: bool) -> None:
    """Capability reads do not dispatch; a typed relation must acknowledge exact IDs."""
    client, _library, album, photo = objects()
    assert album.can_add_photo_correlated(photo)
    client.query.assert_not_called()
    record = CKRecord.model_validate({
        "recordName": "P-IN-A",
        "recordType": "CPLContainerRelation",
        "fields": {
            "itemId": {"type": "STRING", "value": "OTHER" if wrong else "P"},
            "containerId": {"type": "STRING", "value": "A"},
        },
    })
    client.modify.return_value = CKModifyResponse(records=[record])
    if wrong:
        with pytest.raises(PhotosServiceException, match="uncertain"):
            album.add_photo_correlated(photo)
    else:
        assert album.add_photo_correlated(photo)
    client.modify.assert_called_once()


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
