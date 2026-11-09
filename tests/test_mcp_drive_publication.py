"""Offline publication rollback and fresh-inventory regressions."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.drive_acknowledged import _verify_after_route
from pyicloud.services.drive import DriveNode
from tests.test_mcp_additional import adapters
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_uploads import setup_adapter
from tests.test_mcp_drive_writes import setup_writer
from tests.test_mcp_upload_previews import setup_upload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["rename", "create", "recover", "delete", "move", "upload", "preview"]
)
async def test_failed_publication_revokes_ledger_and_selector_maps(kind: str) -> None:
    """A failed operator publication cannot leave hidden approvals."""
    published: list[Any] = []

    def fail(preview: Any) -> None:
        published.append(preview)
        raise RuntimeError("Synthetic publication failure")

    if kind == "rename":
        writer, _client, handle, _data = setup_writer()
        operation = writer.prepare(handle, {"name": "After"}, fail)
    elif kind in {"create", "recover"}:
        writer, _client, handle, _data = setup_action(kind == "recover")
        operation = writer.prepare_acknowledged(
            "drive.recover_item" if kind == "recover" else "drive.create_folder",
            handle,
            {} if kind == "recover" else {"name": "New"},
            fail,
        )
    elif kind == "delete":
        writer, _client, handle, _data = setup_delete()
        operation = writer.prepare_delete(handle, {}, fail)
    elif kind == "move":
        writer, _client, handle, destination, _data, _folder = setup_move()
        operation = writer.prepare_move(handle, destination, {}, fail)
    elif kind == "upload":
        writer, _client, handle, token, _data, _pipeline = setup_adapter()
        operation = writer.prepare_upload(handle, token, 1000, fail)
    else:
        writer, _client, handle, token, _data = setup_upload()
        operation = writer.prepare_upload(handle, token, fail)
    with pytest.raises(RuntimeError, match="publication failure"):
        await operation
    assert len(published) == 1
    preview = published[0]
    assert not writer.ledger._pending
    for name in (
        "_targets",
        "_actions",
        "_sources",
        "_requests",
        "_moves",
        "_destinations",
        "_source_handles",
        "_deletions",
    ):
        assert preview.request_id not in getattr(writer, name, {})
    with pytest.raises(WriteDenied):
        writer.ledger.approve_from_operator(
            preview.request_id, preview.digest, high_risk=True
        )


def test_forced_lazy_refresh_invalidates_eager_children() -> None:
    """Fresh lazy inventory cannot coexist with an old eager child cache."""
    connection = MagicMock()
    node = DriveNode(
        connection, {"drivewsid": "F", "items": [{"name": "Old", "type": "FILE"}]}
    )
    assert node.get_children()[0].name == "Old"
    connection.get_node_data.return_value = {"items": [{"name": "New", "type": "FILE"}]}
    assert list(node.iter_children(force=True))[0].name == "New"
    assert node._children is None
    assert node.get_children()[0].name == "New"


@pytest.mark.parametrize("lazy", [False, True])
def test_missing_refresh_inventory_never_reuses_old_children(lazy: bool) -> None:
    """A refresh without items must fail closed instead of reusing old items."""
    connection = MagicMock()
    node = DriveNode(
        connection, {"drivewsid": "F", "items": [{"name": "Old", "type": "FILE"}]}
    )
    node.get_children()
    connection.get_node_data.return_value = {"status": "OK"}
    if lazy:
        children = node.iter_children(force=True)
        with pytest.raises(KeyError):
            list(children)
    else:
        with pytest.raises(KeyError):
            node.get_children(force=True)
    assert node._children is None
    assert "items" not in node.data


@pytest.mark.asyncio
async def test_trash_query_bounds_child_production() -> None:
    """A selective Trash query stays within its explicit scan budget."""
    client = MagicMock()
    data = {
        "drivewsid": "TRASH_ROOT",
        "items": [{"name": "Other", "type": "FILE"} for _index in range(1000)],
    }
    client.drive.trash = DriveNode(client.drive, dict(data))
    client.drive.get_node_data.return_value = data
    tools = adapters(client)
    result = await tools.drive_trash("Needle", 1, scan_limit=3)
    assert result.records == []
    assert not result.complete
    assert client.drive.trash._children is None
    assert (await tools.drive_trash("", 1, scan_limit=0)).error == "invalid_argument"


def test_acknowledgement_rechecks_drive_account() -> None:
    """A stable top-level account cannot hide a changed service account."""
    _writer, client, _handle, _data = setup_writer()
    client.drive.params["dsid"] = "Different"
    with pytest.raises(WriteDenied):
        _verify_after_route(client, "A", client.drive.service_root)


@pytest.mark.asyncio
async def test_upload_drive_account_change_is_uncertain_and_single_use() -> None:
    """An acknowledged upload with changed service context cannot replay."""
    writer, client, handle, token, _data, pipeline = setup_adapter()
    preview = await writer.prepare_upload(handle, token, 1000)
    writer.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )

    def changed_account(*_args: Any, **_kwargs: Any) -> None:
        client.drive.params["dsid"] = "Different"

    pipeline.upload.side_effect = changed_account
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writer.apply(preview.request_id)
    pipeline.upload.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed_preview"):
        await writer.apply(preview.request_id)
