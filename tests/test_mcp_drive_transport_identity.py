"""Offline adapter identity, receipt continuation and injected download limits."""

# pylint: disable=protected-access

import io
from threading import Thread
from typing import Any, cast
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from requests import Response, Session

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import DownloadState, download_chunk
from pyicloud.mcp.context import BoundedAdapter, ServerConfig, SessionOwner, ToolFailure
from pyicloud.mcp.drive_upload_bytes import DriveUploadBytes, validate_upload_session
from pyicloud.mcp.header_deadline import DeadlineRaw
from pyicloud.mcp.upload_transport import (
    UploadDispatchState,
    UploadNotDispatched,
    UploadTransport,
)
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_content import content_for
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


def test_header_raw_reads_respect_shorter_adapter_timeout() -> None:
    """An absolute owner deadline must not increase an individual read timeout."""
    sock = MagicMock()
    raw = DeadlineRaw(io.BytesIO(b"data"), sock, 100, timeout=2)
    with patch("pyicloud.mcp.header_deadline.time.monotonic", return_value=10):
        assert raw.readinto(bytearray(4)) == 4
    sock.settimeout.assert_called_once_with(2)
    raw.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [False, True])
async def test_noop_move_requires_both_live_handles(source: bool) -> None:
    """No-op outcomes preserve live handles but cannot spend expired selections."""
    writer, client, handle, destination, data, folder = setup_move()
    data["parentId"] = folder["drivewsid"]
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    selected = handle if source else destination
    kind, resource, _expiry = writer.additional._handles[selected]
    writer.additional._handles[selected] = (kind, resource, 0)
    with pytest.raises(WriteDenied, match="target_unavailable"):
        await writer.apply(preview.request_id)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
async def test_separate_document_adapters_are_owned_and_reset_each_operation() -> None:
    """Both registration adapters receive the same budget and fresh request slots."""
    writer, client, _handle, _token, _data, _pipeline = setup_adapter()
    client.get_auth_status.return_value = {"authenticated": True}
    registration, commit = BoundedAdapter(120), BoundedAdapter(120)
    registration.requests_left = commit.requests_left = 0
    client.drive.session.get_adapter.side_effect = lambda url: (
        registration if "/upload/web" in url else commit
    )
    owner = SessionOwner(
        ServerConfig(username="synthetic", session_dir="synthetic", timeout=1),
        lambda: client,
    )
    writer.additional.reads.owner = owner

    def operation(active: Any) -> None:
        deadline = validate_upload_session(
            active.drive,
            {"token": "synthetic"},
            bind_adapter=writer._bind_transport_adapter,
        )
        assert deadline == owner.active_deadline
        for adapter in (registration, commit):
            assert adapter.deadline == deadline
            assert adapter.requests_left == 32
            adapter.requests_left = 0

    try:
        with patch("threading.Thread", Thread):
            await owner.run(operation)
            await owner.run(operation)
        assert registration.deadline is None
        assert commit.deadline is None
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_receipt_is_not_accepted_twice_after_proven_no_dispatch() -> None:
    """An explicit retry reuses the accepted decision, not a consumed receipt file."""
    writer, _client, handle, token, _data, _pipeline = setup_adapter()
    writer.pipeline = DriveUploadBytes(UploadTransport(writer.hosts))
    preview = await writer.prepare_upload(handle, token, 1000)
    accepted: list[str] = []

    def accept(request_id: str) -> None:
        assert not accepted
        accepted.append(request_id)
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)

    clock = [10.0]

    def mime(_name: str) -> tuple[None, None]:
        clock[0] = 100
        return None, None

    with (
        patch(
            "pyicloud.mcp.drive_upload_bytes.time.monotonic",
            side_effect=lambda: clock[0],
        ),
        patch("pyicloud.mcp.drive_upload_bytes.mimetypes.guess_type", side_effect=mime),
    ):
        with pytest.raises(UploadNotDispatched):
            await writer.apply(preview.request_id, accept)
        clock[0] = 10
        writer.pipeline = MagicMock()
        assert (await writer.apply(preview.request_id, accept))["acknowledged"]
    assert accepted == [preview.request_id]
    assert not writer._accepted_receipts


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["?bad=1", "#bad", "?", "#"])
async def test_drive_service_root_delimiters_are_denied_before_preview(
    suffix: str,
) -> None:
    """Appended mutation paths cannot become query or fragment data."""
    writer, client, handle, _data = setup_writer()
    client.drive.service_root += suffix
    with pytest.raises(WriteDenied, match="route_unavailable"):
        await writer.prepare(handle, {"name": "After"})
    client.drive.get_node_data.assert_not_called()
    client.drive.rename_items.assert_not_called()


def test_streamed_download_stops_before_materializing_full_body() -> None:
    """Ignoring Range cannot force a content property or unbounded iterator read."""
    response = Response()
    response.status_code = 200
    response.raw = cast(Any, io.BytesIO())
    chunks = [b"x" * 4, b"y" * 4, b"never"]
    touched: list[bytes] = []

    def stream(**_kwargs: Any) -> Any:
        for chunk in chunks:
            touched.append(chunk)
            yield chunk

    cast(Any, response).iter_content = stream
    state = DownloadState(object())
    with (
        patch.object(
            Response,
            "content",
            new_callable=PropertyMock,
            side_effect=AssertionError("Full body forbidden"),
        ),
        pytest.raises(ToolFailure, match="content_too_large"),
    ):
        download_chunk(state, response, 0, 4, "file")
    assert touched == chunks[:2]


@pytest.mark.asyncio
async def test_injected_plain_drive_download_is_denied_before_sdk_read() -> None:
    """Token discovery and final body reads require bounded injected transports."""
    session = Session()
    session.trust_env = False
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", cast(Any, session), {}
    )
    client = MagicMock()
    tools = content_for(client)
    handle = tools.additional.remember(
        "drive-file",
        DriveNode(
            drive,
            {
                "drivewsid": "FILE::Z::F",
                "docwsid": "F",
                "zone": "Z",
                "name": "file",
                "type": "FILE",
                "size": 1,
            },
        ),
    )
    with patch("requests.adapters.HTTPAdapter.send") as send:
        assert (
            await tools.file("drive", handle, 0, 4)
        ).error == "unsupported_download_transport"
    send.assert_not_called()
    session.close()


def test_registration_uses_marked_adapter_even_if_selection_would_change() -> None:
    """A lost response cannot be mistaken for no dispatch on another adapter."""
    session = cast(Any, Session())
    session.trust_env = False
    marked, other = BoundedAdapter(30), BoundedAdapter(30)
    session.get_adapter = MagicMock(side_effect=[marked, other])
    state = UploadDispatchState()
    hosts = frozenset({"example.test"})
    with (
        patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=OSError("Synthetic lost response"),
        ) as send,
        pytest.raises(WriteDenied, match="upload_outcome_uncertain"),
    ):
        UploadTransport.register(
            session,
            "https://example.test/register",
            hosts,
            {},
            dispatch_state=state,
        )
    assert state.attempted
    session.get_adapter.assert_called_once()
    send.assert_called_once()
    assert marked.before_dispatch is None
    assert other.before_dispatch is None
    session.close()


def test_bounded_injected_download_adapter_receives_read_budget() -> None:
    """Accepted injected download sessions retain body bounds and an absolute budget."""
    session = Session()
    session.trust_env = False
    adapter = BoundedAdapter(120)
    session.mount("https://", adapter)
    tools = content_for(MagicMock())
    tools._guard_download_session(session)
    assert adapter.deadline is not None
    session.close()
