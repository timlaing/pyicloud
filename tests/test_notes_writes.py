"""Offline tests for Notes mutation payloads and acknowledgment handling."""

# Payload fixtures inspect the isolated transport to replace its network dispatch.
# pylint: disable=protected-access

from __future__ import annotations

import base64
from collections.abc import Iterator
import json
from typing import Any, Literal
from unittest.mock import MagicMock, patch
from uuid import UUID
import zlib

from pydantic import ValidationError
import pytest
from requests import PreparedRequest, Request, Response
from requests.adapters import HTTPAdapter
from requests.exceptions import InvalidSchema

from pyicloud.common.cloudkit.client import _CloudKitHTTP
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.notes import (
    NoteParagraph,
    NoteRevisionConflict,
    NotesLinkedWriteIncomplete,
    NotesService,
    NotesWriteRejected,
    NotesWriteUncertain,
    NoteTableWriteResult,
    NoteTagResult,
    NoteTextRun,
    NoteWriteResult,
    RichNoteBody,
    table_encoding,
)
from pyicloud.services.notes.decoding import BodyDecoder
from pyicloud.services.notes.encoding import (
    TABLE_UTI,
    TAG_UTI,
    InlineObject,
    append_inline_note_object,
    encode_checklist_state,
    encode_new_note,
    encode_note_edit,
    inline_note_objects,
    remove_inline_note_object,
    replace_inline_note_text,
    validate_plain_document,
    validate_rich_document,
)
from pyicloud.services.notes.exceptions import NotesError
from pyicloud.services.notes.models.write import NotesModifyOperation, NotesWriteRecord
from pyicloud.services.notes.protobuf import notes_pb2
from pyicloud.services.notes.rendering.renderer import render_note_fragment
from pyicloud.services.notes.table_encoding import (
    encode_new_table,
    encode_table_cell_edit,
    encode_table_resize,
    table_cells,
)
from pyicloud.session import PyiCloudSession

FOLDER = "Folder/synthetic"
ZONE = {"zoneName": "Notes"}
NOTE_ID = "synthetic-note-id"
TRASH = "TrashFolder-CloudKit"
DESTINATION = "Folder/destination"
IMAGE_ID = "synthetic-image"
USER_FOLDER = "621d7211-ea66-459c-9ff5-dc31c526538c"
USER_PARENT = "d2518af9-7da6-4ea8-b3d9-c8c27f146ad3"
PIN_OWNER = "_" + "a" * 32
PIN_ZONE = {**ZONE, "ownerRecordName": PIN_OWNER, "zoneType": "REGULAR_CUSTOM_ZONE"}
PIN_ID = f"Note_UserSpecific::{PIN_OWNER}/Private::Notes::{PIN_OWNER}::{NOTE_ID}"


@pytest.mark.parametrize(
    "outcome",
    ["saved", "stale", "parent-uncertain", "table-uncertain", "table-rejected"],
)
def test_resize_note_table_uses_parent_first(
    notes: NotesService, session: MagicMock, outcome: str
) -> None:
    """Check both revisions, preserve parent bytes, and report partial outcomes."""
    table_id = "synthetic-table"
    current = _existing_note()
    body = append_inline_note_object(
        base64.b64decode(current["fields"]["TextDataEncrypted"]["value"]),
        InlineObject(table_id, TABLE_UTI),
    )
    current["fields"]["TextDataEncrypted"]["value"] = body
    table: dict[str, Any] = {
        "recordName": table_id,
        "recordType": "Attachment",
        "recordChangeTag": "table-old",
        "parent": {"recordName": NOTE_ID},
        "fields": {
            "Note": {"type": "REFERENCE", "value": {"recordName": NOTE_ID}},
            "UTIEncrypted": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(TABLE_UTI.encode()).decode(),
            },
            "MergeableDataEncrypted": {
                "type": "ENCRYPTED_BYTES",
                "value": encode_new_table([["A", "B"], ["C", "D"]]),
            },
        },
    }
    writes = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            (ref,) = payload["records"]
            return _response({
                "records": [current if ref["recordName"] == NOTE_ID else table]
            })
        (operation,) = payload["operations"]
        assert operation["operationType"] == "update"
        assert kwargs["allow_redirects"] is False
        sent = operation["record"]
        writes.append(sent["recordName"])
        if sent["recordName"] == NOTE_ID:
            assert sent["recordChangeTag"] == "old-revision"
            assert sent["fields"]["TextDataEncrypted"]["value"] == body
            if outcome == "parent-uncertain":
                return _response({"records": []})
            ack = _ack(sent, parent=sent["parent"], recordChangeTag="note-new")
        else:
            assert writes == [NOTE_ID, table_id]
            assert sent["recordChangeTag"] == "table-old"
            fields = {
                name: value
                for name, value in sent["fields"].items()
                if value["value"] is not None
            }
            assert table_cells(
                base64.b64decode(fields["MergeableDataEncrypted"]["value"])
            ) == (("A", "B"), ("", ""), ("C", "D"))
            if outcome == "table-uncertain":
                return _response({"records": []})
            if outcome == "table-rejected":
                return _response({
                    "records": [{"recordName": table_id, "serverErrorCode": "CONFLICT"}]
                })
            ack = _ack(
                {**sent, "fields": fields},
                recordType="Attachment",
                parent=sent["parent"],
                recordChangeTag="table-new",
            )
            ack["fields"]["Note"] = table["fields"]["Note"]
        return _response({"records": [ack]})

    session.post.side_effect = post

    def resize() -> NoteTableWriteResult:
        return notes.resize_note_table(
            NOTE_ID,
            table_id=table_id,
            axis="row",
            action="insert",
            index=2,
            expected_change_tag="old-revision",
            expected_table_change_tag="stale" if outcome == "stale" else "table-old",
        )

    if outcome == "stale":
        with pytest.raises(NoteRevisionConflict, match="table revision"):
            resize()
        assert not writes
    elif outcome == "saved":
        result = resize()
        assert result.record_change_tag == "note-new"
        assert result.table_change_tag == "table-new"
        assert writes == [NOTE_ID, table_id]
    else:
        with pytest.raises(NotesLinkedWriteIncomplete) as caught:
            resize()
        if outcome == "parent-uncertain":
            assert (caught.value.note_state, caught.value.object_state) == (
                "uncertain",
                "not_submitted",
            )
            assert writes == [NOTE_ID]
        else:
            assert (caught.value.note_state, caught.value.object_state) == (
                "confirmed",
                outcome.split("-")[1],
            )
            assert writes == [NOTE_ID, table_id]


def test_table_resize_sequence_preserves_remaining_cells() -> None:
    """Follow the four browser operations and preserve independent cell edits."""
    raw = base64.b64decode(encode_new_table([["A", "B"], ["C", "D"]]))
    before = _table_cell_identity(raw)
    raw = base64.b64decode(
        encode_table_resize(raw, axis="row", action="insert", index=2)
    )
    assert table_cells(raw) == (("A", "B"), ("", ""), ("C", "D"))
    assert _table_cell_identity(raw) == before
    raw = base64.b64decode(
        encode_table_resize(raw, axis="column", action="insert", index=2)
    )
    assert table_cells(raw) == (("A", "", "B"), ("", "", ""), ("C", "", "D"))
    assert _table_cell_identity(raw) == before
    with pytest.raises(ValueError, match="non-empty"):
        encode_table_resize(raw, axis="row", action="delete", index=3)
    raw = base64.b64decode(
        encode_table_resize(
            raw, axis="row", action="delete", index=3, confirm_nonempty=True
        )
    )
    assert _table_cell_identity(raw) == {"A": before["A"], "B": before["B"]}
    raw = base64.b64decode(
        encode_table_resize(
            raw, axis="column", action="delete", index=3, confirm_nonempty=True
        )
    )
    assert table_cells(raw) == (("A", ""), ("", ""))
    after = _table_cell_identity(raw)
    assert after["A"] == before["A"]
    data = notes_pb2.MergableDataProto.FromString(
        zlib.decompress(raw)
    ).mergable_data_object.mergeable_data_object_data
    for entry in data.mergeable_data_object_entry:
        if entry.HasField("ordered_set"):
            axis = entry.ordered_set
            assert len(axis.ordering.contents.element) == 3
            assert len(axis.elements.element) == 2
            assert (
                sum(span.tombstone for span in axis.ordering.array.contents.substring)
                == 1
            )
    edited = encode_table_cell_edit(raw, 2, 2, "Still editable")
    assert edited is not None
    assert table_cells(base64.b64decode(edited)) == (("A", ""), ("", "Still editable"))


@pytest.mark.parametrize("axis", ["row", "column"])
@pytest.mark.parametrize("deleted_index", [1, 2, 3])
@pytest.mark.parametrize("inserted_index", [1, 2, 3])
def test_table_resize_insert_after_delete(
    axis: Literal["row", "column"], deleted_index: int, inserted_index: int
) -> None:
    """Insert around retained tombstones without changing surviving identities."""
    original = [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]
    raw = base64.b64decode(encode_new_table(original))
    before = _table_cell_identity(raw)
    raw = base64.b64decode(
        encode_table_resize(
            raw, axis=axis, action="delete", index=deleted_index, confirm_nonempty=True
        )
    )
    surviving = _table_cell_identity(raw)
    assert surviving == {text: before[text] for row in table_cells(raw) for text in row}
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    axes = [
        entry.ordered_set
        for entry in data.mergeable_data_object_entry
        if entry.HasField("ordered_set")
    ]
    markers = axes[0 if axis == "row" else 1].ordering.array.contents
    (deleted_span,) = [span for span in markers.substring if span.tombstone]
    deleted_actor = data.text_timestamp.clock[
        deleted_span.char_id.replica_id - 1
    ].replica_uuid
    raw = base64.b64decode(
        encode_table_resize(raw, axis=axis, action="insert", index=inserted_index)
    )
    expected = [row.copy() for row in original]
    if axis == "row":
        del expected[deleted_index - 1]
        expected.insert(inserted_index - 1, ["", "", ""])
    else:
        for row in expected:
            del row[deleted_index - 1]
            row.insert(inserted_index - 1, "")
    assert table_cells(raw) == tuple(tuple(row) for row in expected)
    assert _table_cell_identity(raw) == surviving
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    axes = [
        entry.ordered_set
        for entry in data.mergeable_data_object_entry
        if entry.HasField("ordered_set")
    ]
    edited_axis = axes[0 if axis == "row" else 1]
    assert len(edited_axis.ordering.contents.element) == 4
    assert len(edited_axis.elements.element) == 3
    markers = edited_axis.ordering.array.contents
    assert markers.note_text == "\ufffc" * 3
    assert [run.length for run in markers.attribute_run] == [1, 1, 1]
    (retained,) = [span for span in markers.substring if span.tombstone]
    assert retained.char_id.clock == deleted_span.char_id.clock
    assert retained.length == deleted_span.length == 1
    assert (
        data.text_timestamp.clock[retained.char_id.replica_id - 1].replica_uuid
        == deleted_actor
    )
    assert sum(span.length for span in markers.substring if not span.tombstone) == 3
    blank_row = inserted_index if axis == "row" else 1
    blank_column = inserted_index if axis == "column" else 1
    edited = encode_table_cell_edit(raw, blank_row, blank_column, "New cell")
    assert edited is not None
    expected[blank_row - 1][blank_column - 1] = "New cell"
    assert table_cells(base64.b64decode(edited)) == tuple(
        tuple(row) for row in expected
    )


