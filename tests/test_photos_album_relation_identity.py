"""Typed relation acknowledgement identity, without actual album membership claims."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.exceptions import PhotosServiceException
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
