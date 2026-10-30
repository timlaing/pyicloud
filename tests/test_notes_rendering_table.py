"""Tests for MergeableData table reconstruction in notes rendering."""

# pylint: disable=protected-access

from collections.abc import Callable
import gzip
import unittest

from pyicloud.services.notes.protobuf import notes_pb2 as pb
from pyicloud.services.notes.rendering.table_builder import (
    Cell,
    TableBuilder,
    render_table_from_mergeable,
)


def _noop_render(_note: pb.Note) -> str:
    """Render a note as a stable HTML fragment for assertions."""
    return "<p>x</p>"


def _make_builder(
    uuid_items: list[bytes] | None = None,
    entries: list[pb.MergeableDataObjectRow] | None = None,
    render_note_cb: Callable[[pb.Note], str] = _noop_render,
) -> TableBuilder:
    """Construct a TableBuilder with optional uuid items and entries."""
    return TableBuilder(
        key_items=[],
        type_items=[],
        uuid_items=uuid_items or [],
        entries=entries or [],
        render_note_cb=render_note_cb,
    )


def _make_uuid_entry(index: int) -> pb.MergeableDataObjectRow:
    """Build a UUID-index entry resolving to ``uuid_items[index]``."""
    entry = pb.MergeableDataObjectRow()
    item = entry.custom_map.map_entry.add()
    item.value.unsigned_integer_value = index
    return entry


def _make_axis_entry(*uuids: bytes) -> pb.MergeableDataObjectRow:
    """Build an ordered-set entry whose attachments reference UUID values."""
    entry = pb.MergeableDataObjectRow()
    for value in uuids:
        entry.ordered_set.ordering.array.attachment.add().uuid = value
    return entry


def _serialize(proto: pb.MergableDataProto) -> bytes:
    """Serialize and gzip a MergeableData protobuf payload."""
    return gzip.compress(proto.SerializeToString())


class TestUuidIndexFromEntry(unittest.TestCase):
    """Tests for TableBuilder._uuid_index_from_entry."""

    def test_resolves_valid_index(self) -> None:
        """A valid UUID index entry resolves through uuid_items."""
        entry = _make_uuid_entry(1)
        builder = _make_builder(uuid_items=[b"row", b"col"])

        self.assertEqual(builder._uuid_index_from_entry(entry), 1)

    def test_returns_none_without_custom_map(self) -> None:
        """An entry lacking a custom_map yields None."""
        entry = pb.MergeableDataObjectRow()
        builder = _make_builder(uuid_items=[b"row"])

        self.assertIsNone(builder._uuid_index_from_entry(entry))

    def test_returns_none_for_out_of_range_value(self) -> None:
        """An out-of-range unsigned value yields None."""
        entry = _make_uuid_entry(99)
        builder = _make_builder(uuid_items=[b"row"])

        self.assertIsNone(builder._uuid_index_from_entry(entry))