@pytest.mark.parametrize("axis", ["row", "column"])
@pytest.mark.parametrize("registry", ["keys", "types", "both"])
def test_table_resize_preserves_reordered_registries(
    axis: Literal["row", "column"], registry: str
) -> None:
    """Resolve new UUID maps by registry names rather than canonical offsets."""
    raw = base64.b64decode(encode_new_table([["A", "B"], ["C", "D"]]))
    before = _table_cell_identity(raw)
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    old_keys = list(data.mergeable_data_object_key_item)
    old_types = list(data.mergeable_data_object_type_item)
    keys = old_keys[1:] + old_keys[:1] if registry != "types" else old_keys
    types = old_types[1:] + old_types[:1] if registry != "keys" else old_types
    data.mergeable_data_object_key_item[:] = keys
    data.mergeable_data_object_type_item[:] = types
    for entry in data.mergeable_data_object_entry:
        if entry.HasField("custom_map"):
            mapping = entry.custom_map
            mapping.type = types.index(old_types[mapping.type])
            for item in mapping.map_entry:
                item.key = keys.index(old_keys[item.key])
    raw = zlib.compress(proto.SerializeToString())
    assert table_cells(raw) == (("A", "B"), ("C", "D"))
    raw = base64.b64decode(
        encode_table_resize(raw, axis=axis, action="insert", index=2)
    )
    expected = (
        (("A", "B"), ("", ""), ("C", "D"))
        if axis == "row"
        else (("A", "", "B"), ("C", "", "D"))
    )
    assert table_cells(raw) == expected
    assert _table_cell_identity(raw) == before
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    resized = proto.mergable_data_object.mergeable_data_object_data
    assert list(resized.mergeable_data_object_key_item) == keys
    assert list(resized.mergeable_data_object_type_item) == types
    for entry in resized.mergeable_data_object_entry:
        if entry.HasField("custom_map") and entry.custom_map.type == types.index(
            "com.apple.CRDT.NSUUID"
        ):
            assert entry.custom_map.map_entry[0].key == keys.index("UUIDIndex")
    reduced = base64.b64decode(
        encode_table_resize(raw, axis=axis, action="delete", index=2)
    )
    assert table_cells(reduced) == (("A", "B"), ("C", "D"))
    assert _table_cell_identity(reduced) == before


@pytest.mark.parametrize("registry", ["keys", "types"])
def test_table_uuid_creation_rejects_missing_registry_name(registry: str) -> None:
    """Missing UUID names fail before appending an identity or graph entry."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    if registry == "keys":
        data.mergeable_data_object_key_item[4] = "unsupported"
    else:
        data.mergeable_data_object_type_item[2] = "unsupported"
    before = data.SerializeToString()
    with pytest.raises(ValueError, match="table structure"):
        table_encoding._create_uuid_row(data, b"N" * 16)
    assert data.SerializeToString() == before


def _table_cell_identity(raw: bytes) -> dict[str, tuple[bytes, bytes, bytes, int]]:
    """Resolve cell and character identities independent of registry offsets."""
    data = notes_pb2.MergableDataProto.FromString(
        zlib.decompress(raw)
    ).mergable_data_object.mergeable_data_object_data
    entries = data.mergeable_data_object_entry
    columns = entries[entries[0].custom_map.map_entry[-1].value.object_index].dictionary
    ids = data.mergeable_data_object_uuid_item
    text_actors = data.text_timestamp.clock

    def uid(ref: notes_pb2.ObjectID) -> bytes:
        slot = (
            entries[ref.object_index]
            .custom_map.map_entry[0]
            .value.unsigned_integer_value
        )
        return bytes(ids[slot])

    result = {}
    for column in columns.element:
        for row in entries[column.value.object_index].dictionary.element:
            cell = entries[row.value.object_index].note
            if cell.note_text:
                char = cell.substring[1].char_id
                result[cell.note_text] = (
                    uid(row.key),
                    uid(column.key),
                    text_actors[char.replica_id - 1].replica_uuid,
                    char.clock,
                )
    return result


@pytest.mark.parametrize(
    "problem",
    [
        "markers",
        "object-clock",
        "entry",
        "empty-entry",
        "multiple-entry-fields",
        "multiple-value-fields",
        "dangling-link",
        "mixed-link",
        "clock-overflow",
        "uuid-index",
        "uuid-key",
    ],
)
def test_table_resize_rejects_unsupported_graph(problem: str) -> None:
    """Reject shapes we cannot rewrite without changing unknown references."""
    raw = base64.b64decode(encode_new_table([["A"], ["B"]]))
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    if problem == "markers":
        data.mergeable_data_object_entry[
            3
        ].ordered_set.ordering.array.contents.note_text = "xx"
    elif problem == "object-clock":
        data.object_timestamp.clock[0].replica_index = 1
    elif problem == "entry":
        data.mergeable_data_object_entry.add().list.SetInParent()
    elif problem == "empty-entry":
        data.mergeable_data_object_entry.add()
    elif problem == "multiple-entry-fields":
        entry = data.mergeable_data_object_entry.add()
        entry.note.SetInParent()
        entry.dictionary.SetInParent()
    elif problem == "multiple-value-fields":
        mapping = data.mergeable_data_object_entry.add().custom_map
        mapping.type = 1
        value = mapping.map_entry.add(key=2).value
        value.string_value = "unsupported"
        value.unsigned_integer_value = 1
    elif problem in ("dangling-link", "mixed-link"):
        link = data.mergeable_data_object_entry.add().register_latest.contents
        link.object_index = 9999 if problem == "dangling-link" else 1
        if problem == "mixed-link":
            link.string_value = "unsupported"
    elif problem == "clock-overflow":
        data.object_timestamp.clock[0].clock = 0xFFFF_FFFD
    else:
        mapping = data.mergeable_data_object_entry.add().custom_map
        mapping.type = 2
        item = mapping.map_entry.add(key=4 if problem == "uuid-index" else 0)
        item.value.unsigned_integer_value = 0 if problem == "uuid-index" else 1
    malformed = zlib.compress(proto.SerializeToString())
    with pytest.raises(ValueError, match="table structure"):
        encode_table_resize(
            malformed,
            axis="row",
            action="insert",
            index=1,
        )


def test_table_resize_rejects_shared_axis() -> None:
    """A readable square grid must not alias its row and column ordered sets."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    entries = data.mergeable_data_object_entry
    root = entries[0].custom_map
    row_axis = root.map_entry[2].value.object_index
    root.map_entry[3].value.object_index = row_axis
    row_key = entries[row_axis].ordered_set.ordering.contents.element[0].value
    columns = entries[root.map_entry[4].value.object_index].dictionary
    columns.element[0].key.CopyFrom(row_key)
    malformed = zlib.compress(proto.SerializeToString())
    assert table_cells(malformed) == (("A",),)
    with pytest.raises(ValueError, match="table structure"):
        encode_table_resize(malformed, axis="row", action="insert", index=1)


@pytest.mark.parametrize(
    ("axis", "action"), [("diagonal", "insert"), ("row", "replace")]
)
def test_table_resize_rejects_unknown_operation(axis: str, action: str) -> None:
    """Validate runtime input even when a caller does not follow type hints."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    operation: Any = encode_table_resize
    with pytest.raises(ValueError, match="row or column insertion or deletion"):
        operation(raw, axis=axis, action=action, index=1)


@pytest.mark.parametrize("insert", [False, True])
def test_table_axis_markers_reject_out_of_range_edit(insert: bool) -> None:
    """Exercise the helper guard that validated public positions cannot reach."""
    markers = notes_pb2.Note(note_text="\ufffc")
    history = [notes_pb2.TextSubstring(length=1)]
    before = markers.SerializeToString()
    insertion = notes_pb2.TextSubstring(length=1) if insert else None
    stamp = notes_pb2.TextIdentifier(replica_id=1, clock=1)
    with pytest.raises(ValueError, match="table structure"):
        table_encoding._edit_axis_markers(
            markers,
            history,
            2 if insert else 1,
            insertion=insertion,
            stamp=stamp,
        )
    assert markers.SerializeToString() == before


def test_table_axis_link_rejects_missing_uuid() -> None:
    """Reject missing deletion links without discarding an unrelated member."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    axis = data.mergeable_data_object_entry[3].ordered_set
    before = axis.elements.SerializeToString()
    graph = table_encoding._TableGraph(data)
    with pytest.raises(ValueError, match="table structure"):
        table_encoding._pop_table_link(graph, axis.elements, b"missing-axis-id!")
    assert axis.elements.SerializeToString() == before


@pytest.mark.parametrize("index", [0, 4, True])
def test_table_resize_rejects_invalid_position(index: int) -> None:
    """Reject indexes outside the active axis and boolean positions."""
    raw = base64.b64decode(encode_new_table([["A"], ["B"]]))
    with pytest.raises(ValueError, match="does not exist"):
        encode_table_resize(raw, axis="row", action="insert", index=index)


