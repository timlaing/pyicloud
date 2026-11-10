"""Synthetic exact saved-draft state; no edit/send/discard or account access."""

from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import ServerConfig
from pyicloud.mcp.draft_revalidation import DraftRevalidation
from tests.test_mcp_mail_writes import setup_mail


async def saved_reader() -> tuple[DraftRevalidation, Any, str]:
    """Only fake approved create establishes the saved typed context."""
    writer, client, payload = setup_mail()
    preview = await writer.prepare(payload)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    result = await writer.apply(preview.request_id)
    client.mail.check_draft.return_value = client.mail.create_draft.return_value.message
    owner = cast(Any, writer.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("mail",),
        content_services=("mail",),
    )
    return DraftRevalidation(writer), client, result["handle"]


@pytest.mark.asyncio
async def test_exact_saved_state_has_private_body_revision_without_mutation() -> None:
    """No new approval or mutable payload is inferred from fresh account content."""
    reader, client, handle = await saved_reader()
    state = reader.read(client, handle)
    assert state.message.id == "draft-101"
    assert state.context.payload["text"] == "Approved"
    assert state.account_binding == "123"
    assert "Approved" not in repr(state)
    assert "From:" not in state.revision
    assert "Approved" not in state.revision
    assert not hasattr(reader, "apply")
    client.mail.edit_draft.assert_not_called()
    client.mail.send_draft.assert_not_called()
    client.mail.discard_draft.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["account", "route", "folder", "sender", "subject", "uid", "body", "from_header"],
)
async def test_external_saved_state_changes_prevent_replacement(case: str) -> None:
    """Exact old content/context must be revalidated, never silently overwritten."""
    reader, client, handle = await saved_reader()
    if case == "account":
        client.params["dsid"] = "456"
    elif case == "route":
        client.mail.service_root = "https://other.test"
    elif case == "folder":
        client.mail.draft_folder.return_value = (
            client.mail.draft_folder.return_value.model_copy(update={"uid_validity": 2})
        )
    elif case == "sender":
        client.mail.senders.return_value = [
            client.mail.senders.return_value[0].model_copy(
                update={"display_name": "Changed"}
            )
        ]
    elif case in {"subject", "uid"}:
        client.mail.check_draft.return_value = (
            client.mail.check_draft.return_value.model_copy(
                update={case: "Changed" if case == "subject" else 102}
            )
        )
    elif case == "body":
        client.mail.body.return_value = client.mail.body.return_value.model_copy(
            update={"parts": []}
        )
    else:
        client.mail.body.return_value = client.mail.body.return_value.model_copy(
            update={"headers": "From: other@example.test\r\n"}
        )
    with pytest.raises(WriteDenied):
        reader.read(client, handle)
    client.mail.edit_draft.assert_not_called()


@pytest.mark.asyncio
async def test_content_and_service_opt_ins_precede_all_revalidation_reads() -> None:
    """Creating a draft does not grant consent to fresh saved-body reads."""
    reader, client, handle = await saved_reader()
    owner = cast(Any, reader.reads.owner)
    for services in (("mail",), ("photos",)):
        owner.config = ServerConfig(
            username="synthetic", session_dir="unused", services=services
        )
        client.mail.check_draft.reset_mock()
        with pytest.raises(WriteDenied):
            reader.read(client, handle)
        client.mail.check_draft.assert_not_called()


@pytest.mark.asyncio
async def test_untyped_context_is_not_reconstructed_from_message_metadata() -> None:
    """Only server-created verified contexts may reach preflight."""
    reader, client, _handle = await saved_reader()
    invalid = reader.additional.remember("mail-draft", MagicMock())
    with pytest.raises(WriteDenied, match="unsupported_target"):
        reader.read(client, invalid)
    client.mail.check_draft.assert_not_called()


@pytest.mark.asyncio
async def test_retained_version_context_is_not_implicitly_editable() -> None:
    """The first adapter foundation does not infer destructive version handling."""
    reader, client, handle = await saved_reader()
    context = reader.additional.resolve("mail-draft", handle)
    draft = context.draft.model_copy(
        update={"retained_drafts": (context.draft.message,)}
    )
    unsupported = reader.additional.remember(
        "mail-draft", replace(context, draft=draft)
    )
    with pytest.raises(WriteDenied, match="unsupported_target"):
        reader.read(client, unsupported)
    client.mail.check_draft.assert_not_called()
