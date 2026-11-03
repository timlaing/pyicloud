"""Typed album creation responses must correlate to the submitted record ID."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.photos.client import PhotosCloudKitClient
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


@pytest.mark.parametrize("case", ["no_client", "untyped", "shared", "owner", "session"])
def test_correlated_entrypoint_never_falls_back_to_unqualified_rest(case: str) -> None:
    """Unavailable verified transport or scope must fail before any dispatch."""
    service = MagicMock()
    service.params = {}
    service.session = object()
    library = PhotoLibrary(
        service,
        client=MagicMock(spec=PhotosCloudKitClient),
        zone_id={"zoneName": "Z"},
        check_indexing=False,
    )
    if case == "no_client":
        library._client = None
    elif case == "untyped":
        library._client = MagicMock()
    elif case == "shared":
        library.scope = "shared-library"
    elif case == "owner":
        library.zone_id["ownerRecordName"] = "SHARED"
    else:
        service.session = MagicMock()
    with (
        patch.object(library, "_create_album_typed") as typed,
        patch.object(library, "_create_album_rest") as rest,
        pytest.raises(PhotosServiceException, match="unavailable"),
    ):
        library.create_album_correlated("New Album")
    typed.assert_not_called()
    rest.assert_not_called()


def test_correlated_entrypoint_dispatches_only_typed_album_creation() -> None:
    """A selected typed private route does not implicitly choose another endpoint."""
    service = MagicMock()
    service.params = {}
    service.session = object()
    client = MagicMock(spec=PhotosCloudKitClient)
    library = PhotoLibrary(
        service, client=client, zone_id={"zoneName": "Z"}, check_indexing=False
    )
    with (
        patch.object(library, "_create_album_typed", return_value=None) as typed,
        patch.object(library, "_create_album_rest") as rest,
    ):
        assert library.create_album_correlated("Name") is None
    typed.assert_called_once()
    args = typed.call_args.args
    assert args[:3] == (client, "TmFtZQ==", AlbumTypeEnum.ALBUM)
    assert args[3] > 0
    rest.assert_not_called()


@pytest.mark.parametrize("name", ["", "x" * 4097, "bad\nname"])
def test_correlated_entrypoint_rejects_invalid_names_before_dispatch(name: str) -> None:
    """Caller data is validated before service/transport access."""
    service = MagicMock()
    service.params = {}
    library = PhotoLibrary(
        service, client=MagicMock(spec=PhotosCloudKitClient), check_indexing=False
    )
    with (
        patch.object(library, "_create_album_typed") as typed,
        pytest.raises(ValueError),
    ):
        library.create_album_correlated(name)
    typed.assert_not_called()
