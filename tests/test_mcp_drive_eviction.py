"""Offline selector capacity eviction must also revoke exact pending decisions."""

# pylint: disable=protected-access

from typing import Any

import pytest

from pyicloud.mcp.authorization import WriteDenied
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_deletions import setup_delete
from tests.test_mcp_drive_moves import setup_move
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rename", "create", "recover", "delete", "move"])
async def test_selector_eviction_revokes_oldest_pending_decision(kind: str) -> None:
    """Unusable evicted requests cannot accept approval or occupy ledger capacity."""
    writer: Any
    registries: tuple[dict[str, Any], ...]
    if kind == "rename":
        writer, _client, handle, _data = setup_writer()

        async def prepare() -> Any:
            return await writer.prepare(handle, {"name": "After"})

        registries = (writer._targets, writer._actions, writer._sources)
    elif kind in {"create", "recover"}:
        writer, _client, handle, _data = setup_action(kind == "recover")

        async def prepare() -> Any:
            return await writer.prepare_acknowledged(
                "drive.recover_item" if kind == "recover" else "drive.create_folder",
                handle,
                {} if kind == "recover" else {"name": "New"},
            )

        registries = (writer._requests,)
    elif kind == "delete":
        writer, _client, handle, _data = setup_delete()

        async def prepare() -> Any:
            return await writer.prepare_delete(handle, {})

        registries = (writer._deletions,)
    else:
        writer, _client, handle, destination, _data, _folder = setup_move()

        async def prepare() -> Any:
            return await writer.prepare_move(handle, destination, {})

        registries = (writer._moves, writer._destinations, writer._source_handles)
    old = await prepare()
    for registry in registries:
        selected = registry[old.request_id]
        registry.update({str(index): selected for index in range(255)})
    current = await prepare()
    assert old.request_id not in writer.ledger._pending
    assert all(old.request_id not in registry for registry in registries)
    with pytest.raises(WriteDenied, match="unknown_or_consumed_preview"):
        writer.ledger.approve_from_operator(
            old.request_id, old.digest, high_risk=old.risk == "high"
        )
    writer.ledger.approve_from_operator(
        current.request_id, current.digest, high_risk=current.risk == "high"
    )
    assert len(writer.ledger._pending) == 1
