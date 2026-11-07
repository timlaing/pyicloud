"""Explicit content permission, UTF-8 budgets, scoped downloads and searches."""

# pylint: disable=protected-access

import base64
from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.content import (
    ContentReads,
    DownloadState,
    clipped_text,
    download_chunk,
    resource_url,
)
from pyicloud.mcp.context import (
    SUPPORTED_SERVICES,
    ServerConfig,
    SessionOwner,
    ToolFailure,
)
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.server import build_server
from pyicloud.services.drive import DriveNode, DriveService


@pytest.mark.asyncio
async def test_fresh_download_resets_previous_entity_tag() -> None:
    """A new offset-zero read starts a new version, unlike a resumed read."""
    client = MagicMock()
    tools = content_for(client)
    open_mock = MagicMock(
        side_effect=[
            response(206, b"data", **{"Content-Range": "bytes 0-3/8", "ETag": '"v1"'}),
            response(206, b"new!", **{"Content-Range": "bytes 0-3/8", "ETag": '"v2"'}),
            response(206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v2"'}),
        ]
    )
    resource = SimpleNamespace(name="file", open=open_mock)
    handle = tools.additional.remember("drive-file", resource)
    assert (await tools.file("drive", handle, 0, 4)).error is None
    assert (await tools.file("drive", handle, 0, 4)).error is None
    assert "If-Match" not in open_mock.call_args.kwargs["headers"]
    assert (await tools.file("drive", handle, 4, 4)).complete
    assert open_mock.call_args.kwargs["headers"]["If-Match"] == '"v2"'


@pytest.mark.parametrize("token", ["data_token", "package_token"])
def test_drive_token_url_is_checked_before_credential_bearing_request(
    token: str,
) -> None:
    """Metadata may never redirect the saved-session download onto a foreign host."""
    drive = MagicMock(spec=DriveService)
    drive.params = {}
    drive._document_root = "https://docws.icloud.com"
    drive.session.get.return_value.json.return_value = {
        token: {"url": "https://evil.example/file"}
    }
    with pytest.raises(ToolFailure, match="invalid_resource_url"):
        DriveService.get_file(drive, "file", download_url_validator=resource_url)
    drive.session.get.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, "0"])
async def test_real_zero_byte_drive_node_never_opens_transport(size: int | str) -> None:
    """Raw zero size must work even when the public size property returns None."""
    client = MagicMock()
    tools = content_for(client)
    data = {"drivewsid": "F", "name": "empty", "type": "FILE", "size": size}
    node = DriveNode(client.drive, data)
    client.drive.get_node_data.return_value = dict(data)
    handle = tools.additional.remember("drive-file", node)
    result = await tools.file("drive", handle, 0, 4)
    assert result.error is None
    assert result.records[0]["data_base64"] == ""
    client.drive.get_file.assert_not_called()
    client.drive.get_node_data.assert_called_once_with("F", None)
    assert (await tools.file("drive", handle, 1, 4)).error == "invalid_download_range"


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, "0"])
async def test_cached_empty_drive_file_can_grow_before_download(
    size: int | str,
) -> None:
    """Fresh metadata prevents a cached zero from hiding newly available bytes."""
    client = MagicMock()
    data = {
        "drivewsid": "F",
        "docwsid": "D",
        "zone": "Z",
        "name": "file",
        "type": "FILE",
        "size": size,
    }
    client.drive.get_node_data.return_value = {**data, "size": 4}
    client.drive.get_file.return_value = response(200, b"data")
    tools = content_for(client)
    handle = tools.additional.remember("drive-file", DriveNode(client.drive, data))
    result = await tools.file("drive", handle, 0, 4)
    assert result.error is None
    assert result.records[0]["data_base64"] == "ZGF0YQ=="
    client.drive.get_node_data.assert_called_once_with("F", None)
    client.drive.get_file.assert_called_once()


