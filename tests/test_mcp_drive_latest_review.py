"""Offline regressions for proxy, alias, deadline and move evidence boundaries."""

# pylint: disable=protected-access
# cspell:words poolmanager

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from urllib3.connection import HTTPSConnection

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import DownloadState
from pyicloud.mcp.context import BoundedAdapter, ToolFailure
from pyicloud.mcp.drive_upload_bytes import validate_upload_session
from pyicloud.mcp.header_deadline import DeadlineSocket, HeaderDeadlineExceeded
from pyicloud.services.drive import DriveNode
from tests.test_mcp_content import content_for
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.parametrize(
    "proxy", ["http://proxy.example.test", "https://proxy.example.test"]
)
def test_proxy_pool_has_absolute_header_deadline(proxy: str) -> None:
    """Proxy HTTPS pools must use the same deadline-aware connections."""
    adapter = BoundedAdapter(30)
    adapter.deadline = 10
    manager = adapter.proxy_manager_for(proxy)
    connection = cast(
        Any, manager.pool_classes_by_scheme["https"].ConnectionCls("example.test")
    )
    original = MagicMock()
    connection.sock = original

    def response(current: Any) -> object:
        assert isinstance(current.sock, DeadlineSocket)
        assert current.sock.deadline == 10
        return object()

    with patch.object(HTTPSConnection, "getresponse", response):
        connection.getresponse()
    assert connection.sock is original
    connection.close()
    adapter.close()


def test_unsupported_proxy_fails_before_dispatch() -> None:
    """An unsupported proxy must not silently bypass the deadline transport."""
    adapter = BoundedAdapter(30)
    with pytest.raises(ToolFailure, match="unsupported_proxy"):
        adapter.proxy_manager_for("ftp://proxy.example.test")
    adapter.close()


@pytest.mark.parametrize("detach", [False, True])
def test_proxy_connect_deadline_and_detached_socket_state(detach: bool) -> None:
    """CONNECT headers get the same budget, and detached sockets stay detached."""
    adapter = BoundedAdapter(30)
    adapter.deadline = 10
    pool = adapter.poolmanager.pool_classes_by_scheme["https"]
    connection = cast(Any, pool.ConnectionCls("example.test"))
    original = MagicMock()
    connection.sock = original

    def tunnel(current: Any) -> None:
        assert isinstance(current.sock, DeadlineSocket)
        assert current.sock.deadline == 10
        if detach:
            current.sock = None

    with patch.object(HTTPSConnection, "_tunnel", tunnel):
        connection._tunnel()
    assert connection.sock is (None if detach else original)
    connection.sock = None
    with pytest.raises(HeaderDeadlineExceeded):
        connection._tunnel()
    adapter.close()


def test_close_response_does_not_restore_a_detached_socket() -> None:
    """urllib3's Connection-close detach survives the temporary socket wrapper."""
    adapter = BoundedAdapter(30)
    pool = adapter.poolmanager.pool_classes_by_scheme["https"]
    connection = cast(Any, pool.ConnectionCls("example.test"))
    connection.sock = MagicMock()

    def response(current: Any) -> object:
        current.sock = None
        return object()

    with patch.object(HTTPSConnection, "getresponse", response):
        connection.getresponse()
    assert connection.sock is None
    adapter.close()


@pytest.mark.asyncio
async def test_expired_move_source_preserves_destination_and_approval() -> None:
    """Atomic handle admission fails before retiring any other selected resource."""
    writer, client, handle, destination, _data, _folder = setup_move()
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    original = writer._pair

    def expired(*args: Any) -> Any:
        result = original(*args)
        kind, resource, _expiry = writer.additional._handles[handle]
        writer.additional._handles[handle] = (kind, resource, 0)
        return result

    with (
        patch.object(writer, "_pair", side_effect=expired),
        pytest.raises(WriteDenied, match="target_unavailable"),
    ):
        await writer.apply(preview.request_id)
    assert writer.additional.resolve("drive-folder", destination)
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    client.drive.move_nodes_to_node.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("shareID", "foreign"),
        ("zone", "foreign"),
        ("etag", "different"),
        ("parentId", "other"),
    ],
)
async def test_move_listing_metadata_must_match_detail(field: str, value: str) -> None:
    """Identifier and name alone are not a private revision-consistent proof."""
    writer, client, handle, destination, data, folder = setup_move()
    preview = await writer.prepare_move(handle, destination, {})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)

    def moved(*_args: Any, **_kwargs: Any) -> None:
        data.update(parentId=folder["drivewsid"], etag="v2")
        folder.update(etag="D2", items=[{**data, field: value}])

    client.drive.move_nodes_to_node.side_effect = moved
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    client.drive.move_nodes_to_node.assert_called_once()


