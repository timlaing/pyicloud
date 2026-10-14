"""Reminder text edits are written as CRDT edits that Apple devices accept."""

# pylint: disable=protected-access

import base64
import gzip
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.reminders._protocol import (
    CRDTDecodeError,
    _decode_crdt_document,
    _edit_crdt_document,
    _encode_crdt_document,
    _merge_resolution_token_map,
    _parse_crdt_string,
)
from pyicloud.services.reminders.models import Reminder
from pyicloud.services.reminders.protobuf import (
    reminders_pb2,
    versioned_document_pb2,
)
from pyicloud.services.reminders.service import RemindersService

LOW_REPLICA = bytes.fromhex("00" * 16)
HIGH_REPLICA = bytes.fromhex("ff" * 16)
DEFAULT_REPLICA = bytes.fromhex("d46bcae41b8766c18d75efe35c9145c3")


def visible_substrings(value: Any) -> list[Any]:
    """Return the non-marker, non-tombstoned substrings in document order."""
    by_index = list(value.substring)
    ordered: list[Any] = []
    seen: set[int] = set()
    stack = [0]
    while stack:
        index = stack.pop()
        if index in seen:
            continue
        seen.add(index)
        node = by_index[index]
        if node.charID.replicaID and not node.tombstone and node.length:
            ordered.append(node)
        stack.extend(reversed(list(node.child)))
    return ordered


def test_edit_tombstones_old_text_and_inserts_new_text() -> None:
    """The old characters are tombstoned and the new ones inserted first."""
    original = _encode_crdt_document("Old title")

    edited = _edit_crdt_document(original, "New title", replica_uuid=LOW_REPLICA)

    assert _decode_crdt_document(edited) == "New title"
    value = _parse_crdt_string(edited)
    tombstoned = [s for s in value.substring if s.tombstone]
    assert [s.length for s in tombstoned] == [len("Old title")]
    visible = visible_substrings(value)
    assert [s.length for s in visible] == [len("New title")]
    assert [r.length for r in value.attributeRun] == [len("New title")]


def test_edit_adds_a_new_replica_and_continues_character_clocks() -> None:
    """A new replica is added and its characters never reuse known clocks."""
    original = _encode_crdt_document("Old title")

    edited = _edit_crdt_document(original, "New", replica_uuid=LOW_REPLICA)

    value = _parse_crdt_string(edited)
    uuids = [bytes(clock.replicaUUID) for clock in value.timestamp.clock]
    assert uuids == sorted([LOW_REPLICA, DEFAULT_REPLICA])
    new_replica_id = uuids.index(LOW_REPLICA) + 1
    inserted = visible_substrings(value)[0]
    assert inserted.charID.replicaID == new_replica_id
    assert inserted.charID.clock == len("Old title")
    new_clock = value.timestamp.clock[new_replica_id - 1]
    assert new_clock.replicaClock[0].clock == len("Old title") + len("New")


def test_edit_remaps_existing_replicas_to_sorted_positions() -> None:
    """Existing character references follow their replica to its new index."""
    original = _encode_crdt_document("Old")

    edited = _edit_crdt_document(original, "New", replica_uuid=LOW_REPLICA)

    value = _parse_crdt_string(edited)
    tombstoned = next(s for s in value.substring if s.tombstone)
    old_id = [bytes(c.replicaUUID) for c in value.timestamp.clock].index(
        DEFAULT_REPLICA
    ) + 1
    assert tombstoned.charID.replicaID == old_id
    assert tombstoned.charID.clock == 0


def test_edit_keeps_replica_ids_when_new_replica_sorts_last() -> None:
    """A replica that sorts last leaves the existing indexes unchanged."""
    original = _encode_crdt_document("Old")

    edited = _edit_crdt_document(original, "New", replica_uuid=HIGH_REPLICA)

    value = _parse_crdt_string(edited)
    tombstoned = next(s for s in value.substring if s.tombstone)
    assert tombstoned.charID.replicaID == 1
    assert visible_substrings(value)[0].charID.replicaID == 2