def owner_for(
    client: Any,
    content_services: tuple[Any, ...] = ("drive", "notes", "photos", "mail"),
) -> SessionOwner:
    """Synthetic lane with explicit operator content permission, no account access."""

    async def run(operation: Callable[[Any], Any]) -> Any:
        return operation(client)

    return cast(
        SessionOwner,
        SimpleNamespace(
            config=ServerConfig(
                username="synthetic",
                session_dir="unused",
                services=SUPPORTED_SERVICES,
                content_services=content_services,
            ),
            authenticated=False,
            run=run,
            close=AsyncMock(),
        ),
    )


def content_for(
    client: Any, enabled: tuple[Any, ...] = ("drive", "notes", "photos", "mail")
) -> ContentReads:
    """Use the same scoped registry as metadata, not arbitrary IDs or URLs."""
    return ContentReads(AdditionalReads(ReadTools(owner_for(client, enabled))))


def response(status: int = 200, data: bytes = b"data", **headers: str) -> MagicMock:
    """A fully synthetic body with observable resource cleanup."""
    result = MagicMock()
    result.status_code = status
    result.content = data
    result.headers = headers
    return result


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content_services": ("notes",)},
        {
            "username": "x",
            "session_dir": "x",
            "services": ("contacts",),
            "content_services": ("contacts",),
        },
    ],
)
def test_content_configuration_requires_matching_service_opt_in(
    kwargs: dict[str, Any],
) -> None:
    """Metadata permission alone never grants content access."""
    with pytest.raises(ValueError):
        ServerConfig(**kwargs)


def test_utf8_clip_and_url_guard() -> None:
    """No broken code points, arbitrary hosts, credentials or non-HTTPS resources."""
    assert clipped_text("éé", 3) == ("é", False)
    assert resource_url("https://cvws.icloud-content.com/resource?signature=synthetic")
    for value in (
        None,
        "http://icloud.com/x",
        "https://icloud.com.evil/x",
        "https://user:secret@icloud.com/x",
        "https://icloud.com:bad/x",
        "https://icloud.com:8443/x",
    ):
        with pytest.raises(ToolFailure, match="invalid_resource_url"):
            resource_url(value)


@pytest.mark.parametrize(
    "status,data,headers,offset,error",
    [
        (200, b"too large", {}, 0, "content_too_large"),
        (403, b"", {}, 0, "download_rejected"),
        (206, b"data", {}, 0, "invalid_download_range"),
        (206, b"data", {"Content-Range": "bytes 1-4/8"}, 0, "invalid_download_range"),
        (
            206,
            b"data",
            {"Content-Range": "bytes 0-3/8"},
            0,
            "download_requires_strong_etag",
        ),
        (
            206,
            b"data",
            {"Content-Range": "bytes 0-3/8", "ETag": 'W/"weak"'},
            0,
            "download_requires_strong_etag",
        ),
        (
            206,
            b"data",
            {"Content-Range": "bytes 0-3/8", "ETag": "unquoted"},
            0,
            "download_requires_strong_etag",
        ),
        (
            206,
            b"data",
            {"Content-Range": "bytes 0-3/8", "ETag": '"bad\nvalue"'},
            0,
            "download_requires_strong_etag",
        ),
    ],
)
def test_invalid_binary_responses_are_closed(
    status: int, data: bytes, headers: dict[str, str], offset: int, error: str
) -> None:
    """No partial binary output is mislabeled or assembled across unverified ranges."""
    reply = response(status, data, **headers)
    state = DownloadState(None)
    with pytest.raises(ToolFailure, match=error):
        download_chunk(state, reply, offset, 4, "file")
    reply.close.assert_called_once()