def test_table_resize_append_and_delete_empty_column() -> None:
    """Append at count + 1, delete an empty column, and retain a minimum size."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    with pytest.raises(ValueError, match="1 to 32"):
        encode_table_resize(raw, axis="row", action="delete", index=1)
    expanded = base64.b64decode(
        encode_table_resize(raw, axis="column", action="insert", index=2)
    )
    assert table_cells(expanded) == (("A", ""),)
    reduced = base64.b64decode(
        encode_table_resize(expanded, axis="column", action="delete", index=2)
    )
    assert table_cells(reduced) == (("A",),)


@pytest.mark.parametrize(
    "touch",
    [
        "saved",
        "changed-parent",
        "rejected",
        "uncertain",
        "table-uncertain",
        "lookup-error",
    ],
)
def test_note_table_create_and_edit(
    notes: NotesService, session: MagicMock, touch: str
) -> None:
    """Use both revisions and keep the note text and other cells untouched."""
    current = _existing_note()
    original = current["fields"]["TextDataEncrypted"]["value"]
    table: dict[str, Any] = {}
    operations: list[str] = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            if touch == "lookup-error" and operations[-1:] == ["update"]:
                raise PyiCloudAPIResponseException("lookup failed")
            (ref,) = payload["records"]
            target = current if ref["recordName"] == NOTE_ID else table
            if (
                target is current
                and touch == "changed-parent"
                and operations[-1:] == ["update"]
            ):
                target = {
                    **current,
                    "fields": {
                        **current["fields"],
                        "TextDataEncrypted": {
                            "type": "ENCRYPTED_BYTES",
                            "value": original,
                        },
                    },
                }
            return _response({"records": [target]})
        (operation,) = payload["operations"]
        sent = operation["record"]
        op = operation["operationType"]
        if sent["recordName"] == NOTE_ID:
            assert op == "update"
            assert sent["recordChangeTag"] == current["recordChangeTag"]
            operations.append("note")
            if len(operations) == 4 and touch == "rejected":
                return _response({
                    "records": [{"recordName": NOTE_ID, "serverErrorCode": "CONFLICT"}]
                })
            if len(operations) == 4 and touch == "uncertain":
                return _response({"records": []})
            ack = _ack(
                sent, parent=sent["parent"], recordChangeTag=f"note-{len(operations)}"
            )
            current.update(ack)
        else:
            operations.append(op)
            assert sent["recordType"] == "Attachment"
            assert sent["parent"] == {"recordName": NOTE_ID}
            fields = dict(sent["fields"])
            for name in (
                "MergeableDataAsset",
                "EncryptedValues",
                "EncryptedValuesAsset",
            ):
                assert fields.pop(name) == {"value": None}
            ack = _ack(
                {**sent, "fields": fields},
                recordType="Attachment",
                parent=sent["parent"],
                recordChangeTag=f"table-{len(operations)}",
            )
            if op == "create":
                assert fields["UTI"] == {"value": TABLE_UTI}
                ack["fields"]["UTI"]["type"] = "STRING"
                blob = base64.b64decode(current["fields"]["TextDataEncrypted"]["value"])
                assert inline_note_objects(blob) == (
                    InlineObject(sent["recordName"], TABLE_UTI),
                )
            else:
                assert sent["recordChangeTag"] == table["recordChangeTag"]
                if touch == "table-uncertain":
                    return _response({"records": []})
                ack["fields"] = {**table["fields"], **ack["fields"]}
            table.update(ack)
        return _response({"records": [ack]})

    session.post.side_effect = post
    added = notes.add_note_table(
        NOTE_ID, rows=[["A", "B"], ["C", "D"]], expected_change_tag="old-revision"
    )
    view = notes.get_note_table(NOTE_ID)
    assert (view.table_id, view.record_change_tag, view.table_change_tag) == (
        added.table_id,
        added.record_change_tag,
        added.table_change_tag,
    )
    assert view.rows == (("A", "B"), ("C", "D"))
    linked_body = current["fields"]["TextDataEncrypted"]["value"]
    previous = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(base64.b64decode(original))
    )
    linked = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(base64.b64decode(linked_body))
    )
    assert linked.document.note.note_text.startswith(previous.document.note.note_text)

    def edit(
        text: str, table_revision: str = view.table_change_tag
    ) -> NoteTableWriteResult:
        return notes.update_note_table_cell(
            NOTE_ID,
            table_id=view.table_id,
            row=2,
            column=1,
            text=text,
            expected_change_tag=view.record_change_tag,
            expected_table_change_tag=table_revision,
        )

    with pytest.raises(NoteRevisionConflict, match="table revision"):
        edit("New", "stale")
    assert not edit("C").changed
    assert operations == ["note", "create"]
    if touch != "saved":
        with pytest.raises(NotesLinkedWriteIncomplete) as caught:
            edit("New")
        assert caught.value.object_state == (
            "uncertain" if touch == "table-uncertain" else "confirmed"
        )
        assert caught.value.note_state == (
            "not_submitted"
            if touch in {"changed-parent", "table-uncertain", "lookup-error"}
            else touch
        )
        assert operations == (
            ["note", "create", "update"]
            if touch in {"changed-parent", "table-uncertain", "lookup-error"}
            else ["note", "create", "update", "note"]
        )
        return
    edited = edit("New")
    assert edited.changed
    assert operations == ["note", "create", "update", "note"]
    assert current["fields"]["TextDataEncrypted"]["value"] == linked_body
    assert notes.get_note_table(NOTE_ID).rows == (("A", "B"), ("New", "D"))


def test_table_codec_preserves_cell_positions_across_edits() -> None:
    """Edit an empty cell and then clear it, keeping all other cells stable."""
    raw = base64.b64decode(encode_new_table([["", "B"], ["C", "D"]]))
    assert table_cells(raw) == (("", "B"), ("C", "D"))
    assert encode_table_cell_edit(raw, 1, 1, "") is None
    edited = encode_table_cell_edit(raw, 1, 1, "New \U0001f600")
    assert edited is not None
    raw = base64.b64decode(edited)
    assert table_cells(raw) == (("New \U0001f600", "B"), ("C", "D"))
    cleared = encode_table_cell_edit(raw, 1, 1, "")
    assert cleared is not None
    assert table_cells(base64.b64decode(cleared)) == (("", "B"), ("C", "D"))


@pytest.mark.parametrize(
    "rows", [[], [[]], [["A"], ["B", "C"]], [["\ufffc"]], [["\ud800"]], [["A" * 65537]]]
)
def test_table_rejects_invalid_rows(rows: list[list[str]]) -> None:
    """Reject malformed dimensions and unsafe cell content."""
    with pytest.raises(ValueError):
        encode_new_table(rows)


@pytest.mark.parametrize(
    "blob",
    [
        b"invalid",
        zlib.compress(b"\xff"),
        zlib.compress(b"") + b"extra",
        zlib.compress(b"\x08\x01"),
    ],
)
def test_table_rejects_invalid_wire_data(blob: bytes) -> None:
    """Fail closed on unverified compression or protobuf shapes."""
    with pytest.raises(ValueError, match="table structure"):
        table_cells(blob)


@pytest.mark.parametrize(
    "problem",
    ["root", "axis-index", "duplicate-key", "span", "style", "clock", "version"],
)
def test_table_edit_rejects_unverified_graph(problem: str) -> None:
    """Refuse to rewrite ambiguous graph references or unknown text history."""
    raw = base64.b64decode(encode_new_table([["A", "B"], ["C", "D"]]))
    proto = notes_pb2.MergableDataProto.FromString(zlib.decompress(raw))
    data = proto.mergable_data_object.mergeable_data_object_data
    entries = data.mergeable_data_object_entry
    if problem == "root":
        entries[0].custom_map.map_entry[-1].value.object_index = len(entries)
    elif problem == "axis-index":
        next(
            item.ordered_set for item in entries if item.HasField("ordered_set")
        ).ordering.array.attachment[0].index = 3
    elif problem == "duplicate-key":
        column_map = next(
            item.dictionary for item in entries if item.HasField("dictionary")
        )
        column_map.element.add().CopyFrom(column_map.element[0])
    elif problem == "clock":
        data.text_timestamp.clock[0].replica_clock[0].clock = 0
    elif problem == "version":
        proto.mergable_data_object.version = 1
    else:
        cell = next(item.note for item in entries if item.HasField("note"))
        if problem == "span":
            cell.substring[1].child[0] = 1
        else:
            cell.attribute_run[0].underlined = 1
    raw = zlib.compress(proto.SerializeToString())
    with pytest.raises(ValueError, match="table structure"):
        encode_table_cell_edit(raw, 1, 1, "New")


@pytest.mark.parametrize("row,column", [(0, 1), (1, 2), (True, 1), (1.0, 1)])
def test_table_edit_rejects_invalid_coordinates(row: int, column: int) -> None:
    """Never coerce an invalid row or column to a real cell."""
    raw = base64.b64decode(encode_new_table([["A"]]))
    with pytest.raises(ValueError, match="row or column"):
        encode_table_cell_edit(raw, row, column, "New")


def test_inline_tag_append_preserves_existing_table_and_formatting() -> None:
    """Appending a linked marker preserves existing runs and object identities."""
    compressed = base64.b64decode(encode_new_note("Title", "").body)
    first = base64.b64decode(
        append_inline_note_object(compressed, InlineObject("table-1", TABLE_UTI))
    )
    second = base64.b64decode(
        append_inline_note_object(first, InlineObject("tag-1", TAG_UTI))
    )
    before = notes_pb2.NoteStoreProto.FromString(zlib.decompress(first)).document.note
    after = notes_pb2.NoteStoreProto.FromString(zlib.decompress(second)).document.note
    assert after.note_text == before.note_text + "\ufffc\n\n"
    assert after.attribute_run[:-2] == before.attribute_run[:]
    assert after.attribute_run[-2].length == 2
    assert inline_note_objects(second) == (
        InlineObject("table-1", TABLE_UTI),
        InlineObject("tag-1", TAG_UTI),
    )
    duplicate = InlineObject("tag-1", TAG_UTI)
    with pytest.raises(ValueError, match="inline objects"):
        append_inline_note_object(second, duplicate)


@pytest.mark.parametrize(
    "outcome",
    [
        "saved",
        "lookup-mismatch",
        "lookup-error",
        "child-uncertain",
        "parent-uncertain",
        "parent-rejected",
        "child-rejected",
    ],
)
def test_add_tag_verifies_linked_writes_without_replay(
    notes: NotesService, session: MagicMock, outcome: str
) -> None:
    """Verify the revision-guarded note link before creating the tag record."""
    current = _existing_note()
    original = base64.b64decode(current["fields"]["TextDataEncrypted"]["value"])
    child_ids: list[str] = []
    saved: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        if url.split("?")[0].endswith("/records/lookup"):
            if saved and outcome == "lookup-error":
                raise PyiCloudAPIResponseException("lookup failed")
            return _response({
                "records": saved
                if saved and outcome != "lookup-mismatch"
                else [current]
            })
        assert kwargs["allow_redirects"] is False
        (operation,) = kwargs["json"]["operations"]
        sent = operation["record"]
        if operation["operationType"] == "create":
            assert child_ids == [sent["recordName"]]
            assert sent["recordType"] == "InlineAttachment"
            assert sent["parent"] == {"recordName": NOTE_ID}
            assert sent["fields"]["Note"] == {
                "value": {"recordName": NOTE_ID, "action": "VALIDATE", "zoneID": ZONE}
            }
            assert (
                base64.b64decode(sent["fields"]["AltTextEncrypted"]["value"])
                == b"#Test-tag"
            )
            assert (
                base64.b64decode(
                    sent["fields"]["TokenContentIdentifierEncrypted"]["value"]
                )
                == b"TEST-TAG"
            )
            assert sent["fields"]["MinimumSupportedNotesVersion"] == {"value": 7}
            if outcome == "child-uncertain":
                return _response({"records": []})
            if outcome == "child-rejected":
                return _response({
                    "records": [
                        {
                            "recordName": sent["recordName"],
                            "serverErrorCode": "CONFLICT",
                        }
                    ]
                })
            return _response({
                "records": [
                    _ack(sent, recordType="InlineAttachment", parent=sent["parent"])
                ]
            })
        assert sent["recordName"] == NOTE_ID
        assert sent["recordChangeTag"] == "old-revision"
        blob = base64.b64decode(sent["fields"]["TextDataEncrypted"]["value"])
        child_ids.append(inline_note_objects(blob)[0].id)
        assert inline_note_objects(blob) == (InlineObject(child_ids[0], TAG_UTI),)
        old_text = validate_plain_document(original).document.note.note_text
        new_text = notes_pb2.NoteStoreProto.FromString(
            zlib.decompress(blob)
        ).document.note.note_text
        assert new_text == old_text + "\ufffc\n\n"
        if outcome == "parent-uncertain":
            return _response({"records": []})
        if outcome == "parent-rejected":
            return _response({
                "records": [{"recordName": NOTE_ID, "serverErrorCode": "CONFLICT"}]
            })
        saved.append(_ack(sent, recordChangeTag="post-link-revision"))
        return _response({"records": [_ack(sent)]})

    session.post.side_effect = post
    if outcome == "saved":
        result = notes.add_note_tag(
            NOTE_ID, tag="#Test-tag", expected_change_tag="old-revision"
        )
        assert result.changed
        assert result.tag_id == child_ids[0]
        assert result.record_change_tag == "post-link-revision"
    elif outcome == "parent-rejected":
        with pytest.raises(NotesWriteRejected):
            notes.add_note_tag(
                NOTE_ID, tag="#Test-tag", expected_change_tag="old-revision"
            )
    else:
        with pytest.raises(NotesLinkedWriteIncomplete) as exc:
            notes.add_note_tag(
                NOTE_ID, tag="#Test-tag", expected_change_tag="old-revision"
            )
        assert exc.value.note_id == NOTE_ID
        assert exc.value.object_id == child_ids[0]
        assert exc.value.note_state == (
            "uncertain"
            if outcome.startswith("parent")
            or outcome in {"lookup-mismatch", "lookup-error"}
            else "confirmed"
        )
        assert (
            exc.value.object_state
            == {
                "parent-uncertain": "not_submitted",
                "child-uncertain": "uncertain",
                "child-rejected": "rejected",
                "lookup-mismatch": "confirmed",
                "lookup-error": "confirmed",
            }[outcome]
        )
    assert session.post.call_count == (
        2
        if outcome.startswith("parent")
        else 4
        if outcome in {"saved", "lookup-mismatch", "lookup-error"}
        else 3
    )


@pytest.mark.parametrize("missing", [False, True])
def test_add_tag_detects_existing_case_insensitive_tag(
    notes: NotesService, session: MagicMock, missing: bool
) -> None:
    """A duplicate tag is a no-op, and unverified existing tags block new writes."""
    current = _existing_note()
    raw = base64.b64decode(current["fields"]["TextDataEncrypted"]["value"])
    current["fields"]["TextDataEncrypted"]["value"] = append_inline_note_object(
        raw, InlineObject("tag-1", TAG_UTI)
    )
    tag = {
        "recordName": "tag-1",
        "recordType": "InlineAttachment",
        "parent": {"recordName": NOTE_ID},
        "fields": {
            "UTIEncrypted": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(TAG_UTI.encode()).decode(),
            },
            "TokenContentIdentifierEncrypted": {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(b"TEST").decode("ascii"),
            },
            "Note": {"type": "REFERENCE", "value": {"recordName": NOTE_ID}},
        },
    }
    session.post.side_effect = [
        _response({"records": [current]}),
        _response({"records": [] if missing else [tag]}),
    ]
    if missing:
        with pytest.raises(NotesError, match="verify existing Notes tags"):
            notes.add_note_tag(NOTE_ID, tag="test", expected_change_tag="old-revision")
    else:
        result = notes.add_note_tag(
            NOTE_ID, tag="test", expected_change_tag="old-revision"
        )
        assert not result.changed
        assert result.tag_id == "tag-1"
    assert session.post.call_count == 2


@pytest.mark.parametrize(
    "case",
    [
        "pin",
        "unpin",
        "already-unpinned",
        "missing-unpin",
        "wrong-owner",
        "wrong-ack",
        "bad-state",
    ],
)
def test_pin_writes_only_verified_per_user_record(
    notes: NotesService, session: MagicMock, case: str
) -> None:
    """Pin state uses its own create or revision-guarded update, never a Note write."""
    note = _existing_note()
    note["fields"]["Folder"]["value"]["zoneID"] = PIN_ZONE
    viewed = 1_700_000_000_123
    state = 1 if case == "unpin" else 0
    if case == "bad-state":
        state = 2
    folder_ref = {"recordName": FOLDER, "action": "NONE", "zoneID": PIN_ZONE}
    note_ref = {"recordName": NOTE_ID, "action": "NONE", "zoneID": ZONE}
    original: dict[str, Any] = {
        "recordName": PIN_ID,
        "recordType": "Note_UserSpecific",
        "recordChangeTag": "pin-old",
        "fields": {
            "IsPinned": {"type": "INT64", "value": state},
            "LastViewedModificationDate": {"type": "TIMESTAMP", "value": viewed},
            "Folder": {
                "type": "REFERENCE",
                "value": folder_ref
                if case != "wrong-owner"
                else {**folder_ref, "zoneID": {**PIN_ZONE, "ownerRecordName": "other"}},
            },
            "Note": {"type": "REFERENCE", "value": note_ref},
        },
    }
    missing = case in {"pin", "missing-unpin"}
    desired = case not in {"unpin", "already-unpinned", "missing-unpin"}

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            record_name = payload["records"][0]["recordName"]
            if record_name == NOTE_ID:
                return _response({"records": [note]})
            assert record_name == PIN_ID
            return _response({
                "records": [
                    {"recordName": PIN_ID, "serverErrorCode": "NOT_FOUND"}
                    if missing
                    else original
                ]
            })
        assert kwargs["allow_redirects"] is False
        op = payload["operations"][0]
        record = op["record"]
        assert op["operationType"] == ("create" if missing else "update")
        assert record["recordName"] == PIN_ID
        assert record["recordType"] == "Note_UserSpecific"
        assert record.get("recordChangeTag") == (None if missing else "pin-old")
        assert record["fields"] == {
            "IsPinned": {"value": int(desired)},
            "Note": {"value": note_ref},
            "Folder": {"value": folder_ref},
            "LastViewedModificationDate": {"value": 0 if missing else viewed},
        }
        ack: dict[str, Any] = {
            **original,
            "recordChangeTag": "pin-new",
            "fields": {
                **original["fields"],
                "IsPinned": {"type": "INT64", "value": int(desired)},
                "LastViewedModificationDate": {
                    "type": "TIMESTAMP",
                    "value": 0 if missing else viewed,
                },
            },
        }
        if case == "wrong-ack":
            ack["fields"]["Note"]["value"] = {**note_ref, "recordName": "unrelated"}
        return _response({"records": [ack]})

    session.post.side_effect = post
    if case in {"wrong-owner", "wrong-ack", "bad-state"}:
        with pytest.raises(NotesWriteUncertain if case == "wrong-ack" else NotesError):
            notes.set_note_pinned(
                NOTE_ID, pinned=desired, expected_change_tag="old-revision"
            )
    else:
        result = notes.set_note_pinned(
            NOTE_ID, pinned=desired, expected_change_tag="old-revision"
        )
        assert (result.id, result.pinned, result.changed) == (
            NOTE_ID,
            desired,
            case in {"pin", "unpin"},
        )
    assert session.post.call_count == (
        3 if case in {"pin", "unpin", "wrong-ack"} else 2
    )


def _named_folder(
    folder_id: str = USER_FOLDER, *, parent: str | None = None, tag: str = "old-tag"
) -> dict[str, Any]:
    record = _folder_record(
        recordName=folder_id,
        recordChangeTag=tag,
        fields={
            "TitleEncrypted": {"type": "ENCRYPTED_BYTES", "value": "Rm9sZGVy"},
        },
    )
    if parent is not None:
        record["fields"]["ParentFolder"] = {
            "type": "REFERENCE",
            "value": {"recordName": parent, "zoneID": ZONE, "action": "VALIDATE"},
        }
        record["parent"] = {"recordName": parent}
    return record


@pytest.mark.parametrize(
    "kind", ["root", "nested", "rename-root", "rename-nested", "move-into", "move-out"]
)
def test_folder_create_rename_and_move(
    notes: NotesService, session: MagicMock, kind: str
) -> None:
    """Send the captured parent shapes, including the explicit null for root moves."""
    source = USER_PARENT if kind in {"rename-nested", "move-out"} else None
    target = USER_PARENT if kind in {"nested", "rename-nested", "move-into"} else None
    creating = kind in {"root", "nested"}

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            lookup_id = payload["records"][0]["recordName"]
            return _response({
                "records": [
                    _named_folder(
                        lookup_id, parent=source if lookup_id == USER_FOLDER else None
                    )
                ]
            })
        assert kwargs["allow_redirects"] is False
        assert payload["zoneID"] == ZONE
        op = payload["operations"][0]
        sent = op["record"]
        assert sent["recordType"] == "Folder"
        if creating:
            assert op["operationType"] == "create"
            assert "recordChangeTag" not in sent
            assert str(UUID(sent["recordName"])) == sent["recordName"]
        else:
            assert op["operationType"] == "update"
            assert sent["recordChangeTag"] == "old-tag"
            assert sent["recordName"] == USER_FOLDER
        if target is not None:
            assert sent["fields"]["ParentFolder"] == {
                "value": {"recordName": target, "zoneID": ZONE, "action": "VALIDATE"}
            }
            assert sent["parent"] == {"recordName": target}
        elif creating:
            assert "ParentFolder" not in sent["fields"]
            assert "parent" not in sent
        else:
            assert sent["fields"]["ParentFolder"] == {"value": None}
            if kind == "move-out":
                assert sent["parent"] == {"recordName": None}
            else:
                assert "parent" not in sent
        name = sent["fields"]["TitleEncrypted"]["value"]
        assert base64.b64decode(name).decode() == (
            "Folder" if kind.startswith("move") else "New folder"
        )
        ack = _named_folder(sent["recordName"], parent=target, tag="saved-tag")
        ack["fields"]["TitleEncrypted"]["value"] = name
        return _response({"records": [ack]})

    session.post.side_effect = post
    if creating:
        result = notes.create_folder("New folder", parent_id=target)
    elif kind.startswith("rename"):
        result = notes.rename_folder(
            USER_FOLDER, "New folder", expected_change_tag="old-tag"
        )
    else:
        result = notes.move_folder(
            USER_FOLDER, parent_id=target, expected_change_tag="old-tag"
        )
    assert result.record_change_tag == "saved-tag"


def test_folder_move_refuses_ancestor_cycle(
    notes: NotesService, session: MagicMock
) -> None:
    """A folder cannot be moved into its own descendant."""
    session.post.side_effect = [
        _response({"records": [_named_folder()]}),
        _response({"records": [_named_folder(USER_PARENT, parent=USER_FOLDER)]}),
    ]
    with pytest.raises(NotesError, match="safe Notes folder ancestry"):
        notes.move_folder(
            USER_FOLDER, parent_id=USER_PARENT, expected_change_tag="old-tag"
        )
    assert session.post.call_count == 2


def test_note_move_refuses_destination_with_shared_ancestor(
    notes: NotesService, session: MagicMock
) -> None:
    """An apparently private child may inherit sharing from its parent."""
    shared = _named_folder(USER_PARENT)
    shared["publicPermission"] = "READ_ONLY"
    session.post.side_effect = [
        _response({"records": [_existing_note()]}),
        _response({"records": [_named_folder(USER_FOLDER, parent=USER_PARENT)]}),
        _response({"records": [shared]}),
    ]
    with pytest.raises(NotesError, match="folder is not available"):
        notes.move_note(
            NOTE_ID, folder_id=USER_FOLDER, expected_change_tag="old-revision"
        )
    assert session.post.call_count == 3


@pytest.mark.parametrize(
    "case", ["empty", "note", "child", "incomplete", "changed", "uncertain", "rejected"]
)
def test_folder_delete_requires_complete_empty_check(
    notes: NotesService, session: MagicMock, case: str
) -> None:
    """Only a confirmed empty folder with the same revision reaches forceDelete."""
    contents = []
    if case == "note":
        contents.append(_existing_note(USER_FOLDER, recordType="PasswordProtectedNote"))
    elif case == "child":
        contents.append(_named_folder(USER_PARENT, parent=USER_FOLDER))
    zone = {
        "zoneID": ZONE,
        "syncToken": "token",
        "moreComing": False,
        "records": contents,
    }
    if case == "incomplete":
        zone.pop("records")
    ack = {"recordName": USER_FOLDER, "deleted": case != "uncertain"}
    if case == "rejected":
        ack["serverErrorCode"] = "CONFLICT"
    session.post.side_effect = [
        _response({"records": [_named_folder()]}),
        _response({"zones": [zone]}),
        _response({
            "records": [
                _named_folder(tag="new-tag" if case == "changed" else "old-tag")
            ]
        }),
        _response({"records": [ack]}),
    ]
    with pytest.raises(ValueError, match="explicit confirmation"):
        notes.delete_folder(USER_FOLDER, expected_change_tag="old-tag")
    session.post.assert_not_called()
    if case == "empty":
        assert notes.delete_folder(
            USER_FOLDER, expected_change_tag="old-tag", confirm_empty=True
        )
        request = session.post.call_args.kwargs
        assert request["allow_redirects"] is False
        assert request["json"] == {
            "zoneID": ZONE,
            "operations": [
                {"operationType": "forceDelete", "record": {"recordName": USER_FOLDER}}
            ],
        }
    else:
        error = {
            "uncertain": NotesWriteUncertain,
            "rejected": NotesWriteRejected,
            "changed": NoteRevisionConflict,
        }.get(case, NotesError)
        with pytest.raises(error):
            notes.delete_folder(
                USER_FOLDER, expected_change_tag="old-tag", confirm_empty=True
            )
    assert (
        session.post.call_count
        == {
            "empty": 4,
            "note": 2,
            "child": 2,
            "incomplete": 2,
            "changed": 3,
            "uncertain": 4,
            "rejected": 4,
        }[case]
    )


def _image_document() -> notes_pb2.NoteStoreProto:
    proto = validate_plain_document(
        base64.b64decode(encode_new_note("Title", "Prefix old suffix\nZ\n").body)
    )
    note = proto.document.note
    note.note_text = note.note_text.replace("Z", "\ufffc")
    run = note.attribute_run[-1]
    note.attribute_run.add(length=1, paragraph_style=run.paragraph_style)
    run.length = 1
    run.attachment_info.attachment_identifier = IMAGE_ID
    run.attachment_info.type_uti = "public.jpeg"
    note.attribute_run[2].font_weight = notes_pb2.FontWeight.FONT_WEIGHT_BOLD
    return proto


def test_image_text_edit_preserves_existing_attachment_and_character_ids() -> None:
    """Only the target fragment is replaced; the styled run and image survive."""
    original = _image_document()
    result = replace_inline_note_text(
        zlib.compress(original.SerializeToString()), "old", "new \U0001f600"
    )
    edited = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(base64.b64decode(result.body))
    )
    before, after = original.document.note, edited.document.note
    assert result.attachment_id == IMAGE_ID
    assert after.note_text == "Title\n\nPrefix new \U0001f600 suffix\n\ufffc\n"
    assert after.attribute_run[-2] == before.attribute_run[-2]
    assert after.attribute_run[2].font_weight == before.attribute_run[2].font_weight
    assert after.attribute_run[2].length == before.attribute_run[2].length + 3
    assert [span.length for span in after.substring if span.tombstone] == [3]
    assert after.substring[-2].char_id.clock == len("Title\n\nPrefix old")
    assert (
        sum(span.length for span in after.substring if not span.tombstone)
        == len(after.note_text.encode("utf-16-le")) // 2
    )


def test_targeted_text_edit_preserves_surrounding_runs_and_linked_objects() -> None:
    """Replace across runs using the first style and preserve an unrelated tag."""
    rich = RichNoteBody(
        paragraphs=(
            NoteParagraph(
                runs=(
                    NoteTextRun(text="Keep old", bold=True),
                    NoteTextRun(text=" text", italic=True),
                    NoteTextRun(text=" after", underline=True),
                )
            ),
        )
    )
    obj = InlineObject("untouched-tag", TAG_UTI)
    before = append_inline_note_object(
        base64.b64decode(encode_new_note("Title", rich).body), obj
    )
    replaced = replace_inline_note_text(base64.b64decode(before), "old text", "new")
    compressed = base64.b64decode(replaced.body)
    assert replaced.attachment_id is None
    assert inline_note_objects(compressed) == (obj,)
    note = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(compressed)
    ).document.note
    assert "Keep new after" in note.note_text
    assert sum(span.length for span in note.substring if span.tombstone) == len(
        "old text"
    )
    assert any(
        run.length == len("Keep new")
        and run.font_weight == notes_pb2.FontWeight.FONT_WEIGHT_BOLD
        for run in note.attribute_run
    )


@pytest.mark.parametrize(
    "old,new",
    [("Title", "New"), ("\ufffc", "New"), ("old", "old"), ("old", "a\nb"), ("", "New")],
)
def test_image_edit_rejects_unsafe_replacements(old: str, new: str) -> None:
    """Do not replace titles, object markers, or ambiguous or invalid text."""
    body = zlib.compress(_image_document().SerializeToString())
    with pytest.raises(ValueError):
        replace_inline_note_text(body, old, new)


@pytest.mark.parametrize(
    "bad", [None, "move", "owner", "missing-preview", "wrong-receipt", "wrong-ack"]
)
def test_replace_text_with_existing_preview(
    notes: NotesService, session: MagicMock, bad: str | None
) -> None:
    """Rereference the exact preview without uploading, then verify the save."""
    current = _existing_note()
    current["fields"].update({
        "TextDataEncrypted": {
            "type": "ENCRYPTED_BYTES",
            "value": base64.b64encode(
                zlib.compress(_image_document().SerializeToString())
            ).decode("ascii"),
        },
        "AttachmentViewType": {"type": "INT64", "value": 1},
        "FirstAttachmentUTIEncrypted": {"type": "ENCRYPTED_BYTES", "value": "anBlZw=="},
    })
    thumb = {
        "fileChecksum": "synthetic-file",
        "referenceChecksum": "synthetic-reference",
        "size": 12,
        "wrappingKey": "synthetic-key",
    }
    current["fields"]["FirstAttachmentThumbnail"] = {"type": "ASSETID", "value": thumb}
    owner = "different-note" if bad == "owner" else NOTE_ID
    preview_list = [{**thumb, "fileChecksum": "different-file"}]
    if bad != "missing-preview":
        preview_list.append({
            **thumb,
            "referenceChecksum": "preview-reference",
            "wrappingKey": "preview-key",
        })
    attachment = {
        "recordName": IMAGE_ID,
        "recordType": "Attachment",
        "parent": {"recordName": NOTE_ID},
        "fields": {
            "Note": {"type": "REFERENCE", "value": {"recordName": owner}},
            "PreviewImages": {"type": "ASSETID_LIST", "value": preview_list},
        },
    }
    locator = {"recordName": IMAGE_ID, "fieldName": "PreviewImages", "listIndex": 1}
    receipt = {
        **thumb,
        **locator,
        "receipt": "synthetic-receipt",
        "referenceChecksum": "new-reference",
    }
    if bad == "wrong-receipt":
        receipt["fileChecksum"] = "unrelated"

    def post(url: str, **kwargs: Any) -> MagicMock:
        endpoint = url.split("?")[0]
        payload = kwargs["json"]
        if endpoint.endswith("/records/lookup"):
            record_id = payload["records"][0]["recordName"]
            if record_id == DESTINATION:
                return _response({"records": [_folder_record(recordName=DESTINATION)]})
            return _response({
                "records": [attachment if record_id == IMAGE_ID else current]
            })
        assert kwargs["allow_redirects"] is False
        if endpoint.endswith("/assets/rereference"):
            assert payload == {"assets": [locator], "zoneID": ZONE}
            return _response({"assets": [receipt]})
        assert endpoint.endswith("/records/modify")
        sent = payload["operations"][0]["record"]
        preview = sent["fields"].pop("FirstAttachmentThumbnail")
        assert preview == {"value": receipt}
        assert sent["fields"]["AttachmentViewType"] == {"value": 1}
        assert sent["fields"]["FirstAttachmentUTIEncrypted"] == {"value": "anBlZw=="}
        if bad == "move":
            assert sent["parent"] == {"recordName": DESTINATION}
            assert (
                sent["fields"]["TextDataEncrypted"]["value"]
                == current["fields"]["TextDataEncrypted"]["value"]
            )
            assert sent["fields"]["ModificationDate"] == {"value": 1_700_000_000_123}
        ack = _ack(sent)
        ack["fields"]["FirstAttachmentThumbnail"] = {
            "type": "ASSETID",
            "value": {
                **thumb,
                "referenceChecksum": "new-reference",
                "size": 13 if bad == "wrong-ack" else 12,
            },
        }
        return _response({"records": [ack]})

    session.post.side_effect = post
    if bad is None:
        assert (
            notes.replace_note_text(
                NOTE_ID, old="old", new="new", expected_change_tag="old-revision"
            ).id
            == NOTE_ID
        )
    elif bad == "move":
        assert (
            notes.move_note(
                NOTE_ID, folder_id=DESTINATION, expected_change_tag="old-revision"
            ).id
            == NOTE_ID
        )
    else:
        with pytest.raises(NotesWriteUncertain if bad == "wrong-ack" else NotesError):
            notes.replace_note_text(
                NOTE_ID, old="old", new="new", expected_change_tag="old-revision"
            )
    assert (
        session.post.call_count
        == {
            None: 4,
            "move": 5,
            "owner": 2,
            "missing-preview": 2,
            "wrong-receipt": 3,
            "wrong-ack": 4,
        }[bad]
    )


@pytest.mark.parametrize(
    ("operation", "tag", "error"),
    [
        ("update", None, "requires a record change tag"),
        ("update", "  ", "requires a record change tag"),
        ("create", "old-revision", "must not specify a record change tag"),
    ],
)
def test_notes_wire_operation_requires_expected_revision_state(
    operation: Literal["create", "update"], tag: str | None, error: str
) -> None:
    """The low-level payload rejects operations that bypass revision guards."""
    record = NotesWriteRecord(recordName=NOTE_ID, fields={}, recordChangeTag=tag)
    with pytest.raises(ValidationError, match=error):
        NotesModifyOperation(operationType=operation, record=record)


def test_notes_wire_operation_disallows_force_update() -> None:
    """An arbitrary wire field cannot opt a guarded write out of revision checks."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        NotesModifyOperation.model_validate({
            "operationType": "update",
            "record": {"recordName": NOTE_ID, "fields": {}, "recordChangeTag": "tag"},
            "force": True,
        })


