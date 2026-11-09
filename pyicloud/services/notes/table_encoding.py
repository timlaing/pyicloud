"""Build and edit the observed Notes mergeable-data table structure."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4
import zlib

from google.protobuf.message import DecodeError

from .protobuf import notes_pb2 as pb

MAX_TABLE_AXIS = 32
_MAX_BYTES = 2 * 1024 * 1024
_MAX_TEXT_BYTES = 64 * 1024
_END = 0xFFFF_FFFF
_BAD_TABLE = "Could not verify the Notes table structure or text history"
_KEYS = (
    "identity",
    "crTableColumnDirection",
    "self",
    "crRows",
    "UUIDIndex",
    "crColumns",
    "cellColumns",
)
_TYPES = (
    "com.apple.CRDT.NSNumber",
    "com.apple.CRDT.NSString",
    "com.apple.CRDT.NSUUID",
    "com.apple.notes.CRTable",
    "com.apple.notes.ICTable",
)


def _text_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _validate_cell_text(text: str) -> int:
    if any((ord(c) < 32 and c not in "\n\t") or c in "\x7f\ufffc" for c in text):
        raise ValueError(
            "Table cells cannot contain attachment markers or control characters"
        )
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Table cell text must be valid Unicode") from None


def _span(replica: int, clock: int, length: int) -> pb.TextSubstring:
    char_id = pb.TextIdentifier(replica_id=replica, clock=clock)
    timestamp = pb.TextIdentifier(replica_id=replica, clock=0) if replica else char_id
    return pb.TextSubstring(char_id=char_id, timestamp=timestamp, length=length)


def _write_spans(note: pb.Note, spans: list[pb.TextSubstring]) -> None:
    all_spans = [_span(0, 0, 0), *spans, _span(0, _END, 0)]
    for index, span in enumerate(all_spans[:-1], 1):
        del span.child[:]
        span.child.append(index)
    del note.substring[:]
    note.substring.extend(all_spans)


def _set_text(note: pb.Note, text: str) -> None:
    note.note_text = text
    del note.attribute_run[:]
    if text:
        note.attribute_run.add(length=_text_length(text))


def _append_dict(data: pb.Dictionary, key: int, value: int) -> None:
    element = data.element.add()
    element.key.object_index = key
    element.value.object_index = value
    element.timestamp.clock.add(replica_index=0, clock=1, subclock=0)


def _create_uuid_row(data: pb.MergeableDataObjectData, uid: bytes) -> int:
    slot = len(data.mergeable_data_object_uuid_item)
    data.mergeable_data_object_uuid_item.append(uid)
    entries = data.mergeable_data_object_entry
    row_index = len(entries)
    mapping = entries.add().custom_map
    mapping.type = 2
    mapping.map_entry.add(key=4).value.unsigned_integer_value = slot
    return row_index


def _create_axis(data: pb.MergeableDataObjectData, count: int) -> tuple[int, list[int]]:
    entries = data.mergeable_data_object_entry
    axis_index = len(entries)
    axis = entries.add().ordered_set
    slots = []
    for i in range(count):
        order_uuid = uuid4().bytes
        slot = _create_uuid_row(data, uuid4().bytes)
        order = _create_uuid_row(data, order_uuid)
        slots.append(slot)
        axis.ordering.array.attachment.add(index=i, uuid=order_uuid)
        _append_dict(axis.ordering.contents, order, slot)
        _append_dict(axis.elements, order, order)
    markers = axis.ordering.array.contents
    markers.note_text = "\ufffc" * count
    for _ in range(count):
        markers.attribute_run.add(length=1)
    _write_spans(markers, [_span(1, 0, count)])
    return axis_index, slots


def _encode_table(proto: pb.MergableDataProto) -> str:
    if proto.ByteSize() > _MAX_BYTES:
        raise ValueError("The Notes table exceeds the inline size limit")
    return base64.b64encode(zlib.compress(proto.SerializeToString())).decode("ascii")


def encode_new_table(rows: Sequence[Sequence[str]]) -> str:
    """Encode a rectangular grid of plain-text Notes table cells."""
    if (
        not rows
        or not 1 <= len(rows) <= MAX_TABLE_AXIS
        or isinstance(rows, (str, bytes))
    ):
        raise ValueError("A Notes table needs 1 to 32 rows")
    width = len(rows[0])
    if not 1 <= width <= MAX_TABLE_AXIS or any(
        isinstance(row, (str, bytes)) or len(row) != width for row in rows
    ):
        raise ValueError("A Notes table needs 1 to 32 columns and equal row lengths")
    if sum(_validate_cell_text(text) for row in rows for text in row) > _MAX_TEXT_BYTES:
        raise ValueError("Notes table text must not exceed 64 KiB")
    proto = pb.MergableDataProto(serialization_version=0)
    wrapper = proto.mergable_data_object
    wrapper.serialization_version = 0
    wrapper.version = 0
    data = wrapper.mergeable_data_object_data
    actor = uuid4().bytes
    data.mergeable_data_object_key_item.extend(_KEYS)
    data.mergeable_data_object_type_item.extend(_TYPES)
    data.mergeable_data_object_uuid_item.append(actor)
    data.object_timestamp.clock.add(replica_index=0, clock=1, subclock=0)
    entries = data.mergeable_data_object_entry
    root = entries.add().custom_map
    root.type = 4
    root.map_entry.add(key=0).value.string_value = uuid4().hex
    root.map_entry.add(key=1).value.object_index = 1
    register = entries.add().register_latest
    register.timestamp.CopyFrom(pb.TextIdentifier(replica_id=0, clock=1))
    register.contents.object_index = 2
    direction = entries.add().custom_map
    direction.type = 1
    direction.map_entry.add(
        key=2
    ).value.string_value = "CRTableColumnDirectionLeftToRight"
    row_axis, row_slots = _create_axis(data, len(rows))
    col_axis, col_slots = _create_axis(data, width)
    root.map_entry.add(key=3).value.object_index = row_axis
    root.map_entry.add(key=5).value.object_index = col_axis
    root.map_entry.add(key=6).value.object_index = len(entries)
    columns = entries.add().dictionary
    clock = 0
    for col, col_slot in enumerate(col_slots):
        _append_dict(columns, col_slot, len(entries))
        column = entries.add().dictionary
        for row, row_slot in enumerate(row_slots):
            _append_dict(column, row_slot, len(entries))
            cell = entries.add().note
            text = rows[row][col]
            length = _text_length(text)
            _set_text(cell, text)
            _write_spans(cell, [_span(1, clock, length)] if length else [])
            clock += length
    vector = data.text_timestamp.clock.add(replica_uuid=actor)
    vector.replica_clock.add(clock=clock)
    vector.replica_clock.add(clock=1)
    return _encode_table(proto)


@dataclass(frozen=True)
class _TableGraph:
    data: pb.MergeableDataObjectData

    def entry(self, index: int, field: str) -> pb.MergeableDataObjectRow:
        """Return an entry of the expected kind."""
        entries: Sequence[pb.MergeableDataObjectRow] = (
            self.data.mergeable_data_object_entry
        )
        if not 0 <= index < len(entries) or not entries[index].HasField(field):
            raise ValueError(_BAD_TABLE)
        return entries[index]

    def uuid(self, index: int) -> bytes:
        """Resolve a UUID map entry to its logical ID."""
        entry = self.entry(index, "custom_map").custom_map
        if self.type_name(entry.type) != _TYPES[2] or len(entry.map_entry) != 1:
            raise ValueError(_BAD_TABLE)
        item = entry.map_entry[0]
        ids = self.data.mergeable_data_object_uuid_item
        slot = item.value.unsigned_integer_value
        if (
            self.key_name(item.key) != "UUIDIndex"
            or not 0 < slot < len(ids)
            or len(ids[slot]) != 16
        ):
            raise ValueError(_BAD_TABLE)
        return bytes(ids[slot])

    def key_name(self, index: int) -> str:
        """Resolve a name in the table's key registry."""
        keys = self.data.mergeable_data_object_key_item
        return keys[index] if 0 <= index < len(keys) else ""

    def type_name(self, index: int) -> str:
        """Resolve a name in the table's type registry."""
        types = self.data.mergeable_data_object_type_item
        return types[index] if 0 <= index < len(types) else ""

    def references(self, dictionary: pb.Dictionary) -> dict[bytes, int]:
        """Resolve dictionary keys without dropping duplicate IDs."""
        refs = {
            self.uuid(item.key.object_index): item.value.object_index
            for item in dictionary.element
        }
        if len(refs) != len(dictionary.element):
            raise ValueError(_BAD_TABLE)
        return refs

    def axis(self, index: int) -> list[bytes]:
        """Resolve the ordered logical row or column IDs."""
        order = self.entry(index, "ordered_set").ordered_set.ordering
        refs = self.references(order.contents)
        if not 1 <= len(order.array.attachment) <= MAX_TABLE_AXIS:
            raise ValueError(_BAD_TABLE)
        slots = []
        for i, attachment in enumerate(order.array.attachment):
            if attachment.index != i or attachment.uuid not in refs:
                raise ValueError(_BAD_TABLE)
            slots.append(self.uuid(refs[attachment.uuid]))
        if len(set(slots)) != len(slots):
            raise ValueError(_BAD_TABLE)
        return slots

    def grid(self) -> list[list[pb.Note]]:
        """Follow the axis and cell dictionaries to produce a row-major grid."""
        roots = [
            row.custom_map
            for row in self.data.mergeable_data_object_entry
            if row.HasField("custom_map")
            and self.type_name(row.custom_map.type) == _TYPES[4]
        ]
        if len(roots) != 1:
            raise ValueError(_BAD_TABLE)
        refs = {
            self.key_name(item.key): item.value.object_index
            for item in roots[0].map_entry
        }
        if (
            len(refs) != len(roots[0].map_entry)
            or not {"crRows", "crColumns", "cellColumns"} <= refs.keys()
        ):
            raise ValueError(_BAD_TABLE)
        rows = self.axis(refs["crRows"])
        cols = self.axis(refs["crColumns"])
        columns = self.references(
            self.entry(refs["cellColumns"], "dictionary").dictionary
        )
        if set(columns) != set(cols):
            raise ValueError(_BAD_TABLE)
        grid: list[list[pb.Note]] = [[] for _ in rows]
        seen_cells: set[int] = set()
        for col in cols:
            column = self.references(self.entry(columns[col], "dictionary").dictionary)
            if set(column) != set(rows):
                raise ValueError(_BAD_TABLE)
            for row, cells in zip(rows, grid, strict=True):
                index = column[row]
                if index in seen_cells:
                    raise ValueError(_BAD_TABLE)
                seen_cells.add(index)
                cells.append(self.entry(index, "note").note)
        return grid