class TestParseAxis(unittest.TestCase):
    """Tests for TableBuilder axis parsing (rows/columns)."""

    def test_parse_rows_from_array_attachments(self) -> None:
        """Row ordering follows array attachments and skips unknown UUIDs."""
        builder = _make_builder(uuid_items=[b"r0", b"r1"])
        entry = _make_axis_entry(b"r1", b"unknown", b"r0")

        builder.parse_rows(entry)

        self.assertEqual(builder.rows.total, 2)
        self.assertEqual(builder.rows.indices, {1: 0, 0: 1})

    def test_parse_cols_from_array_attachments(self) -> None:
        """Column ordering follows array attachments directly."""
        builder = _make_builder(uuid_items=[b"c0"])
        entry = _make_axis_entry(b"c0")

        builder.parse_cols(entry)

        self.assertEqual(builder.cols.total, 1)
        self.assertEqual(builder.cols.indices, {0: 0})

    def test_parse_axis_from_contents_remap(self) -> None:
        """Contents remap orders values and skips unresolvable values."""
        entries = [
            _make_uuid_entry(0),
            _make_uuid_entry(1),
            pb.MergeableDataObjectRow(),
            pb.MergeableDataObjectRow(),
        ]
        builder = _make_builder(uuid_items=[b"a", b"b"], entries=entries)
        entry = pb.MergeableDataObjectRow()
        mapped = entry.ordered_set.ordering.contents.element.add()
        mapped.key.object_index = 0
        mapped.value.object_index = 1
        skipped = entry.ordered_set.ordering.contents.element.add()
        skipped.key.object_index = 0
        skipped.value.object_index = 2
        trailing = entry.ordered_set.ordering.contents.element.add()
        trailing.value.object_index = 2
        trailing.key.object_index = 99

        builder.parse_rows(entry)

        self.assertEqual(builder.rows.total, 1)
        self.assertEqual(builder.rows.indices, {1: 0})

    def test_parse_axis_uses_value_position_as_default_key(self) -> None:
        """A missing key position falls back to the value position."""
        entries = [_make_uuid_entry(0), _make_uuid_entry(1)]
        builder = _make_builder(uuid_items=[b"a", b"b"], entries=entries)
        entry = pb.MergeableDataObjectRow()
        mapped = entry.ordered_set.ordering.contents.element.add()
        mapped.key.object_index = 0
        mapped.value.object_index = 1

        builder.parse_rows(entry)

        self.assertEqual(builder.rows.indices, {1: 0})
        self.assertEqual(builder.rows.total, 1)


class TestInitTableBuffers(unittest.TestCase):
    """Tests for TableBuilder.init_table_buffers guard clauses."""

    def test_rejects_zero_rows(self) -> None:
        """Zero rows clears the cell buffer."""
        builder = _make_builder()
        builder.rows.total = 0
        builder.cols.total = 1

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_rejects_zero_cols(self) -> None:
        """Zero columns clears the cell buffer."""
        builder = _make_builder()
        builder.rows.total = 1
        builder.cols.total = 0

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_rejects_too_many_rows(self) -> None:
        """More than 512 rows clears the cell buffer."""
        builder = _make_builder()
        builder.rows.total = 600
        builder.cols.total = 1

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_rejects_too_many_cols(self) -> None:
        """More than 512 columns clears the cell buffer."""
        builder = _make_builder()
        builder.rows.total = 1
        builder.cols.total = 600

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_rejects_too_many_cells(self) -> None:
        """A product above 50000 clears the cell buffer."""
        builder = _make_builder()
        builder.rows.total = 400
        builder.cols.total = 400

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_accepts_valid_dimensions(self) -> None:
        """Valid dimensions produce a full cell grid."""
        builder = _make_builder()
        builder.rows.total = 2
        builder.cols.total = 3

        builder.init_table_buffers()

        self.assertEqual(len(builder.cells), 2)
        self.assertEqual(len(builder.cells[0]), 3)
        self.assertTrue(all(isinstance(c, Cell) for c in builder.cells[0]))