def _existing_note(folder_id: str = FOLDER, **overrides: Any) -> dict[str, Any]:
    encoded = encode_new_note("Synthetic title", "First line")
    folder = {"recordName": folder_id, "action": "VALIDATE", "zoneID": ZONE}
    timestamp = {"type": "TIMESTAMP", "value": 1_700_000_000_123}
    return {
        "recordName": NOTE_ID,
        "recordType": "Note",
        "recordChangeTag": "old-revision",
        "shortGUID": "synthetic-guid",
        "parent": {"recordName": folder_id},
        "zoneID": ZONE,
        "fields": {
            "TitleEncrypted": {"type": "ENCRYPTED_BYTES", "value": encoded.title},
            "SnippetEncrypted": {"type": "ENCRYPTED_BYTES", "value": encoded.snippet},
            "TextDataEncrypted": {"type": "ENCRYPTED_BYTES", "value": encoded.body},
            "CreationDate": timestamp,
            "ModificationDate": timestamp,
            "FoldersModificationDate": timestamp,
            "Folder": {"type": "REFERENCE", "value": folder},
            "Folders": {"type": "REFERENCE_LIST", "value": [folder]},
        },
        **overrides,
    }


def test_note_edit_preserves_history() -> None:
    """Changing text creates a replica and tombstones, not a fresh document."""
    original = base64.b64decode(encode_new_note("Title", "First line").body)
    first = validate_plain_document(original).document.note
    edited = encode_note_edit(original, "Title", "Second line \U0001f600")
    body = base64.b64decode(edited.body)
    second = validate_plain_document(body).document.note
    assert second.note_text == "Title\n\nSecond line \U0001f600\n"
    assert len(second.timestamp.clock) == 2
    assert second.substring[2].tombstone
    retired = second.substring[2].char_id
    assert retired.clock == first.substring[1].char_id.clock
    assert second.timestamp.clock[retired.replica_id - 1] == first.timestamp.clock[0]
    third = encode_note_edit(body, "Title", "Third line")
    last = validate_plain_document(base64.b64decode(third.body)).document.note
    assert sum(span.tombstone for span in last.substring) == 2


