"""Synthetic revision and membership-bound add-to-album receipts."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_album_writes import PhotoAlbumWrites
from pyicloud.mcp.photo_relations import PhotoRelations
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.photos.constants import ListTypeEnum
from pyicloud.services.photos.queries import parent_filter
from tests.test_mcp_additional import owner_for
from tests.test_photos_album_relation_identity import setup_relation


def setup_add() -> tuple[PhotoRelations, Any, Any, Any, str, str]:
    """Real typed album/asset with entirely synthetic fresh sources."""
    album, asset, _transport = setup_relation()
    album._list_type = ListTypeEnum.CONTAINER
    album._extra_filters = [parent_filter(album.id)]
    asset.asset_record.recordChangeTag = "P1"
    library = album._library
    library.zone_id = {"zoneName": "Z"}
    library.server_albums.return_value = [album]
    library.all.get.return_value = asset
    client = MagicMock()
    client.params = {"dsid": "123"}
    client.photos.service_endpoint = "https://example.test"
    client.photos.libraries = {"Z": library}
    additional = AdditionalReads(ReadTools(owner_for(client)))
    source = additional.remember("photo-asset", asset)
    target = additional.remember("photo-album", album)
    ledger = ApprovalLedger(frozenset({"photos.add_to_album"}))
    return (
        PhotoRelations(PhotoAlbumWrites(additional, ledger)),
        client,
        album,
        asset,
        source,
        target,
    )


@pytest.mark.asyncio
async def test_add_requires_receipt_and_membership_proof() -> None:
    """One correlated callback followed by independent scoped membership."""
    writes, _client, album, asset, source, target = setup_add()
    publish = MagicMock()
    with (
        patch.object(album, "contains_photo_scoped", return_value=False) as membership,
        patch.object(album, "add_photo_correlated") as mutate,
    ):
        preview = await writes.prepare_add(source, target, {}, publish)
        publish.assert_called_once_with(preview)
        with pytest.raises(WriteDenied, match="operator_approval_required"):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()

        def add(_asset: Any) -> bool:
            membership.return_value = True
            return True

        mutate.side_effect = add

        def accept(identifier: str) -> None:
            writes.ledger.approve_from_operator(identifier, preview.digest)

        assert (await writes.apply(preview.request_id, accept))["changed"]
        mutate.assert_called_once_with(asset)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["tag", "master", "zone", "membership", "route", "capability"]
)
async def test_changed_pair_never_dispatches(case: str) -> None:
    """Exact asset/master/zone/revision/member state remains receipt-bound."""
    writes, client, album, asset, source, target = setup_add()
    with (
        patch.object(album, "contains_photo_scoped", return_value=False) as membership,
        patch.object(
            album, "can_add_photo_correlated", return_value=True
        ) as capability,
        patch.object(album, "add_photo_correlated") as mutate,
    ):
        preview = await writes.prepare_add(source, target, {})
        writes.ledger.approve_from_operator(preview.request_id, preview.digest)
        if case == "tag":
            asset.asset_record.recordChangeTag = "P2"
        elif case == "master":
            asset.master_record.recordName = "OTHER"
        elif case == "zone":
            asset.asset_record.zoneID = None
        elif case == "membership":
            membership.return_value = True
        elif case == "route":
            client.photos.service_endpoint = "https://other.test"
        else:
            capability.return_value = False
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [True, False])
async def test_ack_without_membership_is_uncertain_and_retired(ack: bool) -> None:
    """Acknowledgement alone cannot prove relation presence or grant retry."""
    writes, _client, album, _asset, source, target = setup_add()
    with (
        patch.object(album, "contains_photo_scoped", return_value=False),
        patch.object(album, "add_photo_correlated", return_value=ack) as mutate,
    ):
        preview = await writes.prepare_add(source, target, {})
        writes.ledger.approve_from_operator(preview.request_id, preview.digest)
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)
        mutate.assert_called_once()


@pytest.mark.asyncio
async def test_present_noop_consumes_and_inventory_is_bounded() -> None:
    """Already-present relations require receipt but no mutation."""
    writes, _client, album, _asset, source, target = setup_add()
    with (
        patch.object(album, "contains_photo_scoped", return_value=True),
        patch.object(album, "add_photo_correlated") as mutate,
    ):
        writes._relations = {str(index): ("A", "P", "M", "Z") for index in range(256)}
        preview = await writes.prepare_add(source, target, {})
        assert len(writes._relations) == 256
        writes.ledger.approve_from_operator(preview.request_id, preview.digest)
        assert not (await writes.apply(preview.request_id))["changed"]
        mutate.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_payload_revision_source_and_disabled_service() -> None:
    """No untyped source, guessed revision or disabled-service access."""
    writes, _client, _album, asset, source, target = setup_add()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare_add(source, target, {"extra": True})
    invalid = writes.additional.remember("photo-asset", object())
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await writes.prepare_add(invalid, target, {})
    asset.asset_record.recordChangeTag = "null"
    with pytest.raises(WriteDenied, match="revision_unavailable"):
        await writes.prepare_add(source, target, {})
    disabled = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("mail",))))
    with pytest.raises(WriteDenied, match="write_disabled"):
        await PhotoRelations(PhotoAlbumWrites(disabled, writes.ledger)).prepare_add(
            source, target, {}
        )
