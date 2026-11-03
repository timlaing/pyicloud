"""Offline metadata/privacy and scoped-handle contracts for extra MCP services."""

# pylint: disable=protected-access

from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.additional import AdditionalReads, text, timestamp
from pyicloud.mcp.context import SUPPORTED_SERVICES, ServerConfig, SessionOwner
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.server import build_server
from pyicloud.services.mail.models import MailFolder, MailMessage, MailSearchHit
from pyicloud.services.notes.exceptions import (
    NotesAuthError,
    NotesRateLimited,
    NotesZoneNotFoundError,
)
from pyicloud.services.photos.exceptions import (
    PhotosAuthError,
    PhotosRateLimited,
    PhotosZoneNotFoundError,
)


def owner_for(
    client: Any, services: tuple[Any, ...] = SUPPORTED_SERVICES
) -> SessionOwner:
    """No session, network, filesystem or threads are constructed for adapter tests."""

    async def run(operation: Callable[[Any], Any]) -> Any:
        return operation(client)

    return cast(
        SessionOwner,
        SimpleNamespace(
            config=ServerConfig(
                username="synthetic", session_dir="not-read", services=services
            ),
            authenticated=False,
            run=run,
            close=AsyncMock(),
        ),
    )


def adapters(client: Any) -> AdditionalReads:
    """Build projections using the shared authorization and error policies."""
    return AdditionalReads(ReadTools(owner_for(client)))


def test_scalar_projection_rejects_private_nested_data() -> None:
    """Do not leak arbitrary upstream fields by coercing dicts into text."""
    assert text(None) == ""
    assert text("x" * 5000) == "x" * 4096
    assert timestamp(None) is None
    assert timestamp(datetime(2026, 1, 1)) == "2026-01-01T00:00:00"
    with pytest.raises(Exception, match="upstream_failure"):
        text({"secret": "OMIT_ME"})
    with pytest.raises(Exception, match="upstream_failure"):
        timestamp("OMIT_ME")


@pytest.mark.asyncio
async def test_contacts_search_details_are_explicit_and_minimized() -> None:
    """Name search never reveals emails, phones, photos or raw contact fields."""
    client = MagicMock()
    client.contacts.all = [
        {"firstName": "Other", "lastName": "Person"},
        {
            "firstName": "Alice",
            "lastName": "Example",
            "photo": "OMIT_ME",
            "emails": [{"field": "synthetic@example.invalid"}],
            "phones": [{"field": "000"}],
        },
    ]
    tools = adapters(client)
    result = await tools.contacts("ALICE", 1)
    assert len(result.records) == 1
    assert set(result.records[0]) == {"handle", "first_name", "last_name"}
    handle = result.records[0]["handle"]
    minimal = await tools.contact_details(handle, False, False)
    assert set(minimal.records[0]) == {"first_name", "last_name"}
    details = await tools.contact_details(handle, True, True)
    assert details.records[0]["emails"] == ["synthetic@example.invalid"]
    assert details.records[0]["phones"] == ["000"]
    assert "OMIT_ME" not in details.model_dump_json()
    client.contacts.all = None
    assert (await tools.contacts("", 50)).error == "upstream_failure"


@pytest.mark.asyncio
async def test_handle_binding_expiration_and_registry_budget() -> None:
    """Foreign-service, expired and evicted handles cannot route metadata reads."""
    tools = adapters(MagicMock())
    handle = tools._store("contact", {})
    assert (await tools.drive(handle, "", 50)).error == "expired_or_unknown_handle"
    with patch("pyicloud.mcp.additional.time.monotonic", return_value=10**20):
        assert (
            await tools.contact_details(handle, False, False)
        ).error == "expired_or_unknown_handle"
    for _ in range(1000):
        tools._store("contact", {})
    assert len(tools._handles) == 1000
    assert handle not in tools._handles


@pytest.mark.asyncio
async def test_drive_browses_discovered_folders_without_local_paths_or_downloads() -> (
    None
):
    """Nested browse retains each DriveNode, including its shared context."""
    client = MagicMock()
    file = SimpleNamespace(name="File.txt", type="file", date_modified=None)
    folder = SimpleNamespace(
        name="Shared",
        type="folder",
        date_modified=datetime(2026, 1, 1),
        get_children=MagicMock(return_value=[file]),
    )
    client.drive.root = SimpleNamespace(
        type="folder", get_children=MagicMock(return_value=[folder, file])
    )
    tools = adapters(client)
    result = await tools.drive("", "SHARED", 1)
    handle = result.records[0]["handle"]
    nested = await tools.drive(handle, "", 50)
    assert [
        {key: value for key, value in row.items() if key != "handle"}
        for row in nested.records
    ] == [{"name": "File.txt", "type": "file", "modified": None}]
    folder.get_children.assert_called_once_with(force=True)
    client.drive.get_file.assert_not_called()
    bad = tools._store("drive-folder", SimpleNamespace(type="file"))
    assert (await tools.drive(bad, "", 50)).error == "invalid_argument"
    assert (
        await tools.drive("/local/path", "", 50)
    ).error == "expired_or_unknown_handle"


@pytest.mark.asyncio
async def test_notes_scan_recent_titles_only_and_close_generator() -> None:
    """No snippets, bodies, attachments, or false global-search completeness."""
    client = MagicMock()
    closed = []

    def recents(*, limit: int) -> Any:
        assert limit == 200
        try:
            for deleted, title in [
                (True, "Find"),
                (False, "Other"),
                (False, "Find"),
                (False, "Later"),
            ]:
                yield SimpleNamespace(
                    id="N",
                    title=title,
                    snippet="OMIT_ME",
                    is_deleted=deleted,
                    modified_at=None,
                    is_locked=True,
                )
        finally:
            closed.append(True)

    client.notes.recents.side_effect = recents
    result = await adapters(client).notes("find", 1)
    assert [
        {key: value for key, value in row.items() if key != "handle"}
        for row in result.records
    ] == [{"id": "N", "title": "Find", "modified": None, "locked": True}]
    assert not result.complete
    assert closed == [True]
    client.notes.get.assert_not_called()