def test_rich_note_preserves_styles_and_utf16_offsets() -> None:
    """Encode formatting with a paragraph style and UTF-16 inline run lengths."""
    body = RichNoteBody(
        paragraphs=(
            NoteParagraph(style="heading", runs=(NoteTextRun(text="Section"),)),
            NoteParagraph(
                style="bullet",
                runs=(
                    NoteTextRun(text="\U0001f600", bold=True),
                    NoteTextRun(text="one", italic=True),
                    NoteTextRun(
                        text="two",
                        bold=True,
                        italic=True,
                        underline=True,
                        strikethrough=True,
                    ),
                ),
            ),
            NoteParagraph(runs=()),
        )
    )
    encoded = encode_new_note("Title", body)
    compressed = base64.b64decode(encoded.body)
    proto = validate_rich_document(compressed)
    note = proto.document.note
    assert note.note_text == "Title\n\nSection\n\U0001f600onetwo\n\n"
    assert base64.b64decode(encoded.snippet).decode() == "Section \U0001f600onetwo"
    assert [r.length for r in note.attribute_run] == [6, 1, 7, 1, 2, 3, 3, 1, 1]
    assert (
        note.attribute_run[2].paragraph_style.style_type
        == notes_pb2.StyleType.STYLE_TYPE_HEADING
    )
    bullet, continuation, combined = note.attribute_run[4:7]
    assert (
        bullet.paragraph_style.style_type
        == notes_pb2.StyleType.STYLE_TYPE_BULLET_LIST_ITEM
    )
    assert bullet.font_weight == notes_pb2.FontWeight.FONT_WEIGHT_BOLD
    assert continuation.font_weight == notes_pb2.FontWeight.FONT_WEIGHT_ITALIC
    assert continuation.paragraph_style == bullet.paragraph_style
    assert combined.font_weight == notes_pb2.FontWeight.FONT_WEIGHT_BOLD_ITALIC
    assert (combined.underlined, combined.strikethrough) == (1, 1)
    html = render_note_fragment(note, None)
    assert "<h2>Section</h2>" in html
    assert "<ul><li>" in html
    assert "<span style='font-weight:bold'>\U0001f600</span>" in html
    assert "<span style='font-style:italic'>one</span>" in html
    assert "text-decoration:underline line-through'>two</span>" in html
    with pytest.raises(ValueError, match="inline plain-text"):
        encode_note_edit(compressed, "Title", "Unintended plain-text replacement")
    replaced = encode_note_edit(compressed, "Title", RichNoteBody(paragraphs=()))
    edited = validate_rich_document(base64.b64decode(replaced.body)).document.note
    assert edited.note_text == "Title\n"
    assert edited.substring[2].tombstone


def test_rich_note_cannot_encode_line_break_in_inline_run() -> None:
    """Paragraph boundaries are explicit rather than hidden in a text run."""
    body = RichNoteBody(
        paragraphs=(NoteParagraph(runs=(NoteTextRun(text="One\nTwo"),)),)
    )
    with pytest.raises(ValueError, match="single line"):
        encode_new_note("Title", body)


def test_rich_note_encodes_checked_items_and_web_links() -> None:
    """Use distinct checklist IDs and preserve link text and checked state."""
    body = RichNoteBody(
        paragraphs=(
            NoteParagraph(style="checklist", runs=(NoteTextRun(text="Open item"),)),
            NoteParagraph(
                style="checklist",
                indent=1,
                checked=True,
                runs=(NoteTextRun(text="Reference", link="https://example.test/path"),),
            ),
            NoteParagraph(
                style="numbered", indent=1, runs=(NoteTextRun(text="Follow up"),)
            ),
        )
    )
    original = encode_new_note("Title", body)
    document = validate_rich_document(base64.b64decode(original.body)).document.note
    first, second = [
        r.paragraph_style.checklist
        for r in document.attribute_run
        if r.paragraph_style.HasField("checklist")
    ][::2]
    assert first.done == 0
    assert second.done == 1
    assert len(first.uuid) == 16
    assert len(second.uuid) == 16
    assert first.uuid != second.uuid
    assert document.attribute_run[4].paragraph_style.indent_amount == 1
    html = render_note_fragment(document, None)
    assert html.count('type="checkbox"') == 2
    assert html.count("disabled checked") == 1
    assert 'href="https://example.test/path"' in html
    assert "Reference</a>" in html
    changed = encode_note_edit(base64.b64decode(original.body), "Title", body)
    assert (
        validate_rich_document(base64.b64decode(changed.body)).document.note.note_text
        == document.note_text
    )


