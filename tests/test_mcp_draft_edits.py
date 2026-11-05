"""Synthetic exact edits retire source handles before any version-changing save."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.draft_edits import DraftEdits
from pyicloud.mcp.mail_writes import MailWrites, canonical_draft_html
from tests.test_mcp_draft_revalidation import saved_reader


async def setup_edit() -> tuple[DraftEdits, Any, str, dict[str, Any]]:
    """Only a verified synthetic create can provide the saved source context."""
    reader, client, handle = await saved_reader()
    writer = MailWrites(
        reader.reads, ApprovalLedger(frozenset({"mail.edit_draft"})), reader.additional
    )
    return (
        DraftEdits(writer),
        client,
        handle,
        {"subject": "After", "text": "Changed", "to": [], "cc": [], "bcc": []},
    )


@pytest.mark.asyncio
async def test_edit_receipt_retires_old_handle_and_verifies_replacement() -> None:
    """One save, rotated identity, old absence and exact fresh new-body proof."""
    edits, client, handle, payload = await setup_edit()
    publish = MagicMock()
    preview = await edits.prepare_edit(handle, payload, publish)
    publish.assert_called_once_with(preview)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await edits.apply(preview.request_id)
    client.mail.edit_draft.assert_not_called()
    old = edits.additional.resolve("mail-draft", handle).draft

    def edit(_draft: Any, **kwargs: Any) -> Any:
        with pytest.raises(ToolFailure):
            edits.additional.resolve("mail-draft", handle)
        assert kwargs["html"] == ""
        assert kwargs["attachments"] == ()
        message = old.message.model_copy(
            update={"id": "draft-102", "uid": 102, "subject": kwargs["subject"]}
        )
        saved = old.model_copy(update={"message": message})
        client.mail.body.return_value = client.mail.body.return_value.model_copy(
            update={
                "guid": "message:Drafts/102",
                "parts": [
                    client.mail.body.return_value.parts[0].model_copy(
                        update={
                            "guid": "messagepart:Drafts/102-1",
                            "content": canonical_draft_html(kwargs["text"]),
                        }
                    )
                ],
            }
        )
        client.mail.check_draft.return_value = message
        return saved

    client.mail.edit_draft.side_effect = edit
    client.mail.message_by_uid.return_value = None

    def accept(identifier: str) -> None:
        edits.ledger.approve_from_operator(identifier, preview.digest)

    result = await edits.apply(preview.request_id, accept)
    assert result["id"] == "draft-102"
    replacement = edits.additional.resolve("mail-draft", result["handle"])
    assert replacement.payload["text"] == payload["text"]
    assert edits.reader.read(client, result["handle"]).message.uid == 102
    with pytest.raises(ToolFailure):
        edits.additional.resolve("mail-draft", handle)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await edits.apply(preview.request_id)
    client.mail.edit_draft.assert_called_once()
    client.mail.send_draft.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["body", "old_present", "same_uid", "transport"])
async def test_uncertain_edit_has_no_retry_request_or_source_handle(case: str) -> None:
    """A typed acknowledgement or timeout never re-enables a consumed source."""
    edits, client, handle, payload = await setup_edit()
    preview = await edits.prepare_edit(handle, payload)
    edits.ledger.approve_from_operator(preview.request_id, preview.digest)
    old = edits.additional.resolve("mail-draft", handle).draft
    message = old.message.model_copy(
        update={
            "id": "draft-102",
            "uid": 101 if case == "same_uid" else 102,
            "subject": payload["subject"],
        }
    )
    client.mail.edit_draft.return_value = old.model_copy(update={"message": message})
    client.mail.message_by_uid.return_value = (
        old.message if case == "old_present" else None
    )
    if case == "transport":
        client.mail.edit_draft.side_effect = OSError("private")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await edits.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await edits.apply(preview.request_id)
    with pytest.raises(ToolFailure):
        edits.additional.resolve("mail-draft", handle)
    client.mail.edit_draft.assert_called_once()


@pytest.mark.asyncio
async def test_stale_context_prevents_dispatch_without_source_retirement() -> None:
    """Pre-dispatch refusal does not claim a mutation happened."""
    edits, client, handle, payload = await setup_edit()
    preview = await edits.prepare_edit(handle, payload)
    edits.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.params["dsid"] = "456"
    with pytest.raises(WriteDenied, match="stale_saved_context"):
        await edits.apply(preview.request_id)
    assert edits.additional.resolve("mail-draft", handle)
    client.mail.edit_draft.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_input_content_guard_and_bounded_inventory() -> None:
    """No implicit sender/attachment changes or unbounded edit selectors."""
    edits, client, handle, payload = await setup_edit()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await edits.prepare_edit(handle, {**payload, "sender": "other@example.test"})
    edits._edits = {str(index): handle for index in range(256)}
    await edits.prepare_edit(handle, payload)
    assert len(edits._edits) == 256
    invalid = edits.additional.remember("mail-draft", object())
    with pytest.raises(WriteDenied, match="unsupported_target"):
        await edits.prepare_edit(invalid, payload)
    owner = edits.writer.reads.owner
    owner.config = type(owner.config)(
        username="synthetic", session_dir="unused", services=("mail",)
    )
    with pytest.raises(WriteDenied, match="content_disabled"):
        await edits.prepare_edit(handle, payload)
    client.mail.edit_draft.assert_not_called()
