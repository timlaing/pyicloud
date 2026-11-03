"""Synthetic conservative folder creation; no real Mail namespaces are touched."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.mail_folders import MailFolderWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.mail.models import MailFolder
from tests.test_mcp_additional import owner_for


def setup_folders() -> tuple[MailFolderWrites, Any]:
    """Typed namespace metadata from an entirely fake facade."""
    client = MagicMock()
    client.params = {"dsid": "123"}
    client.mail.service_root = "https://example.test"
    client.mail.folders.return_value = [
        MailFolder(identifier="F", name="Inbox", uidValidity=1)
    ]
    return MailFolderWrites(
        ReadTools(owner_for(client)), ApprovalLedger(frozenset({"mail.create_folder"}))
    ), client


@pytest.mark.asyncio
async def test_verified_creation_requires_receipt_and_one_new_identity() -> None:
    """Acknowledgement plus fresh matching identity, never an unverified name."""
    writes, client = setup_folders()
    publish = MagicMock()
    preview = await writes.prepare({"name": "New Folder"}, publish)
    publish.assert_called_once_with(preview)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    client.mail.create_folder.assert_not_called()

    def create(_name: str) -> MailFolder:
        result = MailFolder(identifier="N", name="New Folder", uidValidity=2)
        client.mail.folders.return_value = [*client.mail.folders.return_value, result]
        return result

    client.mail.create_folder.side_effect = create

    def accept(identifier: str) -> None:
        writes.ledger.approve_from_operator(identifier, preview.digest)

    assert await writes.apply(preview.request_id, accept) == {
        "action": "mail.create_folder",
        "id": "N",
    }
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.mail.create_folder.assert_called_once_with("New Folder")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["namespace", "route", "account", "collision", "duplicate", "missing"]
)
async def test_changed_namespace_never_dispatches(case: str) -> None:
    """Changed snapshots require a new preview, not recycled operator approval."""
    writes, client = setup_folders()
    preview = await writes.prepare({"name": "New"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "namespace":
        client.mail.folders.return_value = []
    elif case == "route":
        client.mail.service_root = "https://other.test"
    elif case == "account":
        client.params["dsid"] = "456"
    elif case == "collision":
        client.mail.folders.return_value.append(MailFolder(identifier="N", name="new"))
    elif case == "duplicate":
        client.mail.folders.return_value *= 2
    else:
        client.mail.service_root = None
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.mail.create_folder.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_creation_is_uncertain_and_replay_denied() -> None:
    """An unverified save is not a failed save that may be blindly repeated."""
    writes, client = setup_folders()
    preview = await writes.prepare({"name": "New"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.mail.create_folder.return_value = MailFolder(identifier="N", name="New")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.mail.create_folder.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "Inbox", "a/b", "&encoded", "é", " x", "x" * 256])
async def test_conservative_names_rejected_before_discovery(name: str) -> None:
    """No system folders, encoded paths or unverified name transformations."""
    writes, client = setup_folders()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare({"name": name})
    client.mail.folders.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_inventory_and_disabled_service() -> None:
    """Keep internal pending selectors bounded and respect service opt-in."""
    writes, _client = setup_folders()
    # pylint: disable=protected-access
    writes._names = {str(index): "Old" for index in range(256)}
    await writes.prepare({"name": "New"})
    assert len(writes._names) == 256
    disabled = MailFolderWrites(
        ReadTools(owner_for(MagicMock(), services=("drive",))), writes.ledger
    )
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled.prepare({"name": "New"})