def test_successive_edits_never_reuse_character_ids() -> None:
    """Every edit uses character IDs that no earlier version contained."""
    first = _edit_crdt_document(
        _encode_crdt_document("One"), "Two", replica_uuid=LOW_REPLICA
    )
    second = _edit_crdt_document(first, "Three", replica_uuid=HIGH_REPLICA)

    value = _parse_crdt_string(second)
    assert _decode_crdt_document(second) == "Three"
    uuids = [bytes(c.replicaUUID) for c in value.timestamp.clock]
    char_ids = [
        (bytes(value.timestamp.clock[s.charID.replicaID - 1].replicaUUID), c)
        for s in value.substring
        if s.charID.replicaID
        for c in range(s.charID.clock, s.charID.clock + s.length)
    ]
    assert len(char_ids) == len(set(char_ids))
    assert len(uuids) == 3


def test_edit_counts_utf16_code_units() -> None:
    """Lengths and clocks are measured in UTF-16 code units."""
    edited = _edit_crdt_document(
        _encode_crdt_document("ab"), "x😀", replica_uuid=LOW_REPLICA
    )

    value = _parse_crdt_string(edited)
    assert visible_substrings(value)[0].length == 3
    assert value.attributeRun[0].length == 3
    assert _decode_crdt_document(edited) == "x😀"


def test_edit_to_empty_text_only_tombstones() -> None:
    """Clearing the text tombstones everything and inserts nothing."""
    edited = _edit_crdt_document(
        _encode_crdt_document("Old"), "", replica_uuid=LOW_REPLICA
    )

    value = _parse_crdt_string(edited)
    assert _decode_crdt_document(edited) == ""
    assert not visible_substrings(value)
    assert not list(value.attributeRun)


def test_edit_accepts_gzip_documents_written_by_devices() -> None:
    """Documents compressed with gzip, as the Reminders app writes them, parse."""
    value = _parse_crdt_string(_encode_crdt_document("Device text"))
    version = versioned_document_pb2.Version(  # type: ignore[attr-defined]
        data=value.SerializeToString()
    )
    document = versioned_document_pb2.Document(  # type: ignore[attr-defined]
        version=[version]
    )
    device_payload = base64.b64encode(
        gzip.compress(document.SerializeToString())
    ).decode()

    edited = _edit_crdt_document(device_payload, "Edited", replica_uuid=LOW_REPLICA)

    assert _decode_crdt_document(edited) == "Edited"


def test_edit_rejects_existing_replica_uuid() -> None:
    """Reusing a replica that already wrote to the document is refused."""
    with pytest.raises(ValueError, match="already present"):
        _edit_crdt_document(
            _encode_crdt_document("Old"), "New", replica_uuid=DEFAULT_REPLICA
        )


def test_edit_rejects_documents_without_structure() -> None:
    """A document without substrings cannot be edited."""
    bare = reminders_pb2.String(string="no structure")  # type: ignore[attr-defined]
    payload = base64.b64encode(bare.SerializeToString()).decode()

    with pytest.raises(CRDTDecodeError):
        _edit_crdt_document(payload, "New")


def test_merge_token_map_bumps_counters_and_keeps_other_tokens() -> None:
    """Written fields get a higher counter; other tokens are preserved."""
    existing = json.dumps({
        "map": {
            "titleDocument": {"counter": 3, "replicaID": "DEVICE"},
            "completed": {"counter": 2, "replicaID": "DEVICE"},
        }
    })

    merged = json.loads(
        _merge_resolution_token_map(
            existing, ["titleDocument", "dueDate"], replica_id="LIB"
        )
    )["map"]

    assert merged["titleDocument"]["counter"] == 4
    assert merged["titleDocument"]["replicaID"] == "LIB"
    assert merged["dueDate"]["counter"] == 1
    assert merged["completed"] == {"counter": 2, "replicaID": "DEVICE"}


@pytest.mark.parametrize("existing", [None, "", "not json", "[]", '{"map": 3}'])
def test_merge_token_map_tolerates_missing_or_invalid_maps(existing: Any) -> None:
    """Missing or malformed maps start counting from one."""
    merged = json.loads(_merge_resolution_token_map(existing, ["completed"]))
    assert merged["map"]["completed"]["counter"] == 1


def server_record(title: str, notes: str, token_map: dict[str, Any]) -> CKRecord:
    """Build the server copy of a synthetic reminder."""
    return CKRecord.model_validate({
        "recordName": "Reminder/REM-EDIT",
        "recordType": "Reminder",
        "recordChangeTag": "server-tag",
        "fields": {
            "TitleDocument": {
                "type": "STRING",
                "value": _encode_crdt_document(title),
            },
            "NotesDocument": {
                "type": "STRING",
                "value": _encode_crdt_document(notes),
            },
            "ResolutionTokenMap": {
                "type": "STRING",
                "value": json.dumps({"map": token_map}),
            },
        },
    })


