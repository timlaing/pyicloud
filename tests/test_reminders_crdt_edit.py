"""Reminder text edits are written as CRDT edits that Apple devices accept."""

# pylint: disable=protected-access

import base64
import gzip
import json
import logging
from typing import Any
from unittest.mock import MagicMock
import zlib

import pytest

from pyicloud.common.cloudkit import CKErrorItem, CKModifyResponse, CKRecord
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._protocol import (
    CRDTDecodeError,
    CRDTStructureError,
    _decode_crdt_document,
    _edit_crdt_document,
    _encode_crdt_document,
    _merge_resolution_token_map,
    _parse_crdt_string,
    _serialize_crdt_string,
    _structure_has_visible_text,
)
from pyicloud.services.reminders._writes import RemindersWriteAPI
from pyicloud.services.reminders.client import RemindersApiError
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
    existing = _encode_crdt_document("Old")

    with pytest.raises(ValueError, match="already present"):
        _edit_crdt_document(existing, "New", replica_uuid=DEFAULT_REPLICA)


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


def lookup_error_service(code: str) -> RemindersService:
    """Create a service whose lookup answers with a CloudKit error item."""
    svc = make_service(None)
    raw: Any = svc._raw
    raw.lookup.return_value = MagicMock(
        records=[CKErrorItem(recordName="REM-EDIT", serverErrorCode=code)]
    )
    return svc


def edited_reminder() -> Reminder:
    """Return a reminder whose title differs from any server copy."""
    return Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Title",
        desc="Notes",
        record_change_tag="server-tag",
    )


def test_update_raises_on_lookup_errors_without_writing() -> None:
    """A failed lookup never falls back to writing fresh documents."""
    svc = lookup_error_service("THROTTLED")
    reminder = edited_reminder()

    with pytest.raises(RemindersApiError, match="THROTTLED"):
        svc.update(reminder)

    raw: Any = svc._raw
    raw.modify.assert_not_called()


def test_update_treats_not_found_as_missing_server_copy() -> None:
    """A record the server does not know gets freshly encoded documents."""
    svc = lookup_error_service("NOT_FOUND")

    svc.update(edited_reminder())

    fields = updated_fields(svc)
    assert _decode_crdt_document(fields["TitleDocument"].value) == "Title"


def structureless_document(text: str, *, wrapped: bool) -> str:
    """Encode ``text`` as a String without substrings, as some records hold."""
    value = reminders_pb2.String()  # type: ignore[attr-defined]
    value.string = text
    version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
    version.data = value.SerializeToString()
    payload = version.SerializeToString()
    if wrapped:
        document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
        document.version.append(version)
        payload = zlib.compress(document.SerializeToString())
    return base64.b64encode(payload).decode("ascii")


def version_wrapped(encoded: str) -> str:
    """Re-wrap a structured document as a bare, uncompressed ``Version``."""
    document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
    document.ParseFromString(zlib.decompress(base64.b64decode(encoded)))
    return base64.b64encode(document.version[0].SerializeToString()).decode("ascii")


def fields_with_title(document: str) -> Any:
    """Return server fields whose TitleDocument is ``document``."""
    return CKRecord.model_validate({
        "recordName": "Reminder/REM-EDIT",
        "recordType": "Reminder",
        "fields": {"TitleDocument": {"type": "STRING", "value": document}},
    }).fields


def test_update_edits_version_wrapped_documents() -> None:
    """A structured document wrapped in a bare Version is edited in place."""
    current = fields_with_title(version_wrapped(_encode_crdt_document("Old")))

    document = RemindersWriteAPI._text_document_for_update(
        current, "TitleDocument", "New"
    )

    assert document is not None
    assert _decode_crdt_document(document) == "New"
    assert any(s.tombstone for s in _parse_crdt_string(document).substring)


@pytest.mark.parametrize("wrapped", [True, False])
def test_update_re_encodes_documents_without_crdt_structure(wrapped: bool) -> None:
    """Readable documents without CRDT structure get a fresh document."""
    current = fields_with_title(structureless_document("Old", wrapped=wrapped))

    document = RemindersWriteAPI._text_document_for_update(
        current, "TitleDocument", "New"
    )

    assert document is not None
    assert _decode_crdt_document(document) == "New"


def test_edit_rejects_documents_without_a_vector_clock() -> None:
    """Substrings without a vector clock cannot be edited."""
    value = _parse_crdt_string(_encode_crdt_document("Old"))
    del value.timestamp.clock[:]
    document = _serialize_crdt_string(value)

    with pytest.raises(CRDTDecodeError, match="vector clock"):
        _edit_crdt_document(document, "New")


def test_edit_rejects_documents_without_a_start_sentinel() -> None:
    """Inserting text needs the start sentinel to attach to."""
    value = _parse_crdt_string(_encode_crdt_document("Old"))
    value.substring[0].charID.clock = 7
    document = _serialize_crdt_string(value)

    with pytest.raises(CRDTDecodeError, match="start sentinel"):
        _edit_crdt_document(document, "New")