def test_download_version_is_bound_across_chunks() -> None:
    """Resume only against the same strong ETag; reject a replaced resource."""
    state = DownloadState(None)
    first = download_chunk(
        state,
        response(206, b"data", **{"Content-Range": "bytes 0-3/8", "ETag": '"v1"'}),
        0,
        4,
        "file",
    )
    assert first.records[0]["next_offset"] == 4
    assert not first.complete
    changed = response(206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v2"'})
    with pytest.raises(ToolFailure, match="stale_download"):
        download_chunk(state, changed, 4, 4, "file")
    last = download_chunk(
        state,
        response(206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v1"'}),
        4,
        4,
        "file",
    )
    assert last.complete
    assert last.records[0]["next_offset"] is None


@pytest.mark.asyncio
async def test_note_and_mail_content_are_explicit_and_bounded() -> None:
    """Bodies are untrusted data; headers/attachments remain omitted."""
    client = MagicMock()
    disabled = content_for(client, ())
    assert (await disabled.note("unknown", 10)).error == "content_disabled"
    tools = content_for(client)
    note = tools.additional.remember("note", "N")
    client.notes.get.return_value = SimpleNamespace(
        is_locked=False, title="Note", text="éé"
    )
    result = await tools.note(note, 3)
    assert result.records[0]["text"] == "é"
    assert not result.complete
    client.notes.get.assert_called_once_with("N", with_attachments=False)
    client.notes.get.return_value.is_locked = True
    assert (await tools.note(note, 10)).error == "content_unavailable"
    assert (await tools.note(note, 0)).error == "invalid_argument"
    previous = SimpleNamespace(folder="F", id="M")
    message = tools.additional.remember("mail-message", previous)
    client.mail.message.return_value = SimpleNamespace(subject="Subject")
    client.mail.body.return_value = SimpleNamespace(
        content_type="text/html",
        parts=[
            SimpleNamespace(content="<b>untrusted</b>"),
            SimpleNamespace(content="truncated"),
        ],
        headers="OMIT_ME",
    )
    result = await tools.mail_body(message, 5)
    assert result.records[0]["parts"] == [{"content": "<b>un"}]
    assert result.records[0]["untrusted"] is True
    assert "OMIT_ME" not in result.model_dump_json()
    client.mail.message.return_value = None
    assert (await tools.mail_body(message, 10)).error == "content_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parts", "maximum", "expected", "complete"),
    [
        (["éé"], 4, ["éé"], True),
        (["é", "é"], 4, ["é", "é"], True),
        (["éé", "later"], 4, ["éé"], False),
        (["éé"], 3, ["é"], False),
        (["éé", "later"], 3, ["é"], False),
    ],
)
async def test_mail_body_exact_budget_completeness(
    parts: list[str], maximum: int, expected: list[str], complete: bool
) -> None:
    """Exact final UTF-8 budgets are complete, unlike clipped or omitted parts."""
    client = MagicMock()
    tools = content_for(client)
    handle = tools.additional.remember(
        "mail-message", SimpleNamespace(folder="F", id="M")
    )
    client.mail.message.return_value = SimpleNamespace(subject="Subject")
    client.mail.body.return_value = SimpleNamespace(
        content_type="text/plain",
        parts=[SimpleNamespace(content=part) for part in parts],
    )
    result = await tools.mail_body(handle, maximum)
    assert result.error is None
    assert result.records[0]["parts"] == [{"content": part} for part in expected]
    assert result.complete is complete


