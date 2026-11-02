"""Synthetic read contracts: budgets, privacy, shared routes and sanitized errors."""

# cspell:words mcpserver

from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
import pytest

from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudServiceUnavailable
from pyicloud.mcp.context import ServerConfig, SessionOwner, ToolFailure
from pyicloud.mcp.reads import ReadResult, ReadTools, bounded_result, calendar_time
from pyicloud.mcp.server import build_server
from pyicloud.services.reminders.exceptions import RemindersAuthError
from pyicloud.services.reminders.models import Reminder, RemindersList


def owner_for(
    client: Any, *, services: tuple[Any, ...] = ("calendar", "reminders")
) -> SessionOwner:
    """An in-memory lane avoids account construction, threads and filesystem reads."""

    async def run(operation: Callable[[Any], Any]) -> Any:
        return operation(client)

    return cast(
        SessionOwner,
        SimpleNamespace(
            config=ServerConfig(
                username="synthetic", session_dir="synthetic", services=services
            ),
            run=run,
            close=AsyncMock(),
            authenticated=False,
        ),
    )


def test_response_budgets() -> None:
    """Oversized records never slip through the response budget."""
    assert bounded_result([{"x": 1}], 1).complete
    assert not bounded_result([{"x": 1}, {"x": 2}], 1).complete
    result = bounded_result([{"x": "x" * 250_000}], 1)
    assert not result.records
    assert not result.complete


@pytest.mark.parametrize(
    "value,expected",
    [
        (["20260101", 2026, 1, 1, 12, 30, 750], "2026-01-01T12:30:00"),
        ({"private": "signed URL"}, None),
        (["20260101", "private", 1, 1, 12, 30, 750], None),
    ],
)
def test_calendar_time_projection(value: Any, expected: str | None) -> None:
    """Malformed upstream timestamps cannot expose nested account data."""
    assert calendar_time(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (ToolFailure("timeout"), "timeout"),
        (RemindersAuthError("private token"), "authentication_required"),
        (PyiCloudServiceUnavailable("private"), "service_unavailable"),
        (PyiCloudAPIResponseException("private", 429), "rate_limited"),
        (PyiCloudAPIResponseException("private", 401), "authentication_required"),
        (PyiCloudAPIResponseException("private", 500), "upstream_failure"),
        (RuntimeError("signed URL private"), "upstream_failure"),
    ],
)
async def test_errors_never_expose_upstream_payload(
    error: Exception, code: str
) -> None:
    """Every error category discards its original private payload."""
    tools = ReadTools(owner_for(None))

    def fail(_client: Any) -> ReadResult:
        raise error

    assert (await tools.execute("calendar", 50, fail)).model_dump() == {
        "records": [],
        "complete": False,
        "error": code,
    }


@pytest.mark.asyncio
async def test_allowlist_and_input_limits_precede_requests() -> None:
    """Rejected arguments cannot authorize account calls."""
    client = MagicMock()
    assert (
        await ReadTools(owner_for(client, services=())).calendars(50)
    ).error == "service_disabled"
    tools = ReadTools(owner_for(client))
    for limit in (0, 201):
        assert (await tools.calendars(limit)).error == "invalid_argument"
    for start, end, identifier in [
        ("bad", "bad", "id"),
        ("2026-01-01", "2026-03-01", "id"),
        ("2026-01-02", "2026-01-01", "id"),
        ("2026-01-01", "2026-01-01", ""),
        ("9999-12-31", "9999-12-31", "C"),
    ]:
        assert (
            await tools.events(identifier, start, end, 50)
        ).error == "invalid_argument"
    client.calendar.get_events.assert_not_called()
    client.calendar.get_calendars.assert_not_called()


@pytest.mark.asyncio
async def test_calendar_fields_are_minimized_and_scoped() -> None:
    """Only selected-calendar fields survive the projection."""
    client = MagicMock()
    client.calendar.get_calendars.return_value = [
        {"guid": "C", "title": "Calendar", "private": "secret"}
    ]
    client.calendar.get_events.return_value = [
        {
            "guid": "E",
            "pGuid": "C",
            "title": "Data, not commands",
            "description": "secret",
        },
        {"guid": "OTHER", "pGuid": "OTHER"},
    ]
    tools = ReadTools(owner_for(client))
    assert (await tools.calendars(50)).records == [{"id": "C", "title": "Calendar"}]
    result = await tools.events("C", "2026-01-01", "2026-01-01", 50)
    assert result.records == [
        {
            "id": "E",
            "calendar_id": "C",
            "title": "Data, not commands",
            "start": None,
            "end": None,
        }
    ]
    assert client.calendar.get_events.call_args.kwargs == {
        "from_dt": datetime(2026, 1, 1),
        "to_dt": datetime(2026, 1, 2),
    }