def _load_table(blob: bytes) -> pb.MergableDataProto:
    if len(blob) > _MAX_BYTES:
        raise ValueError(_BAD_TABLE)
    try:
        stream = zlib.decompressobj(wbits=47)
        raw = stream.decompress(blob, _MAX_BYTES + 1)
        if not stream.eof or stream.unused_data or len(raw) > _MAX_BYTES:
            raise ValueError(_BAD_TABLE)
        proto = pb.MergableDataProto()
        proto.ParseFromString(raw)
    except (zlib.error, DecodeError):
        raise ValueError(_BAD_TABLE) from None
    known = pb.MergableDataProto()
    known.CopyFrom(proto)
    known.DiscardUnknownFields()
    wrapper = proto.mergable_data_object
    if (
        known != proto
        or proto.serialization_version
        or wrapper.serialization_version
        or wrapper.version
    ):
        raise ValueError(_BAD_TABLE)
    return proto


def table_cells(blob: bytes) -> tuple[tuple[str, ...], ...]:
    """Return text in row-major order after verifying the object graph."""
    data = _load_table(blob).mergable_data_object.mergeable_data_object_data
    grid = _TableGraph(data).grid()
    return tuple(tuple(cell.note_text for cell in row) for row in grid)


def _replace_cell_history(
    cell: pb.Note, clocks: list[tuple[int, int]]
) -> list[pb.TextSubstring]:
    spans = cell.substring
    start = _span(0, 0, 0)
    start.child.append(1)
    if (
        len(spans) < 2
        or spans[0] != start
        or spans[-1] != _span(0, _END, 0)
        or any(list(span.child) != [i] for i, span in enumerate(spans[:-1], 1))
        or sum(span.length for span in spans if not span.tombstone)
        != _text_length(cell.note_text)
        or sum(run.length for run in cell.attribute_run) != _text_length(cell.note_text)
        or any(
            run != pb.AttributeRun(length=run.length) or run.length <= 0
            for run in cell.attribute_run
        )
    ):
        raise ValueError(_BAD_TABLE)
    kept = list(spans[1:-1])
    for span in kept:
        owner, style_owner = span.char_id.replica_id, span.timestamp.replica_id
        if (
            not 0 < owner <= len(clocks)
            or not 0 < style_owner <= len(clocks)
            or not span.length
            or span.char_id.clock + span.length > clocks[owner - 1][0]
            or span.timestamp.clock >= clocks[style_owner - 1][1]
        ):
            raise ValueError(_BAD_TABLE)
    return kept