def _two_item_checklist() -> bytes:
    return base64.b64decode(
        encode_new_note(
            "Title",
            RichNoteBody(
                paragraphs=(
                    NoteParagraph(
                        style="checklist",
                        runs=(
                            NoteTextRun(text="First \U0001f600", bold=True),
                            NoteTextRun(text=" item"),
                        ),
                    ),
                    NoteParagraph(
                        style="checklist",
                        checked=True,
                        runs=(NoteTextRun(text="Second item"),),
                    ),
                )
            ),
        ).body
    )


def test_checklist_toggle_preserves_character_and_item_ids() -> None:
    """A targeted toggle changes style timestamps, not text or item identities."""
    compressed = _two_item_checklist()
    before = validate_rich_document(compressed).document.note
    changed = encode_checklist_state(compressed, 1, checked=True)
    assert changed is not None
    after = validate_rich_document(base64.b64decode(changed)).document.note
    assert after.note_text == before.note_text
    old_uuid = before.timestamp.clock[0].replica_uuid
    visible = after.substring[1:-1]
    assert all(
        after.timestamp.clock[s.char_id.replica_id - 1].replica_uuid == old_uuid
        and not s.tombstone
        for s in visible
    )
    assert [s.char_id.clock for s in visible] == [
        0,
        len("Title\n\n"),
        len("Title\n\nFirst  item\n") + 2,
    ]
    assert (
        after.timestamp.clock[visible[1].timestamp.replica_id - 1].replica_uuid
        != old_uuid
    )
    for index, (old, new) in enumerate(
        zip(before.attribute_run, after.attribute_run, strict=True)
    ):
        if index in {2, 3, 4}:
            assert (
                old.paragraph_style.checklist.uuid == new.paragraph_style.checklist.uuid
            )
            assert not old.paragraph_style.checklist.done
            assert new.paragraph_style.checklist.done
        else:
            assert old == new
    assert encode_checklist_state(base64.b64decode(changed), 1, checked=True) is None
    assert (
        encode_checklist_state(base64.b64decode(changed), 1, checked=False) is not None
    )


@pytest.mark.parametrize("item", [0, 3, True])
def test_checklist_toggle_requires_valid_item(item: int) -> None:
    """Refuse nonexistent or invalid checklist item numbers."""
    body = _two_item_checklist()
    with pytest.raises(ValueError, match="item"):
        encode_checklist_state(body, item, checked=True)


@pytest.mark.parametrize("changed", [True, False])
def test_checklist_service_keeps_revision_and_text(
    notes: NotesService, session: MagicMock, changed: bool
) -> None:
    """Write only when the selected item state actually needs to change."""
    record = _existing_note()
    record["fields"]["TextDataEncrypted"]["value"] = base64.b64encode(
        _two_item_checklist()
    ).decode("ascii")

    def post(url: str, **kwargs: Any) -> MagicMock:
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({"records": [record]})
        sent = kwargs["json"]["operations"][0]["record"]
        assert sent["recordChangeTag"] == "old-revision"
        assert (
            sent["fields"]["SnippetEncrypted"]["value"]
            == record["fields"]["SnippetEncrypted"]["value"]
        )
        return _response({"records": [_ack(sent)]})

    session.post.side_effect = post
    result = notes.set_checklist_item(
        NOTE_ID, item=1, checked=changed, expected_change_tag="old-revision"
    )
    assert result.record_change_tag == ("saved-revision" if changed else "old-revision")
    assert session.post.call_count == (2 if changed else 1)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://",
        "https://user@example.test",
        "https://user:password@example.test",
        "https://example.test:abc",
        "https://example.test:0",
        "https://example.test/a b",
        "https://example.test\\a",
        "",
        "https://[broken",
    ],
)
def test_rich_note_rejects_unverified_web_links(url: str) -> None:
    """Reject unsafe or ambiguous link destinations before encoding a write."""
    with pytest.raises(ValidationError, match="must be HTTP"):
        NoteTextRun(text="Link", link=url)


@pytest.mark.parametrize(
    "options", [{"indent": 1}, {"checked": True}, {"style": "checklist"}]
)
def test_rich_note_rejects_invalid_list_options(options: dict[str, Any]) -> None:
    """Do not silently discard checked state or indentation."""
    with pytest.raises(ValidationError):
        NoteParagraph(runs=(), **options)


@pytest.mark.parametrize(
    "shape",
    [
        "attachment",
        "bad-weight",
        "bad-alignment",
        "unsafe-link",
        "bad-checklist",
        "bad-indent",
    ],
)
def test_rich_note_does_not_overwrite_unsupported_formatting(shape: str) -> None:
    """An explicit rich update still rejects unknown styles and attachments."""
    compressed = base64.b64decode(encode_new_note("Title", "Body").body)
    proto = validate_rich_document(compressed)
    run = proto.document.note.attribute_run[-1]
    if shape == "attachment":
        run.attachment_info.attachment_identifier = "synthetic-attachment"
    elif shape == "bad-weight":
        run.font_weight = 99
    elif shape == "bad-alignment":
        run.paragraph_style.alignment = notes_pb2.Alignment.ALIGNMENT_RIGHT
    elif shape == "unsafe-link":
        run.link = "javascript:alert(1)"
    elif shape == "bad-checklist":
        run.paragraph_style.style_type = notes_pb2.StyleType.STYLE_TYPE_CHECKLIST_ITEM
        run.paragraph_style.checklist.uuid = b"short"
    else:
        run.paragraph_style.indent_amount = 1
    raw = zlib.compress(proto.SerializeToString())
    body = RichNoteBody(paragraphs=())
    with pytest.raises(ValueError, match="attachment-free"):
        encode_note_edit(raw, "Title", body)


def test_note_edit_preserves_browser_split_ranges_and_tombstones() -> None:
    """Use a synthetic browser-shaped document, independent of the encoder."""
    proto = notes_pb2.NoteStoreProto(serialization_version=0)
    proto.document.serialization_version = 0
    proto.document.version = 0
    note = proto.document.note
    note.note_text = "Synthetic test note\n\nSecond line \U0001f600\n"
    for index, length in enumerate((20, 1, 12, 2, 1)):
        run = note.attribute_run.add(length=length)
        run.paragraph_style.style_type = (
            notes_pb2.StyleType.STYLE_TYPE_TITLE
            if index == 0
            else notes_pb2.StyleType.STYLE_TYPE_BODY
        )
        run.paragraph_style.alignment = notes_pb2.Alignment.ALIGNMENT_NATURAL
    for index, (replica, clock, style_clock, length) in enumerate((
        (0, 0, 0, 0),
        (1, 0, 0, 19),
        (1, 20, 0, 2),
        (1, 32, 0, 14),
        (1, 22, 8, 10),  # Retired first body, still part of the history.
        (1, 19, 2, 1),
        (0, 0xFFFF_FFFF, 0xFFFF_FFFF, 0),
    )):
        span = note.substring.add(length=length)
        span.char_id.replica_id, span.char_id.clock = replica, clock
        span.timestamp.replica_id, span.timestamp.clock = replica, style_clock
        if index < 6:
            span.child.append(index + 1)
        if index == 4:
            span.tombstone = True
    browser_uuid = UUID(int=2)
    vector = note.timestamp.clock.add(replica_uuid=browser_uuid.bytes)
    vector.replica_clock.add(clock=46)
    vector.replica_clock.add(clock=9)

    # Force the new replica to sort before the existing one. The retired
    # range's old timestamp must still refer to the browser's replica.
    with patch("pyicloud.services.notes.encoding.uuid4", return_value=UUID(int=1)):
        encoded = encode_note_edit(
            zlib.compress(proto.SerializeToString()), "Edited title", "Replacement"
        )
    body = base64.b64decode(encoded.body)
    edited = validate_plain_document(body).document.note
    assert edited.note_text == "Edited title\n\nReplacement\n"
    assert edited.timestamp.clock[1] == vector
    assert edited.substring[1].char_id.clock == 46
    assert [span.char_id.clock for span in edited.substring[2:-1]] == [
        0,
        20,
        32,
        22,
        19,
    ]
    assert all(span.tombstone for span in edited.substring[2:-1])
    assert all(span.char_id.replica_id == 2 for span in edited.substring[2:-1])
    assert edited.substring[5].timestamp == notes_pb2.TextIdentifier(
        replica_id=2, clock=8
    )


@pytest.mark.parametrize("shape", ["rich", "cycle", "bad-clock", "unknown", "trailing"])
def test_note_edit_rejects_unsupported_document(shape: str) -> None:
    """Malformed, newer, and rich documents cannot be overwritten."""
    original = base64.b64decode(encode_new_note("Title", "First line").body)
    proto = validate_plain_document(original)
    note = proto.document.note
    if shape == "rich":
        note.attribute_run[
            1
        ].paragraph_style.style_type = notes_pb2.StyleType.STYLE_TYPE_CHECKLIST_ITEM
    elif shape == "cycle":
        note.substring[1].child[0] = 0
    elif shape == "bad-clock":
        note.substring[1].timestamp.clock = 2
    raw = proto.SerializeToString() + (b"\xa0\x06\x01" if shape == "unknown" else b"")
    compressed = zlib.compress(raw) + (b"trailing" if shape == "trailing" else b"")
    with pytest.raises(ValueError, match="Only supported inline plain-text"):
        encode_note_edit(compressed, "Title", "Replacement")


@pytest.mark.parametrize("operation", ["update", "trash", "restore", "move"])
def test_note_revision_guarded_updates(
    notes: NotesService, session: MagicMock, operation: str
) -> None:
    """Use the existing revision and preserve unchanged content and timestamps."""
    source = TRASH if operation == "restore" else FOLDER
    target = {"trash": TRASH, "move": DESTINATION}.get(operation, FOLDER)
    current = _existing_note(source)

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            requested = payload["records"][0]["recordName"]
            return _response({
                "records": [
                    _folder_record(recordName=requested)
                    if requested in {FOLDER, DESTINATION}
                    else current
                ]
            })
        assert kwargs["allow_redirects"] is False
        (op,) = payload["operations"]
        assert op["operationType"] == "update"
        record = op["record"]
        assert record["recordName"] == NOTE_ID
        assert record["recordChangeTag"] == "old-revision"
        assert record["shortGUID"] == "synthetic-guid"
        assert record["parent"] == {"recordName": target}
        fields = record["fields"]
        assert fields["Folder"]["value"]["recordName"] == target
        assert fields["Folders"] == {"value": [fields["Folder"]["value"]]}
        assert fields["CreationDate"]["value"] == 1_700_000_000_123
        if operation == "update":
            body = base64.b64decode(fields["TextDataEncrypted"]["value"])
            assert (
                validate_plain_document(body).document.note.note_text
                == "New title\n\nReplacement\n"
            )
            assert fields["FoldersModificationDate"]["value"] == 1_700_000_000_123
        else:
            assert (
                fields["TextDataEncrypted"]["value"]
                == current["fields"]["TextDataEncrypted"]["value"]
            )
            if operation in {"trash", "move"}:
                assert fields["ModificationDate"]["value"] == 1_700_000_000_123
            else:
                assert (
                    fields["ModificationDate"]["value"]
                    >= fields["FoldersModificationDate"]["value"]
                )
        saved = _ack(record, recordChangeTag="new-revision")
        return _response({"records": [saved]})

    session.post.side_effect = post
    if operation == "update":
        result = notes.update_note(
            NOTE_ID,
            title="New title",
            text="Replacement",
            expected_change_tag="old-revision",
        )
    elif operation == "trash":
        result = notes.trash_note(NOTE_ID, expected_change_tag="old-revision")
    elif operation == "restore":
        result = notes.restore_note(
            NOTE_ID, folder_id=FOLDER, expected_change_tag="old-revision"
        )
    else:
        result = notes.move_note(
            NOTE_ID, folder_id=DESTINATION, expected_change_tag="old-revision"
        )
    assert result.id == NOTE_ID
    assert result.record_change_tag == "new-revision"
    assert session.post.call_count == (3 if operation in {"restore", "move"} else 2)


