"""Synthetic changes pages expose nested folder relationships without account data."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import (
    CKRecord,
    CKTombstoneRecord,
    CKZoneChangesZone,
    CKZoneID,
)
from pyicloud.services.notes.service import NotesService


def folder(name: Any, parent: Any = None) -> Any:
    """Create a synthetic encrypted folder title and optional parent reference."""
    fields = {"TitleEncrypted": {"type": "STRING", "value": name, "isEncrypted": True}}
    if parent:
        fields["ParentFolder"] = {"type": "REFERENCE", "value": {"recordName": parent}}
    return CKRecord.model_validate({
        "recordName": name,
        "recordType": "Folder",
        "fields": fields,
    })


def service(*pages: Any) -> Any:
    """Supply a mocked Notes service with successive change-feed pages."""
    value = NotesService("https://example.invalid", MagicMock(), {})
    value._raw = MagicMock()
    value._raw.changes.return_value = iter(pages)
    return value


def page(*records: Any) -> Any:
    """Wrap synthetic records in a Notes zone change page."""
    return CKZoneChangesZone(
        zoneID=CKZoneID(zoneName="Notes"),
        syncToken="fixture-cursor",
        records=list(records),
    )


def test_children_can_precede_parents_across_pages() -> None:
    """Children can precede parents across pages."""
    api = service(
        page(folder("child", "root")), page(folder("root"), folder("leaf", "child"))
    )
    rows = {row.id: row for row in api.folders()}
    assert set(rows) == {"root", "child", "leaf"}
    assert rows["child"].parent_id == "root" and rows["leaf"].parent_id == "child"
    assert rows["root"].parent_id is None
    assert rows["root"].has_subfolders and rows["child"].has_subfolders
    assert not rows["leaf"].has_subfolders
    assert api._folder_name_cache["child"] == "child"
    request = api._raw.changes.call_args.kwargs["zone_req"]
    assert "ParentFolder" in request.desiredKeys
    api._raw.query.assert_not_called()


def test_empty_feed_and_tombstones_do_not_invent_folders() -> None:
    """Empty feed and tombstones do not invent folders."""
    api = service(page(CKTombstoneRecord(recordName="gone", deleted=True)))
    assert not list(api.folders())


def test_change_feed_failure_is_not_an_empty_listing() -> None:
    """Change feed failure is not an empty listing."""
    api = service()
    api._raw.changes.side_effect = RuntimeError("synthetic refusal")
    with pytest.raises(RuntimeError, match="synthetic refusal"):
        list(api.folders())