class TestParseCellColumns(unittest.TestCase):
    """Tests for TableBuilder.parse_cell_columns."""

    def _setup(  # noqa: S3776
        self, note_text: str | None = "x"
    ) -> tuple[TableBuilder, pb.MergeableDataObjectRow]:
        """Build a builder with one column mapping to one row cell."""
        col_dict = pb.MergeableDataObjectRow()
        cell = pb.MergeableDataObjectRow()
        if note_text is not None:
            cell.note.note_text = note_text
        entries = [
            _make_uuid_entry(1),
            col_dict,
            _make_uuid_entry(0),
            cell,
        ]
        row_map = col_dict.dictionary.element.add()
        row_map.key.object_index = 2
        row_map.value.object_index = 3

        cellcols = pb.MergeableDataObjectRow()
        col_map = cellcols.dictionary.element.add()
        col_map.key.object_index = 0
        col_map.value.object_index = 1

        builder = _make_builder(uuid_items=[b"row", b"col"], entries=entries)
        builder.rows.total = 1
        builder.cols.total = 1
        builder.rows.indices = {0: 0}
        builder.cols.indices = {1: 0}
        builder.init_table_buffers()
        return builder, cellcols

    def test_renders_cell_contents(self) -> None:
        """A complete mapping renders the cell through the callback."""
        builder, cellcols = self._setup()

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "<p>x</p>")

    def test_skips_missing_column_position(self) -> None:
        """A column absent from the column index is skipped."""
        builder, cellcols = self._setup()
        builder.cols.indices = {}

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_missing_row_position(self) -> None:
        """A row absent from the row index is skipped."""
        builder, cellcols = self._setup()
        builder.rows.indices = {}

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_cell_without_note(self) -> None:
        """A referenced cell without a note is skipped."""
        builder, cellcols = self._setup(note_text=None)

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_raised_render_callback(self) -> None:
        """A raising render callback is swallowed."""

        def _boom(_note: pb.Note) -> str:
            """Raise to exercise the callback failure path."""
            raise RuntimeError("boom")

        builder, cellcols = self._setup()
        builder.render_note_cb = _boom

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_out_of_range_column_key(self) -> None:
        """An out-of-range column key entry is skipped."""
        builder, cellcols = self._setup()
        cellcols.dictionary.element[0].key.object_index = 99

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_out_of_range_row_key(self) -> None:
        """An out-of-range row key entry is skipped."""
        builder, _ = self._setup()
        cellcols = pb.MergeableDataObjectRow()
        col_map = cellcols.dictionary.element.add()
        col_map.key.object_index = 0
        col_map.value.object_index = 1
        builder.entries[1].dictionary.element[0].key.object_index = 99

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_row_beyond_buffer(self) -> None:
        """A row position beyond the allocated buffer is skipped."""
        builder, cellcols = self._setup()
        builder.rows.indices = {0: 5}

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")

    def test_skips_col_beyond_buffer(self) -> None:
        """A column position beyond the allocated buffer is skipped."""
        builder, cellcols = self._setup()
        builder.cols.indices = {1: 5}
        builder.cells = [[]]

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells, [[]])

    def test_skips_unresolvable_column_uuid(self) -> None:
        """A column key with no resolvable UUID is skipped."""
        builder, cellcols = self._setup()
        builder.entries[0] = pb.MergeableDataObjectRow()

        builder.parse_cell_columns(cellcols)

        self.assertEqual(builder.cells[0][0].html, "")


class TestRenderHtmlTable(unittest.TestCase):
    """Tests for TableBuilder.render_html_table."""

    def test_returns_none_without_cells(self) -> None:
        """No cells yields None."""
        builder = _make_builder()

        self.assertIsNone(builder.render_html_table())

    def test_returns_none_without_rows(self) -> None:
        """Zero rows yields None."""
        builder = _make_builder()
        builder.cells = [[Cell()]]
        builder.rows.total = 0
        builder.cols.total = 1

        self.assertIsNone(builder.render_html_table())

    def test_returns_none_without_cols(self) -> None:
        """Zero columns yields None."""
        builder = _make_builder()
        builder.cells = [[Cell()]]
        builder.rows.total = 1
        builder.cols.total = 0

        self.assertIsNone(builder.render_html_table())

    def test_renders_cells(self) -> None:
        """A populated grid renders a table with the cell HTML."""
        builder = _make_builder()
        builder.cells = [[Cell(html="<b>hi</b>"), Cell()]]
        builder.rows.total = 1
        builder.cols.total = 2

        html = builder.render_html_table()

        assert html is not None
        self.assertIn("<table>", html)
        self.assertIn("<b>hi</b>", html)
        self.assertEqual(html.count("<td>"), 2)


