"""Offline tests for Notes mutation payloads and acknowledgment handling."""

from __future__ import annotations

import base64
from typing import Any, Literal
from unittest.mock import MagicMock, patch
from uuid import UUID
import zlib

from pydantic import ValidationError
import pytest
from requests import Response

from pyicloud.services.notes import (
    NoteParagraph,
    NoteRevisionConflict,
    NotesLinkedWriteIncomplete,
    NotesService,
    NotesWriteRejected,
    NotesWriteUncertain,
    NoteTableWriteResult,
    NoteTextRun,
    RichNoteBody,
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
    replace_image_note_text,
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


@pytest.mark.parametrize("touch", ["saved", "changed-parent", "rejected", "uncertain"])
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
        assert caught.value.object_state == "confirmed"
        assert caught.value.note_state == (
            "not_submitted" if touch == "changed-parent" else touch
        )
        assert operations == (
            ["note", "create", "update"]
            if touch == "changed-parent"
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
    with pytest.raises(ValueError, match="table structure"):
        encode_table_cell_edit(zlib.compress(proto.SerializeToString()), 1, 1, "New")


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
    with pytest.raises(ValueError, match="inline objects"):
        append_inline_note_object(second, InlineObject("tag-1", TAG_UTI))


@pytest.mark.parametrize(
    "outcome",
    [
        "saved",
        "lookup-mismatch",
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
        assert (
            sent["recordName"] == NOTE_ID and sent["recordChangeTag"] == "old-revision"
        )
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
        assert (
            result.changed
            and result.tag_id == child_ids[0]
            and result.record_change_tag == "post-link-revision"
        )
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
        assert exc.value.note_id == NOTE_ID and exc.value.object_id == child_ids[0]
        assert exc.value.note_state == (
            "uncertain"
            if outcome.startswith("parent") or outcome == "lookup-mismatch"
            else "confirmed"
        )
        assert (
            exc.value.object_state
            == {
                "parent-uncertain": "not_submitted",
                "child-uncertain": "uncertain",
                "child-rejected": "rejected",
                "lookup-mismatch": "confirmed",
            }[outcome]
        )
    assert session.post.call_count == (
        2
        if outcome.startswith("parent")
        else 4
        if outcome in {"saved", "lookup-mismatch"}
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
        assert not result.changed and result.tag_id == "tag-1"
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
        assert (
            record["recordName"] == PIN_ID
            and record["recordType"] == "Note_UserSpecific"
        )
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
            assert op["operationType"] == "create" and "recordChangeTag" not in sent
            assert str(UUID(sent["recordName"])) == sent["recordName"]
        else:
            assert (
                op["operationType"] == "update" and sent["recordChangeTag"] == "old-tag"
            )
            assert sent["recordName"] == USER_FOLDER
        if target is not None:
            assert sent["fields"]["ParentFolder"] == {
                "value": {"recordName": target, "zoneID": ZONE, "action": "VALIDATE"}
            }
            assert sent["parent"] == {"recordName": target}
        elif creating:
            assert "ParentFolder" not in sent["fields"] and "parent" not in sent
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
    result = replace_image_note_text(
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


@pytest.mark.parametrize(
    "old,new",
    [("Title", "New"), ("\ufffc", "New"), ("old", "old"), ("old", "a\nb"), ("", "New")],
)
def test_image_edit_rejects_unsafe_replacements(old: str, new: str) -> None:
    """Do not replace titles, object markers, or ambiguous or invalid text."""
    with pytest.raises(ValueError):
        replace_image_note_text(
            zlib.compress(_image_document().SerializeToString()), old, new
        )


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
    with pytest.raises(ValidationError, match=error):
        NotesModifyOperation(
            operationType=operation,
            record=NotesWriteRecord(recordName=NOTE_ID, fields={}, recordChangeTag=tag),
        )


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
    with pytest.raises(ValueError, match="single line"):
        encode_new_note(
            "Title",
            RichNoteBody(
                paragraphs=(NoteParagraph(runs=(NoteTextRun(text="One\nTwo"),)),)
            ),
        )


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
    assert first.done == 0 and second.done == 1
    assert len(first.uuid) == len(second.uuid) == 16 and first.uuid != second.uuid
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
            assert (
                not old.paragraph_style.checklist.done
                and new.paragraph_style.checklist.done
            )
        else:
            assert old == new
    assert encode_checklist_state(base64.b64decode(changed), 1, checked=True) is None
    assert (
        encode_checklist_state(base64.b64decode(changed), 1, checked=False) is not None
    )


@pytest.mark.parametrize("item", [0, 3, True])
def test_checklist_toggle_requires_valid_item(item: int) -> None:
    """Refuse nonexistent or invalid checklist item numbers."""
    with pytest.raises(ValueError, match="item"):
        encode_checklist_state(_two_item_checklist(), item, checked=True)


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
    with pytest.raises(ValueError, match="attachment-free"):
        encode_note_edit(
            zlib.compress(proto.SerializeToString()),
            "Title",
            RichNoteBody(paragraphs=()),
        )


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
    with pytest.raises(NotesError, match=message):
        getattr(notes, f"{operation}_note")(
            NOTE_ID, expected_change_tag="old-revision", **kwargs.get(operation, {})
        )
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


@pytest.fixture(name="session")
def _session() -> MagicMock:
    return MagicMock(spec=PyiCloudSession)


@pytest.fixture(name="notes")
def _notes(session: MagicMock) -> NotesService:
    return NotesService("https://example.test", session, {})


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
