"""Elevated send tests execute fake callbacks only, never real mail submissions."""

from dataclasses import replace
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.draft_sends import DraftSends
from pyicloud.mcp.mail_writes import MailWrites
from pyicloud.mcp.saved_drafts import SavedDraftContext
from pyicloud.services.mail.models import MailSendUpdate
from tests.test_mcp_draft_revalidation import saved_reader


async def setup_send() -> tuple[DraftSends, Any, str]:
    """Retain verified context and explicit roles in an entirely synthetic account."""
    reader, client, handle = await saved_reader()
    context = reader.additional.resolve("mail-draft", handle)
    payload = context.payload
    payload["to"] = ["recipient@example.test"]
    # A verified addressed context, never a guess from metadata-only identifiers.

    draft = context.draft.model_copy(update={"to": ("recipient@example.test",)})
    addressed = SavedDraftContext(
        draft,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        context.account_binding,
        context.resource_binding,
        context.revision,
    )
    handle = reader.additional.remember("mail-draft", addressed)
    client.mail.check_draft.return_value = draft.message
    client.mail.body.return_value = client.mail.body.return_value.model_copy(
        update={"headers": "From: user@example.test\r\nTo: recipient@example.test\r\n"}
    )
    ledger = ApprovalLedger(
        frozenset({"mail.send_draft"}), high_risk_actions=frozenset({"mail.send_draft"})
    )
    return (
        DraftSends(MailWrites(reader.reads, ledger, reader.additional)),
        client,
        handle,
    )


@pytest.mark.asyncio
async def test_elevated_send_retires_source_without_delivery_claim() -> None:
    """Exact roles/body are approved; source is retired before the one fake send."""
    sends, client, handle = await setup_send()
    publish = MagicMock()
    preview = await sends.prepare_send(handle, {}, publish)
    publish.assert_called_once_with(preview)
    assert preview.risk == "high"
    assert preview.payload["to"] == ["recipient@example.test"]
    with pytest.raises(WriteDenied, match="high_risk_operator_approval_required"):
        sends.ledger.approve_from_operator(preview.request_id, preview.digest)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await sends.apply(preview.request_id)
    client.mail.send_draft.assert_not_called()

    def send(draft: Any, *, confirm: bool) -> MailSendUpdate:
        assert confirm is True
        with pytest.raises(ToolFailure):
            sends.additional.resolve("mail-draft", handle)
        return MailSendUpdate(
            source_message_id=draft.message.id,
            source_folder_id=draft.message.folder.id,
            source_uid=draft.message.uid,
        )

    client.mail.send_draft.side_effect = send
    client.mail.message_by_uid.return_value = None

    def accept(identifier: str) -> None:
        sends.ledger.approve_from_operator(identifier, preview.digest, high_risk=True)

    assert await sends.apply(preview.request_id, accept) == {
        "action": "mail.send_draft",
        "acknowledged": True,
        "delivery_confirmed": False,
    }
    client.mail.send_draft.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await sends.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["transport", "ack", "present", "route"])
async def test_uncertain_send_has_no_source_or_request_retry(case: str) -> None:
    """An ambiguous send may already have sent mail; never blindly retry."""
    sends, client, handle = await setup_send()
    preview = await sends.prepare_send(handle, {})
    sends.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    draft = sends.additional.resolve("mail-draft", handle).draft

    def send(_draft: Any, **_kwargs: Any) -> Any:
        if case == "transport":
            raise OSError("private")
        if case == "route":
            client.mail.service_root = "https://other.test"
        return (
            None
            if case == "ack"
            else MailSendUpdate(
                source_message_id=draft.message.id,
                source_folder_id=draft.message.folder.id,
                source_uid=draft.message.uid,
            )
        )

    client.mail.send_draft.side_effect = send
    client.mail.message_by_uid.return_value = (
        draft.message if case == "present" else None
    )
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await sends.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await sends.apply(preview.request_id)
    with pytest.raises(ToolFailure):
        sends.additional.resolve("mail-draft", handle)
    client.mail.send_draft.assert_called_once()


@pytest.mark.asyncio
async def test_send_permission_payload_and_queue_guards() -> None:
    """Caller confirm flags cannot grant elevated permission or alter saved roles."""
    sends, client, handle = await setup_send()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await sends.prepare_send(handle, {"confirm": True})
    blocked = DraftSends(
        MailWrites(
            sends.writer.reads,
            ApprovalLedger(frozenset({"mail.send_draft"})),
            sends.additional,
        )
    )
    with pytest.raises(WriteDenied, match="high_risk_disabled"):
        await blocked.prepare_send(handle, {})
    # pylint: disable=protected-access
    sends._sends = {str(index): handle for index in range(256)}
    await sends.prepare_send(handle, {})
    assert len(sends._sends) == 256
    client.mail.send_draft.assert_not_called()


@pytest.mark.asyncio
async def test_empty_recipient_context_never_previews_send() -> None:
    """No sender/recipient inference or empty-envelope mutation."""
    reader, client, handle = await saved_reader()
    ledger = ApprovalLedger(
        frozenset({"mail.send_draft"}), high_risk_actions=frozenset({"mail.send_draft"})
    )
    with pytest.raises(WriteDenied, match="recipients_required"):
        await DraftSends(
            MailWrites(reader.reads, ledger, reader.additional)
        ).prepare_send(handle, {})
    client.mail.send_draft.assert_not_called()


@pytest.mark.asyncio
async def test_send_refuses_any_downgraded_preview_or_consumed_tier() -> None:
    """Policy/ledger integration mistakes must not dispatch a standard-tier send."""
    sends, client, handle = await setup_send()
    preview = await sends.prepare_send(handle, {})
    with (
        patch.object(
            sends.ledger, "preview", return_value=replace(preview, risk="standard")
        ),
        pytest.raises(WriteDenied, match="high_risk_operator_approval_required"),
    ):
        await sends.prepare_send(handle, {})
    with (
        patch.object(
            sends.ledger, "consume", return_value=replace(preview, risk="standard")
        ),
        pytest.raises(WriteDenied, match="high_risk_operator_approval_required"),
    ):
        await sends.apply(preview.request_id)
    assert sends.additional.resolve("mail-draft", handle)
    client.mail.send_draft.assert_not_called()
