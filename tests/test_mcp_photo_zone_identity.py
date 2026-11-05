"""Private PrimarySync ownership from typed fixtures, never guessed DSID mappings."""

# Regression construction and inventory binding checks intentionally inspect internals.
# pylint: disable=protected-access

import base64
import hashlib
from typing import Any, cast
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pyicloud.common.cloudkit import CKRecord, CKZoneListResponse
from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_album_creates import PhotoAlbumCreates
from pyicloud.mcp.photo_album_writes import PhotoAlbumWrites
from pyicloud.mcp.photo_uploads import PhotoUploads
from pyicloud.mcp.photo_writes import PhotoWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.upload_staging import UploadStaging
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.models import PhotosPutAssetResult
from pyicloud.services.photos.service import (
    PhotoAsset,
    PhotoLibrary,
    PhotosService,
    private_photo_zone,
)
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture
from tests.test_mcp_additional import owner_for
from tests.test_photos_album_identity import make_album


def observed_service() -> tuple[PhotosService, PhotoLibrary, dict[str, Any]]:
    """Capture identity through the real private zones discovery with synthetic HTTP."""
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
        ) as private,
        patch.object(
            service._shared_client,
            "zones_list",
            return_value=CKZoneListResponse(zones=[]),
        ),
    ):
        assert service.libraries["root"] is library
        private.assert_called_once()
    return service, library, fixture["zones"][0]["zoneID"]


def photo_for(service: PhotosService, zone: dict[str, Any]) -> PhotoAsset:
    """A returned indexed record can include its full owned zone metadata."""
    master = CKRecord.model_validate({
        "recordName": "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    record = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "recordChangeTag": "R1",
        "zoneID": zone,
        "fields": {"isFavorite": {"type": "INT64", "value": 0}},
    })
    return PhotoAsset(service, master, record)


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
    """Only the observed private primary owner is admitted; shared/unknown denied."""
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


@pytest.mark.asyncio
async def test_favourite_binding_matches_primary_owner_metadata_and_rejects_drift() -> (
    None
):
    """Full asset metadata can match a minimal library zone without losing binding."""
    service, library, zone = observed_service()
    asset = photo_for(service, zone)
    client = MagicMock()
    client.params = service.params
    client.photos = service
    writes = PhotoWrites(
        AdditionalReads(ReadTools(owner_for(client))),
        ApprovalLedger(frozenset({"photos.set_favorite"})),
    )
    handle = writes.additional.remember("photo-asset", asset)
    with patch.object(
        type(library),
        "all",
        new_callable=PropertyMock,
        return_value=MagicMock(get=MagicMock(return_value=asset)),
    ):
        preview = await writes.prepare(handle, {"favorite": True})
        writes.ledger.approve_from_operator(preview.request_id, preview.digest)
        service._private_primary_zone = {**zone, "ownerRecordName": "FOREIGN"}
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)


def test_primary_metadata_is_detached_and_duplicate_inventory_is_not_trusted() -> None:
    """No caller mutation or ambiguous private inventory establishes ownership."""
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


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [False, True])
async def test_uploaded_primary_asset_accepts_only_observed_owned_zone(
    drift: bool,
) -> None:
    """Upload proof admits owned PrimarySync metadata but rejects owner drift."""
    service, library, zone = observed_service()
    service._photos_upload_url = "https://upload.example.test"
    cast(Any, library).server_albums = MagicMock(return_value=[])
    client = MagicMock()
    client.params = service.params
    client.photos = service
    owner = cast(Any, owner_for(client))
    owner.add_cleanup = MagicMock()
    additional = AdditionalReads(ReadTools(owner))
    ledger = ApprovalLedger(
        frozenset({"photos.upload_file"}),
        high_risk_actions=frozenset({"photos.upload_file"}),
    )
    creator = PhotoAlbumCreates(PhotoAlbumWrites(additional, ledger))
    stages = UploadStaging()
    data = b"synthetic"
    token = stages.begin("photo.jpg", len(data), hashlib.sha256(data).hexdigest())
    stages.append(token, 0, base64.b64encode(data).decode())
    stages.seal(token)
    pipeline = MagicMock()

    def uploaded(*_args: Any, **_kwargs: Any) -> PhotosPutAssetResult:
        if drift:
            service._private_primary_zone = {**zone, "ownerRecordName": "FOREIGN"}
        return PhotosPutAssetResult(cplMaster="M", cplAsset="P")

    pipeline.upload.side_effect = uploaded
    uploads = PhotoUploads(
        creator, stages, frozenset({"content.example.test"}), pipeline
    )
    handle = additional.remember("photo-library", library)
    preview = await uploads.prepare_upload(handle, token, 1000)
    ledger.approve_from_operator(preview.request_id, preview.digest, high_risk=True)
    with patch.object(
        type(library),
        "all",
        new_callable=PropertyMock,
        return_value=MagicMock(get=MagicMock(return_value=photo_for(service, zone))),
    ):
        if drift:
            with pytest.raises(WriteDenied, match="mutation_uncertain"):
                await uploads.apply(preview.request_id)
        else:
            assert (await uploads.apply(preview.request_id))["verified"] is True
    pipeline.upload.assert_called_once()
    with pytest.raises(WriteDenied):
        stages.peek(token)
