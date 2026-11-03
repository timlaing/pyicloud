"""Core private-zone identity checks using typed fixtures, without MCP dependencies."""

# pylint: disable=protected-access

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKRecord, CKZoneListResponse
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.service import (
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    private_photo_zone,
)
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture
from tests.test_photos_album_identity import make_album


def observed_service() -> tuple[PhotosService, PhotoLibrary, dict[str, Any]]:
    """Observe private primary metadata through existing typed zone discovery."""
    service = PhotosService(
        "https://photos.example.test",
        cast(PyiCloudSession, object()),
        {"dsid": "123"},
        None,
        None,
    )
    library = PhotoLibrary(
        service,
        zone_id={"zoneName": "PrimarySync"},
        client=MagicMock(spec=PhotosCloudKitClient),
        check_indexing=False,
    )
    service._root_library = library
    fixture = load_fixture("photos_zones_list_response.json")
    with (
        patch.object(PhotoLibrary, "_ensure_indexing_ready"),
        patch.object(
            service.private_client,
            "zones_list",
            return_value=CKZoneListResponse.model_validate(fixture),
        ) as inventory,
        patch.object(
            service._shared_client,
            "zones_list",
            return_value=CKZoneListResponse(zones=[]),
        ),
    ):
        assert service.libraries["root"] is library
        inventory.assert_called_once()
    return service, library, fixture["zones"][0]["zoneID"]


def photo_for(service: PhotosService, zone: dict[str, Any]) -> PhotoAsset:
    """Build an indexed record with complete fixture zone metadata."""
    master = CKRecord.model_validate({
        "recordName": "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    asset = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "recordChangeTag": "R1",
        "zoneID": zone,
        "fields": {"isFavorite": {"type": "INT64", "value": 0}},
    })
    return PhotoAsset(service, master, asset)


@pytest.mark.parametrize(
    "case",
    [
        "own",
        "foreign",
        "shared",
        "unknown",
        "type",
        "account",
        "route",
        "observed_type",
    ],
)
def test_private_owner_checks_are_passive_and_fail_closed(case: str) -> None:
    """Own primary metadata is supported without network in capability checks."""
    service, library, original = observed_service()
    zone = dict(original)
    if case == "foreign":
        zone["ownerRecordName"] = "FOREIGN"
    elif case == "shared":
        zone["zoneName"] = "SharedSync-other"
    elif case == "unknown":
        service._private_primary_binding = None
    elif case == "type":
        zone["zoneType"] = "UNVERIFIED"
    elif case == "account":
        service.params["dsid"] = "456"
    elif case == "route":
        service.service_endpoint = "https://other.test"
    elif case == "observed_type":
        service._private_primary_zone = {**original, "zoneType": "UNVERIFIED"}
    album = make_album(library)
    album._zone_id = {"zoneName": "PrimarySync"}
    with patch.object(service.private_client, "zones_list") as inventory:
        assert (private_photo_zone(service, zone) == {"zoneName": "PrimarySync"}) is (
            case == "own"
        )
        assert album.can_add_photo_correlated(photo_for(service, zone)) is (
            case == "own"
        )
        inventory.assert_not_called()


def test_primary_metadata_is_detached_and_ambiguous_inventory_is_not_trusted() -> None:
    """A copied mapping cannot redirect ownership; duplicate inventory fails closed."""
    service, _library, zone = observed_service()
    observed = service.private_primary_zone
    assert observed is not None
    observed["ownerRecordName"] = "FOREIGN"
    assert service.private_primary_zone == zone
    repeated = load_fixture("photos_zones_list_response.json")
    repeated["zones"].append(repeated["zones"][0])
    with (
        patch.object(PhotoLibrary, "_ensure_indexing_ready"),
        patch.object(
            service.private_client,
            "zones_list",
            return_value=CKZoneListResponse.model_validate(repeated),
        ),
        patch.object(
            service._shared_client,
            "zones_list",
            return_value=CKZoneListResponse(zones=[]),
        ),
    ):
        service._discover_typed_zones({})
    assert service.private_primary_zone is None
    assert private_photo_zone(service, zone) is None
