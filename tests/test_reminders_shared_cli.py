"""Shared CLI resolution never silently uses the private database after a miss."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud import PyiCloudService
from pyicloud.cli.commands.reminders import _list_reminder_rows, _resolve_reminder
from pyicloud.cli.context import CLIAbort
from pyicloud.services.reminders.exceptions import RemindersApiError, RemindersAuthError
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
    api = api_for(reminders)
    with pytest.raises(CLIAbort, match="Ambiguous shared"):
        _resolve_reminder(api, "Reminder/R")


def test_shared_cli_lookup_propagates_auth_failure() -> None:
    """Authentication failures during discovery remain explicit CLI errors."""
    reminders = MagicMock()
    reminders.get.side_effect = LookupError("Missing")
    reminders.lists.side_effect = RemindersAuthError("Authentication")
    api = api_for(reminders)
    with pytest.raises(CLIAbort, match="Authentication"):
        _resolve_reminder(api, "Reminder/R")


def test_shared_lookup_deduplicates_lists_in_one_zone() -> None:
    """Several lists in one zone must not make one record appear ambiguous."""
    reminders = MagicMock()
    reminders.lists.return_value = [
        RemindersList(id=identifier, title="Shared", scope="shared", zone_owner="A")
        for identifier in ["List/A", "List/B"]
    ]
    reminders.get.side_effect = shared_lookup
    assert _resolve_reminder(api_for(reminders), "Reminder/R").id == "Reminder/R"


@pytest.mark.parametrize("authentication", [False, True])
def test_shared_lookup_zone_errors(authentication: bool) -> None:
    """API errors stay zone-local, but authentication must not be hidden."""
    reminders = MagicMock()
    reminders.lists.return_value = [
        RemindersList(id="List/L", title="Shared", scope="shared", zone_owner=owner)
        for owner in ["BAD", "GOOD"]
    ]
    result = Reminder(id="Reminder/R", list_id="List/L", title="Shared", scope="shared")
    error = (
        RemindersAuthError("Authentication")
        if authentication
        else RemindersApiError("Unavailable")
    )
    reminders.get.side_effect = [LookupError("Missing"), error, result]
    if authentication:
        with pytest.raises(CLIAbort, match="Authentication"):
            _resolve_reminder(api_for(reminders), result.id)
        assert reminders.get.call_count == 2
    else:
        assert _resolve_reminder(api_for(reminders), result.id) is result
        assert reminders.get.call_count == 3


def test_global_listing_preserves_private_and_shared_list_contexts() -> None:
    """Colliding list IDs must retain route models for both scopes."""
    reminders = MagicMock()
    contexts = [
        RemindersList(id="List/L", title="Private"),
        RemindersList(id="List/L", title="Shared", scope="shared", zone_owner="A"),
    ]
    reminders.lists.return_value = contexts
    reminders.list_reminders.return_value = SimpleNamespace(reminders=[])
    assert (
        _list_reminder_rows(api_for(reminders), include_completed=True, limit=10) == []
    )
    assert [
        call.kwargs["list_id"] for call in reminders.list_reminders.call_args_list
    ] == contexts
