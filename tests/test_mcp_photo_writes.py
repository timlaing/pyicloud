"""Synthetic private Photos writes; no account access or live CloudKit calls."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKRecord
from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_writes import PhotoWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.photos.service import PhotoAsset
from tests.test_mcp_additional import owner_for


def setup_photos() -> tuple[PhotoWrites, MagicMock, PhotoAsset, str]:
    """Typed record handles with a fake fresh-read source."""
    client = MagicMock()
    client.params = {"dsid": "A"}
    client.photos.service_endpoint = "https://example.test"
    master = CKRecord.model_validate({
        "recordName": "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    record = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "recordChangeTag": "R1",
        "zoneID": {"zoneName": "Z"},
        "fields": {"isFavorite": {"value": 0, "type": "INT64"}},
    })
    asset = PhotoAsset(client.photos, master, record)
    library = MagicMock()
    library.scope = "private"
    library.zone_id = {"zoneName": "Z"}
    library.all.get.return_value = asset
    client.photos.libraries = {"Z": library}
    additional = AdditionalReads(ReadTools(owner_for(client)))
    handle = additional._store("photo-asset", asset)
    return (
        PhotoWrites(additional, ApprovalLedger(frozenset({"photos.set_favorite"}))),
        client,
        asset,
        handle,
    )


@pytest.mark.asyncio
async def test_favorite_requires_approval_then_independent_read() -> None:
    """No generic OK acknowledgement alone proves a state change."""
    writes, client, asset, handle = setup_photos()
    publish = MagicMock()
    preview = await writes.prepare(handle, {"favorite": True}, publish)
    publish.assert_called_once_with(preview)
    with patch.object(asset, "set_favorite") as mutate:
        with pytest.raises(WriteDenied, match="operator_approval_required"):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()
        updated = asset.asset_record.model_dump()
        updated["fields"]["isFavorite"]["value"] = 1
        updated["recordChangeTag"] = "R2"
        verified = PhotoAsset(
            client.photos, asset.master_record, CKRecord.model_validate(updated)
        )

        def mutation(_value: bool) -> bool:
            client.photos.libraries["Z"].all.get.return_value = verified
            return True

        mutate.side_effect = mutation

        def accept(identifier: str) -> None:
            writes.ledger.approve_from_operator(identifier, preview.digest)

        assert (await writes.apply(preview.request_id, accept))["changed"]
        mutate.assert_called_once_with(True)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_noop_consumes_approval_without_mutation() -> None:
    """Even an already-correct state consumes approval."""
    writes, _client, asset, handle = setup_photos()
    preview = await writes.prepare(handle, {"favorite": False})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    with patch.object(asset, "set_favorite") as mutate:
        assert not (await writes.apply(preview.request_id))["changed"]
        mutate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["account", "route", "revision", "shared", "missing", "invalid_state"]
)
async def test_stale_private_scope_never_dispatches(case: str) -> None:
    """Reject stale or unsupported scope before mutation."""
    writes, client, asset, handle = setup_photos()
    preview = await writes.prepare(handle, {"favorite": True})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "account":
        client.params["dsid"] = "B"
    elif case == "route":
        client.photos.service_endpoint = "https://other.test"
    elif case == "shared":
        client.photos.libraries["Z"].scope = "shared-library"
    elif case == "missing":
        client.photos.libraries["Z"].all.get.return_value = None
    else:
        record = asset.asset_record.model_dump()
        if case == "revision":
            record["recordChangeTag"] = "R2"
        else:
            record["fields"]["isFavorite"]["value"] = 2
        client.photos.libraries["Z"].all.get.return_value = PhotoAsset(
            client.photos, asset.master_record, CKRecord.model_validate(record)
        )
    with patch.object(asset, "set_favorite") as mutate:
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
        mutate.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_ack_is_uncertain_and_retired() -> None:
    """Never reuse approval after ambiguous dispatch."""
    writes, _client, asset, handle = setup_photos()
    preview = await writes.prepare(handle, {"favorite": True})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    with patch.object(asset, "set_favorite", return_value=True) as mutate:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        with pytest.raises(WriteDenied, match="unknown_or_consumed"):
            await writes.apply(preview.request_id)
        mutate.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, {"favorite": 1}, {"favorite": True, "upload": True}]
)
async def test_invalid_payload_before_read(payload: dict[str, Any]) -> None:
    """Only an exact boolean is accepted."""
    writes, client, _asset, handle = setup_photos()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare(handle, payload)
    client.photos.libraries["Z"].all.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["owner", "account", "revision", "type"])
async def test_unavailable_revision_or_target_refused(case: str) -> None:
    """Never guess a missing revision or permit shared-owner scope."""
    writes, client, asset, handle = setup_photos()
    if case == "account":
        client.params = {}
    elif case == "type":
        handle = writes.additional._store("photo-asset", object())
    else:
        record = asset.asset_record.model_dump()
        if case == "owner":
            record["zoneID"]["ownerRecordName"] = "SHARED"
        else:
            record["recordChangeTag"] = None
        changed = PhotoAsset(
            client.photos, asset.master_record, CKRecord.model_validate(record)
        )
        client.photos.libraries["Z"].all.get.return_value = changed
        handle = writes.additional._store("photo-asset", changed)
    with pytest.raises(WriteDenied):
        await writes.prepare(handle, {"favorite": True})


@pytest.mark.asyncio
async def test_bounded_target_inventory_and_disabled_service() -> None:
    """Do not retain unbounded targets; disabled services fail before discovery."""
    writes, _client, _asset, handle = setup_photos()
    writes._targets = {str(index): ("P", "M", "Z") for index in range(256)}
    await writes.prepare(handle, {"favorite": True})
    assert len(writes._targets) == 256
    assert "0" not in writes._targets
    disabled = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("mail",))))
    disabled_writer = PhotoWrites(disabled, writes.ledger)
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled_writer.prepare(handle, {"favorite": True})