@pytest.mark.asyncio
async def test_reminder_handles_preserve_colliding_private_shared_routes() -> None:
    """Shared owner identities stay server-side behind expiring handles."""
    client = MagicMock()
    contexts = [
        RemindersList(id="List/SAME", title="Private"),
        RemindersList(
            id="List/SAME", title="Shared", scope="shared", zone_owner="SECRET_OWNER"
        ),
        RemindersList(id="List/DELETED", title="Deleted", deleted=True),
    ]
    client.reminders.lists.side_effect = lambda: iter(contexts)
    client.reminders.list_reminders.return_value = SimpleNamespace(
        reminders=[
            Reminder(
                id="R",
                list_id="List/SAME",
                title="Reminder",
                due_date=datetime(2026, 1, 1),
            ),
            Reminder(id="D", list_id="List/SAME", title="Deleted", deleted=True),
        ]
    )
    tools = ReadTools(owner_for(client))
    lists = await tools.lists(50)
    assert len(lists.records) == 2
    assert not lists.complete
    assert "SECRET_OWNER" not in lists.model_dump_json()
    handles = [row["handle"] for row in lists.records]
    for handle, context in zip(handles, contexts[:2], strict=True):
        result = await tools.reminders(handle, True, 50)
        assert result.records[0]["due_date"] == "2026-01-01T00:00:00"
        assert client.reminders.list_reminders.call_args.kwargs["list_id"] is context
        assert (
            client.reminders.list_reminders.call_args.kwargs["include_completed"]
            is True
        )
    with patch("pyicloud.mcp.reads.clock.monotonic", return_value=10**20):
        assert (
            await tools.reminders(handles[0], False, 50)
        ).error == "expired_or_unknown_handle"
    await tools.lists(50)
    assert (
        await tools.reminders(handles[0], False, 50)
    ).error == "expired_or_unknown_handle"


@pytest.mark.asyncio
async def test_protocol_tool_registration_and_shutdown() -> None:
    """Every enabled tool serializes through the SDK and lifespan drains once."""
    client = MagicMock()
    client.calendar.get_calendars.return_value = []
    client.calendar.get_events.return_value = []
    client.reminders.lists.return_value = iter([])
    owner = owner_for(client)
    with patch("pyicloud.mcp.server.MCPServer", wraps=MCPServer) as factory:
        server = build_server(owner=owner)
    lifespan = factory.call_args.kwargs["lifespan"]
    async with lifespan(server):
        names = {tool.name for tool in await server.list_tools()}
        assert len(names) == 6
        for name, args in [
            ("icloud_status", {}),
            ("icloud_capabilities", {}),
            ("icloud_calendar_list", {}),
            (
                "icloud_calendar_events",
                {"calendar_id": "C", "start": "2026-01-01", "end": "2026-01-01"},
            ),
            ("icloud_reminders_lists", {}),
            ("icloud_reminders_list", {"handle": "unknown"}),
        ]:
            result = await server.call_tool(name, args)
            assert isinstance(result, CallToolResult)
            assert not result.is_error
    cast(AsyncMock, owner.close).assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_list_iterator_is_closed() -> None:
    """Generator closure preserves library snapshot ownership on bounded discovery."""
    closed: list[bool] = []

    def contexts() -> Any:
        try:
            for index in range(4):
                yield RemindersList(id=str(index), title="Synthetic")
        finally:
            closed.append(True)

    client = MagicMock()
    client.reminders.lists.return_value = contexts()
    result = await ReadTools(owner_for(client)).lists(1)
    assert len(result.records) == 1
    assert not result.complete
    assert closed == [True]


@pytest.mark.asyncio
async def test_deleted_lists_do_not_consume_live_discovery_limit() -> None:
    """Many tombstones cannot hide the next usable shared context."""
    client = MagicMock()
    deleted = RemindersList(id="deleted", title="Deleted", deleted=True)
    client.reminders.lists.return_value = iter([
        deleted,
        deleted,
        deleted,
        RemindersList(id="live", title="Live", scope="shared"),
    ])
    result = await ReadTools(owner_for(client)).lists(1)
    assert [row["title"] for row in result.records] == ["Live"]
