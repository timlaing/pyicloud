"""Build and edit the observed Notes mergeable-data table structure."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4
import zlib

from google.protobuf.message import DecodeError

from .protobuf import notes_pb2 as pb

MAX_TABLE_AXIS = 32
TableAxis = Literal["row", "column"]
TableResizeAction = Literal["insert", "delete"]
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


def _append_dict(
    data: pb.Dictionary, key: int, value: int, *, replica: int = 0, clock: int = 1
) -> None:
    element = data.element.add()
    element.key.object_index = key
    element.value.object_index = value
    element.timestamp.clock.add(replica_index=replica, clock=clock, subclock=0)


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

    def root_references(self) -> dict[str, int]:
        """Locate the axis and cell dictionaries from the unique table root."""
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
        return refs

    def grid(self) -> list[list[pb.Note]]:
        """Follow the axis and cell dictionaries to produce a row-major grid."""
        refs = self.root_references()
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
    """Allocate a fresh text replica and advance the table's vector clock."""
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
        _shift_text_replicas(node, replica, len(ids))
    vector = actors.add(replica_uuid=actor)
    vector.replica_clock.add(clock=clock + length)
    vector.replica_clock.add(clock=style + 1)
    actors.insert(slot, vector)
    del actors[-1]
    if actor not in data.mergeable_data_object_uuid_item:
        data.mergeable_data_object_uuid_item.append(actor)
    return replica, clock, style


def _shift_text_replicas(node: pb.Note, replica: int, actor_count: int) -> None:
    """Preserve existing references when a replica slot is inserted."""
    if node.HasField("timestamp") or any(
        run.HasField("timestamp") for run in node.attribute_run
    ):
        raise ValueError(_BAD_TABLE)
    identifiers = (
        identifier
        for span in node.substring
        for identifier in (span.char_id, span.timestamp)
    )
    for identifier in identifiers:
        if identifier.replica_id > actor_count:
            raise ValueError(_BAD_TABLE)
        if identifier.replica_id >= replica:
            identifier.replica_id += 1


def _table_text_clocks(data: pb.MergeableDataObjectData) -> list[tuple[int, int]]:
    """Return character and style clocks for verified text replicas."""
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
    return [(a.replica_clock[0].clock, a.replica_clock[1].clock) for a in actors]


def _table_text_nodes(data: pb.MergeableDataObjectData) -> list[pb.Note]:
    """Collect all text nodes whose replica indices may need shifting."""
    nodes: list[pb.Note] = []
    for entry in data.mergeable_data_object_entry:
        if entry.HasField("note"):
            nodes.append(entry.note)
        elif entry.HasField("ordered_set"):
            nodes.append(entry.ordered_set.ordering.array.contents)
    return nodes


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
    clocks = _table_text_clocks(data)
    history = _replace_cell_history(cell, clocks)
    length = _text_length(text)
    replica, clock, style = _new_table_actor(
        data, _table_text_nodes(data), clocks, length
    )
    for span in history:
        if not span.tombstone:
            span.tombstone = True
            span.timestamp.CopyFrom(pb.TextIdentifier(replica_id=replica, clock=style))
    if length:
        history.insert(0, _span(replica, clock, length))
    _write_spans(cell, history)
    _set_text(cell, text)
    return _encode_table(proto)


def _dictionary_links(dictionary: pb.Dictionary) -> list[pb.ObjectID]:
    return [link for item in dictionary.element for link in (item.key, item.value)]


def _entry_links(entry: pb.MergeableDataObjectRow) -> list[pb.ObjectID]:
    kinds = [descriptor.name for descriptor, _ in entry.ListFields()]
    if len(kinds) != 1:
        raise ValueError(_BAD_TABLE)
    kind = kinds[0]
    if kind == "custom_map":
        links = []
        for item in entry.custom_map.map_entry:
            if len(item.value.ListFields()) != 1:
                raise ValueError(_BAD_TABLE)
            if item.value.object_index:
                links.append(item.value)
        return links
    if kind == "register_latest":
        return [entry.register_latest.contents]
    if kind == "dictionary":
        return _dictionary_links(entry.dictionary)
    if kind == "ordered_set":
        axis = entry.ordered_set
        return _dictionary_links(axis.elements) + _dictionary_links(
            axis.ordering.contents
        )
    if kind == "note":
        return []
    raise ValueError(_BAD_TABLE)