def test_stale_note_revision_stops_before_write(
    notes: NotesService, session: MagicMock
) -> None:
    """A stale caller snapshot must never be replaced by the latest tag."""
    session.post.return_value = _response({"records": [_existing_note()]})
    with pytest.raises(NoteRevisionConflict, match="revision changed"):
        notes.trash_note(NOTE_ID, expected_change_tag="stale-revision")
    session.post.assert_called_once()


def test_ambiguous_note_lookup_stops_before_write(
    notes: NotesService, session: MagicMock
) -> None:
    """Do not choose one of multiple matching snapshots for an update."""
    record = _existing_note()
    session.post.return_value = _response({"records": [record, record]})
    with pytest.raises(NotesError, match="Could not verify one note record"):
        notes.trash_note(NOTE_ID, expected_change_tag="old-revision")
    session.post.assert_called_once()


@pytest.mark.parametrize(
    ("operation", "source", "message"),
    [
        ("update", TRASH, "Restore the note"),
        ("trash", TRASH, "already in Recently Deleted"),
        ("restore", FOLDER, "Only notes in Recently Deleted"),
    ],
)
def test_note_mutation_requires_expected_folder_state(
    notes: NotesService, session: MagicMock, operation: str, source: str, message: str
) -> None:
    """Do not edit Trash or move an already-active or already-trashed note."""
    session.post.return_value = _response({"records": [_existing_note(source)]})
    kwargs = {
        "update": {"title": "New title", "text": "Body"},
        "restore": {"folder_id": FOLDER},
    }
    mutate = getattr(notes, f"{operation}_note")
    options = kwargs.get(operation, {})
    with pytest.raises(NotesError, match=message):
        mutate(NOTE_ID, expected_change_tag="old-revision", **options)
    session.post.assert_called_once()


@pytest.mark.parametrize(
    "shape",
    [
        "locked",
        "deleted",
        "attachment",
        "other-zone",
        "wrong-parent",
        "wrong-folders",
        "missing-revision",
        "missing-short-guid",
    ],
)
def test_note_mutation_rejects_unsupported_record(
    notes: NotesService, session: MagicMock, shape: str
) -> None:
    """Unknown or potentially shared and attachment-bearing records are read-only."""
    record = _existing_note()
    if shape == "locked":
        record["recordType"] = "PasswordProtectedNote"
    elif shape == "deleted":
        record["fields"]["Deleted"] = {"type": "INT64", "value": 1}
    elif shape == "attachment":
        record["fields"]["Attachments"] = {"type": "REFERENCE_LIST", "value": []}
    elif shape == "other-zone":
        record["zoneID"] = {"zoneName": "Shared"}
    elif shape == "wrong-parent":
        record["parent"] = {"recordName": "AnotherFolder"}
    elif shape == "wrong-folders":
        record["fields"]["Folders"]["value"] = []
    elif shape == "missing-revision":
        del record["recordChangeTag"]
    else:
        del record["shortGUID"]
    session.post.return_value = _response({"records": [record]})
    with pytest.raises(NotesError):
        notes.trash_note(NOTE_ID, expected_change_tag="old-revision")
    session.post.assert_called_once()


def test_trash_folder_note_is_read_as_deleted(
    notes: NotesService, session: MagicMock
) -> None:
    """Folder membership alone identifies a note in Recently Deleted."""
    session.post.side_effect = [
        _response({"records": [_existing_note(TRASH)]}),
        _response({"records": []}),
    ]
    result = notes.get(NOTE_ID)
    assert result.is_deleted is True
    assert result.folder_id == TRASH
    assert result.record_change_tag == "old-revision"


@pytest.mark.parametrize(
    "case", ["saved", "no-confirm", "active", "stale", "wrong-ack"]
)
def test_permanent_note_delete(
    notes: NotesService, session: MagicMock, case: str
) -> None:
    """Only a confirmed, revision-guarded Trash note can be marked deleted."""
    current = _existing_note(FOLDER if case == "active" else TRASH)
    writes = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({"records": [current]})
        (op,) = payload["operations"]
        sent = op["record"]
        writes.append(sent)
        assert op["operationType"] == "update"
        assert sent["recordChangeTag"] == "old-revision"
        assert sent["fields"]["Deleted"] == {"value": 1}
        assert (
            sent["fields"]["TextDataEncrypted"]["value"]
            == current["fields"]["TextDataEncrypted"]["value"]
        )
        ack = _ack(sent)
        ack["fields"]["Deleted"] = {"type": "INT64", "value": int(case != "wrong-ack")}
        return _response({"records": [ack]})

    session.post.side_effect = post
    error = {
        "no-confirm": ValueError,
        "active": NotesError,
        "stale": NoteRevisionConflict,
        "wrong-ack": NotesWriteUncertain,
    }.get(case)
    kwargs = {"expected_change_tag": "stale" if case == "stale" else "old-revision"}
    if error is not None:
        with pytest.raises(error):
            notes.delete_note_forever(NOTE_ID, confirm=case != "no-confirm", **kwargs)
    else:
        assert notes.delete_note_forever(NOTE_ID, confirm=True, **kwargs).id == NOTE_ID
    assert len(writes) == int(case in {"saved", "wrong-ack"})


@pytest.mark.parametrize(
    "case",
    [
        "tag",
        "tag-missing",
        "tag-bad-fields",
        "tag-parent-uncertain",
        "tag-child-uncertain",
        "tag-child-rejected",
        "table",
        "table-no-confirm",
        "table-unlinked",
        "table-stale",
    ],
)
def test_remove_note_objects(
    notes: NotesService, session: MagicMock, case: str
) -> None:
    """Unlink one object without changing surviving links or deleting shared tags."""
    is_tag = case.startswith("tag")
    target = InlineObject("target", TAG_UTI if is_tag else TABLE_UTI)
    keep = InlineObject("keep", TAG_UTI)
    current = _existing_note()
    body = base64.b64decode(current["fields"]["TextDataEncrypted"]["value"])
    for obj in (target, keep):
        body = base64.b64decode(append_inline_note_object(body, obj))
    current["fields"]["TextDataEncrypted"]["value"] = base64.b64encode(body).decode()

    def child(obj: InlineObject, token: bytes) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "Note": {"type": "REFERENCE", "value": {"recordName": NOTE_ID}},
            "CreationDate": {"type": "TIMESTAMP", "value": 1_700_000_000_123},
            "MinimumSupportedNotesVersion": {"type": "INT64", "value": 7},
        }
        for key, value in {
            "UTIEncrypted": obj.uti.encode(),
            "TokenContentIdentifierEncrypted": token,
            "AltTextEncrypted": b"#Test",
        }.items():
            fields[key] = {
                "type": "ENCRYPTED_BYTES",
                "value": base64.b64encode(value).decode(),
            }
        if obj.uti == TABLE_UTI:
            fields["MergeableDataEncrypted"] = {
                "type": "ENCRYPTED_BYTES",
                "value": encode_new_table([["Cell"]]),
            }
        if case == "tag-bad-fields":
            del fields["AltTextEncrypted"]
        return {
            "recordName": obj.id,
            "recordChangeTag": "child-old",
            "recordType": "InlineAttachment" if obj.uti == TAG_UTI else "Attachment",
            "parent": {"recordName": NOTE_ID},
            "fields": fields,
        }

    records = {
        NOTE_ID: current,
        target.id: child(target, b"TEST"),
        keep.id: child(keep, b"KEEP"),
    }
    writes = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({
                "records": [records[ref["recordName"]] for ref in payload["records"]]
            })
        (op,) = payload["operations"]
        sent = op["record"]
        writes.append(sent["recordName"])
        assert op["operationType"] == "update"
        ack = _ack(sent, recordType=sent["recordType"], parent=sent["parent"])
        if sent["recordName"] == NOTE_ID:
            encoded = base64.b64decode(sent["fields"]["TextDataEncrypted"]["value"])
            assert inline_note_objects(encoded) == (keep,)
            assert (
                "First line"
                in notes_pb2.NoteStoreProto.FromString(
                    zlib.decompress(encoded)
                ).document.note.note_text
            )
            if case == "tag-parent-uncertain":
                return _response({"records": []})
        else:
            assert sent["recordChangeTag"] == "child-old"
            assert sent["fields"]["Deleted"] == {"value": 1}
            if case == "tag-child-uncertain":
                return _response({"records": []})
            if case == "tag-child-rejected":
                return _response({
                    "records": [
                        {"recordName": target.id, "serverErrorCode": "CONFLICT"}
                    ]
                })
        return _response({"records": [ack]})

    session.post.side_effect = post

    def remove() -> NoteTagResult | NoteWriteResult:
        if is_tag:
            return notes.remove_note_tag(
                NOTE_ID,
                tag="missing" if case == "tag-missing" else "#test",
                expected_change_tag="old-revision",
            )
        return notes.remove_note_table(
            NOTE_ID,
            table_id="unlinked" if case == "table-unlinked" else target.id,
            expected_change_tag="old-revision",
            expected_table_change_tag="stale" if case == "table-stale" else "child-old",
            confirm=case != "table-no-confirm",
        )

    if case in {"tag", "table"}:
        assert remove().id == NOTE_ID
    elif "uncertain" in case or case == "tag-child-rejected":
        with pytest.raises(NotesLinkedWriteIncomplete):
            remove()
    else:
        with pytest.raises((NotesError, ValueError)):
            remove()
    if case in {"tag", "tag-child-uncertain", "tag-child-rejected"}:
        assert writes == [NOTE_ID, target.id]
    elif case in {"table", "tag-parent-uncertain"}:
        assert writes == [NOTE_ID]
    else:
        assert not writes


@pytest.mark.parametrize("include_newline", [False, True])
def test_remove_tag_preserves_paragraph_break(include_newline: bool) -> None:
    """Remove only the marker even when its run includes the paragraph newline."""
    before = "Before \U0001f600"
    encoded = encode_new_note("Title", f"{before}X\nAfter")
    proto = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(base64.b64decode(encoded.body))
    )
    note = proto.document.note
    note.note_text = note.note_text.replace("X", "\ufffc")
    prefix = notes_pb2.AttributeRun()
    prefix.CopyFrom(note.attribute_run[2])
    prefix.length = len(before.encode("utf-16-le")) // 2
    marker = notes_pb2.AttributeRun()
    marker.CopyFrom(note.attribute_run[2])
    marker.length = 1 + int(include_newline)
    marker.attachment_info.attachment_identifier = "target-tag"
    marker.attachment_info.type_uti = TAG_UTI
    note.attribute_run[2].CopyFrom(marker)
    note.attribute_run.insert(2, prefix)
    if not include_newline:
        newline = notes_pb2.AttributeRun()
        newline.CopyFrom(prefix)
        newline.length = 1
        note.attribute_run.insert(4, newline)
    keep = InlineObject("keep-tag", TAG_UTI)
    compressed = base64.b64decode(
        append_inline_note_object(zlib.compress(proto.SerializeToString()), keep)
    )
    original = notes_pb2.NoteStoreProto.FromString(zlib.decompress(compressed))
    result = remove_inline_note_object(compressed, InlineObject("target-tag", TAG_UTI))
    removed = base64.b64decode(result.body)
    assert inline_note_objects(removed) == (keep,)
    edited = notes_pb2.NoteStoreProto.FromString(zlib.decompress(removed)).document.note
    assert edited.note_text == original.document.note.note_text.replace("\ufffc", "", 1)
    assert f"{before}\nAfter" in edited.note_text
    assert sum(span.length for span in edited.substring if span.tombstone) == 1
    tail_start = 4 if include_newline else 5
    assert edited.attribute_run[4:] == original.document.note.attribute_run[tail_start:]
    if include_newline:
        expected = notes_pb2.AttributeRun()
        expected.CopyFrom(marker)
        expected.length = 1
        expected.ClearField("attachment_info")
        assert edited.attribute_run[3] == expected


def _response(payload: Any, *, status: int = 200) -> MagicMock:
    response = MagicMock(spec=Response)
    response.status_code = status
    response.json.return_value = payload
    return response


def _folder_record(**changes: Any) -> dict[str, Any]:
    return {"recordName": FOLDER, "recordType": "Folder", **changes}


