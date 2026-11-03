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
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_mail_folder_renames import setup_rename
from tests.test_mcp_mail_folders import setup_folders
from tests.test_mcp_mail_writes import setup_mail
from tests.test_mcp_photo_album_creates import setup_create
from tests.test_mcp_photo_album_writes import make_album, setup_album
from tests.test_mcp_photo_relations import setup_add
from tests.test_mcp_photo_writes import setup_photos
from tests.test_mcp_reminder_writes import writer


@pytest.fixture(name="directory")
def approval_directory() -> Iterator[Path]:
    """Reuse the existing sandbox-only descriptor test shim, not OS permissions."""
    yield from cast(Any, sandbox_directory_fixture).__wrapped__()


@pytest.mark.asyncio
async def test_relation_gateway_binds_discovered_pair_and_membership() -> None:
    """One private receipt authorizes one independently verified synthetic add."""
    relations, _client, album, asset, source, target = setup_add()
    owner = cast(Any, relations.additional.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("photos",),
        write_actions=("photos.add_to_album",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(relations.additional.reads, relations.additional)
    with (
        patch.object(album, "contains_photo_scoped", return_value=False) as membership,
        patch.object(album, "add_photo_correlated") as mutate,
    ):
        invalid = await gateway.prepare("photos.add_to_album", "", {}, None, source)
        assert invalid.error is not None
        prepared = await gateway.prepare(
            "photos.add_to_album", "", {}, None, source, destination_handle=target
        )
        assert prepared.preview is not None
        preview = prepared.preview
        mailbox.accept.side_effect = WriteDenied("operator_approval_required")
        assert (
            await gateway.apply(preview["request_id"])
        ).error == "operator_approval_required"
        mutate.assert_not_called()

        def accept(identifier: str, ledger: ApprovalLedger) -> None:
            ledger.approve_from_operator(identifier, preview["digest"])

        mailbox.accept.side_effect = accept

        def add(_asset: Any) -> bool:
            membership.return_value = True
            return True

        mutate.side_effect = add
        result = await gateway.apply(preview["request_id"])
        assert result.error is None
        assert result.result == {"action": "photos.add_to_album", "changed": True}
        mutate.assert_called_once_with(asset)
        assert (
            await gateway.apply(preview["request_id"])
        ).error == "unknown_or_consumed_preview"


@pytest.mark.asyncio
async def test_album_create_gateway_requires_receipt_and_new_server_identity() -> None:
    """One private receipt authorizes one synthetic correlated creation."""
    creates, _client, library, handle = setup_create()
    owner = cast(Any, creates.additional.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("photos",),
        write_actions=("photos.create_album",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(creates.additional.reads, creates.additional)
    invalid = await gateway.prepare("photos.create_album", "", {"name": "New"}, None)
    assert invalid.error is not None
    prepared = await gateway.prepare(
        "photos.create_album", "", {"name": "New"}, None, handle
    )
    assert prepared.preview is not None
    preview = prepared.preview
    mailbox.accept.side_effect = WriteDenied("operator_approval_required")
    with patch.object(library, "create_album_correlated") as create:
        assert (
            await gateway.apply(preview["request_id"])
        ).error == "operator_approval_required"
        create.assert_not_called()

        def accept(identifier: str, ledger: ApprovalLedger) -> None:
            ledger.approve_from_operator(identifier, preview["digest"])

        mailbox.accept.side_effect = accept

        def mutation(name: str) -> Any:
            library.server_albums.return_value = [make_album(library, name, "R1")]
            return make_album(library, name, "R1")

        create.side_effect = mutation
        result = await gateway.apply(preview["request_id"])
        assert result.error is None
        assert result.result == {"action": "photos.create_album", "id": "A"}
        create.assert_called_once_with("New")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"


@pytest.mark.asyncio
async def test_album_gateway_shares_discovered_registry_and_receipt() -> None:
    """Even a synthetic already-correct album name requires exact approval."""
    albums, _client, _album, handle = setup_album()
    owner = cast(Any, albums.additional.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("photos",),
        write_actions=("photos.rename_album",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(albums.additional.reads, albums.additional)
    prepared = await gateway.prepare(
        "photos.rename_album", "", {"name": "Before"}, None, handle
    )
    assert prepared.preview is not None
    preview = prepared.preview
    mailbox.accept.side_effect = WriteDenied("operator_approval_required")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "operator_approval_required"

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept
    result = await gateway.apply(preview["request_id"])
    assert result.error is None
    assert result.result == {"action": "photos.rename_album", "changed": False}
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"


@pytest.mark.asyncio
async def test_mail_rename_gateway_preserves_discovered_identity() -> None:
    """Shared registry selectors plus one private receipt authorize one rename."""
    renames, client, handle, original = setup_rename()
    owner = cast(Any, renames.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("mail",),
        write_actions=("mail.rename_folder",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(renames.reads, renames.additional)
    prepared = await gateway.prepare(
        "mail.rename_folder", "", {"name": "Renamed"}, None, handle
    )
    assert prepared.preview is not None
    preview = prepared.preview
    mailbox.accept.side_effect = WriteDenied("operator_approval_required")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "operator_approval_required"
    client.mail.rename_folder.assert_not_called()

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept

    def rename(_folder: Any, name: str) -> Any:
        result = original.model_copy(update={"name": name})
        client.mail.folders.return_value = [client.mail.folders.return_value[0], result]
        return result

    client.mail.rename_folder.side_effect = rename
    result = await gateway.apply(preview["request_id"])
    assert result.error is None
    assert result.result == {"action": "mail.rename_folder", "id": "C"}
    client.mail.rename_folder.assert_called_once_with(original, "Renamed")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"


@pytest.mark.asyncio
async def test_mail_folder_gateway_uses_exact_namespace_receipt() -> None:
    """One approved synthetic create requires independently matching namespace."""
    folders, client = setup_folders()
    owner = cast(Any, folders.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("mail",),
        write_actions=("mail.create_folder",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(folders.reads)
    prepared = await gateway.prepare(
        "mail.create_folder", "", {"name": "New Folder"}, None
    )
    assert prepared.preview is not None
    preview = prepared.preview
    mailbox.accept.side_effect = WriteDenied("operator_approval_required")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "operator_approval_required"
    client.mail.create_folder.assert_not_called()

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept

    def create(_name: str) -> Any:
        result = client.mail.folders.return_value[0].model_copy(
            update={"id": "NEW", "name": "New Folder"}
        )
        client.mail.folders.return_value = [*client.mail.folders.return_value, result]
        return result

    client.mail.create_folder.side_effect = create
    result = await gateway.apply(preview["request_id"])
    assert result.error is None
    assert result.result == {"action": "mail.create_folder", "id": "NEW"}
    client.mail.create_folder.assert_called_once()
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"


@pytest.mark.asyncio
async def test_drive_move_gateway_binds_two_discovered_handles() -> None:
    """A synthetic exact receipt dispatches one verified private move."""
    moves, client, handle, destination, data, folder = setup_move()
    reads = moves.additional.reads
    owner = cast(Any, reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("drive",),
        write_actions=("drive.move_file",),
        approval_dir="unused",
    )
    owner.add_cleanup = MagicMock()
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(reads, moves.additional)
    invalid = await gateway.prepare("drive.move_file", "", {}, None, handle)
    assert invalid.error is not None
    preview_result = await gateway.prepare(
        "drive.move_file", "", {}, None, handle, destination_handle=destination
    )
    assert preview_result.preview is not None
    preview = preview_result.preview
    mailbox.accept.side_effect = WriteDenied("operator_approval_required")
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "operator_approval_required"
    client.drive.move_nodes_to_node.assert_not_called()

    def accept(identifier: str, ledger: ApprovalLedger) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = accept

    def move(_nodes: list[Any], _destination: Any) -> None:
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[dict(data)])

    client.drive.move_nodes_to_node.side_effect = move
    result = await gateway.apply(preview["request_id"])
    assert result.error is None
    assert result.result == {"action": "drive.move_file", "changed": True}
    client.drive.move_nodes_to_node.assert_called_once()
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"


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
            (
                "icloud_write_preview",
                {
                    "action": "drive.move_file",
                    "target_handle": "source",
                    "destination_handle": "destination",
                    "payload": {},
                },
            ),
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
