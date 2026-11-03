"""Typed album creation responses must correlate to the submitted record ID."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.photos.constants import AlbumTypeEnum
from pyicloud.services.photos.exceptions import PhotosServiceException
from pyicloud.services.photos.service import PhotoLibrary


@pytest.mark.parametrize("case", ["wrong_id", "wrong_type", "multiple"])
def test_uncorrelated_typed_creation_never_returns_or_caches_album(case: str) -> None:
    """No acknowledged but unrelated record may become a usable album handle."""
    service = MagicMock()
    service.params = {}
    client = MagicMock()
    library = PhotoLibrary(
        service, client=client, zone_id={"zoneName": "Z"}, check_indexing=False
    )

    def response(**kwargs: Any) -> CKModifyResponse:
        requested = kwargs["operations"][0].record.recordName
        record = CKRecord.model_validate({
            "recordName": "OTHER" if case == "wrong_id" else requested,
            "recordType": "CPLAsset" if case == "wrong_type" else "CPLAlbum",
            "fields": {},
        })
        return CKModifyResponse(
            records=[record, record] if case == "multiple" else [record]
        )

    client.modify.side_effect = response
    with pytest.raises(PhotosServiceException, match="outcome is uncertain"):
        library._create_album_typed(client, "TmFtZQ==", AlbumTypeEnum.ALBUM, 1)
    client.modify.assert_called_once()
    assert not library._pending_albums
