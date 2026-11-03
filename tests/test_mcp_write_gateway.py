"""Synthetic runtime gating: no real account mutations or operator decisions."""

from collections.abc import Iterator
import io
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ServerConfig, ToolFailure
from pyicloud.mcp.server import build_server
from pyicloud.mcp.write_gateway import WriteGateway
from tests.test_mcp_approval_channel import (
    sandbox_directory as sandbox_directory_fixture,
)
from tests.test_mcp_approval_channel import terminal
from tests.test_mcp_calendar_writes import payload as calendar_payload
from tests.test_mcp_calendar_writes import setup_calendar
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_mail_writes import setup_mail
from tests.test_mcp_photo_writes import setup_photos
from tests.test_mcp_reminder_writes import writer


@pytest.fixture(name="directory")
def approval_directory() -> Iterator[Path]:
    """Reuse the existing sandbox-only descriptor test shim, not OS permissions."""
    yield from cast(Any, sandbox_directory_fixture).__wrapped__()


@pytest.mark.asyncio
async def test_photo_gateway_shares_discovered_registry() -> None:
    """An approved synthetic no-op proves dispatch and single-use gating."""
    photo_writer, _client, _asset, handle = setup_photos()
    reads = photo_writer.additional.reads
    owner = cast(Any, reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("photos",),
        write_actions=("photos.set_favorite",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(reads, photo_writer.additional)
    prepared = await gateway.prepare(
        "photos.set_favorite", "", {"favorite": False}, None, handle
    )
    assert prepared.preview is not None
    preview = prepared.preview

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept
    result = await gateway.apply(preview["request_id"])
    assert result.error is None
    assert result.result == {"action": "photos.set_favorite", "changed": False}


@pytest.mark.asyncio
async def test_mail_gateway_uses_private_receipt() -> None:
    """Dispatch a single synthetic draft save through the shared mailbox gate."""
    mail_writer, client, payload = setup_mail()
    owner = cast(Any, mail_writer.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("mail",),
        write_actions=("mail.create_draft",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(mail_writer.reads)
    prepared = await gateway.prepare("mail.create_draft", "", payload, None)
    assert prepared.preview is not None
    preview = prepared.preview

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept
    assert (await gateway.apply(preview["request_id"])).error is None
    client.mail.create_draft.assert_called_once()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"write_actions": ("reminders.create",)},
        {
            "username": "x",
            "session_dir": "x",
            "services": ("reminders",),
            "write_actions": ("unsupported",),
            "approval_dir": "unused",
        },
        {
            "username": "x",
            "session_dir": "x",
            "services": ("calendar",),
            "write_actions": ("reminders.create",),
            "approval_dir": "unused",
        },
        {
            "username": "x",
            "session_dir": "x",
            "services": ("reminders",),
            "approval_dir": "unused",
        },
    ],
)
def test_invalid_write_configuration(kwargs: dict[str, Any]) -> None:
    """Unknown actions, missing service and directory-alone fail before access."""
    with pytest.raises(ValueError):
        ServerConfig(**kwargs)


def gateway_for() -> tuple[WriteGateway, Any, MagicMock, MagicMock]:
    """Use a private mailbox double, never read real storage or credentials."""
    reminder_writer, client, _context, _reminder = writer()
    owner = cast(Any, reminder_writer.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("reminders",),
        write_actions=("reminders.create",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(reminder_writer.reads)
    return gateway, owner, client, mailbox


@pytest.mark.asyncio
async def test_exact_receipt_and_allowlist_are_required() -> None:
    """Only the independently approved snapshot can reach a mutation callback."""
    gateway, owner, client, mailbox = gateway_for()
    assert (
        await gateway.prepare("reminders.delete", "H", {}, "R")
    ).error == "write_disabled"
    item = await gateway.prepare("reminders.create", "H", {"title": "After"}, None)
    assert item.preview is not None
    preview = item.preview
    mailbox.publish.assert_called_once()
    request_id = preview["request_id"]
    mailbox.accept.side_effect = WriteDenied("approval_record_unavailable")
    assert (await gateway.apply(request_id)).error == "approval_record_unavailable"
    client.reminders.create.assert_not_called()

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        assert identifier == request_id
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept
    result = await gateway.apply(request_id)
    assert result.error is None
    assert result.result is not None
    assert result.result["action"] == "reminders.create"
    client.reminders.create.assert_called_once()
    owner.add_cleanup.assert_called_once_with(mailbox.close)


@pytest.mark.asyncio
async def test_gateway_failure_categories_are_safe() -> None:
    """No raw storage, account or backend exceptions escape into tool results."""
    gateway, _owner, _client, _mailbox = gateway_for()
    with patch.object(
        gateway.writer, "prepare", new=AsyncMock(side_effect=ToolFailure("timeout"))
    ):
        assert (
            await gateway.prepare("reminders.create", "H", {}, None)
        ).error == "timeout"
    with patch.object(
        gateway.writer, "prepare", new=AsyncMock(side_effect=RuntimeError("private"))
    ):
        assert (
            await gateway.prepare("reminders.create", "H", {}, None)
        ).error == "preview_failed"
    with patch.object(
        gateway.writer, "apply", new=AsyncMock(side_effect=RuntimeError("private"))
    ):
        assert (await gateway.apply("unknown")).error == "write_failed"


@pytest.mark.asyncio
async def test_registration_requires_operator_startup_flags() -> None:
    """There is no model-callable approval tool or write-enabling switch."""
    reminder_writer, _client, _context, _reminder = writer()
    owner = reminder_writer.reads.owner
    assert not any(
        tool.name.startswith("icloud_write_")
        for tool in await build_server(owner=owner).list_tools()
    )
    gateway, owner, _client, _mailbox = gateway_for()
    with (
        patch.object(
            gateway, "prepare", new=AsyncMock(return_value={"error": "write_disabled"})
        ),
        patch.object(
            gateway,
            "apply",
            new=AsyncMock(return_value={"error": "operator_approval_required"}),
        ),
        patch("pyicloud.mcp.server.WriteGateway", return_value=gateway),
    ):
        server = build_server(owner=owner)
        names = {tool.name for tool in await server.list_tools()}
        assert {"icloud_write_preview", "icloud_write_execute"} <= names
        assert not any("approve" in name for name in names)
        for name, args in [
            (
                "icloud_write_preview",
                {
                    "action": "reminders.create",
                    "list_handle": "H",
                    "payload": {"title": "After"},
                },
            ),
            ("icloud_write_execute", {"request_id": "unknown"}),
        ]:
            result = await server.call_tool(name, cast(dict[str, Any], args))
            assert isinstance(result, CallToolResult)
            assert not result.is_error


@pytest.mark.asyncio
async def test_real_mailbox_round_trip_gates_synthetic_mutation(
    directory: Path,
) -> None:
    """Exercise publication, separate terminal decision and consumption together."""
    reminder_writer, client, _context, _reminder = writer()
    owner = cast(Any, reminder_writer.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("reminders",),
        write_actions=("reminders.create",),
        approval_dir=str(directory),
    )
    owner.add_cleanup = MagicMock()
    gateway = WriteGateway(reminder_writer.reads)
    try:
        prepared = await gateway.prepare(
            "reminders.create", "H", {"title": "After"}, None
        )
        assert prepared.preview is not None
        request_id = prepared.preview["request_id"]
        assert (await gateway.apply(request_id)).error == "approval_record_unavailable"
        client.reminders.create.assert_not_called()
        gateway.mailbox.decide(
            request_id, terminal("APPROVE " + request_id), io.StringIO()
        )
        assert (await gateway.apply(request_id)).error is None
        client.reminders.create.assert_called_once()
        assert (await gateway.apply(request_id)).error == "unknown_or_consumed_preview"
    finally:
        gateway.mailbox.close()


@pytest.mark.asyncio
async def test_drive_gateway_shares_registry_and_operator_gate() -> None:
    """Only the discovered private node reaches the verified rename dispatcher."""
    drive_writer, client, handle, data = setup_writer()
    owner = cast(Any, drive_writer.additional.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("drive",),
        write_actions=("drive.rename",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(
            drive_writer.additional.reads, additional=drive_writer.additional
        )
    prepared = await gateway.prepare(
        "drive.rename", "", {"name": "After"}, None, target_handle=handle
    )
    assert prepared.preview is not None
    preview = prepared.preview

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept

    def rename(_identifier: str, _etag: str, name: str) -> dict[str, Any]:
        data.update(name=name, etag="v2")
        return {}

    client.drive.rename_items.side_effect = rename
    assert (await gateway.apply(preview["request_id"])).error is None
    client.drive.rename_items.assert_called_once()


@pytest.mark.asyncio
async def test_calendar_gateway_binds_fixed_creation_guid() -> None:
    """The common gateway uses exactly the approved Calendar creation selector."""
    calendar_writer, client, _event = setup_calendar()
    owner = cast(Any, calendar_writer.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("calendar",),
        write_actions=("calendar.create_event",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(calendar_writer.reads)
    prepared = await gateway.prepare(
        "calendar.create_event", "", calendar_payload(), None, calendar_id="C"
    )
    assert prepared.preview is not None
    preview = prepared.preview

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept

    def create(event: Any) -> dict[str, Any]:
        client.calendar.get_event_detail.return_value = event
        return {}

    client.calendar.add_event.side_effect = create
    assert (await gateway.apply(preview["request_id"])).error is None
    client.calendar.add_event.assert_called_once()
