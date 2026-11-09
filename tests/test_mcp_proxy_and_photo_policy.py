"""Offline optional proxy routing and Photos approval policy regressions."""

# pylint: disable=protected-access
# cspell:words connectionpool

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Session
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.mcp.header_deadline import DeadlineSocket
from pyicloud.services.drive import DriveService
from tests.test_mcp_additional import adapters
from tests.test_mcp_photo_uploads import setup_upload


def test_optional_proxy_pool_preserves_routed_connection_class() -> None:
    """Deadline wrapping retains optional SOCKS connect behavior rather than HTTP."""
    marker = object()

    class RoutedConnection(HTTPSConnection):
        """Synthetic optional SOCKS connection establishment, without sockets."""

        def _new_conn(self) -> Any:
            return marker

    class RoutedPool(HTTPSConnectionPool):
        """Synthetic optional proxy pool with its routing connection class."""

        ConnectionCls = RoutedConnection

    manager = SimpleNamespace(pool_classes_by_scheme={"https": RoutedPool})
    adapter = BoundedAdapter(2)
    adapter.deadline = 10
    with patch("requests.adapters.HTTPAdapter.proxy_manager_for", return_value=manager):
        current = adapter.proxy_manager_for("socks5h://proxy.example.test")
        pool = current.pool_classes_by_scheme["https"]
        assert (
            adapter.proxy_manager_for(
                "socks5h://proxy.example.test"
            ).pool_classes_by_scheme["https"]
            is pool
        )
    connection = cast(Any, pool.ConnectionCls("example.test"))
    assert connection._new_conn() is marker
    connection.sock = MagicMock()

    def response(active: Any) -> object:
        assert isinstance(active.sock, DeadlineSocket)
        assert active.sock.deadline == 10
        assert active.sock.timeout == 2
        return object()

    with patch.object(HTTPSConnection, "getresponse", response):
        connection.getresponse()
    connection.close()
    adapter.close()


@pytest.mark.asyncio
async def test_photos_failed_publication_revokes_ledger_and_request() -> None:
    """Unpublished decisions cannot accept approval or occupy bounded capacity."""
    writer, _client, _library, handle, token, _pipeline = setup_upload()
    publish = MagicMock(side_effect=RuntimeError("Synthetic publication failure"))
    with pytest.raises(RuntimeError):
        await writer.prepare_upload(handle, token, 1000, publish)
    preview = publish.call_args.args[0]
    with pytest.raises(WriteDenied):
        writer.ledger.approve_from_operator(
            preview.request_id, preview.digest, high_risk=True
        )
    assert preview.request_id not in writer._requests


@pytest.mark.asyncio
async def test_photos_receipt_cache_precedes_metadata_revalidation() -> None:
    """A failed post-acceptance recheck cannot require reading the receipt twice."""
    writer, _client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)
    accepted: list[str] = []

    def accept(request_id: str) -> None:
        assert not accepted
        accepted.append(request_id)
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)

    with patch.object(
        writer,
        "_verify_after_binding",
        side_effect=WriteDenied("inventory_unavailable"),
    ):
        for _attempt in range(2):
            with pytest.raises(WriteDenied):
                await writer.apply(preview.request_id, accept)
    assert accepted == [preview.request_id]
    assert writer.staging.peek(token).data
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_photos_account_change_after_acceptance_preserves_resources() -> None:
    """A separate Photos account dictionary remains independently bound."""
    writer, client, _library, handle, token, pipeline = setup_upload()
    preview = await writer.prepare_upload(handle, token, 1000)

    def accept(request_id: str) -> None:
        writer.ledger.approve_from_operator(request_id, preview.digest, high_risk=True)
        client.photos.params = {"dsid": "999"}

    with pytest.raises(WriteDenied, match="account_unavailable"):
        await writer.apply(preview.request_id, accept)
    assert writer.staging.peek(token).data
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 4 * 1024 * 1024 + 1])
async def test_invalid_photo_stage_size_never_creates_approval(size: int) -> None:
    """The pipeline's byte bound applies before the exact preview can be approved."""
    writer, _client, _library, handle, token, pipeline = setup_upload()
    stage = replace(writer.staging.peek(token), size=size)
    with (
        patch.object(writer.staging, "peek", return_value=stage),
        pytest.raises(WriteDenied, match="invalid_upload_bytes"),
    ):
        await writer.prepare_upload(handle, token, 1000)
    assert not writer.ledger._pending
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["browse", "trash", "search"])
async def test_plain_injected_drive_metadata_transport_is_denied_before_sdk(
    service: str,
) -> None:
    """Discovery cannot eagerly load HTTP responses through an unbounded session."""
    session = Session()
    session.trust_env = False
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", cast(Any, session), {}
    )
    client = SimpleNamespace(drive=drive)
    reads = adapters(client)
    with patch("requests.adapters.HTTPAdapter.send") as send:
        if service == "browse":
            result = await reads.drive("", "", 1)
        elif service == "trash":
            result = await reads.drive_trash("", 1)
        else:
            result = await reads.search("drive", "", "match", 1, 1)
    assert result.error == "unsupported_drive_transport"
    send.assert_not_called()
    session.close()


@pytest.mark.asyncio
async def test_dynamic_inventory_adapter_selection_is_denied_before_read() -> None:
    """A mutable selector cannot replace the adapter validated for inventory reads."""
    session = cast(Any, Session())
    session.trust_env = False
    session.get_adapter = MagicMock(
        side_effect=[BoundedAdapter(30), Session().get_adapter("https://example.test")]
    )
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", session, {}
    )
    reads = adapters(SimpleNamespace(drive=drive))
    with patch("requests.adapters.HTTPAdapter.send") as send:
        result = await reads.drive("", "", 1)
    assert result.error == "unsupported_drive_transport"
    session.get_adapter.assert_not_called()
    send.assert_not_called()
    session.close()
