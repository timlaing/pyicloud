"""Approved mutations through the real library facade with offline HTTP responses."""

from collections.abc import Callable
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from requests import PreparedRequest, Response, Session

from pyicloud.mcp.authorization import ApprovalLedger
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.services.drive import CLOUD_DOCS_ZONE_ID_TRASH, DriveService
from tests.test_mcp_drive_deletions import delete_ack, setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["rename", "move", "trash", "delete"])
async def test_real_drive_service_dispatches_approved_mutation_once(
    action: str,
) -> None:
    """Read-only session properties must not prevent existing write APIs working."""
    writer: Any
    folder: dict[str, Any] = {}
    if action == "move":
        writer, client, handle, destination, data, folder = setup_move()
    elif action == "delete":
        writer, client, handle, data = setup_delete()
    else:
        writer, client, handle, data = setup_writer()
        if action == "trash":
            writer.ledger = ApprovalLedger(frozenset({"drive.trash_file"}))
    session = Session()
    session.trust_env = False
    session.mount("https://", BoundedAdapter(30))
    drive = DriveService(
        "https://drive.example.test",
        "https://docs.example.test",
        cast(Any, session),
        dict(client.params),
    )
    client = SimpleNamespace(params=dict(client.params), drive=drive)

    async def run(operation: Callable[[Any], Any]) -> Any:
        return operation(client)

    cast(Any, writer.additional.reads.owner).run = run
    mutations: list[str] = []
    deleted = [False]

    def offline(request: PreparedRequest, **_kwargs: Any) -> Response:
        endpoint = urlsplit(request.url or "").path.rsplit("/", 1)[-1]
        response = Response()
        response.status_code = 200
        response.request = request
        response.url = request.url or "https://drive.example.test"
        body = json.loads(request.body or "null")
        result: Any = {"status": "OK"}
        if endpoint == "retrieveItemDetailsInFolders":
            identifier = body[0]["drivewsid"]
            if identifier == CLOUD_DOCS_ZONE_ID_TRASH:
                result = [
                    {
                        "drivewsid": identifier,
                        "type": "FOLDER",
                        "items": [] if deleted[0] else [dict(data)],
                    }
                ]
            elif deleted[0]:
                response.status_code = 404
                result = {}
            else:
                result = [dict(data if identifier == data["drivewsid"] else folder)]
        else:
            mutations.append(endpoint)
            if action == "rename":
                data.update(name="After", etag="v2")
                data.pop("extension", None)
            elif action == "move":
                data.update(parentId=folder["drivewsid"], etag="v2")
                folder.update(etag="D2", items=[dict(data)])
            elif action == "trash":
                data.update(parentId="TRASH_ROOT", restorePath="Before.txt", etag="v2")
            else:
                deleted[0] = True
                result = delete_ack(data)
        vars(response).update(
            _content=json.dumps(result).encode(), _content_consumed=True
        )
        return response

    try:
        with patch("requests.adapters.HTTPAdapter.send", side_effect=offline):
            if action == "move":
                preview = await writer.prepare_move(handle, destination, {})
            elif action == "delete":
                preview = await writer.prepare_delete(handle, {})
            else:
                preview = await writer.prepare(
                    handle,
                    {"name": "After"} if action == "rename" else {},
                    action="drive.rename" if action == "rename" else "drive.trash_file",
                )
            assert not mutations
            writer.ledger.approve_from_operator(
                preview.request_id, preview.digest, high_risk=preview.risk == "high"
            )
            result = await writer.apply(preview.request_id)
        assert result.get("changed", result.get("acknowledged")) is True
        expected = {
            "rename": "renameItems",
            "move": "moveItems",
            "trash": "moveItemsToTrash",
            "delete": "deleteItems",
        }
        assert mutations == [expected[action]]
        assert drive.session is session
    finally:
        session.close()