@pytest.mark.asyncio
async def test_drive_and_photo_downloads_never_accept_paths_or_urls() -> None:
    """Downloads require discovered resources and preserve range/version state."""
    client = MagicMock()
    get_mock = MagicMock()
    client.session = SimpleNamespace(get=get_mock)
    tools = content_for(client)
    file = SimpleNamespace(
        name="file",
        open=MagicMock(
            side_effect=[
                response(
                    206, b"data", **{"Content-Range": "bytes 0-3/8", "ETag": '"v1"'}
                ),
                response(
                    206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v1"'}
                ),
            ]
        ),
    )
    handle = tools.additional.remember("drive-file", file)
    assert (
        await tools.file("drive", handle, 4, 4)
    ).error == "download_requires_strong_etag"
    assert not (await tools.file("drive", handle, 0, 4)).complete
    assert (await tools.file("drive", handle, 4, 4)).complete
    assert file.open.call_args.kwargs["headers"]["If-Match"] == '"v1"'
    empty_data = {"drivewsid": "F", "name": "empty", "type": "FILE", "size": 0}
    client.drive.get_node_data.return_value = dict(empty_data)
    empty = tools.additional.remember("drive-file", DriveNode(client.drive, empty_data))
    assert (await tools.file("drive", empty, 0, 4)).records[0]["data_base64"] == ""
    assert (await tools.file("drive", empty, 1, 4)).error == "invalid_download_range"
    photo = SimpleNamespace(
        filename="photo",
        download_url=MagicMock(
            return_value="https://cvws.icloud-content.com/synthetic"
        ),
    )
    photo_handle = tools.additional.remember("photo-asset", photo)
    get_mock.return_value = response()
    assert (await tools.file("photos", photo_handle, 0, 4)).complete
    assert get_mock.call_args is not None
    assert get_mock.call_args.kwargs["allow_redirects"] is False
    assert (
        await tools.file("drive", "/local/path", 0, 4)
    ).error == "expired_or_unknown_handle"
    assert (await tools.file("drive", handle, -1, 4)).error == "invalid_argument"
    assert (await tools.file("mail", handle, 0, 4)).error == "invalid_argument"
    tools._downloads = {str(index): DownloadState(None) for index in range(1000)}
    get_mock.return_value = response()
    assert (await tools.file("photos", photo_handle, 0, 4, "medium")).complete


@pytest.mark.asyncio
async def test_searches_have_scan_budgets_and_body_permission() -> None:
    """Title, filename and recursive-name searches have explicit scopes."""
    client = MagicMock()
    tools = content_for(client).additional
    notes = [
        SimpleNamespace(
            id="N", title=title, is_deleted=deleted, modified_at=None, is_locked=False
        )
        for title, deleted in [("Match", True), ("Other", False), ("Match", False)]
    ]
    client.notes.iter_all.side_effect = lambda: iter(notes)
    result = await tools.search("notes", "", "Match", 1, 10)
    assert result.records[0]["title"] == "Match"
    album = tools.remember(
        "photo-album",
        [SimpleNamespace(id="P", filename="Match", created=datetime(2026, 1, 1))],
    )
    assert (await tools.search("photos", album, "Match", 1, 10)).records[0][
        "filename"
    ] == "Match"
    file = SimpleNamespace(type="file", name="Match", date_modified=None)
    child = SimpleNamespace(
        type="folder",
        name="Other",
        date_modified=None,
        get_children=MagicMock(return_value=[file]),
    )
    client.drive.root = SimpleNamespace(
        type="folder", get_children=MagicMock(return_value=[child])
    )
    assert (await tools.search("drive", "", "Match", 1, 10)).records[0][
        "name"
    ] == "Match"
    assert (await tools.search("drive", "", "", 1, 10)).error == "invalid_argument"
    bad = tools.remember("drive-folder", SimpleNamespace(type="file"))
    assert (
        await tools.search("drive", bad, "Match", 1, 10)
    ).error == "invalid_argument"
    folder = tools.remember("mail-folder", "F")
    client.mail.search.return_value = SimpleNamespace(results=[])
    await tools.mail_search(folder, "Match", 50, "body")
    assert client.mail.search.call_args.kwargs["field"] == "body"
    disabled = content_for(client, ()).additional
    assert (
        await disabled.mail_search("unknown", "Match", 50, "body")
    ).error == "content_disabled"
    assert (
        await tools.mail_search(folder, "Match", 50, "unknown")
    ).error == "invalid_argument"


