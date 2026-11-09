"""Offline transfer, enumeration, credential and redirect regressions."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import PreparedRequest, Response, Session

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_additional import adapters
from tests.test_mcp_content import content_for, response
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
async def test_interleaved_transfers_cannot_mix_entity_versions() -> None:
    """A second caller cannot overwrite the first caller's pinned entity tag."""
    tools = content_for(MagicMock())
    open_mock = MagicMock(
        side_effect=[
            response(206, b"old!", **{"Content-Range": "bytes 0-3/8", "ETag": '"v1"'}),
            response(206, b"new!", **{"Content-Range": "bytes 0-3/8", "ETag": '"v2"'}),
            response(206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v2"'}),
            response(206, b"more", **{"Content-Range": "bytes 4-7/8", "ETag": '"v2"'}),
        ]
    )
    handle = tools.additional.remember(
        "drive-file", SimpleNamespace(name="file", open=open_mock)
    )
    first = await tools.file("drive", handle, 0, 4)
    second = await tools.file("drive", handle, 0, 4)
    a, b = first.records[0]["transfer_token"], second.records[0]["transfer_token"]
    assert a != b
    stale = await tools.file("drive", handle, 4, 4, transfer_token=a)
    assert stale.error == "stale_download"
    assert open_mock.call_args.kwargs["headers"]["If-Match"] == '"v1"'
    assert (await tools.file("drive", handle, 4, 4, transfer_token=b)).complete
    assert open_mock.call_args.kwargs["headers"]["If-Match"] == '"v2"'


@pytest.mark.asyncio
async def test_resume_token_cannot_change_handle_or_selected_version() -> None:
    """Opaque transfer state binds the exact original service, handle and version."""
    tools = content_for(MagicMock())
    resource = SimpleNamespace(
        name="file",
        open=MagicMock(
            return_value=response(
                206, b"data", **{"Content-Range": "bytes 0-3/8", "ETag": '"v1"'}
            )
        ),
    )
    handle = tools.additional.remember("drive-file", resource)
    other = tools.additional.remember("drive-file", resource)
    first = await tools.file("drive", handle, 0, 4)
    token = first.records[0]["transfer_token"]
    assert (
        await tools.file("drive", other, 4, 4, transfer_token=token)
    ).error == "invalid_download_transfer"
    assert (
        await tools.file("drive", handle, 4, 4, version="other", transfer_token=token)
    ).error == "invalid_download_transfer"
    assert (
        await tools.file("drive", handle, 0, 4, transfer_token=token)
    ).error == "invalid_download_transfer"
    resource.open.assert_called_once()


@pytest.mark.asyncio
async def test_selective_browse_is_limited_before_child_construction() -> None:
    """A selective query cannot force every folder child into a DriveNode."""
    client = MagicMock()
    data = {
        "drivewsid": "F",
        "type": "FOLDER",
        "items": [
            {
                "drivewsid": str(index),
                "type": "FILE",
                "name": "needle" if index == 4 else "other",
            }
            for index in range(1000)
        ],
    }
    client.drive.root = DriveNode(client.drive, dict(data))
    client.drive.get_node_data.return_value = data
    tools = adapters(client)
    with patch("pyicloud.services.drive.DriveNode", wraps=DriveNode) as constructor:
        result = await tools.drive("", "needle", 50, scan_limit=3)
    assert result.records == []
    assert not result.complete
    assert constructor.call_count == 3
    assert client.drive.root._children is None


@pytest.mark.asyncio
async def test_cookie_lookup_failure_cannot_spend_upload_resources() -> None:
    """Malformed synthetic cookies fail before accepting or consuming anything."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    client.drive._get_token_from_cookie.side_effect = ValueError("private")
    accept = MagicMock()
    with pytest.raises(WriteDenied, match="upload_credentials_unavailable"):
        await writer.apply(preview.request_id, accept)
    accept.assert_not_called()
    assert writer.staging.peek(token).data
    assert writer.additional.resolve("drive-folder", handle)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    pipeline.upload.assert_not_called()


@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("verification", [False, True])
def test_real_session_does_not_repeat_redirected_mutation(
    status: int, verification: bool
) -> None:
    """An unfollowed redirect raises before Requests can issue another POST."""
    session = Session()
    session.trust_env = False
    drive = DriveService(
        "https://drive.example.test",
        "https://docs.example.test",
        cast(Any, session),
        {},
    )

    def redirected(request: PreparedRequest, **_kwargs: Any) -> Response:
        result = Response()
        result.status_code = status
        result.headers["Location"] = "https://drive.example.test/elsewhere"
        result.request = request
        result.url = request.url or "https://drive.example.test"
        vars(result).update(_content=b"", _content_consumed=True)
        return result

    try:
        with (
            patch("requests.adapters.HTTPAdapter.send", side_effect=redirected) as send,
            pytest.raises(PyiCloudAPIResponseException, match="redirect not followed"),
        ):
            if verification:
                drive.get_node_data("F")
            else:
                drive.rename_items("F", "R", "New", allow_redirects=False)
        send.assert_called_once()
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("shareID", "foreign"), ("zone", "foreign"), ("etag", "different")]
)
async def test_contradictory_post_trash_listing_is_uncertain(
    field: str, value: str
) -> None:
    """Observed Recently Deleted membership must match the private detail record."""
    writer, client, handle, data = setup_writer()

    writer.ledger = ApprovalLedger(frozenset({"drive.trash_file"}))
    preview = await writer.prepare(handle, {}, action="drive.trash_file")
    writer.ledger.approve_from_operator(preview.request_id, preview.digest)

    def trash(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        data.update(restorePath="Before.txt", parentId="TRASH_ROOT", etag="v2")
        client.drive.trash.get_children.return_value = [
            DriveNode(client.drive, {**data, field: value})
        ]
        return {"status": "OK"}

    client.drive.move_items_to_trash.side_effect = trash
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    client.drive.move_items_to_trash.assert_called_once()