def updated_fields(svc: RemindersService) -> Any:
    """Return the fields written by the last update."""
    raw: Any = svc._raw
    return raw.modify.call_args.kwargs["operations"][0].record.fields


def make_service(current: CKRecord | None) -> RemindersService:
    """Create a service whose lookup returns ``current``."""
    svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
    raw = MagicMock()
    raw.lookup.return_value = MagicMock(records=[current] if current else [])
    raw.modify.return_value = CKModifyResponse(records=[], syncToken="mock-sync")
    svc._raw = raw
    return svc


def test_update_edits_changed_text_on_top_of_the_server_document() -> None:
    """A changed title is written as an edit of the server's document."""
    svc = make_service(
        server_record("Old title", "Notes", {"titleDocument": {"counter": 3}})
    )
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="New title",
        desc="Notes",
        record_change_tag="server-tag",
    )

    svc.update(reminder)

    fields = updated_fields(svc)
    title_doc = fields["TitleDocument"].value
    assert _decode_crdt_document(title_doc) == "New title"
    value = _parse_crdt_string(title_doc)
    assert any(s.tombstone for s in value.substring)
    assert len(value.timestamp.clock) == 2
    assert "NotesDocument" not in fields
    tokens = json.loads(fields["ResolutionTokenMap"].value)["map"]
    assert tokens["titleDocument"]["counter"] == 4
    assert "notesDocument" not in tokens


def test_update_leaves_unchanged_text_documents_alone() -> None:
    """A flag-only update does not rewrite the title or notes."""
    svc = make_service(server_record("Title", "Notes", {}))
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Title",
        desc="Notes",
        flagged=True,
        record_change_tag="server-tag",
    )

    svc.update(reminder)

    fields = updated_fields(svc)
    assert "TitleDocument" not in fields
    assert "NotesDocument" not in fields
    tokens = json.loads(fields["ResolutionTokenMap"].value)["map"]
    assert tokens["flagged"]["counter"] == 1


def test_update_without_a_server_copy_encodes_fresh_documents() -> None:
    """Without a server copy, both documents are encoded from scratch."""
    svc = make_service(None)
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Title",
        desc="Notes",
        record_change_tag="server-tag",
    )

    svc.update(reminder)

    fields = updated_fields(svc)
    assert _decode_crdt_document(fields["TitleDocument"].value) == "Title"
    assert _decode_crdt_document(fields["NotesDocument"].value) == "Notes"
    raw: Any = svc._raw
    raw.lookup.assert_called_once()


def test_parse_rejects_invalid_base64() -> None:
    """Invalid base64 input raises a decode error."""
    with pytest.raises(CRDTDecodeError, match="base64"):
        _parse_crdt_string("a")


def test_parse_accepts_a_bare_topotext_string() -> None:
    """A bare, uncompressed topotext String with structure is accepted."""
    value = _parse_crdt_string(_encode_crdt_document("Bare"))

    parsed = _parse_crdt_string(value.SerializeToString())

    assert parsed.string == "Bare"
    assert len(parsed.substring) == len(value.substring)


def test_parse_rejects_bytes_that_are_not_a_document() -> None:
    """Bytes that are no protobuf at all raise a decode error."""
    with pytest.raises(CRDTDecodeError):
        _parse_crdt_string(b"\xff\xff\xff\xff")


def test_merge_token_map_ignores_non_numeric_counters() -> None:
    """A non-numeric counter is treated as zero."""
    existing = json.dumps({"map": {"completed": {"counter": "x"}}})

    merged = json.loads(_merge_resolution_token_map(existing, ["completed"]))

    assert merged["map"]["completed"]["counter"] == 1


def test_edit_stores_substrings_in_document_order() -> None:
    """Substrings are stored in document order, like the Reminders app does."""
    edited = _edit_crdt_document(
        _encode_crdt_document("Old"), "New", replica_uuid=LOW_REPLICA
    )

    value = _parse_crdt_string(edited)
    for index, substring in enumerate(value.substring):
        assert all(child > index for child in substring.child)
    terminal = value.substring[len(value.substring) - 1]
    assert terminal.charID.replicaID == 0
    assert not terminal.child
