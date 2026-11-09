"""Synthetic typed-only creation with exact receipts and new server identity proof."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_album_creates import PhotoAlbumCreates
from pyicloud.mcp.photo_album_writes import PhotoAlbumWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.service import PhotoLibrary
from tests.test_mcp_additional import owner_for
from tests.test_mcp_photo_album_writes import make_album


def setup_create() -> tuple[PhotoAlbumCreates, Any, Any, str]:
    """Real private library identity, mocked server reads, no transport."""
    client = MagicMock()
    client.params = {"dsid": "123"}
    client.photos.params = {}
    client.photos.session = SimpleNamespace(post=MagicMock())
    client.photos.service_endpoint = "https://example.test"
    library = cast(
        Any,
        PhotoLibrary(
            client.photos,
            client=MagicMock(spec=PhotosCloudKitClient),
            zone_id={"zoneName": "Z"},
            check_indexing=False,
        ),
    )
    library.server_albums = MagicMock(return_value=[])
    client.photos.libraries = {"Z": library}
    additional = AdditionalReads(ReadTools(owner_for(client)))
    handle = additional.remember("photo-library", library)
    ledger = ApprovalLedger(frozenset({"photos.create_album"}))
    return (
        PhotoAlbumCreates(PhotoAlbumWrites(additional, ledger)),
        client,
        library,
        handle,
    )


@pytest.mark.asyncio
async def test_create_requires_receipt_and_independent_new_identity() -> None:
    """Fresh post-state is a distinct observed album, not the created cache object."""
    writes, _client, library, handle = setup_create()
    publish = MagicMock()
    preview = await writes.prepare_create(handle, {"name": "New"}, publish)
    publish.assert_called_once_with(preview)
    with patch.object(library, "create_album_correlated") as create:
        with pytest.raises(WriteDenied, match="operator_approval_required"):
            await writes.apply(preview.request_id)
        create.assert_not_called()
        created = make_album(library, "New", "R1")
        observed = make_album(library, "New", "R1")

        def mutation(_name: str) -> Any:
            library.server_albums.return_value = [observed]
            return created

        create.side_effect = mutation

        def accept(identifier: str) -> None:
            writes.ledger.approve_from_operator(identifier, preview.digest)

        assert await writes.apply(preview.request_id, accept) == {
            "action": "photos.create_album",
            "id": "A",
        }
        create.assert_called_once_with("New")
        assert library.server_albums.call_args.kwargs["max_records"] == 1000
        assert all(
            call.kwargs["max_records"] == 999
            for call in library.server_albums.call_args_list[:-1]
        )
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["snapshot", "route", "transport", "shared"])
async def test_changed_library_prevents_dispatch(case: str) -> None:
    """Snapshot, endpoint and supported transport must remain exactly approved."""
    writes, client, library, handle = setup_create()
    preview = await writes.prepare_create(handle, {"name": "New"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "snapshot":
        library.server_albums.return_value = [make_album(library)]
    elif case == "route":
        client.photos.service_endpoint = "https://other.test"
    elif case == "transport":
        library._client = None
    else:
        library.scope = "shared-library"
    with patch.object(library, "create_album_correlated") as create:
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
        create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["absent", "cached", "wrong_name", "no_handle"])
async def test_unverified_creation_is_uncertain_and_non_retrying(case: str) -> None:
    """One acknowledgement never substitutes for independent visibility."""
    writes, _client, library, handle = setup_create()
    preview = await writes.prepare_create(handle, {"name": "New"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    created = make_album(library, "Wrong" if case == "wrong_name" else "New", "R1")

    def mutation(_name: str) -> Any:
        library.server_albums.return_value = (
            []
            if case == "absent"
            else [created]
            if case == "cached"
            else [make_album(library, created.name)]
        )
        return None if case == "no_handle" else created

    with patch.object(
        library, "create_album_correlated", side_effect=mutation
    ) as create:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)
        create.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "collision",
        "revision",
        "duplicate",
        "owner",
        "untyped",
        "invalid_payload",
        "untyped_album",
    ],
)
async def test_unsupported_creation_context_never_publishes(case: str) -> None:
    """No overwrite, unknown inventory revision, shared zone or guessed library."""
    writes, _client, library, handle = setup_create()
    if case == "collision":
        library.server_albums.return_value = [make_album(library, "new")]
    elif case == "revision":
        library.server_albums.return_value = [make_album(library, tag=None)]
    elif case == "duplicate":
        library.server_albums.return_value = [make_album(library), make_album(library)]
    elif case == "owner":
        library.zone_id["ownerRecordName"] = "OWNER"
    elif case == "untyped":
        handle = writes.additional.remember("photo-library", object())
    elif case == "untyped_album":
        library.server_albums.return_value = [object()]
    with pytest.raises(WriteDenied):
        await writes.prepare_create(
            handle,
            {"name": "New", "extra": True}
            if case == "invalid_payload"
            else {"name": "New"},
        )


@pytest.mark.asyncio
async def test_bounded_inventory_and_service_opt_in() -> None:
    """Bound internal selectors and refuse disabled service before discovery."""
    writes, _client, library, handle = setup_create()
    writes._libraries = {str(index): "Z" for index in range(256)}
    await writes.prepare_create(handle, {"name": "New"})
    assert len(writes._libraries) == 256
    library.server_albums.return_value = [make_album(library)] * 1001
    with pytest.raises(WriteDenied, match="inventory_unavailable"):
        await writes.prepare_create(handle, {"name": "New"})
    disabled = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("mail",))))
    disabled_writer = PhotoAlbumCreates(PhotoAlbumWrites(disabled, writes.ledger))
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled_writer.prepare_create(handle, {"name": "New"})
