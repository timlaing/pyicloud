"""
Table reconstruction for Apple Notes MergeableData payloads (CloudKit/iCloud path).

Given a gzipped MergeableData payload for a table attachment, reconstructs the
row/column ordering and renders a plain HTML <table> with cell contents. Cell
contents are themselves Notes; callers provide a callback to render a pb.Note
into HTML using the existing renderer.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from tinyhtml import h, raw  # type: ignore[import-not-found]

from ..protobuf import notes_pb2 as pb


class TypeName(str, Enum):
    ICTABLE = "com.apple.notes.ICTable"


class MapKey(str, Enum):
    CR_ROWS = "crRows"
    CR_COLUMNS = "crColumns"
    CELL_COLUMNS = "cellColumns"


@dataclass(frozen=True, slots=True)
class TableSpec:
    type_name: TypeName
    rows_key: MapKey
    cols_key: MapKey
    cellcols_key: MapKey


TABLE_SPEC = TableSpec(
    type_name=TypeName.ICTABLE,
    rows_key=MapKey.CR_ROWS,
    cols_key=MapKey.CR_COLUMNS,
    cellcols_key=MapKey.CELL_COLUMNS,
)

MAX_TABLE_AXIS_ITEMS = 512
MAX_TABLE_CELLS = 50_000


@dataclass(slots=True)
class Cell:
    html: str = ""


@dataclass(slots=True)
class AxisState:
    indices: dict[int, int] = field(default_factory=dict)
    total: int = 0


@dataclass(slots=True)
class TableBuilder:
    key_items: List[str]
    type_items: List[str]
    uuid_items: List[bytes]
    entries: List[pb.MergeableDataObjectRow]
    render_note_cb: Callable[[pb.Note], str]

    uuid_index: dict[bytes, int] = field(init=False)
    rows: AxisState = field(default_factory=AxisState)
    cols: AxisState = field(default_factory=AxisState)
    cells: List[List[Cell]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.uuid_index = {u: i for i, u in enumerate(self.uuid_items)}

    def _uuid_index_from_entry(self, entry: pb.MergeableDataObjectRow) -> Optional[int]:
        try:
            # custom_map.map_entry[0].value.unsigned_integer_value -> UUID VALUE
            val = entry.custom_map.map_entry[0].value.unsigned_integer_value
            return (
                self.uuid_index.get(self.uuid_items[val], None)
                if 0 <= val < len(self.uuid_items)
                else None
            )
        except Exception:
            return None

    def _parse_axis(self, entry: pb.MergeableDataObjectRow, axis: AxisState) -> None:
        axis.total = 0
        axis.indices.clear()
        # 1) Array attachments reference UUID VALUES directly
        try:
            for att in entry.ordered_set.ordering.array.attachment:
                idx = self.uuid_index.get(att.uuid)
                if idx is None:
                    continue
                axis.indices[idx] = axis.total
                axis.total += 1
        except Exception:
            pass
        # 2) Contents remap (key -> value) using entries addressed by object_index
        try:
            for elem in entry.ordered_set.ordering.contents.element:
                k_ent = self.entries[elem.key.object_index]
                v_ent = self.entries[elem.value.object_index]
                k_idx = self._uuid_index_from_entry(k_ent)
                v_idx = self._uuid_index_from_entry(v_ent)
                if v_idx is None:
                    continue
                pos = axis.indices.get(k_idx, axis.indices.get(v_idx, 0))  # type: ignore[arg-type]
                axis.total = max(axis.total, pos + 1)
                axis.indices[v_idx] = pos
        except Exception:
            pass

    def parse_rows(self, entry: pb.MergeableDataObjectRow) -> None:
        self._parse_axis(entry, self.rows)

    def parse_cols(self, entry: pb.MergeableDataObjectRow) -> None:
        self._parse_axis(entry, self.cols)

    def init_table_buffers(self) -> None:
        if (
            self.rows.total <= 0
            or self.cols.total <= 0
            or self.rows.total > MAX_TABLE_AXIS_ITEMS
            or self.cols.total > MAX_TABLE_AXIS_ITEMS
            or self.rows.total * self.cols.total > MAX_TABLE_CELLS
        ):
            self.cells = []
            return
        self.cells = [
            [Cell() for _ in range(self.cols.total)] for _ in range(self.rows.total)
        ]

    def parse_cell_columns(self, entry: pb.MergeableDataObjectRow) -> None:
        # entry.dictionary.element: key -> column dict
        for col in entry.dictionary.element:
            try:
                col_key_ent = self.entries[col.key.object_index]
                col_pos = self.cols.indices.get(
                    self._uuid_index_from_entry(col_key_ent)  # type: ignore[arg-type]
                )
                if col_pos is None:
                    continue
                col_dict_ent = self.entries[col.value.object_index]
            except Exception:
                continue
            for row in col_dict_ent.dictionary.element:
                try:
                    row_key_ent = self.entries[row.key.object_index]
                    row_pos = self.rows.indices.get(
                        self._uuid_index_from_entry(row_key_ent)  # type: ignore[arg-type]
                    )
                    if row_pos is None:
                        continue
                    cell_ent = self.entries[row.value.object_index]
                except Exception:
                    continue
                if not cell_ent.HasField("note"):
                    continue
                try:
                    cell_note = cell_ent.note
                    inner_html = self.render_note_cb(cell_note)
                    if row_pos >= len(self.cells) or col_pos >= len(
                        self.cells[row_pos]
                    ):
                        continue
                    self.cells[row_pos][col_pos].html = inner_html
                except Exception:
                    continue

    def render_html_table(self) -> Optional[str]:
        if not self.cells or self.rows.total == 0 or self.cols.total == 0:
            return None
        trs: List[object] = []
        for r in range(self.rows.total):
            tds: List[object] = []
            for c in range(self.cols.total):
                cell_html = self.cells[r][c].html or ""
                tds.append(h("td")(raw(cell_html)))  # type: ignore[arg-type]
            trs.append(h("tr")(*tds))  # type: ignore[arg-type]
        return h("table")(*trs).render()  # type: ignore[arg-type]


ALLOWED_TABLE_TYPES = {
    TypeName.ICTABLE.value,
    "com.apple.notes.ICTable2",
    "com.apple.notes.CRTable",
}


def render_table_from_mergeable(
    gz_bytes: bytes, render_note_cb: Callable[[pb.Note], str]
) -> Optional[str]:
    if not gz_bytes:
        return None
    try:
        payload = gzip.decompress(gz_bytes)
    except Exception:
        payload = gz_bytes
    try:
        m = pb.MergableDataProto()
        m.ParseFromString(payload)
        data = m.mergable_data_object.mergeable_data_object_data
        key_items = list(data.mergeable_data_object_key_item)
        type_items = list(data.mergeable_data_object_type_item)
        uuid_items = list(data.mergeable_data_object_uuid_item)
        entries = list(data.mergeable_data_object_entry)
    except Exception:
        return None

    # Find root entry by type name and walk
    for e in entries:
        if not e.HasField("custom_map"):
            continue
        try:
            type_idx = e.custom_map.type
            tname_ok = (
                0 <= type_idx < len(type_items)
                and type_items[type_idx] in ALLOWED_TABLE_TYPES
            )
        except Exception:
            tname_ok = False

        # Fallback: treat as table if it contains the expected keys
        has_rows = has_cols = has_cells = False
        try:
            keynames = [
                key_items[me.key]
                for me in e.custom_map.map_entry
                if 0 <= me.key < len(key_items)
            ]
            has_rows = TABLE_SPEC.rows_key.value in keynames
            has_cols = TABLE_SPEC.cols_key.value in keynames
            has_cells = TABLE_SPEC.cellcols_key.value in keynames
        except Exception:
            pass

        if not (tname_ok or (has_rows and has_cols and has_cells)):
            continue

        tb = TableBuilder(
            key_items=key_items,
            type_items=type_items,
            uuid_items=uuid_items,
            entries=entries,
            render_note_cb=render_note_cb,
        )
        pending_cell_columns: Optional[pb.MergeableDataObjectRow] = None
        for me in e.custom_map.map_entry:
            kname = key_items[me.key] if 0 <= me.key < len(key_items) else None
            try:
                target = entries[me.value.object_index]
            except Exception:
                continue
            if kname == TABLE_SPEC.rows_key.value:
                try:
                    tb.parse_rows(target)
                except Exception:
                    continue
            elif kname == TABLE_SPEC.cols_key.value:
                try:
                    tb.parse_cols(target)
                except Exception:
                    continue
            elif kname == TABLE_SPEC.cellcols_key.value:
                pending_cell_columns = target
        if tb.rows.total <= 0 or tb.cols.total <= 0:
            continue
        tb.init_table_buffers()
        if not tb.cells:
            continue
        if pending_cell_columns:
            try:
                tb.parse_cell_columns(pending_cell_columns)
            except Exception:
                continue
        html_table = tb.render_html_table()
        if html_table:
            return html_table
    return None
