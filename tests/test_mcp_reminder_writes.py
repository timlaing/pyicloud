"""Synthetic Reminders write callbacks never contact accounts or mutate real data."""

# pylint: disable=protected-access

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ServerConfig, SessionOwner, ToolFailure
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.reminder_writes import REMINDER_ACTIONS, ReminderWrites
from pyicloud.services.reminders.models import Reminder, RemindersList


def writer() -> tuple[ReminderWrites, MagicMock, RemindersList, Reminder]:
    """All account, owner and revision values are synthetic."""
    context = RemindersList(
        id="List/SAME",
        title="Shared",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
        record_change_tag="L1",
    )
    reminder = Reminder(
        id="R",
        list_id=context.id,
        title="Before",
        scope=context.scope,
        zone_name=context.zone_name,
        zone_owner=context.zone_owner,
        record_change_tag="R1",
    )
    client = MagicMock()
    client.params = {"dsid": "A"}
    client.reminders.service_root = "https://reminders.example.test"
    client.reminders.lists.side_effect = lambda: iter([context])
    client.reminders.get.return_value = reminder
    client.reminders.create.return_value = reminder

    async def run(operation: Callable[[Any], Any]) -> Any:
        return operation(client)

    owner = cast(
        SessionOwner,
        SimpleNamespace(
            config=ServerConfig(
                username="synthetic", session_dir="unused", services=("reminders",)
            ),
            run=run,
            close=AsyncMock(),
            authenticated=True,
        ),
    )
    reads = ReadTools(owner)
    reads._routes["H"] = (context, 10**20)
    return (
        ReminderWrites(reads, ApprovalLedger(REMINDER_ACTIONS)),
        client,
        context,
        reminder,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_prepare_is_read_only_and_approved_dispatch_is_single_use(
    action: str,
) -> None:
    """One trusted decision allows exactly one precise scoped operation."""
    writes, client, context, reminder = writer()
    payload = (
        {"title": "After", "description": "Body", "completed": True}
        if action != "delete"
        else {}
    )
    item = await writes.prepare(
        "reminders." + action, "H", payload, None if action == "create" else "R"
    )
    for method in (
        client.reminders.create,
        client.reminders.update,
        client.reminders.delete,
    ):
        method.assert_not_called()
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(item.request_id)
    writes.approvals.approve_from_operator(item.request_id, item.digest)
    result = await writes.apply(item.request_id)
    assert result["id"] == reminder.id
    if action == "create":
        client.reminders.create.assert_called_once()
        assert client.reminders.create.call_args.args[0] is context
        assert client.reminders.create.call_args.args[1] == "After"
        assert client.reminders.create.call_args.kwargs == {
            "desc": "Body",
            "completed": True,
        }
    elif action == "update":
        client.reminders.update.assert_called_once_with(reminder)
        assert (reminder.title, reminder.desc, reminder.completed) == (
            "After",
            "Body",
            True,
        )
    else:
        client.reminders.delete.assert_called_once_with(reminder)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(item.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "account", "route", "deleted"])
async def test_stale_scope_or_revision_never_dispatches(change: str) -> None:
    """Same record IDs in another zone are not interchangeable write targets."""
    writes, client, context, reminder = writer()
    item = await writes.prepare("reminders.update", "H", {"title": "After"}, "R")
    writes.approvals.approve_from_operator(item.request_id, item.digest)
    if change == "revision":
        reminder.record_change_tag = "R2"
    elif change == "account":
        client.params["dsid"] = "B"
    elif change == "route":
        context.zone_owner = "OTHER"
    else:
        reminder.deleted = True
    with pytest.raises(WriteDenied):
        await writes.apply(item.request_id)
    client.reminders.update.assert_not_called()


@pytest.mark.asyncio
async def test_uncertain_acknowledgement_is_sanitized_and_not_replayed() -> None:
    """Approval is retired before a failed network acknowledgement."""
    writes, client, _context, _reminder = writer()
    item = await writes.prepare("reminders.delete", "H", {}, "R")
    writes.approvals.approve_from_operator(item.request_id, item.digest)
    client.reminders.delete.side_effect = RuntimeError("private payload")
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(item.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(item.request_id)
    client.reminders.delete.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,payload,identifier",
    [
        ("notes.create", {}, None),
        ("reminders.create", {}, None),
        ("reminders.update", {}, "R"),
        ("reminders.update", {"unsupported": True}, "R"),
        ("reminders.update", {"completed": "yes"}, "R"),
        ("reminders.update", {"title": None}, "R"),
        ("reminders.update", {"title": "\ud800"}, "R"),
        ("reminders.create", {"title": "x"}, "R"),
    ],
)
async def test_invalid_actions_and_payloads_do_not_mutate(
    action: str, payload: dict[str, Any], identifier: str | None
) -> None:
    """The adapter is a narrow API, never an arbitrary setattr or method dispatcher."""
    writes, client, _context, _reminder = writer()
    with pytest.raises(WriteDenied):
        await writes.prepare(action, "H", payload, identifier)
    client.reminders.create.assert_not_called()
    client.reminders.update.assert_not_called()
    client.reminders.delete.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_lists_do_not_consume_the_fresh_lookup_window() -> None:
    """A live discovered list remains writable behind many deleted records."""
    writes, client, context, _reminder = writer()
    deleted = context.model_copy(update={"deleted": True})
    client.reminders.lists.side_effect = lambda: iter([deleted] * 1001 + [context])
    preview = await writes.prepare("reminders.create", "H", {"title": "After"})
    assert preview.action == "reminders.create"
    client.reminders.create.assert_not_called()


@pytest.mark.asyncio
async def test_missing_target_account_and_revision_fail_closed() -> None:
    """A preview cannot invent missing state or authorize an unverified target."""
    writes, client, context, reminder = writer()
    client.reminders.lists.side_effect = lambda: iter([])
    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writes.prepare("reminders.create", "H", {"title": "After"})
    client.reminders.lists.side_effect = lambda: iter([context])
    client.params = {}
    with pytest.raises(WriteDenied, match="account_unavailable"):
        await writes.prepare("reminders.update", "H", {"title": "After"}, "R")
    client.params = {"dsid": "A"}
    reminder.record_change_tag = None
    with pytest.raises(WriteDenied, match="revision_required"):
        await writes.prepare("reminders.update", "H", {"title": "After"}, "R")
    with pytest.raises(ToolFailure, match="expired_or_unknown_handle"):
        await writes.prepare("reminders.create", "unknown", {"title": "After"})
