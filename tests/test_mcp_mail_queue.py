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
from tests.test_mcp_mail_folder_renames import setup_rename
from tests.test_mcp_mail_folders import setup_folders


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["edit", "send", "move", "delete", "create_folder", "rename_folder"]
)
async def test_queued_eviction_preserves_retirement_and_fixed_error(kind: str) -> None:
    """Capture before queuing, evict while queued, then dispatch exactly once."""
    writes: Any
    resource: str | None
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
    elif kind == "delete":
        writes, client, handle = setup_delete()
        preview = await writes.prepare_delete(handle, {})
        mapping, resource, method = (
            "_requests",
            "mail-message",
            client.mail.delete_permanently,
        )
    elif kind == "create_folder":
        writes, client = setup_folders()
        handle = ""
        preview = await writes.prepare({"name": "New"})
        mapping, resource, method = "_names", None, client.mail.create_folder
    else:
        writes, client, handle, _original = setup_rename()
        preview = await writes.prepare_rename(handle, {"name": "New"})
        mapping, resource, method = "_renames", None, client.mail.rename_folder
    writes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=preview.risk == "high"
    )

    async def queued_run(operation: Callable[[Any], Any]) -> Any:
        getattr(writes, mapping).pop(preview.request_id)
        return operation(client)

    owner = (
        writes.reads.owner if kind == "create_folder" else writes.additional.reads.owner
    )
    cast(Any, owner).run = queued_run
    method.side_effect = RuntimeError("private")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    if resource is not None:
        with pytest.raises(ToolFailure):
            writes.additional.resolve(resource, handle)
    method.assert_called_once()


@pytest.mark.parametrize("deletion", [False, True])
def test_message_count_is_not_message_identity_or_revision(deletion: bool) -> None:
    """Folder statistics may change without changing any approved message fields."""
    writes: Any
    arguments: tuple[Any, ...]
    if deletion:
        writes, client, handle = setup_delete()
        arguments = (client, handle)
    else:
        writes, client, handle, _destination = setup_action()
        arguments = (client, handle, None)
    read = writes._read  # pylint: disable=protected-access
    before = read(*arguments)[-1]
    message = client.mail.message_by_uid.return_value
    client.mail.message_by_uid.return_value = message.model_copy(
        update={"folder": message.folder.model_copy(update={"message_count": 50})}
    )
    assert read(*arguments)[-1] == before
    client.mail.message_by_uid.return_value = message.model_copy(
        update={"folder": message.folder.model_copy(update={"uid_validity": 99})}
    )
    with pytest.raises(WriteDenied, match="stale_target"):
        read(*arguments)