@pytest.mark.asyncio
async def test_plaintext_route_cannot_publish_preview() -> None:
    """Neither metadata reads nor mutation selectors may go to HTTP."""
    writer, client, handle, _data = setup_writer()
    client.drive.service_root = "http://drive.example.test"
    publish = MagicMock()
    with pytest.raises(WriteDenied, match="insecure_mutation_route"):
        await writer.prepare(handle, {"name": "After"}, publish)
    publish.assert_not_called()
    client.drive.get_node_data.assert_not_called()
    client.drive.rename_items.assert_not_called()


@pytest.mark.asyncio
async def test_rename_retires_every_alias_for_identifier_and_zone() -> None:
    """A second discovered handle cannot retain an old downloadable filename."""
    writer, client, handle, data = setup_writer()
    alias = writer.additional.remember(
        "drive-file", DriveNode(client.drive, dict(data))
    )
    other = writer.additional.remember(
        "drive-file", DriveNode(client.drive, {**data, "zone": "other"})
    )
    preview = await writer.prepare(handle, {"name": "After"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)

    def renamed(*_args: Any, **_kwargs: Any) -> None:
        with pytest.raises(ToolFailure):
            writer.additional.resolve("drive-file", alias)
        data.update(name="After", etag="v2")
        data.pop("extension", None)

    client.drive.rename_items.side_effect = renamed
    assert (await writer.apply(preview.request_id))["changed"]
    assert writer.additional.resolve("drive-file", other)


@pytest.mark.asyncio
async def test_displayed_leaf_rename_is_noop_and_preserves_handle() -> None:
    """Only the actual displayed leaf, including extension, is a rename no-op."""
    writer, client, handle, _data = setup_writer()
    preview = await writer.prepare(handle, {"name": "Before.txt"})
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    assert not (await writer.apply(preview.request_id))["changed"]
    assert writer.additional.resolve("drive-file", handle)
    client.drive.rename_items.assert_not_called()


@pytest.mark.parametrize("repeated", [False, True])
def test_transfer_token_collision_has_bounded_retry(repeated: bool) -> None:
    """Rare random collisions cannot overwrite another transfer or imply capacity."""
    tools = content_for(MagicMock())
    tools._downloads["used"] = DownloadState(object())
    tokens = ["used"] * 4 if repeated else ["used", "fresh"]
    with patch("pyicloud.mcp.content.token_urlsafe", side_effect=tokens) as generate:
        if repeated:
            with pytest.raises(ToolFailure, match="download_token_unavailable"):
                tools._new_transfer_token()
        else:
            assert tools._new_transfer_token() == "fresh"
    assert generate.call_count == (4 if repeated else 2)


@pytest.mark.parametrize("expired", [False, True])
def test_upload_admission_carries_earliest_owner_deadline(expired: bool) -> None:
    """The reservation and commit budgets cannot be renewed after metadata reads."""
    _writer, client, _handle, _token, _data, _pipeline = setup_adapter()
    adapter = BoundedAdapter(30)
    adapter.deadline = 0 if expired else 20
    client.drive.session.get_adapter.return_value = adapter
    with patch("pyicloud.mcp.drive_upload_bytes.time.monotonic", return_value=10):
        if expired:
            with pytest.raises(WriteDenied, match="upload_deadline_exceeded"):
                validate_upload_session(client.drive, {"token": "synthetic"})
        else:
            assert validate_upload_session(client.drive, {"token": "synthetic"}) == 20
