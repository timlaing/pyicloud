"""Calendar create/delete with synthetic facade callbacks and revision approval."""

# pylint: disable=protected-access

from datetime import datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.calendar_writes import CALENDAR_ACTIONS, CalendarWrites
from pyicloud.mcp.reads import ReadTools
from pyicloud.services.calendar import EventObject
from tests.test_mcp_additional import owner_for


def setup_calendar() -> tuple[CalendarWrites, MagicMock, EventObject]:
    """No transport, account, timezone filesystem or actual data is accessed."""
    client = MagicMock()
    client.params = {"dsid": "A"}
    client.calendar.get_calendars.return_value = [{"guid": "C", "title": "Calendar"}]
    client.calendar.get_ctag.return_value = "C1"
    event = EventObject(
        pguid="C",
        guid="E",
        title="Before",
        start_date=datetime(2026, 1, 1, 12),
        end_date=datetime(2026, 1, 1, 13),
        tz="UTC",
        etag="E1",
    )
    client.calendar.get_event_detail.return_value = event
    return (
        CalendarWrites(ReadTools(owner_for(client)), ApprovalLedger(CALENDAR_ACTIONS)),
        client,
        event,
    )


def payload() -> dict[str, str]:
    """Explicit local time and zone, never incidental process defaults."""
    return {
        "title": "After",
        "start": "2026-01-01T12:00:00",
        "end": "2026-01-01T13:00:00",
        "timezone": "UTC",
    }


@pytest.mark.asyncio
async def test_creation_uses_preview_guid_and_verifies_fetched_event() -> None:
    """Preparation reads only; a single approval creates the exact new entity."""
    writes, client, _event = setup_calendar()
    preview = await writes.prepare("calendar.create_event", "C", payload())
    client.calendar.add_event.assert_not_called()
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    def create(event: EventObject) -> dict[str, Any]:
        assert event.guid == preview.payload["event_id"]
        client.calendar.get_event_detail.return_value = event
        return {"ignored": "not proof"}

    client.calendar.add_event.side_effect = create
    result = await writes.apply(preview.request_id)
    assert result["event_id"] == preview.payload["event_id"]
    client.calendar.add_event.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_deletion_preserves_verified_etag_and_requires_absence_proof() -> None:
    """The public remove_event method carries the captured etag as ifMatch."""
    writes, client, event = setup_calendar()
    preview = await writes.prepare("calendar.delete_event", "C", {}, "E")
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.calendar.get_event_detail.side_effect = [
        event,
        PyiCloudAPIResponseException("synthetic", 404),
    ]
    assert (await writes.apply(preview.request_id))["action"] == "calendar.delete_event"
    client.calendar.remove_event.assert_called_once_with(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["account", "ctag", "missing", "etag", "scope", "invitees"]
)
async def test_changed_or_unsupported_targets_never_dispatch(case: str) -> None:
    """No cross-account/calendar event, invitations or recurrence guessed writes."""
    writes, client, event = setup_calendar()
    action = (
        "calendar.create_event"
        if case in {"account", "ctag", "missing"}
        else "calendar.delete_event"
    )
    preview = await writes.prepare(
        action,
        "C",
        payload() if action.endswith("create_event") else {},
        None if action.endswith("create_event") else "E",
    )
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "account":
        client.params["dsid"] = "B"
    elif case == "ctag":
        client.calendar.get_ctag.return_value = "C2"
    elif case == "missing":
        client.calendar.get_calendars.return_value = []
    elif case == "etag":
        event.etag = "E2"
    elif case == "scope":
        event.pguid = "OTHER"
    else:
        event.invitees = ["synthetic@example.invalid"]
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.calendar.add_event.assert_not_called()
    client.calendar.remove_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["create", "delete", "transport"])
async def test_uncertain_acknowledgement_is_retired(case: str) -> None:
    """An OK envelope or ambiguous follow-up cannot authorize a blind retry."""
    writes, client, event = setup_calendar()
    action = "calendar.create_event" if case == "create" else "calendar.delete_event"
    preview = await writes.prepare(
        action,
        "C",
        payload() if case == "create" else {},
        None if case == "create" else "E",
    )
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "transport":
        client.calendar.get_event_detail.side_effect = [
            event,
            PyiCloudAPIResponseException("private", 500),
        ]
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_invalid_input_and_missing_revision_fail_closed() -> None:
    """Never implement update as an undocumented upsert or overwrite a supplied GUID."""
    writes, client, _event = setup_calendar()
    cases = [
        ("calendar.update_event", "C", payload(), "E"),
        ("calendar.create_event", "", payload(), None),
        ("calendar.create_event", "C", payload(), "E"),
        ("calendar.delete_event", "C", {}, None),
        ("calendar.delete_event", "C", {"unexpected": True}, "E"),
        ("calendar.create_event", "C", {}, None),
        ("calendar.create_event", "C", {**payload(), "title": True}, None),
        ("calendar.create_event", "C", {**payload(), "title": ""}, None),
        ("calendar.create_event", "C", {**payload(), "start": "bad"}, None),
        (
            "calendar.create_event",
            "C",
            {**payload(), "start": "2026-01-01T12:00:00+00:00"},
            None,
        ),
    ]
    for action, calendar, data, identifier in cases:
        with pytest.raises(WriteDenied):
            await writes.prepare(
                action, calendar, cast(dict[str, Any], data), identifier
            )
    client.params = {}
    with pytest.raises(WriteDenied, match="account_unavailable"):
        await writes.prepare("calendar.create_event", "C", payload())
    client.calendar.add_event.assert_not_called()