def _new_table_actor(
    data: pb.MergeableDataObjectData,
    text_nodes: list[pb.Note],
    clocks: list[tuple[int, int]],
    length: int,
) -> tuple[int, int, int]:
    clock = max(value[0] for value in clocks)
    style = max(value[1] for value in clocks)
    if clock + length >= _END or style >= _END:
        raise ValueError(_BAD_TABLE)
    actor = uuid4().bytes
    actors = data.text_timestamp.clock
    ids = [item.replica_uuid for item in actors]
    if actor in ids:
        raise ValueError(_BAD_TABLE)
    slot = sum(uid < actor for uid in ids)
    replica = slot + 1
    for node in text_nodes:
        if node.HasField("timestamp") or any(
            run.HasField("timestamp") for run in node.attribute_run
        ):
            raise ValueError(_BAD_TABLE)
        for span in node.substring:
            for identifier in (span.char_id, span.timestamp):
                if identifier.replica_id > len(ids):
                    raise ValueError(_BAD_TABLE)
                if identifier.replica_id >= replica:
                    identifier.replica_id += 1
    vector = actors.add(replica_uuid=actor)
    vector.replica_clock.add(clock=clock + length)
    vector.replica_clock.add(clock=style + 1)
    actors.insert(slot, vector)
    del actors[-1]
    if actor not in data.mergeable_data_object_uuid_item:
        data.mergeable_data_object_uuid_item.append(actor)
    return replica, clock, style


