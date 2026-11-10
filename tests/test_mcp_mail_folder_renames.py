"""Synthetic exact-ID custom mailbox rename, never account or endpoint access."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.mail_folder_renames import MailFolderRenames
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.mail.models import MailFolder
from tests.test_mcp_additional import owner_for
from tests.test_mcp_mail_folders import setup_folders


def setup_rename() -> tuple[MailFolderRenames, Any, str, MailFolder]:
    """A discovered custom folder with one fixed incarnation."""
    folders, client = setup_folders()
    original = MailFolder(identifier="C", name="Custom", uidValidity=2)
    client.mail.folders.return_value.append(original)
    additional = AdditionalReads(folders.reads)
    handle = additional.remember("mail-folder", original)
    return (
        MailFolderRenames(
            additional, ApprovalLedger(frozenset({"mail.rename_folder"}))
        ),
        client,
        handle,
        original,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message_count", [None, 2])
async def test_exact_rename_requires_receipt_then_same_identity_proof(
    message_count: int | None,
) -> None:
    """Only a fresh matching renamed identity proves success after one dispatch."""
    writes, client, handle, original = setup_rename()
    publish = MagicMock()
    preview = await writes.prepare_rename(handle, {"name": "Renamed"}, publish)
    publish.assert_called_once_with(preview)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    client.mail.rename_folder.assert_not_called()

    def rename(target: MailFolder, name: str) -> MailFolder:
        assert target == original
        result = original.model_copy(update={"name": name})
        observed = result.model_copy(update={"message_count": message_count})
        client.mail.folders.return_value = [
            client.mail.folders.return_value[0],
            observed,
        ]
        return result

    client.mail.rename_folder.side_effect = rename

    def accept(identifier: str) -> None:
        writes.ledger.approve_from_operator(identifier, preview.digest)

    assert await writes.apply(preview.request_id, accept) == {
        "action": "mail.rename_folder",
        "id": "C",
    }
    client.mail.rename_folder.assert_called_once_with(original, "Renamed")
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["identity", "child", "collision", "route", "snapshot"]
)
async def test_fresh_namespace_prevents_unsafe_dispatch(case: str) -> None:
    """No stale UID, descendant tree mutation, overwrite or stale endpoint."""
    writes, client, handle, original = setup_rename()
    preview = await writes.prepare_rename(handle, {"name": "Renamed"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "identity":
        client.mail.folders.return_value[1] = original.model_copy(
            update={"uid_validity": 3}
        )
    elif case == "child":
        client.mail.folders.return_value.append(
            MailFolder(identifier="CH", name="Custom/Child")
        )
    elif case == "collision":
        client.mail.folders.return_value.append(
            MailFolder(identifier="OTHER", name="renamed")
        )
    elif case == "route":
        client.mail.service_root = "https://other.test"
    else:
        client.mail.folders.return_value.append(
            MailFolder(identifier="OTHER", name="Unrelated")
        )
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.mail.rename_folder.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_rename_never_replays() -> None:
    """Typed acknowledgement without new namespace state is uncertain."""
    writes, client, handle, original = setup_rename()
    preview = await writes.prepare_rename(handle, {"name": "Renamed"})
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.mail.rename_folder.return_value = original.model_copy(
        update={"name": "Renamed"}
    )
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.mail.rename_folder.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["system", "hierarchy", "missing_validity", "unchanged"]
)
async def test_unsupported_source_before_discovery(case: str) -> None:
    """Conservative top-level custom sources only; no unknown incarnation."""
    writes, client, handle, original = setup_rename()
    if case == "system":
        original = original.model_copy(update={"name": "Sent Messages"})
    elif case == "hierarchy":
        original = original.model_copy(update={"name": "Parent/Custom"})
    elif case == "missing_validity":
        original = original.model_copy(update={"uid_validity": None})
    handle = writes.additional.remember("mail-folder", original)
    with pytest.raises(WriteDenied):
        await writes.prepare_rename(
            handle, {"name": "Custom" if case == "unchanged" else "Renamed"}
        )
    client.mail.folders.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_inventory_and_disabled_service() -> None:
    """Do not retain unbounded selectors or discover disabled namespaces."""
    writes, _client, handle, original = setup_rename()
    writes._renames = {str(index): (original, "Old") for index in range(256)}
    await writes.prepare_rename(handle, {"name": "Renamed"})
    assert len(writes._renames) == 256
    disabled = AdditionalReads(ReadTools(owner_for(MagicMock(), services=("drive",))))
    disabled_writer = MailFolderRenames(disabled, writes.ledger)
    with pytest.raises(WriteDenied, match="write_disabled"):
        await disabled_writer.prepare_rename(handle, {"name": "Renamed"})