@pytest.mark.asyncio
async def test_valid_search_and_content_wrappers_use_their_services() -> None:
    """Valid protocol calls preserve service routing and fixture content."""
    client = MagicMock()
    get_mock = MagicMock()
    client.session = SimpleNamespace(get=get_mock)
    tools = content_for(client)
    note = SimpleNamespace(
        id="N",
        title="Note",
        is_deleted=False,
        modified_at=None,
        is_locked=False,
        text="Note body",
    )
    client.notes.iter_all.side_effect = lambda: iter([note])
    client.notes.get.return_value = note
    file = SimpleNamespace(
        name="Drive",
        type="file",
        date_modified=None,
        open=MagicMock(return_value=response(200, b"drive")),
    )
    client.drive.root = SimpleNamespace(
        type="folder", get_children=MagicMock(return_value=[file])
    )
    photo = SimpleNamespace(
        id="P",
        filename="Photo",
        created=None,
        download_url=MagicMock(return_value="https://icloud-content.com/photo"),
    )
    get_mock.return_value = response(200, b"photo")
    client.mail.message.return_value = SimpleNamespace(subject="Mail")
    client.mail.body.return_value = SimpleNamespace(
        content_type="text/plain", parts=[SimpleNamespace(content="Mail body")]
    )
    album_handle = tools.additional.remember("photo-album", [photo])
    handles = {
        "icloud_note_content": tools.additional.remember("note", "N"),
        "icloud_mail_content": tools.additional.remember(
            "mail-message", SimpleNamespace(folder="F", id="M")
        ),
        "icloud_drive_download": tools.additional.remember("drive-file", file),
        "icloud_photo_download": tools.additional.remember("photo-asset", photo),
    }
    with patch("pyicloud.mcp.server.AdditionalReads", return_value=tools.additional):
        server = build_server(owner=owner_for(client))
    available = {tool.name for tool in await server.list_tools()}
    for name, args, field, expected in [
        ("icloud_notes_search", {"query": "Note"}, "title", "Note"),
        ("icloud_drive_search", {"query": "Drive"}, "name", "Drive"),
        (
            "icloud_photos_search",
            {"query": "Photo", "album_handle": album_handle},
            "filename",
            "Photo",
        ),
    ]:
        if name not in available:
            continue
        result = await server.call_tool(name, args)
        assert isinstance(result, CallToolResult)
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["error"] is None
        assert result.structured_content["records"][0][field] == expected
    for name, field, content_value in [
        ("icloud_note_content", "text", "Note body"),
        ("icloud_mail_content", "parts", [{"content": "Mail body"}]),
        ("icloud_drive_download", "data_base64", base64.b64encode(b"drive").decode()),
        ("icloud_photo_download", "data_base64", base64.b64encode(b"photo").decode()),
    ]:
        if name not in available:
            continue
        result = await server.call_tool(name, {"handle": handles[name]})
        assert isinstance(result, CallToolResult)
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["error"] is None
        assert result.structured_content["records"][0][field] == content_value


@pytest.mark.asyncio
async def test_content_tool_registration_is_opt_in() -> None:
    """Metadata-only launch never registers content/download tools."""
    client = MagicMock()
    server = build_server(owner=owner_for(client))
    names = {tool.name for tool in await server.list_tools()}
    assert {"icloud_status", "icloud_capabilities"} <= names
    for name, args in [
        ("icloud_note_content", {"handle": "unknown"}),
        ("icloud_mail_content", {"handle": "unknown"}),
        ("icloud_drive_download", {"handle": "unknown"}),
        ("icloud_photo_download", {"handle": "unknown"}),
        ("icloud_notes_search", {"query": ""}),
        ("icloud_photos_search", {"album_handle": "unknown", "query": ""}),
        ("icloud_drive_search", {"query": ""}),
    ]:
        if name not in names:
            continue
        result = await server.call_tool(name, args)
        assert isinstance(result, CallToolResult)
        assert result.is_error is (name == "icloud_drive_download")
    metadata = build_server(owner=owner_for(client, ()))
    assert not any(
        tool.name.endswith("_download") or tool.name.endswith("_content")
        for tool in await metadata.list_tools()
    )
