"""Synthetic private album renames with fresh revision and independent post-state."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_album_writes import PhotoAlbumWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.photos.constants import (
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
)
from pyicloud.services.photos.service import PhotoAlbum, PhotoAlbumRecordState
from tests.test_mcp_additional import owner_for


def make_album(
    library: Any,
    name: str = "Before",
    tag: str | None = "R1",
    parent: str | None = None,
) -> PhotoAlbum:
    """No transport in construction; all records and names are synthetic."""
    return PhotoAlbum(
        library,
        name=name,
        record_id="A",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        zone_id={"zoneName": "Z"},
        record=PhotoAlbumRecordState(parent_id=parent, record_change_tag=tag),
    )


def setup_album() -> tuple[PhotoAlbumWrites, Any, PhotoAlbum, str]:
    """Discovered typed album plus fake refreshed authoritative library data."""
    client = MagicMock()
    client.params = {"dsid": "123"}
    client.photos.service_endpoint = "https://example.test"
    client.photos.params = {}
    library = MagicMock()
    library.service = client.photos
    library.zone_id = {"zoneName": "Z"}
    library.scope = "private"
    album = make_album(library)
    library.server_albums.return_value = [album]
    client.photos.libraries = {"Z": library}
    additional = AdditionalReads(ReadTools(owner_for(client)))
    handle = additional.remember("photo-album", album)
    return (
        PhotoAlbumWrites(
            additional, ApprovalLedger(frozenset({"photos.rename_album"}))
        ),
        client,
        album,
        handle,
    )


@pytest.mark.asyncio
async def test_rename_requires_receipt_and_fresh_independent_post_state() -> None:
    """One exact receipt allows one callback, not optimistic-local proof."""
    writes, client, album, handle = setup_album()
    publish = MagicMock()
    preview = await writes.prepare(handle, {"name": "After"}, publish)
    publish.assert_called_once_with(preview)
    with patch.object(album, "rename") as mutate:
        with pytest.raises(WriteDenied, match="operator_approval_required"):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()

        def rename(name: str) -> None:
            library = client.photos.libraries["Z"]
            library.server_albums.return_value = [make_album(library, name, "R2")]

        mutate.side_effect = rename

        def accept(identifier: str) -> None:
            writes.ledger.approve_from_operator(identifier, preview.digest)

        assert (await writes.apply(preview.request_id, accept))["changed"]
        mutate.assert_called_once_with("After")
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["tag", "name", "parent", "shared", "missing", "route", "no_revision"]
)
async def test_changed_fresh_targets_never_dispatch(case: str) -> None:
    """Unknown tags, other parents, shared scopes and stale bindings fail closed."""
    writes, client, album, handle = setup_album()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    library = client.photos.libraries["Z"]
    if case == "shared":
        library.scope = "shared-library"
    elif case == "missing":
        library.server_albums.return_value = []
    elif case == "route":
        client.photos.service_endpoint = "https://other.test"
    else:
        library.server_albums.return_value = [
            make_album(
                library,
                "Changed" if case == "name" else "Before",
                None if case == "no_revision" else "R2" if case == "tag" else "R1",
                "OTHER" if case == "parent" else None,
            )
        ]
    with patch.object(album, "rename") as mutate:
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()


@pytest.mark.asyncio
async def test_ack_only_rename_is_uncertain_and_retired() -> None:
    """Fresh album lists cannot be replaced with the mutation acknowledgement."""
    writes, _client, album, handle = setup_album()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    with patch.object(album, "rename") as mutate:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)
        mutate.assert_called_once()


@pytest.mark.asyncio
async def test_noop_consumes_without_dispatch() -> None:
    """A no-op still needs an exact single-use operator receipt."""
    writes, _client, album, handle = setup_album()
    preview = await writes.prepare(handle, {"name": "Before"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    with patch.object(album, "rename") as mutate:
        assert not (await writes.apply(preview.request_id))["changed"]
        mutate.assert_not_called()


@pytest.mark.asyncio
async def test_locally_updated_cached_album_is_not_independent_proof() -> None:
    """Pending local album caches may survive refresh; never trust their optimism."""
    writes, _client, album, handle = setup_album()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def optimistic(name: str) -> None:
        album._name = name

    with (
        patch.object(album, "rename", side_effect=optimistic),
        pytest.raises(WriteDenied, match="mutation_uncertain"),
    ):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "x" * 4097}, {"name": "path/name"}]
)
async def test_invalid_payload_before_discovery(payload: dict[str, Any]) -> None:
    """No unbounded titles or path-like selectors."""
    writes, client, _album, handle = setup_album()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare(handle, payload)
    client.photos.libraries["Z"].server_albums.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["type", "owner", "nested", "route"])
async def test_unsupported_discovered_context_is_refused(case: str) -> None:
    """Never target untyped virtual data or a caller-selected shared owner."""
    writes, client, _album, handle = setup_album()
    library = client.photos.libraries["Z"]
    if case == "type":
        handle = writes.additional.remember("photo-album", object())
    elif case == "route":
        client.photos.service_endpoint = None
    else:
        album = make_album(library, parent="PARENT" if case == "nested" else None)
        if case == "owner":
            album = PhotoAlbum(
                library,
                name="Shared",
                record_id="A",
                obj_type=ObjectTypeEnum.CONTAINER,
                list_type=ListTypeEnum.DEFAULT,
                direction=DirectionEnum.ASCENDING,
                zone_id={"zoneName": "Z", "ownerRecordName": "OWNER"},
            )
        handle = writes.additional.remember("photo-album", album)
    with pytest.raises(WriteDenied):
        await writes.prepare(handle, {"name": "After"})


@pytest.mark.asyncio
async def test_cached_pending_album_cannot_authorize_preview() -> None:
    """A valid-looking local revision is not independent server visibility."""
    writes, client, album, handle = setup_album()
    library = client.photos.libraries["Z"]
    library.refresh_albums.return_value = [album]
    library.server_albums.return_value = []
    with patch.object(album, "rename") as mutate:
        with pytest.raises(WriteDenied, match="target_unavailable"):
            await writes.prepare(handle, {"name": "After"})
        mutate.assert_not_called()
    library.refresh_albums.assert_not_called()


@pytest.mark.asyncio
async def test_optimistic_pending_copy_cannot_prove_rename() -> None:
    """Even a distinct locally pending object cannot substitute for a server read."""
    writes, client, album, handle = setup_album()
    library = client.photos.libraries["Z"]
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def optimistic(_name: str) -> None:
        library.refresh_albums.return_value = [make_album(library, "After", "R2")]
        library.server_albums.return_value = []

    with patch.object(album, "rename", side_effect=optimistic) as mutate:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        mutate.assert_called_once()
    library.refresh_albums.assert_not_called()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_bounded_inventory_and_disabled_service() -> None:
    """Keep selector storage bounded and do not access disabled service data."""
    writes, _client, _album, handle = setup_album()
    writes._targets = {str(index): ("A", "Z") for index in range(256)}
    await writes.prepare(handle, {"name": "After"})
    assert len(writes._targets) == 256
    additional = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("mail",))))
    disabled_writer = PhotoAlbumWrites(additional, writes.ledger)
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled_writer.prepare(handle, {"name": "After"})
