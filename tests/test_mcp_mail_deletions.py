"""Synthetic single-message deletion; no real accounts or destruction."""

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.mail_deletions import MailDeletions
from pyicloud.services.mail.models import MailMessageDeletion
from tests.test_mcp_mail_writes import setup_mail


def setup_delete() -> tuple[MailDeletions, Any, str]:
    """Use a typed synthetic discovered Trash message, not a caller identifier."""
    writer, client, _payload = setup_mail()
    message = client.mail.create_draft.return_value.message
    folder = message.folder.model_copy(update={"name": "Deleted Messages"})
    message = message.model_copy(update={"folder": folder})
    handle = writer.additional.remember("mail-message", message)
    writer.ledger = ApprovalLedger(
        frozenset({"mail.delete_forever_message"}),
        high_risk_actions=frozenset({"mail.delete_forever_message"}),
    )
    client.mail.check_delete_permanently.return_value = "123"
    client.mail.message_by_uid.return_value = message
    return MailDeletions(writer), client, handle


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["service", "target", "route", "binding", "risk", "capacity"]
)
async def test_prepare_guards_and_bounded_publication(case: str) -> None:
    """Fail closed before mutation and bound the private preview registry."""
    deletes, client, handle = setup_delete()
    publish = MagicMock()
    if case == "service":
        deletes.writer.reads.owner.config = replace(
            deletes.writer.reads.owner.config, services=()
        )
    elif case == "target":
        handle = deletes.additional.remember("mail-message", object())
    elif case == "route":
        client.mail.service_root = None
    elif case == "binding":
        client.mail.check_delete_permanently.return_value = "456"
    elif case == "capacity":
        # Inspect the private bounded registry, never an operator mailbox.
        deletes._requests.update({str(i): handle for i in range(256)})  # pylint: disable=protected-access
        preview = await deletes.prepare_delete(handle, {}, publish)
        assert len(deletes._requests) == 256  # pylint: disable=protected-access
        publish.assert_called_once_with(preview)
        return
    if case == "risk":
        original = deletes.ledger.preview
        with (
            patch.object(
                deletes.ledger,
                "preview",
                side_effect=lambda *a, **k: replace(original(*a, **k), risk="standard"),
            ),
            pytest.raises(WriteDenied),
        ):
            await deletes.prepare_delete(handle, {})
    else:
        with pytest.raises(WriteDenied):
            await deletes.prepare_delete(handle, {})
    client.mail.delete_permanently.assert_not_called()


@pytest.mark.asyncio
async def test_consumed_risk_cannot_be_downgraded() -> None:
    """A corrupted policy result cannot dispatch an irreversible write."""
    deletes, client, handle = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    deletes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    original = deletes.ledger.consume
    with (
        patch.object(
            deletes.ledger,
            "consume",
            side_effect=lambda *a, **k: replace(original(*a, **k), risk="standard"),
        ),
        pytest.raises(WriteDenied, match="high_risk"),
    ):
        await deletes.apply(preview.request_id)
    client.mail.delete_permanently.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["ok", "transport", "ack", "wrong_ack", "uid", "id", "route", "account", "lookup"],
)
async def test_exact_once_retirement_and_independent_absence(case: str) -> None:
    """Ambiguity retires both source and request; both identities are checked."""
    deletes, client, handle = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    assert preview.risk == "high"
    with pytest.raises(WriteDenied):
        await deletes.apply(preview.request_id)
    client.mail.delete_permanently.assert_not_called()
    deletes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def destroy(message: Any, *, confirm: bool) -> Any:
        assert confirm is True
        with pytest.raises(ToolFailure):
            deletes.additional.resolve("mail-message", handle)
        if case == "transport":
            raise OSError("private")
        client.mail.message_by_uid.return_value = message if case == "uid" else None
        client.mail.message.return_value = message if case == "id" else None
        if case == "route":
            client.mail.service_root = "https://other.test"
        if case == "account":
            client.params["dsid"] = "456"
        if case == "lookup":
            client.mail.message.side_effect = OSError("private lookup error")
        return (
            None
            if case == "ack"
            else MailMessageDeletion(
                message_id="OTHER" if case == "wrong_ack" else message.id,
                folder_id=message.folder.id,
                uid=message.uid,
            )
        )

    client.mail.delete_permanently.side_effect = destroy
    if case == "ok":
        assert await deletes.apply(preview.request_id) == {
            "action": "mail.delete_forever_message",
            "permanent": True,
        }
    else:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await deletes.apply(preview.request_id)
    client.mail.delete_permanently.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await deletes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["payload", "target", "account", "identity", "metadata", "route"]
)
async def test_unsafe_or_stale_context_never_dispatches(case: str) -> None:
    """Metadata changes invalidate approval without interpreting flag meanings."""
    deletes, client, handle = setup_delete()
    if case == "payload":
        with pytest.raises(WriteDenied, match="invalid_payload"):
            await deletes.prepare_delete(handle, {"confirm": True})
        return
    preview = await deletes.prepare_delete(handle, {})
    deletes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    if case == "target":
        deletes.additional.retire("mail-message", handle)
    elif case == "account":
        client.params["dsid"] = "not-account"
    elif case == "route":
        client.mail.service_root = "https://other.test"
    else:
        current = client.mail.message_by_uid.return_value
        client.mail.message_by_uid.return_value = current.model_copy(
            update={"id": "OTHER"} if case == "identity" else {"subject": "Changed"}
        )
    with pytest.raises((WriteDenied, ToolFailure)):
        await deletes.apply(preview.request_id)
    client.mail.delete_permanently.assert_not_called()


@pytest.mark.asyncio
async def test_independent_elevated_acceptance_is_required() -> None:
    """Standard approval cannot authorize permanent destruction."""
    deletes, client, handle = setup_delete()
    preview = await deletes.prepare_delete(handle, {})
    with pytest.raises(WriteDenied, match="high_risk"):
        await deletes.apply(
            preview.request_id,
            lambda request: deletes.ledger.approve_from_operator(
                request, preview.digest
            ),
        )
    client.mail.delete_permanently.assert_not_called()
    deletes.additional.resolve("mail-message", handle)