def _valid_table_proto() -> pb.MergableDataProto:
    """Build a table payload with a valid root and a full cell path."""
    proto = pb.MergableDataProto()
    data = proto.mergable_data_object.mergeable_data_object_data
    data.mergeable_data_object_key_item.extend(["crRows", "crColumns", "cellColumns"])
    data.mergeable_data_object_type_item.append("com.apple.notes.ICTable")
    data.mergeable_data_object_uuid_item.extend([b"row", b"col"])
    entries = data.mergeable_data_object_entry

    root = entries.add()
    root.custom_map.type = 0
    bad = root.custom_map.map_entry.add()
    bad.key = 0
    bad.value.object_index = 999
    for key_index, target_index in ((0, 1), (1, 2), (2, 3)):
        item = root.custom_map.map_entry.add()
        item.key = key_index
        item.value.object_index = target_index
    trailing = root.custom_map.map_entry.add()
    trailing.key = 99
    trailing.value.object_index = 1

    rows_entry = entries.add()
    rows_entry.ordered_set.ordering.array.attachment.add().uuid = b"row"
    cols_entry = entries.add()
    cols_entry.ordered_set.ordering.array.attachment.add().uuid = b"col"

    cellcols = entries.add()
    col_map = cellcols.dictionary.element.add()
    col_map.key.object_index = 4
    col_map.value.object_index = 5

    entries.append(_make_uuid_entry(1))
    col_dict = entries.add()
    row_map = col_dict.dictionary.element.add()
    row_map.key.object_index = 6
    row_map.value.object_index = 7
    entries.append(_make_uuid_entry(0))
    cell = entries.add()
    cell.note.note_text = "x"
    return proto


