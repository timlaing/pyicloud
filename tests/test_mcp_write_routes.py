"""Endpoint changes invalidate already approved synthetic mutation previews."""

from typing import Any

import pytest

from pyicloud.mcp.authorization import WriteDenied
from tests.test_mcp_calendar_writes import setup_calendar
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_reminder_writes import writer


async def prepared(service: str) -> tuple[Any, Any, Any]:
    """Use existing synthetic facades; no account or transport is constructed."""
    writes: Any
    if service == "drive":
        writes, client, handle, _data = setup_writer()
        preview = await writes.prepare(handle, {"name": "After"})
    elif service == "calendar":
        writes, client, _event = setup_calendar()
        preview = await writes.prepare("calendar.delete_event", "C", {}, "E")
    else:
        writes, client, _context, _reminder = writer()
        preview = await writes.prepare("reminders.create", "H", {"title": "After"})
    return writes, client, preview


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["drive", "calendar", "reminders"])
@pytest.mark.parametrize("endpoint", ["https://changed.example.test", "", None])
async def test_changed_endpoint_never_dispatches(
    service: str, endpoint: str | None
) -> None:
    """Approval cannot migrate to a new or unknown discovered service endpoint."""
    writes, client, preview = await prepared(service)
    ledger = writes.approvals if service == "reminders" else writes.ledger
    ledger.approve_from_operator(preview.request_id, preview.digest)
    getattr(client, service).service_root = endpoint
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.drive.rename_items.assert_not_called()
    client.calendar.remove_event.assert_not_called()
    client.reminders.create.assert_not_called()
