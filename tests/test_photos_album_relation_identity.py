"""Typed relation acknowledgement identity, without actual album membership claims."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.constants import ListTypeEnum
from pyicloud.services.photos.exceptions import PhotosServiceException
from pyicloud.services.photos.queries import parent_filter
from pyicloud.services.photos.service import PhotoAsset
from tests.test_mcp_photo_album_writes import make_album


def setup_relation() -> tuple[Any, PhotoAsset, Any]:
    """Synthetic typed album/asset pair and one mocked typed-client transport."""
    library = MagicMock()
    library.service.params = {}
    library.service.session = object()
    library.scope = "private"
    client = MagicMock(spec=PhotosCloudKitClient)
    library._client = client
    album = make_album(library)
    master = CKRecord.model_validate({
        "recordName": "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    asset = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "fields": {},
        "zoneID": {"zoneName": "Z"},
    })
    return album, PhotoAsset(library.service, master, asset), client


@pytest.mark.parametrize(
    "case", ["exact", "id", "type", "item", "container", "missing", "multiple"]
)
def test_relation_acknowledgement_requires_exact_submitted_identity(case: str) -> None:
    """A record from another relation cannot become generic boolean success."""
    album, photo, client = setup_relation()

    def response(**kwargs: Any) -> CKModifyResponse:
        operation = kwargs["operations"][0]
        assert operation.operationType == "create"
        assert operation.record.recordName == "P-IN-A"
        assert kwargs["atomic"] is True
        assert kwargs["zone_id"].zoneName == "Z"
        record = CKRecord.model_validate({
            "recordName": "OTHER" if case == "id" else "P-IN-A",
            "recordType": "CPLAsset" if case == "type" else "CPLContainerRelation",
            "fields": {}
            if case == "missing"
            else {
                "itemId": {
                    "type": "STRING",
                    "value": "OTHER" if case == "item" else "P",
                },
                "containerId": {
                    "type": "STRING",
                    "value": "OTHER" if case == "container" else "A",
                },
            },
        })
        return CKModifyResponse(
            records=[record, record] if case == "multiple" else [record]
        )

    client.modify.side_effect = response
    if case == "exact":
        assert album.add_photo_correlated(photo) is True
    else:
        with pytest.raises(PhotosServiceException, match="outcome is uncertain"):
            album.add_photo_correlated(photo)
    client.modify.assert_called_once()


@pytest.mark.parametrize(
    "case", ["shared", "owner", "zone", "untyped", "session", "nested"]
)
def test_relation_unsupported_routes_never_dispatch(case: str) -> None:
    """No cross-zone, nested/shared or untyped transport fallback."""
    album, photo, client = setup_relation()
    if case == "shared":
        album._library.scope = "shared-library"
    elif case == "owner":
        album._zone_id["ownerRecordName"] = "OWNER"
    elif case == "zone":
        album._zone_id["zoneName"] = "OTHER"
    elif case == "untyped":
        album._client = None
    elif case == "session":
        album.service.session = MagicMock()
    else:
        album._parent_id = "PARENT"
    with pytest.raises(PhotosServiceException, match="unavailable"):
        album.add_photo_correlated(photo)
    client.modify.assert_not_called()


def test_relation_capability_check_has_no_transport_side_effects() -> None:
    """Capability observations do not grant approval or send requests."""
    album, photo, client = setup_relation()
    assert album.can_add_photo_correlated(photo)
    album._library.scope = "shared-library"
    assert not album.can_add_photo_correlated(photo)
    client.modify.assert_not_called()
    client.query.assert_not_called()


@pytest.mark.parametrize("case", ["present", "absent", "wrong_master", "untyped"])
def test_scoped_membership_uses_exact_asset_identity(case: str) -> None:
    """Observed presence/absence must come from the exact container lookup."""
    album, photo, client = setup_relation()
    album._list_type = ListTypeEnum.CONTAINER
    album._extra_filters = [parent_filter(album.id)]
    found: Any = photo
    if case == "absent":
        found = None
    elif case == "untyped":
        found = object()
    elif case == "wrong_master":
        master = CKRecord.model_validate({
            "recordName": "OTHER",
            "recordType": "CPLMaster",
            "fields": {},
        })
        found = PhotoAsset(album.service, master, photo.asset_record)
    with patch.object(album, "get", return_value=found) as lookup:
        if case in {"present", "absent"}:
            assert album.contains_photo_scoped(photo) is (case == "present")
        else:
            with pytest.raises(PhotosServiceException, match="different asset"):
                album.contains_photo_scoped(photo)
        lookup.assert_called_once_with(photo.id)
    client.modify.assert_not_called()


@pytest.mark.parametrize(
    "case", ["list_type", "no_filter", "wrong_parent", "extra_filter", "shared"]
)
def test_unscoped_membership_is_refused_before_lookup(case: str) -> None:
    """Extra predicates cannot make a false absence look like relation state."""
    album, photo, client = setup_relation()
    album._list_type = ListTypeEnum.CONTAINER
    album._extra_filters = [parent_filter(album.id)]
    if case == "list_type":
        album._list_type = ListTypeEnum.DEFAULT
    elif case == "no_filter":
        album._extra_filters = []
    elif case == "wrong_parent":
        album._extra_filters = [parent_filter("OTHER")]
    elif case == "extra_filter":
        album._extra_filters.append(parent_filter("OTHER"))
    else:
        album._library.scope = "shared-library"
    with (
        patch.object(album, "get") as lookup,
        pytest.raises(PhotosServiceException, match="unavailable"),
    ):
        album.contains_photo_scoped(photo)
    lookup.assert_not_called()
    client.modify.assert_not_called()