@pytest.mark.asyncio
async def test_photos_library_album_and_asset_routes_remain_scoped() -> None:
    """Colliding album/asset IDs never require a global/private fallback."""
    client = MagicMock()
    libraries = {}
    for scope in ("Private", "Shared"):
        asset = SimpleNamespace(
            id="SAME",
            filename=scope,
            created=datetime(2026, 1, 1),
            download_url="OMIT_ME",
            latitude="OMIT_ME",
        )
        album = MagicMock()
        album.title = scope
        album.__iter__.side_effect = lambda item=asset: iter([item])
        libraries[scope] = SimpleNamespace(all=album, albums=[album])
    client.photos.libraries = libraries
    tools = adapters(client)
    discovered = await tools.photo_libraries(50)
    assert len(discovered.records) == 2
    for row, scope in zip(discovered.records, libraries, strict=True):
        albums = await tools.photo_albums(row["handle"], 50)
        assert albums.records[1]["title"] == scope
        photos = await tools.photos(albums.records[1]["handle"], 1)
        assert photos.records[0]["filename"] == scope
        assert "OMIT_ME" not in photos.model_dump_json()
    assert len((await tools.photo_libraries(1)).records) == 1
    assert (
        len((await tools.photo_albums(discovered.records[0]["handle"], 1)).records) == 1
    )


@pytest.mark.asyncio
async def test_mail_metadata_and_subject_search_never_expose_bodies_or_previews() -> (
    None
):
    """Typed mailbox models are retained; subject search does not query body bytes."""
    client = MagicMock()
    folder = MailFolder(identifier="F", name="Inbox", uidValidity=123)
    message = MailMessage.model_validate({
        "identifier": "M",
        "uid": 1,
        "mboxRef": {"id": "F"},
        "folder": folder,
        "subject": "Synthetic",
        "flags": 1,
        "from": "OMIT_ME",
    })
    client.mail.folders.return_value = [folder]
    client.mail.messages.return_value = SimpleNamespace(messages=[message])
    client.mail.search.return_value = SimpleNamespace(
        results=[MailSearchHit(identifier="T", subject="Match", preview="OMIT_ME")]
    )
    tools = adapters(client)
    handle = (await tools.mail_folders(50)).records[0]["handle"]
    messages = (await tools.mail_messages(handle, 50)).records
    assert [
        {key: value for key, value in row.items() if key != "handle"}
        for row in messages
    ] == [{"id": "M", "subject": "Synthetic", "flags": 1}]
    result = await tools.mail_search(handle, "Match", 50)
    assert result.records == [{"thread_id": "T", "subject": "Match"}]
    assert client.mail.search.call_args.kwargs == {"field": "subject", "limit": 50}
    assert client.mail.search.call_args.args[0] is folder
    assert (await tools.mail_search(handle, " ", 50)).error == "invalid_argument"
    assert (await tools.mail_search(handle, "x" * 257, 50)).error == "invalid_argument"
    client.mail.message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (NotesAuthError("private"), "authentication_required"),
        (PhotosAuthError("private"), "authentication_required"),
        (NotesRateLimited("private"), "rate_limited"),
        (PhotosRateLimited("private"), "rate_limited"),
        (NotesZoneNotFoundError("private"), "service_unavailable"),
        (PhotosZoneNotFoundError("private"), "service_unavailable"),
    ],
)
async def test_additional_service_error_categories(error: Exception, code: str) -> None:
    """Service-specific exceptions remain sanitized without losing their category."""
    client = MagicMock()
    client.notes.recents.side_effect = error
    assert (await adapters(client).notes("", 50)).error == code


@pytest.mark.asyncio
async def test_additional_tool_registration_and_disabled_default() -> None:
    """New services are explicit opt-ins and every wrapper delegates safely."""
    client = MagicMock()
    client.contacts.all = []
    client.drive.root = SimpleNamespace(
        type="folder", get_children=MagicMock(return_value=[])
    )
    client.notes.recents.return_value = iter([])
    client.photos.libraries = {}
    client.mail.folders.return_value = []
    server = build_server(owner=owner_for(client))
    tools = await server.list_tools()
    assert len(tools) == 20
    for name, args in [
        ("icloud_contacts_list", {}),
        ("icloud_contact_details", {"handle": "unknown"}),
        ("icloud_drive_list", {}),
        ("icloud_drive_trash_list", {}),
        ("icloud_notes_list", {}),
        ("icloud_photos_libraries", {}),
        ("icloud_photos_albums", {"library_handle": "unknown"}),
        ("icloud_photos_list", {"album_handle": "unknown"}),
        ("icloud_mail_folders", {}),
        ("icloud_mail_messages", {"folder_handle": "unknown"}),
        ("icloud_mail_search", {"folder_handle": "unknown", "query": "subject"}),
    ]:
        result = await server.call_tool(name, args)
        assert isinstance(result, CallToolResult)
        assert not result.is_error
    assert len(await build_server().list_tools()) == 2
    disabled = AdditionalReads(ReadTools(owner_for(client, services=())))
    assert (await disabled.contacts("", 50)).error == "service_disabled"
    assert (await adapters(client).contacts("", 0)).error == "invalid_argument"
