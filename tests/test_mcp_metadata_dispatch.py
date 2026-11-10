"""Offline metadata dispatch-path validation and complete Trash bounds."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Session

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.context import BoundedAdapter
from pyicloud.services.drive import DriveNode, DriveService
from tests.test_mcp_additional import adapters
from tests.test_mcp_content import content_for
from tests.test_mcp_drive_acknowledged import setup_action
from tests.test_mcp_drive_writes import setup_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["request", "send"])
async def test_metadata_dispatch_override_is_rejected_before_inventory(
    method: str,
) -> None:
    """Known adapter selection alone cannot authorize an overridden dispatch path."""
    session = cast(Any, Session())
    session.trust_env = False
    session.mount("https://", BoundedAdapter(30))
    override = MagicMock(side_effect=AssertionError("Unbounded dispatch forbidden"))
    setattr(session, method, override)
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", session, {}
    )
    result = await adapters(SimpleNamespace(drive=drive)).drive("", "", 1)
    assert result.error == "unsupported_drive_transport"
    override.assert_not_called()
    session.close()


@pytest.mark.asyncio
async def test_drive_mutation_metadata_rejects_dynamic_adapter_before_preview() -> None:
    """The write preflight cannot read metadata through an alternating selector."""
    writer, _client, _handle, data = setup_writer()
    session = cast(Any, Session())
    session.trust_env = False
    session.get_adapter = MagicMock(return_value=BoundedAdapter(30))
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", session, {"dsid": "A"}
    )
    handle = writer.additional.remember("drive-file", DriveNode(drive, dict(data)))
    client = SimpleNamespace(drive=drive, params={"dsid": "A"})

    async def run(operation: Any) -> Any:
        return operation(client)

    cast(Any, writer.additional.reads.owner).run = run
    with pytest.raises(WriteDenied, match="mutation_session_policy_unavailable"):
        await writer.prepare(handle, {"name": "After"})
    session.get_adapter.assert_not_called()
    session.close()


@pytest.mark.asyncio
async def test_arbitrary_photo_url_producer_is_not_invoked() -> None:
    """Unknown custom metadata callbacks cannot block before URL validation."""
    client = MagicMock()
    tools = content_for(client)
    producer = MagicMock(side_effect=AssertionError("Metadata resolution forbidden"))
    handle = tools.additional.remember(
        "photo-asset", SimpleNamespace(filename="photo", download_url=producer)
    )
    result = await tools.file("photos", handle, 0, 4)
    assert result.error == "unsupported_photo_url_producer"
    producer.assert_not_called()
    client.session.get.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_recovery_inventory_does_not_construct_children() -> None:
    """Recovery uses complete bounded raw inventory rather than eager child objects."""
    writer, _client, _handle, _data = setup_action(True)
    session = Session()
    drive = DriveService(
        "https://drive.icloud.com", "https://docs.icloud.com", cast(Any, session), {}
    )
    drive._trash = DriveNode(
        drive, {"drivewsid": "FOLDER::Z::TRASH_ROOT", "type": "FOLDER", "items": []}
    )
    client = SimpleNamespace(drive=drive)
    data = {"drivewsid": "FOLDER::Z::TRASH_ROOT", "items": [{}] * 2001}
    with (
        patch.object(writer, "_require_metadata_transport"),
        patch.object(drive, "get_node_data", return_value=data),
        patch.object(
            DriveNode, "__init__", autospec=True, return_value=None
        ) as constructor,
        pytest.raises(WriteDenied, match="unsupported_trash_inventory"),
    ):
        writer._trash_inventory(client)
    constructor.assert_not_called()
    session.close()