def encode_table_cell_edit(blob: bytes, row: int, column: int, text: str) -> str | None:
    """Replace one plain-text cell, retaining its character history and other cells."""
    if _validate_cell_text(text) > _MAX_TEXT_BYTES:
        raise ValueError("Notes table text must not exceed 64 KiB")
    proto = _load_table(blob)
    data = proto.mergable_data_object.mergeable_data_object_data
    grid = _TableGraph(data).grid()
    if (
        not isinstance(row, int)
        or not isinstance(column, int)
        or isinstance(row, bool)
        or isinstance(column, bool)
        or not 1 <= row <= len(grid)
        or not 1 <= column <= len(grid[0])
    ):
        raise ValueError("Notes table row or column does not exist")
    cell = grid[row - 1][column - 1]
    if cell.note_text == text:
        return None
    actors = data.text_timestamp.clock
    ids = [actor.replica_uuid for actor in actors]
    if (
        not 0 < len(ids) < 256
        or ids != sorted(set(ids))
        or any(
            len(actor.replica_uuid) != 16
            or len(actor.replica_clock) != 2
            or any(c.subclock for c in actor.replica_clock)
            for actor in actors
        )
    ):
        raise ValueError(_BAD_TABLE)
    clocks = [(a.replica_clock[0].clock, a.replica_clock[1].clock) for a in actors]
    history = _replace_cell_history(cell, clocks)
    length = _text_length(text)
    nodes: list[pb.Note] = []
    for entry in data.mergeable_data_object_entry:
        if entry.HasField("note"):
            nodes.append(entry.note)
        elif entry.HasField("ordered_set"):
            nodes.append(entry.ordered_set.ordering.array.contents)
    replica, clock, style = _new_table_actor(data, nodes, clocks, length)
    for span in history:
        if not span.tombstone:
            span.tombstone = True
            span.timestamp.CopyFrom(pb.TextIdentifier(replica_id=replica, clock=style))
    if length:
        history.insert(0, _span(replica, clock, length))
    _write_spans(cell, history)
    _set_text(cell, text)
    return _encode_table(proto)
