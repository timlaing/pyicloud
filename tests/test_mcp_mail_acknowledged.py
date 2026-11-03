"""Synthetic acknowledged-only flags/moves, never inferred final Mail state."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.mail_acknowledged import MailAcknowledged
from pyicloud.services.mail.models import MailFlagUpdate, MailMoveUpdate
from tests.test_mcp_mail_deletions import setup_delete


def setup_action() -> tuple[MailAcknowledged, Any, str, str]:
    """Reuse typed fake message discovery and independent fake SDK outcomes."""
    deletes, client, handle = setup_delete()
    writer = deletes.writer
    writer.ledger = ApprovalLedger(
        frozenset({"mail.set_read", "mail.set_flagged", "mail.move_message"})
    )
    message = writer.additional.resolve("mail-message", handle)
    folder = message.folder.model_copy(
        update={"id": "destination", "name": "Testing", "uid_validity": 99}
    )
    destination = writer.additional.remember("mail-folder", folder)
    client.mail.check_move.return_value = "123"
    return MailAcknowledged(writer), client, handle, destination


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["mail.set_read", "mail.set_flagged", "mail.move_message"]
)
@pytest.mark.parametrize("case", ["ok", "transport", "ack", "route"])
async def test_exact_once_acknowledgement_never_claims_final_state(
    action: str, case: str
) -> None:
    """Retire source before one call, require exact typed result and deny replay."""
    writes, client, handle, destination = setup_action()
    moving = action == "mail.move_message"
    publish = MagicMock()
    preview = await writes.prepare(
        action,
        handle,
        {} if moving else {"enabled": False},
        destination if moving else None,
        publish,
    )
    publish.assert_called_once_with(preview)
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    message = writes.additional.resolve("mail-message", handle)
    method = (
        client.mail.move
        if moving
        else client.mail.set_read
        if action == "mail.set_read"
        else client.mail.set_flagged
    )
    method.assert_not_called()
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        with pytest.raises(ToolFailure):
            writes.additional.resolve("mail-message", handle)
        if case == "transport":
            raise OSError("private")
        if case == "route":
            client.mail.service_root = "https://other.test"
        if case == "ack":
            return None
        return (
            MailMoveUpdate(
                source_message_id=message.id,
                source_folder_id=message.folder.id,
                destination_folder_id="destination",
            )
            if moving
            else MailFlagUpdate(
                message_id=message.id,
                flag="seen" if action == "mail.set_read" else "flagged",
                enabled=False,
                modseq=101,
            )
        )

    method.side_effect = dispatch
    if case == "ok":
        assert await writes.apply(preview.request_id) == {
            "action": action,
            "acknowledged": True,
            "final_state_verified": False,
        }
    else:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
    method.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "action",
        "payload",
        "destination",
        "type",
        "validity",
        "identity",
        "account",
        "snapshot",
        "binding",
    ],
)
async def test_unsafe_or_stale_context_never_dispatches(case: str) -> None:
    """No masks, untyped selectors or changed approved metadata can be submitted."""
    writes, client, handle, destination = setup_action()
    message = writes.additional.resolve("mail-message", handle)
    if case == "type":
        handle = writes.additional.remember("mail-message", object())
    if case == "validity":
        handle = writes.additional.remember(
            "mail-message",
            message.model_copy(
                update={
                    "folder": message.folder.model_copy(update={"uid_validity": None})
                }
            ),
        )
    if case == "identity":
        client.mail.message_by_uid.return_value = message.model_copy(
            update={"id": "other"}
        )
    if case == "account":
        client.params["dsid"] = None
    if case == "binding":
        client.mail.check_move.return_value = "456"
    if case == "snapshot":
        preview = await writes.prepare("mail.set_read", handle, {"enabled": True})
        writes.ledger.approve_from_operator(preview.request_id, preview.digest)
        client.mail.message_by_uid.return_value = message.model_copy(
            update={"subject": "changed"}
        )
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
    else:
        with pytest.raises(WriteDenied):
            await writes.prepare(
                "unknown"
                if case == "action"
                else "mail.move_message"
                if case in {"destination", "binding"}
                else "mail.set_read",
                handle,
                {"enabled": 1}
                if case == "payload"
                else {}
                if case in {"destination", "binding"}
                else {"enabled": True},
                destination if case == "binding" else None,
            )
    client.mail.move.assert_not_called()
    client.mail.set_read.assert_not_called()
