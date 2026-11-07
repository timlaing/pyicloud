"""Queued mapping eviction must not bypass single-use Mail retirement."""

from collections.abc import Callable
from typing import Any, cast

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import ToolFailure
from tests.test_mcp_draft_edits import setup_edit
from tests.test_mcp_draft_sends import setup_send
from tests.test_mcp_mail_acknowledged import setup_action
from tests.test_mcp_mail_deletions import setup_delete


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["edit", "send", "move", "delete"])
async def test_queued_eviction_preserves_retirement_and_fixed_error(kind: str) -> None:
    """Capture before queuing, evict while queued, then dispatch exactly once."""
    writes: Any
    if kind == "edit":
        writes, client, handle, payload = await setup_edit()
        preview = await writes.prepare_edit(handle, payload)
        mapping, resource, method = "_edits", "mail-draft", client.mail.edit_draft
    elif kind == "send":
        writes, client, handle = await setup_send()
        preview = await writes.prepare_send(handle, {})
        mapping, resource, method = "_sends", "mail-draft", client.mail.send_draft
    elif kind == "move":
        writes, client, handle, destination = setup_action()
        preview = await writes.prepare("mail.move_message", handle, {}, destination)
        mapping, resource, method = "_requests", "mail-message", client.mail.move
    else:
        writes, client, handle = setup_delete()
        preview = await writes.prepare_delete(handle, {})
        mapping, resource, method = (
            "_requests",
            "mail-message",
            client.mail.delete_permanently,
        )
    writes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=preview.risk == "high"
    )

    async def queued_run(operation: Callable[[Any], Any]) -> Any:
        getattr(writes, mapping).pop(preview.request_id)
        return operation(client)

    cast(Any, writes.additional.reads.owner).run = queued_run
    method.side_effect = RuntimeError("private")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    with pytest.raises(ToolFailure):
        writes.additional.resolve(resource, handle)
    method.assert_called_once()
