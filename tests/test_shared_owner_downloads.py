"""Synthetic shared-folder downloads retain the owner's addressing scope."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest
from requests import Response

from pyicloud.services.drive import DriveNode, DriveService


def node(**changes: Any) -> Any:
    """Create a synthetic Drive node with overridable sharing metadata."""
    data = {
        "drivewsid": "FILE_IN_SHARED_FOLDER::zone::asset",
        "docwsid": "document",
        "zone": "zone",
        "size": 1,
        "shareID": {"recordName": "share"},
        "owner": {"ownerRecordName": "owner-fixture"},
    }
    data.update(changes)
    return DriveNode(MagicMock(), data)


def test_shared_node_passes_its_owner() -> None:
    """Shared node passes its owner."""
    item = node()
    item.open(stream=True)
    item.connection.get_file.assert_called_once_with(
        "document", zone="zone", owner_record_name="owner-fixture", stream=True
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"drivewsid": "FILE::zone::asset"},
        {"shareID": None},
        {"owner": None},
        {"owner": {}},
        {"owner": {"ownerRecordName": ""}},
        {"owner": {"ownerRecordName": 1}},
    ],
)
def test_ordinary_or_unresolved_nodes_keep_the_existing_request(changes: Any) -> None:
    """Ordinary or unresolved nodes keep the existing request."""
    item = node(**changes)
    assert item.owner_record_name is None
    item.open()
    item.connection.get_file.assert_called_once_with("document", zone="zone")


def test_owner_is_escaped_as_one_path_component() -> None:
    """Owner is escaped as one path component."""
    response = MagicMock(spec=Response)
    response.json.return_value = {
        "data_token": {"url": "https://example.invalid/content"}
    }
    session = MagicMock(get=MagicMock(side_effect=[response, Response()]))
    service = DriveService(
        "https://example.invalid/drive",
        "https://example.invalid/doc",
        session,
        {"dsid": "fixture-id"},
    )
    service.get_file("document", owner_record_name="owner/with space", stream=True)
    assert (
        session.get
        .call_args_list[0]
        .args[0]
        .endswith("/download/by_id/owner%2Fwith%20space")  # cspell:ignore Fwith
    )
    assert session.get.call_args_list[0].kwargs["params"]["document_id"] == "document"
    assert session.get.call_args_list[0].kwargs["params"]["dsid"] == "fixture-id"
    assert session.get.call_args_list[1].kwargs["stream"]


def test_empty_file_needs_no_owner_request() -> None:
    """Empty file needs no owner request."""
    item = node(size=0)
    assert item.open().raw.read() == b""
    item.connection.get_file.assert_not_called()