def test_edit_fills_an_empty_document_without_tombstones() -> None:
    """Editing an empty document only inserts text."""
    document = _edit_crdt_document(_encode_crdt_document(""), "New")

    value = _parse_crdt_string(document)
    assert _decode_crdt_document(document) == "New"
    assert not any(s.tombstone for s in value.substring)


def test_edit_drops_child_links_that_point_past_the_document() -> None:
    """A dangling child index does not break the document-order rewrite."""
    value = _parse_crdt_string(_encode_crdt_document("Old"))
    value.substring[0].child.append(99)

    document = _edit_crdt_document(_serialize_crdt_string(value), "New")

    edited = _parse_crdt_string(document)
    assert _decode_crdt_document(document) == "New"
    assert all(c < len(edited.substring) for s in edited.substring for c in s.child)


def test_update_treats_a_non_matching_lookup_record_as_missing() -> None:
    """A lookup that answers for another record gives no server copy."""
    other = server_record("Other", "Other notes", {}).model_copy(
        update={"recordName": "Reminder/SOMETHING-ELSE"}
    )
    svc = make_service(other)

    svc.update(edited_reminder())

    fields = updated_fields(svc)
    assert _decode_crdt_document(fields["TitleDocument"].value) == "Title"


def test_update_keeps_device_text_when_the_caller_did_not_change_it() -> None:
    """A stale object does not revert a title edited on a device."""
    svc = make_service(server_record("Edited on iPhone", "Notes", {}))
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Buy milk",
        desc="Notes",
        flagged=True,
        record_change_tag="server-tag",
        server_title="Buy milk",
        server_desc="Notes",
    )

    svc.update(reminder)

    fields = updated_fields(svc)
    assert "TitleDocument" not in fields
    assert "NotesDocument" not in fields


def test_update_writes_text_the_caller_changed_and_remembers_it() -> None:
    """A changed title is written, and becomes the new baseline."""
    svc = make_service(server_record("Buy milk", "Notes", {}))
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Buy oat milk",
        desc="Notes",
        record_change_tag="server-tag",
        server_title="Buy milk",
        server_desc="Notes",
    )

    svc.update(reminder)

    fields = updated_fields(svc)
    assert _decode_crdt_document(fields["TitleDocument"].value) == "Buy oat milk"
    assert "NotesDocument" not in fields
    assert reminder.server_title == "Buy oat milk"


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


def cached_string_only_document(text: str) -> str:
    """Build the fixture shape: substrings and a clock, but an empty ``string``.

    ``_decode_crdt_document`` reads the cached ``string`` field and never the
    substrings, so this decodes to ``""`` even though the text lives in the
    substrings.
    """
    value = _parse_crdt_string(_encode_crdt_document(text))
    value.string = ""
    return _serialize_crdt_string(value)


def record_with_title(document: str) -> CKRecord:
    """Build a record whose only field is a ``TitleDocument``."""
    return CKRecord.model_validate({
        "recordName": "Reminder/REM-EDIT",
        "recordType": "Reminder",
        "recordChangeTag": "server-tag",
        "fields": {"TitleDocument": {"type": "STRING", "value": document}},
    })


def test_update_writes_text_whose_baseline_decode_lost_it() -> None:
    """A caller's title still reaches the server when decode dropped the text.

    The fixture documents carry their text in substrings while the cached
    ``string`` field is empty, so the mapper sees ``""``. Recording that as
    the baseline would make a real edit look unchanged and skip the write.
    """
    document = cached_string_only_document("Old")
    assert _decode_crdt_document(document) == ""
    assert visible_substrings(_parse_crdt_string(document))
    mapped = RemindersRecordMapper(
        MagicMock(), logging.getLogger(__name__)
    ).record_to_reminder(record_with_title(document))
    assert mapped.server_title is None

    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="Buy milk",
        desc="",
        record_change_tag="server-tag",
        server_title=mapped.server_title,
        server_desc=mapped.server_desc,
    )
    svc = make_service(record_with_title(document))

    svc.update(reminder)

    fields = updated_fields(svc)
    assert _decode_crdt_document(fields["TitleDocument"].value) == "Buy milk"


def test_mapper_keeps_a_baseline_for_ordinary_documents() -> None:
    """A normally shaped document still records the text it was read with."""
    mapped = RemindersRecordMapper(
        MagicMock(), logging.getLogger(__name__)
    ).record_to_reminder(server_record("Buy milk", "Notes", {}))

    assert mapped.title == "Buy milk"
    assert mapped.server_title == "Buy milk"
    assert mapped.server_desc == "Notes"