def _ack(sent: dict[str, Any], **changes: Any) -> dict[str, Any]:
    fields = {}
    for name, wrapper in sent["fields"].items():
        if "value" not in wrapper:
            continue
        value = wrapper["value"]
        kind = {
            int: "TIMESTAMP",
            str: "ENCRYPTED_BYTES",
            dict: "REFERENCE",
            list: "REFERENCE_LIST",
        }[type(value)]
        fields[name] = {"type": kind, "value": value}
    return {
        "recordName": sent["recordName"],
        "recordType": "Note",
        "recordChangeTag": "saved-revision",
        "zoneID": ZONE,
        "fields": fields,
        **changes,
    }


@pytest.fixture(autouse=True)
def _mock_mutation_dispatch() -> Iterator[None]:
    """Keep payload tests offline; real one-shot dispatch has separate regressions."""

    def prepare(_http: Any, url: str, payload: Any, headers: Any) -> Any:
        return Request("POST", url, json=payload, headers=headers).prepare(), {}

    def send(
        http: Any, _adapter: HTTPAdapter, request: PreparedRequest, _settings: Any
    ) -> Any:
        return http._session.post(
            request.url,
            json=json.loads(request.body or b"{}"),
            timeout=http._timeout,
            headers=dict(request.headers),
            allow_redirects=False,
        )

    with (
        patch.object(
            _CloudKitHTTP,
            "_prepare_mutation_request",
            autospec=True,
            side_effect=prepare,
        ),
        patch.object(
            _CloudKitHTTP, "_send_mutation_once", autospec=True, side_effect=send
        ),
    ):
        yield


@pytest.fixture(name="session")
def _session() -> MagicMock:
    session = MagicMock(spec=PyiCloudSession)
    session.get_adapter.return_value = HTTPAdapter()
    return session


@pytest.fixture(name="notes")
def _notes(session: MagicMock) -> Iterator[NotesService]:
    notes = NotesService("https://example.test", session, {})
    # Payload tests isolate source ancestry, covered separately with real lookups.
    with patch.object(notes, "_verify_note_source_folder"):
        yield notes


@pytest.mark.parametrize(
    "case", ["private", "shared-source", "shared-ancestor", "missing", "trash"]
)
def test_existing_note_checks_source_privacy(session: MagicMock, case: str) -> None:
    """Verify source ancestry before writing, even for notes without a share flag."""
    notes = NotesService("https://example.test", session, {})
    source = TRASH if case == "trash" else FOLDER
    folder = _folder_record(recordName=source)
    ancestor = _folder_record(recordName="ancestor")
    if case == "shared-source":
        folder["share"] = {"recordName": "share"}
    if case == "shared-ancestor":
        folder["parent"] = {"recordName": "ancestor"}
        folder["fields"] = {
            "ParentFolder": {"type": "REFERENCE", "value": {"recordName": "ancestor"}}
        }
        ancestor["share"] = {"recordName": "share"}
    replies = {
        NOTE_ID: [_existing_note(source)],
        source: [] if case == "missing" else [folder],
        "ancestor": [ancestor],
    }
    writes = []

    def post(url: str, **kwargs: Any) -> MagicMock:
        payload = kwargs["json"]
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({"records": replies[payload["records"][0]["recordName"]]})
        sent = payload["operations"][0]["record"]
        writes.append(sent)
        return _response({"records": [_ack(sent)]})

    session.post.side_effect = post
    if case == "trash":
        replies[FOLDER] = [_folder_record()]
        notes.restore_note(
            NOTE_ID, folder_id=FOLDER, expected_change_tag="old-revision"
        )
        assert len(writes) == 1
    elif case == "private":
        notes.trash_note(NOTE_ID, expected_change_tag="old-revision")
        assert len(writes) == 1
    else:
        with pytest.raises(NotesError, match="folder is not available"):
            notes.trash_note(NOTE_ID, expected_change_tag="old-revision")
        assert not writes


@pytest.mark.parametrize("policy", ["retry", "unavailable"])
def test_note_mutation_rejects_unverified_transport(
    notes: NotesService, session: MagicMock, policy: str
) -> None:
    """An adapter that might retry is rejected before the POST is sent."""
    session.post.return_value = _response({"records": [_folder_record()]})
    if policy == "retry":
        session.get_adapter.return_value = HTTPAdapter(max_retries=2)
    else:
        session.get_adapter.side_effect = InvalidSchema
    with pytest.raises(NotesWriteUncertain):
        notes.create_note("Title", folder_id=FOLDER)
    assert session.post.call_count == 1


def test_new_note_rejects_oversized_rich_document() -> None:
    """Link metadata is bounded even when the visible text is small."""
    link = "https://example.test/" + "a" * 3900
    body = RichNoteBody(
        paragraphs=(NoteParagraph(runs=(NoteTextRun(text="a", link=link),) * 600),)
    )
    with pytest.raises(ValueError, match="inline size limit"):
        encode_new_note("Title", body)


def test_create_note_matches_captured_write_shape(
    notes: NotesService, session: MagicMock
) -> None:
    """Send the Notes-specific fields, not the shared typed read wrappers."""
    folder_ref = {"recordName": FOLDER, "action": "VALIDATE", "zoneID": ZONE}

    def post(url: str, **kwargs: Any) -> MagicMock:
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({"records": [_folder_record()]})
        assert url.split("?")[0].endswith("/records/modify")
        assert kwargs["allow_redirects"] is False
        payload = kwargs["json"]
        assert payload["zoneID"] == ZONE
        (operation,) = payload["operations"]
        assert operation["operationType"] == "create"
        record = operation["record"]
        assert record["recordType"] == "Note"
        assert record["parent"] == {"recordName": FOLDER}
        assert record["createShortGUID"] is True
        assert "recordChangeTag" not in record
        assert UUID(record["recordName"]).version == 4
        fields = record["fields"]
        assert fields["Folder"] == {"value": folder_ref}
        assert fields["Folders"] == {"value": [folder_ref]}
        assert fields["CreationDate"] == fields["ModificationDate"]
        assert fields["CreationDate"]["value"] > 1_000_000_000_000
        assert fields["FirstAttachmentThumbnail"] == {}
        assert fields["FirstAttachmentUTIEncrypted"] == {}
        assert fields["TextDataAsset"] == {}
        assert base64.b64decode(fields["TitleEncrypted"]["value"]) == b"Test note"
        body = BodyDecoder().decode(fields["TextDataEncrypted"]["value"])
        assert body is not None
        assert body.text == "Test note\n\nA line \U0001f600\n"
        return _response({"records": [_ack(record)]})

    session.post.side_effect = post
    result = notes.create_note("Test note", "A line \U0001f600", folder_id=FOLDER)
    assert session.post.call_count == 2
    record_id = session.post.call_args.kwargs["json"]["operations"][0]["record"][
        "recordName"
    ]
    assert result.id == record_id
    assert result.record_change_tag == "saved-revision"


@pytest.mark.parametrize("body", ["", "A line \U0001f600\nAnother"])
def test_new_note_has_complete_topotext_state(body: str) -> None:
    """The serialized note keeps the observed sentinel and replica structure."""
    encoded = encode_new_note("Title", body)
    proto = notes_pb2.NoteStoreProto.FromString(
        zlib.decompress(base64.b64decode(encoded.body))
    )
    note = proto.document.note
    units = len(note.note_text.encode("utf-16-le")) // 2
    assert proto.HasField("serialization_version")
    assert proto.document.HasField("serialization_version")
    assert proto.document.HasField("version")
    assert [s.length for s in note.substring] == [0, units, 0]
    assert [s.char_id.replica_id for s in note.substring] == [0, 1, 0]
    assert [list(s.child) for s in note.substring] == [[1], [2], []]
    assert note.substring[-1].char_id.clock == 0xFFFF_FFFF
    assert [c.clock for c in note.timestamp.clock[0].replica_clock] == [units, 1]
    assert len(note.timestamp.clock[0].replica_uuid) == 16
    assert sum(run.length for run in note.attribute_run) == units
    assert (
        note.attribute_run[0].paragraph_style.style_type
        == notes_pb2.StyleType.STYLE_TYPE_TITLE
    )
    assert all(
        run.paragraph_style.style_type == notes_pb2.StyleType.STYLE_TYPE_BODY
        for run in note.attribute_run[1:]
    )
    expected_snippet = (
        b"A line \xf0\x9f\x98\x80 Another" if body else b"No additional text"
    )
    assert base64.b64decode(encoded.snippet) == expected_snippet


@pytest.mark.parametrize(
    ("title", "body", "folder"),
    [
        ("", "", FOLDER),
        ("Two\nlines", "", FOLDER),
        ("Title", "", ""),
        ("Title", "\x00", FOLDER),
        ("Title", "\ufffc", FOLDER),
        ("Title", "\ud800", FOLDER),
        ("Title", "x" * (64 * 1024), FOLDER),
    ],
)
def test_invalid_note_input_never_contacts_cloudkit(
    notes: NotesService, session: MagicMock, title: str, body: str, folder: str
) -> None:
    """Reject unsupported input before any request."""
    with pytest.raises(ValueError):
        notes.create_note(title, body, folder_id=folder)
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "records",
    [
        [],
        [_folder_record(), _folder_record()],
        [_folder_record(recordName="Folder/wrong")],
        [_folder_record(recordType="Note")],
        [_folder_record(deleted=True)],
        [_folder_record(fields={"Deleted": {"type": "INT64", "value": 1}})],
        [_folder_record(zoneID={"zoneName": "Other"})],
        [{"recordName": FOLDER, "serverErrorCode": "NOT_FOUND"}],
    ],
)
def test_unverified_folder_cannot_receive_note(
    notes: NotesService, session: MagicMock, records: list[dict[str, Any]]
) -> None:
    """A successful write requires a fresh lookup of the exact active folder."""
    session.post.return_value = _response({"records": records})
    with pytest.raises(NotesError, match="folder is not available"):
        notes.create_note("Title", folder_id=FOLDER)
    assert session.post.call_count == 1


@pytest.mark.parametrize(
    "kind",
    [
        "timeout",
        "redirect",
        "empty",
        "ambiguous",
        "wrong-id",
        "wrong-type",
        "wrong-zone",
        "wrong-folder",
        "wrong-parent",
        "wrong-body",
        "no-revision",
        "malformed",
        "rejected",
        "invalid-error",
    ],
)
def test_create_does_not_retry_or_claim_unverified_success(
    notes: NotesService, session: MagicMock, kind: str
) -> None:
    """Report known rejection separately from a possibly committed write."""

    def post(url: str, **kwargs: Any) -> MagicMock:
        if url.split("?")[0].endswith("/records/lookup"):
            return _response({"records": [_folder_record()]})
        sent = kwargs["json"]["operations"][0]["record"]
        if kind == "timeout":
            raise TimeoutError("private details")
        if kind == "redirect":
            return _response({}, status=307)
        record = _ack(sent)
        if kind == "empty":
            return _response({"records": []})
        if kind == "ambiguous":
            return _response({"records": [record, record]})
        if kind == "wrong-id":
            record["recordName"] = "unrelated"
        elif kind == "wrong-type":
            record["recordType"] = "Folder"
        elif kind == "wrong-zone":
            record["zoneID"] = {"zoneName": "Other"}
        elif kind == "wrong-folder":
            record["fields"] = {}
        elif kind == "wrong-parent":
            record["parent"] = {"recordName": DESTINATION}
        elif kind == "wrong-body":
            record["fields"]["TextDataEncrypted"]["value"] = "YQ=="
        elif kind == "no-revision":
            record.pop("recordChangeTag")
        elif kind == "malformed":
            record["fields"] = {"TitleEncrypted": {"type": "TIMESTAMP", "value": 1}}
        elif kind in {"rejected", "invalid-error"}:
            record["serverErrorCode"] = "CONFLICT" if kind == "rejected" else None
            record["reason"] = "private details"
        return _response({"records": [record]})

    session.post.side_effect = post
    error = NotesWriteRejected if kind == "rejected" else NotesWriteUncertain
    with pytest.raises(error) as caught:
        notes.create_note("Title", folder_id=FOLDER)
    assert isinstance(caught.value, (NotesWriteRejected, NotesWriteUncertain))
    assert "private details" not in str(caught.value)
    assert session.post.call_count == 2
    assert (
        caught.value.note_id
        == session.post.call_args.kwargs["json"]["operations"][0]["record"][
            "recordName"
        ]
    )
