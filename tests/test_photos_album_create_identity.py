"""Typed album creation responses must correlate to the submitted record ID."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.photos.constants import AlbumTypeEnum
from pyicloud.services.photos.exceptions import PhotosServiceException
from pyicloud.services.photos.service import AlbumContainer, PhotoLibrary


@pytest.mark.parametrize(
    "case",
    [
        "wrong_id",
        "wrong_type",
        "multiple",
        "wrong_name",
        "missing_name",
        "missing_tag",
        "blank_tag",
        "sentinel_tag",
        "padded_tag",
    ],
)
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
            "fields": {}
            if case == "missing_name"
            else {
                "albumNameEnc": {
                    "value": "T3RoZXI=" if case == "wrong_name" else "TmFtZQ==",
                    "type": "ENCRYPTED_BYTES",
                }
            },
            "recordChangeTag": {
                "missing_tag": None,
                "blank_tag": " ",
                "sentinel_tag": "null",
                "padded_tag": " R1 ",
            }.get(case, "R1"),
        })
        return CKModifyResponse(
            records=[record, record] if case == "multiple" else [record]
        )

    client.modify.side_effect = response
    with pytest.raises(PhotosServiceException, match="outcome is uncertain"):
        library._create_album_typed(client, "TmFtZQ==", AlbumTypeEnum.ALBUM, 1)
    client.modify.assert_called_once()
    assert not library._pending_albums


def test_server_album_reads_never_merge_pending_creation_cache() -> None:
    """An acknowledgement cache must not supply independent server visibility."""
    service = MagicMock()
    service.params = {}
    library = PhotoLibrary(service, client=MagicMock(), check_indexing=False)
    pending = MagicMock()
    cached = AlbumContainer([pending])
    library._pending_albums = {"PENDING": pending}
    library._albums = cached
    observed = AlbumContainer()
    with patch.object(library, "_get_albums", return_value=observed) as query:
        assert library.server_albums() is observed
    query.assert_called_once_with()
    assert library._albums is cached
    assert library._pending_albums == {"PENDING": pending}
    assert len(observed) == 0