def test_update_refuses_a_structureless_copy_that_cannot_be_preserved() -> None:
    """A structured document that is not editable raises rather than degrading.

    Substrings and a vector clock are present, so re-encoding would drop
    replica state Apple devices have already merged; the error surfaces
    instead.
    """
    value = _parse_crdt_string(_encode_crdt_document("Old"))
    value.substring[0].charID.clock = 7  # the start sentinel is no longer found
    document = _serialize_crdt_string(value)
    svc = make_service(record_with_title(document))
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="New",
        desc="Notes",
        record_change_tag="server-tag",
    )

    with pytest.raises(CRDTDecodeError, match="start sentinel"):
        svc.update(reminder)

    raw: Any = svc._raw
    raw.modify.assert_not_called()


def test_parse_marks_an_uneditable_payload_as_a_structure_error() -> None:
    """A payload with no substrings raises the narrower structure error."""
    value = reminders_pb2.String(string="no structure")  # type: ignore[attr-defined]
    payload = value.SerializeToString()

    with pytest.raises(CRDTStructureError):
        _parse_crdt_string(payload)


def test_structure_error_is_a_decode_error() -> None:
    """The narrow error stays catchable as the broad one."""
    assert issubclass(CRDTStructureError, CRDTDecodeError)


def test_mapper_keeps_empty_baseline_for_genuinely_empty_documents() -> None:
    """A valid empty document keeps its \"\" baseline.

    Only a document whose structure still carries text the cached string
    dropped may lose its baseline; a genuinely empty one must keep it so an
    unchanged caller still preserves a newer edit made on a device.
    """
    mapped = RemindersRecordMapper(
        MagicMock(), logging.getLogger(__name__)
    ).record_to_reminder(server_record("", "", {}))

    assert mapped.title == ""
    assert mapped.server_title == ""
    assert mapped.server_desc == ""


def test_update_preserves_device_text_when_the_stale_caller_had_none() -> None:
    """A caller who read an empty title does not overwrite a newer device edit."""
    stale = RemindersRecordMapper(
        MagicMock(), logging.getLogger(__name__)
    ).record_to_reminder(server_record("", "", {}))
    assert stale.title == ""
    svc = make_service(server_record("Device edit", "", {}))

    svc.update(stale)

    fields = updated_fields(svc)
    assert "TitleDocument" not in fields
    assert "NotesDocument" not in fields


def unclocked_bare_document() -> str:
    """Build a bare String whose substrings survive but whose clock is gone."""
    value = _parse_crdt_string(_encode_crdt_document("Old"))
    del value.timestamp.clock[:]
    return base64.b64encode(value.SerializeToString()).decode()


def test_parse_reports_unclocked_substrings_as_decode_errors() -> None:
    """Substrings without a vector clock are not the structureless case.

    Character identifiers are present, so re-encoding from scratch would
    discard replica state; the broad error keeps callers from falling back.
    """
    document = unclocked_bare_document()

    with pytest.raises(CRDTDecodeError) as excinfo:
        _parse_crdt_string(document)

    assert not isinstance(excinfo.value, CRDTStructureError)
    assert "no vector clock" in str(excinfo.value)


def test_parse_treats_a_degenerate_substring_as_no_structure() -> None:
    """An entry without characters carries no state, so this stays structureless.

    A cross-parse produces exactly this shape, so only substrings that
    reference characters may keep a payload out of the fallback.
    """
    inner = reminders_pb2.String()  # type: ignore[attr-defined]
    inner.substring.add()
    version = versioned_document_pb2.Version()  # type: ignore[attr-defined]
    version.data = inner.SerializeToString()
    payload = base64.b64encode(version.SerializeToString()).decode()

    with pytest.raises(CRDTStructureError):
        _parse_crdt_string(payload)


def test_update_refuses_a_bare_copy_whose_substrings_have_no_clock() -> None:
    """An unclocked bare document aborts the update instead of being replaced."""
    document = unclocked_bare_document()
    svc = make_service(record_with_title(document))
    reminder = Reminder(
        id="Reminder/REM-EDIT",
        list_id="List/LIST-001",
        title="New",
        desc="Notes",
        record_change_tag="server-tag",
    )

    with pytest.raises(CRDTDecodeError, match="no vector clock"):
        svc.update(reminder)

    raw: Any = svc._raw
    raw.modify.assert_not_called()


def test_visible_text_lookup_tells_dropped_text_from_empty() -> None:
    """The lookup distinguishes a lossy decode from a genuinely empty one."""
    assert _structure_has_visible_text(cached_string_only_document("Old"))
    assert not _structure_has_visible_text(_encode_crdt_document(""))


def test_visible_text_lookup_ignores_payloads_it_cannot_inspect() -> None:
    """A payload whose structure cannot be read shows no hidden text."""
    value = reminders_pb2.String(string="no structure")  # type: ignore[attr-defined]

    assert not _structure_has_visible_text(value.SerializeToString())
    assert not _structure_has_visible_text(b"\xff" * 16)