class TestRenderTableFromMergeable(unittest.TestCase):
    """Tests for render_table_from_mergeable."""

    def test_returns_none_for_empty_input(self) -> None:
        """Empty bytes yields None."""
        self.assertIsNone(render_table_from_mergeable(b"", _noop_render))

    def test_returns_none_for_non_gzip_malformed(self) -> None:
        """Non-gzip malformed bytes yield None."""
        self.assertIsNone(render_table_from_mergeable(b"not-a-table", _noop_render))

    def test_returns_none_for_invalid_wire_payload(self) -> None:
        """A gzip payload that fails to parse yields None."""
        self.assertIsNone(
            render_table_from_mergeable(gzip.compress(b"\x08"), _noop_render)
        )

    def test_renders_valid_table(self) -> None:
        """A valid gzipped payload renders an HTML table."""
        html = render_table_from_mergeable(
            _serialize(_valid_table_proto()), _noop_render
        )

        assert html is not None
        self.assertIn("<table>", html)
        self.assertIn("<p>x</p>", html)

    def test_skips_entry_without_custom_map(self) -> None:
        """A leading entry without custom_map is skipped."""
        proto = _valid_table_proto()
        data = proto.mergable_data_object.mergeable_data_object_data
        rows = data.mergeable_data_object_entry
        rows.insert(0, pb.MergeableDataObjectRow())
        # Every object_index shifted by one because of the inserted entry.
        for entry in rows:
            for item in entry.custom_map.map_entry:
                item.value.object_index += 1
            for element in entry.dictionary.element:
                element.key.object_index += 1
                element.value.object_index += 1

        html = render_table_from_mergeable(_serialize(proto), _noop_render)

        assert html is not None
        self.assertIn("<table>", html)

    def test_rejects_root_without_keys(self) -> None:
        """A root whose type is invalid and has no keys is rejected."""
        proto = pb.MergableDataProto()
        data = proto.mergable_data_object.mergeable_data_object_data
        data.mergeable_data_object_key_item.extend(["crRows"])
        data.mergeable_data_object_type_item.append("com.apple.notes.ICTable")
        root = data.mergeable_data_object_entry.add()
        root.custom_map.type = 99

        self.assertIsNone(render_table_from_mergeable(_serialize(proto), _noop_render))

    def test_uses_key_fallback_when_type_invalid(self) -> None:
        """An out-of-range type index falls back to the expected keys."""
        proto = _valid_table_proto()
        data = proto.mergable_data_object.mergeable_data_object_data
        root = data.mergeable_data_object_entry[0]
        root.custom_map.type = 99

        html = render_table_from_mergeable(_serialize(proto), _noop_render)

        assert html is not None
        self.assertIn("<table>", html)

    def test_returns_none_when_axis_empty(self) -> None:
        """A matched root with empty axis entries yields None."""
        proto = _valid_table_proto()
        entries = proto.mergable_data_object.mergeable_data_object_data
        rows_entry = entries.mergeable_data_object_entry[1]
        cols_entry = entries.mergeable_data_object_entry[2]
        del rows_entry.ordered_set.ordering.array.attachment[:]
        del cols_entry.ordered_set.ordering.array.attachment[:]

        self.assertIsNone(render_table_from_mergeable(_serialize(proto), _noop_render))

    def test_returns_none_when_buffers_empty(self) -> None:
        """An oversized axis clears the buffers and yields None."""
        proto = pb.MergableDataProto()
        data = proto.mergable_data_object.mergeable_data_object_data
        data.mergeable_data_object_key_item.extend(["crRows", "crColumns"])
        data.mergeable_data_object_type_item.append("com.apple.notes.ICTable")
        uuids = [i.to_bytes(2, "big") for i in range(513)]
        data.mergeable_data_object_uuid_item.extend(uuids)
        entries = data.mergeable_data_object_entry
        root = entries.add()
        root.custom_map.type = 0
        for key_index, target_index in ((0, 1), (1, 2)):
            item = root.custom_map.map_entry.add()
            item.key = key_index
            item.value.object_index = target_index
        rows_entry = entries.add()
        for uuid in uuids:
            rows_entry.ordered_set.ordering.array.attachment.add().uuid = uuid
        cols_entry = entries.add()
        cols_entry.ordered_set.ordering.array.attachment.add().uuid = uuids[0]

        self.assertIsNone(render_table_from_mergeable(_serialize(proto), _noop_render))

    def test_skips_out_of_range_map_target(self) -> None:
        """A map entry pointing outside entries is skipped."""
        proto = _valid_table_proto()
        entries = proto.mergable_data_object.mergeable_data_object_data
        root = entries.mergeable_data_object_entry[0]
        bogus = root.custom_map.map_entry.add()
        bogus.key = 0
        bogus.value.object_index = 999

        html = render_table_from_mergeable(_serialize(proto), _noop_render)

        assert html is not None
        self.assertIn("<table>", html)

    def test_renders_empty_grid_without_cell_columns(self) -> None:
        """A table without cellColumns still renders its empty grid."""
        proto = pb.MergableDataProto()
        data = proto.mergable_data_object.mergeable_data_object_data
        data.mergeable_data_object_key_item.extend(["crRows", "crColumns"])
        data.mergeable_data_object_type_item.append("com.apple.notes.ICTable")
        data.mergeable_data_object_uuid_item.extend([b"row", b"col"])
        entries = data.mergeable_data_object_entry
        root = entries.add()
        root.custom_map.type = 0
        for key_index, target_index in ((0, 1), (1, 2)):
            item = root.custom_map.map_entry.add()
            item.key = key_index
            item.value.object_index = target_index
        rows_entry = entries.add()
        rows_entry.ordered_set.ordering.array.attachment.add().uuid = b"row"
        cols_entry = entries.add()
        cols_entry.ordered_set.ordering.array.attachment.add().uuid = b"col"

        html = render_table_from_mergeable(_serialize(proto), _noop_render)

        assert html is not None
        self.assertIn("<table>", html)


if __name__ == "__main__":
    unittest.main()
