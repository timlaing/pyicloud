"""Shared CLI resolution never silently uses the private database after a miss."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud import PyiCloudService
from pyicloud.cli.commands.reminders import _resolve_reminder
from pyicloud.cli.context import CLIAbort
from pyicloud.services.reminders.exceptions import RemindersAuthError
from pyicloud.services.reminders.models import Reminder, RemindersList


def api_for(reminders: MagicMock) -> PyiCloudService:
    """Only synthetic service objects are used, with no auth/network construction."""
    return cast(
        PyiCloudService, SimpleNamespace(reminders=reminders, account_name="Synthetic")
    )


def test_private_cli_lookup_does_not_enumerate_unrelated_lists() -> None:
    """A successful private lookup retains its original single-request behavior."""
    reminders = MagicMock()
    reminder = Reminder(
        id="Reminder/R", list_id="List/L", title="Synthetic", scope="private"
    )
    reminders.get.return_value = reminder
    assert _resolve_reminder(api_for(reminders), reminder.id) is reminder
    reminders.get.assert_called_once_with(reminder.id)
    reminders.lists.assert_not_called()


def shared_lookup(identifier: str, **kwargs: Any) -> Reminder:
    """Only an explicit shared context can return the synthetic shared record."""
    context = kwargs.get("list_context")
    if context is None:
        raise LookupError("Reminder not found")
    return Reminder(
        id=identifier,
        list_id=context.id,
        title="Synthetic",
        scope=context.scope,
        zone_name=context.zone_name,
        zone_owner=context.zone_owner,
    )


def test_shared_cli_lookup_passes_discovered_context() -> None:
    """A private miss is followed by a lookup in the explicit discovered share."""
    reminders = MagicMock()
    context = RemindersList(
        id="List/L",
        title="Shared",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
    )
    reminders.lists.return_value = [context]
    reminders.get.side_effect = shared_lookup
    reminder = _resolve_reminder(api_for(reminders), "Reminder/R")
    assert (reminder.scope, reminder.zone_name, reminder.zone_owner) == (
        "shared",
        "Shared",
        "OWNER",
    )
    assert reminders.get.call_args.kwargs == {"list_context": context}


def test_shared_cli_lookup_rejects_ambiguous_shared_ids() -> None:
    """Several shared records with one ID cannot be chosen silently for mutation."""
    reminders = MagicMock()
    reminders.lists.return_value = [
        RemindersList(id="List/L", title="Shared", scope="shared", zone_owner=owner)
        for owner in ["A", "B"]
    ]
    reminders.get.side_effect = shared_lookup
    with pytest.raises(CLIAbort, match="Ambiguous shared"):
        _resolve_reminder(api_for(reminders), "Reminder/R")


def test_shared_cli_lookup_propagates_auth_failure() -> None:
    """Authentication failures during discovery remain explicit CLI errors."""
    reminders = MagicMock()
    reminders.get.side_effect = LookupError("Missing")
    reminders.lists.side_effect = RemindersAuthError("Authentication")
    with pytest.raises(CLIAbort, match="Authentication"):
        _resolve_reminder(api_for(reminders), "Reminder/R")
