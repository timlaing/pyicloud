"""Bounded separate Recently Deleted metadata, not recovery or file-content access."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.reads import ReadTools
from tests.test_mcp_additional import owner_for


@pytest.mark.asyncio
async def test_trash_metadata_has_distinct_scoped_handles_and_no_paths() -> None:
    """Cannot use trash handles as ordinary write/content handles."""
    client = MagicMock()
    first = SimpleNamespace(
        name="Deleted.txt",
        type="file",
        date_modified=None,
        restorePath="PRIVATE/PATH",
        download_url="PRIVATE_URL",
    )
    folder = SimpleNamespace(name="Folder", type="folder", date_modified=None)
    client.drive.trash.get_children.return_value = [
        SimpleNamespace(name="Unknown", type="virtual"),
        first,
        folder,
    ]
    reads = AdditionalReads(ReadTools(owner_for(client)))
    result = await reads.drive_trash("", 50)
    assert result.error is None
    assert len(result.records) == 2
    assert set(result.records[0]) == {"handle", "name", "type", "modified"}
    handle = result.records[0]["handle"]
    assert reads.resolve("drive-trash-file", handle) is first
    assert reads.resolve("drive-trash-folder", result.records[1]["handle"]) is folder
    with pytest.raises(ToolFailure):
        reads.resolve("drive-file", handle)
    assert "PRIVATE" not in result.model_dump_json()
    client.drive.trash.get_children.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_trash_query_limit_and_service_guard() -> None:
    """Only bounded filtered metadata; no partial listing is called complete."""
    client = MagicMock()
    client.drive.trash.get_children.return_value = [
        SimpleNamespace(name=name, type="file", date_modified=None)
        for name in ("Other", "Deleted1", "Deleted2")
    ]
    reads = AdditionalReads(ReadTools(owner_for(client)))
    result = await reads.drive_trash("deleted", 1)
    assert result.error is None
    assert [item["name"] for item in result.records] == ["Deleted1"]
    assert not result.complete
    assert (await reads.drive_trash("", 0)).error is not None
    disabled = AdditionalReads(ReadTools(owner_for(client, services=("mail",))))
    assert (await disabled.drive_trash("", 1)).error is not None
