"""Synthetic etag-bound Drive rename proof, never real-account operations."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest
from requests.adapters import HTTPAdapter

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.drive_writes import DriveWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.drive import DriveNode
from tests.test_mcp_additional import owner_for


def setup_writer() -> tuple[DriveWrites, MagicMock, str, dict[str, Any]]:
    """Use actual public DriveNode methods with a fully mocked transport facade."""
    client = MagicMock()
    client.params = {"dsid": "A"}
    client.drive.params = dict(client.params)
    client.drive.session.get_adapter.return_value = HTTPAdapter(max_retries=0)
    client.drive.service_root = "https://drive.example.test"
    data = {
        "drivewsid": "FILE::com.apple.CloudDocs::SYNTHETIC",
        "zone": "com.apple.CloudDocs",
        "type": "FILE",
        "name": "Before",
        "extension": "txt",
        "etag": "v1",
    }
    additional = AdditionalReads(ReadTools(owner_for(client)))
    handle = additional.remember("drive-file", DriveNode(client.drive, dict(data)))
    client.drive.get_node_data.side_effect = lambda _identifier: dict(data)
    return (
        DriveWrites(additional, ApprovalLedger(frozenset({"drive.rename"}))),
        client,
        handle,
        data,
    )


@pytest.mark.asyncio
async def test_rename_requires_operator_approval_and_verifies_post_read() -> None:
    """Never trust an arbitrary returned success envelope or stale etag."""
    writes, client, handle, data = setup_writer()
    preview = await writes.prepare(handle, {"name": "After"})
    client.drive.rename_items.assert_not_called()
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def rename(identifier: str, etag: str, name: str) -> dict[str, Any]:
        assert (identifier, etag, name) == (data["drivewsid"], "v1", "After")
        data.update(name=name, etag="v2")
        return {"ignored": "Not proof of acknowledgement"}

    client.drive.rename_items.side_effect = rename
    result = await writes.apply(preview.request_id)
    assert result == {"action": "drive.rename", "changed": True}
    client.drive.rename_items.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_noop_rename_still_consumes_approval() -> None:
    """Already-correct names do not cause unnecessary network mutations."""
    writes, client, handle, _data = setup_writer()
    preview = await writes.prepare(handle, {"name": "Before"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    assert (await writes.apply(preview.request_id))["changed"] is False
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["etag", "account", "zone", "identifier", "shared", "type"]
)
async def test_changed_bindings_fail_closed(case: str) -> None:
    """Every fresh read must still address the approved private node."""
    writes, client, handle, data = setup_writer()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "account":
        client.params["dsid"] = "OTHER"
    else:
        key, value = {
            "etag": ("etag", "v2"),
            "zone": ("zone", "Other"),
            "identifier": ("drivewsid", "Other"),
            "shared": ("shareID", {"owner": "OTHER"}),
            "type": ("type", "UNKNOWN"),
        }[case]
        data[key] = value
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_or_uncertain_write_cannot_be_replayed() -> None:
    """A mocked OK response without matching post-state is not successful."""
    writes, client, handle, _data = setup_writer()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.rename_items.return_value = {"status": "OK"}
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_invalid_names_unknown_permissions_and_shared_nodes_rejected() -> None:
    """Never accept local paths, unknown targets or unverified shared mutation APIs."""
    writes, client, handle, data = setup_writer()
    for payload in (
        {},
        {"name": "../outside"},
        {"name": ".."},
        {"name": "x" * 256},
        {"name": "x", "extra": True},
    ):
        with pytest.raises(WriteDenied, match="invalid_payload"):
            await writes.prepare(handle, payload)
    client.params = {}
    with pytest.raises(WriteDenied, match="account_unavailable"):
        await writes.prepare(handle, {"name": "After"})
    client.params = {"dsid": "A"}
    shared = DriveNode(client.drive, {**data, "shareID": {"owner": "OTHER"}})
    shared_handle = writes.additional.remember("drive-file", shared)
    with pytest.raises(WriteDenied, match="unsupported_shared_write"):
        await writes.prepare(shared_handle, {"name": "After"})
    writes.additional.reads.owner.config = type(writes.additional.reads.owner.config)()
    with pytest.raises(WriteDenied, match="write_disabled"):
        await writes.prepare(handle, {"name": "After"})


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, "", " ", "null", "NULL", " v1 ", 1, True])
async def test_invalid_or_sentinel_revision_never_publishes(revision: Any) -> None:
    """Deleted-state sentinel etags are not valid mutation revision bindings."""
    writes, client, handle, data = setup_writer()
    data["etag"] = revision
    with pytest.raises(WriteDenied, match="revision_required"):
        await writes.prepare(handle, {"name": "After"})
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["route", "account", "sentinel"])
async def test_rename_post_read_requires_bound_context_and_real_revision(
    case: str,
) -> None:
    """Correct-looking names in unbound post-state are not proof of success."""
    writes, client, handle, data = setup_writer()
    preview = await writes.prepare(handle, {"name": "After"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def rename(_identifier: str, _etag: str, name: str) -> None:
        data.update(name=name, etag="v2")
        if case == "route":
            client.drive.service_root = "https://other.test"
        elif case == "account":
            client.params["dsid"] = "OTHER"
        else:
            data["etag"] = "null"

    client.drive.rename_items.side_effect = rename
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.drive.rename_items.assert_called_once()