def _compact_table(data: pb.MergeableDataObjectData, removed: set[int]) -> None:
    entries = data.mergeable_data_object_entry
    positions = {
        old: new
        for new, old in enumerate(i for i in range(len(entries)) if i not in removed)
    }
    for old in positions:
        for link in _entry_links(entries[old]):
            target = positions.get(link.object_index)
            if target is None or link.string_value or link.unsigned_integer_value:
                raise ValueError(_BAD_TABLE)
            link.object_index = target
    for old in sorted(removed, reverse=True):
        del entries[old]


def _join_object_clock(data: pb.MergeableDataObjectData) -> tuple[int, int]:
    """Place the just-added text replica in the object clock's UUID prefix."""
    clock = data.object_timestamp.clock
    slot = len(clock)
    ids = data.mergeable_data_object_uuid_item
    if (
        not 0 < slot < len(ids) - 1
        or [item.replica_index for item in clock] != list(range(slot))
        or any(item.subclock for item in clock)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(_BAD_TABLE)
    value = max(item.clock for item in clock) + 2
    if value >= _END:
        raise ValueError(_BAD_TABLE)
    actor = ids.pop()
    ids.insert(slot, actor)
    graph = _TableGraph(data)
    for entry in data.mergeable_data_object_entry:
        if (
            entry.HasField("custom_map")
            and graph.type_name(entry.custom_map.type) == _TYPES[2]
        ):
            for item in entry.custom_map.map_entry:
                old = item.value.unsigned_integer_value
                if (
                    not slot <= old < len(ids) - 1
                    or graph.key_name(item.key) != "UUIDIndex"
                ):
                    raise ValueError(_BAD_TABLE)
                item.value.unsigned_integer_value += 1
    clock.add(replica_index=slot, clock=value, subclock=0)
    return slot, value


def _edit_axis_markers(
    markers: pb.Note,
    history: list[pb.TextSubstring],
    position: int,
    *,
    insertion: pb.TextSubstring | None,
    stamp: pb.TextIdentifier,
) -> None:
    result: list[pb.TextSubstring] = []
    remaining = position
    applied = False
    for span in history:
        if applied or span.tombstone or remaining >= span.length:
            result.append(span)
            if not applied and not span.tombstone:
                remaining -= span.length
            continue
        if remaining:
            prefix = pb.TextSubstring()
            prefix.CopyFrom(span)
            prefix.length = remaining
            result.append(prefix)
        suffix = pb.TextSubstring()
        suffix.CopyFrom(span)
        suffix.char_id.clock += remaining
        suffix.length -= remaining
        if insertion is not None:
            result.append(insertion)
        else:
            removed = pb.TextSubstring()
            removed.CopyFrom(suffix)
            removed.length = 1
            removed.tombstone = True
            removed.timestamp.CopyFrom(stamp)
            result.append(removed)
            suffix.length -= 1
            suffix.char_id.clock += 1
        if suffix.length:
            result.append(suffix)
        applied = True
    if not applied:
        if insertion is None or remaining:
            raise ValueError(_BAD_TABLE)
        result.append(insertion)
    count = len(markers.note_text) + (1 if insertion is not None else -1)
    markers.note_text = "\ufffc" * count
    del markers.attribute_run[:]
    for _ in range(count):
        markers.attribute_run.add(length=1)
    _write_spans(markers, result)


def _empty_table_cell(data: pb.MergeableDataObjectData) -> int:
    entries = data.mergeable_data_object_entry
    index = len(entries)
    note = entries.add().note
    note.note_text = ""
    _write_spans(note, [])
    return index


@dataclass(frozen=True)
class _TableResizeGraph:
    graph: _TableGraph
    axis: pb.OrderedSet
    row_keys: list[int]
    columns: pb.Dictionary
    target: TableAxis

    def insert(self, position: int, replica: int, clock: int) -> None:
        """Add one axis member and empty cells without moving existing records."""
        data = self.graph.data
        ordered = uuid4().bytes
        logical = _create_uuid_row(data, uuid4().bytes)
        key = _create_uuid_row(data, ordered)
        array = self.axis.ordering.array.attachment
        member = array.add(uuid=ordered, index=position)
        array.insert(position, member)
        del array[-1]
        _append_dict(
            self.axis.ordering.contents, key, logical, replica=replica, clock=clock
        )
        _append_dict(self.axis.elements, key, key, replica=replica, clock=clock)
        if self.target == "row":
            for column in self.columns.element:
                cells = self.graph.entry(
                    column.value.object_index, "dictionary"
                ).dictionary
                _append_dict(
                    cells,
                    logical,
                    _empty_table_cell(data),
                    replica=replica,
                    clock=clock,
                )
        else:
            entries = data.mergeable_data_object_entry
            _append_dict(
                self.columns, logical, len(entries), replica=replica, clock=clock
            )
            cells = entries.add().dictionary
            for row in self.row_keys:
                _append_dict(
                    cells, row, _empty_table_cell(data), replica=replica, clock=clock
                )

    def delete(self, position: int) -> set[int]:
        """Remove active links while retaining the ordering-to-identity mapping."""
        array = self.axis.ordering.array.attachment
        ordered = array[position].uuid
        logical = self.graph.uuid(
            self.graph.references(self.axis.ordering.contents)[ordered]
        )
        del array[position]
        _pop_table_link(self.graph, self.axis.elements, ordered)
        if self.target == "row":
            removed = {
                _pop_table_link(
                    self.graph,
                    self.graph.entry(col.value.object_index, "dictionary").dictionary,
                    logical,
                )
                for col in self.columns.element
            }
        else:
            col = _pop_table_link(self.graph, self.columns, logical)
            cells = self.graph.entry(col, "dictionary").dictionary
            removed = {col, *(item.value.object_index for item in cells.element)}
        return removed


def _pop_table_link(graph: _TableGraph, dictionary: pb.Dictionary, uid: bytes) -> int:
    for index, element in enumerate(dictionary.element):
        if graph.uuid(element.key.object_index) == uid:
            value = int(element.value.object_index)
            del dictionary.element[index]
            return value
    raise ValueError(_BAD_TABLE)


def _checked_axis(graph: _TableGraph, index: int) -> tuple[pb.OrderedSet, list[int]]:
    axis = graph.entry(index, "ordered_set").ordered_set
    active = {bytes(item.uuid) for item in axis.ordering.array.attachment}
    refs = graph.references(axis.elements)
    markers = axis.ordering.array.contents
    if (
        refs.keys() != active
        or any(graph.uuid(value) != key for key, value in refs.items())
        or markers.note_text != "\ufffc" * len(active)
        or [run.length for run in markers.attribute_run] != [1] * len(active)
    ):
        raise ValueError(_BAD_TABLE)
    order = graph.references(axis.ordering.contents)
    return axis, [order[item.uuid] for item in axis.ordering.array.attachment]


def encode_table_resize(
    blob: bytes,
    *,
    axis: TableAxis,
    action: TableResizeAction,
    index: int,
    confirm_nonempty: bool = False,
) -> str:
    """Insert before a one-based position, or delete the member at that position."""
    if axis not in ("row", "column") or action not in ("insert", "delete"):
        raise ValueError("Expected a row or column insertion or deletion")
    proto = _load_table(blob)
    data = proto.mergable_data_object.mergeable_data_object_data
    graph = _TableGraph(data)
    grid = graph.grid()
    count = len(grid) if axis == "row" else len(grid[0])
    insert = action == "insert"
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 1 <= index <= count + int(insert)
    ):
        raise ValueError("Notes table row or column does not exist")
    if (insert and count == MAX_TABLE_AXIS) or (not insert and count == 1):
        raise ValueError("A Notes table must retain 1 to 32 rows and columns")
    position = index - 1
    if not insert:
        affected = grid[position] if axis == "row" else [row[position] for row in grid]
        if confirm_nonempty is not True and any(cell.note_text for cell in affected):
            raise ValueError("Confirm removal of non-empty Notes table cells")
    refs = graph.root_references()
    if refs["crRows"] == refs["crColumns"]:
        raise ValueError(_BAD_TABLE)
    rows, row_keys = _checked_axis(graph, refs["crRows"])
    columns, _ = _checked_axis(graph, refs["crColumns"])
    axis_record = rows if axis == "row" else columns
    resize = _TableResizeGraph(
        graph,
        axis_record,
        row_keys,
        graph.entry(refs["cellColumns"], "dictionary").dictionary,
        axis,
    )
    markers = axis_record.ordering.array.contents
    clocks = _table_text_clocks(data)
    history = _replace_cell_history(markers, clocks)
    replica, char_clock, style = _new_table_actor(
        data, _table_text_nodes(data), clocks, int(insert)
    )
    object_replica, object_clock = _join_object_clock(data)
    _edit_axis_markers(
        markers,
        history,
        position,
        insertion=_span(replica, char_clock, 1) if insert else None,
        stamp=pb.TextIdentifier(replica_id=replica, clock=style),
    )
    if insert:
        resize.insert(position, object_replica, object_clock)
        removed: set[int] = set()
    else:
        removed = resize.delete(position)
    for i, member in enumerate(axis_record.ordering.array.attachment):
        member.index = i
    _compact_table(data, removed)
    graph.grid()
    return _encode_table(proto)
