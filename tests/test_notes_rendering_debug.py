"""Tests for the notes rendering debug helpers."""

# Escape sequences and fixture tokens inside note text (cspell).
# cspell:ignore ufffcij Xabc

import unittest

from pyicloud.services.notes.protobuf import notes_pb2 as pb
from pyicloud.services.notes.rendering.debug_tools import (
    _enum_name,
    annotate_note_runs_html,
    dump_runs_text,
    map_attribute_runs,
    map_merged_runs,
)


def _control_char_note() -> pb.Note:
    """Build a note whose runs cover every escaped character branch.

    The runs alternate between styled and unstyled and end with an
    attachment so that all presence checks are exercised.
    """
    note = pb.Note()
    note.note_text = "ab\ncd\u2028ef\x00gh\ufffcij"
    lengths = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    for index, length in enumerate(lengths):
        run = note.attribute_run.add()
        run.length = length
        if index % 2 == 0:
            run.paragraph_style.style_type = pb.StyleType.STYLE_TYPE_HEADING
            run.paragraph_style.alignment = pb.Alignment.ALIGNMENT_CENTER
            run.paragraph_style.writing_direction_paragraph = (
                pb.WritingDirection.WRITING_DIRECTION_LTR
            )
            run.paragraph_style.indent_amount = index
    note.attribute_run[-1].attachment_info.attachment_identifier = "att-0"
    return note


def _merged_note() -> pb.Note:
    """Build a note with two mergeable runs plus an attachment run."""
    note = pb.Note()
    note.note_text = "HelloWorldXabc"

    first = note.attribute_run.add()
    first.length = 5
    first.paragraph_style.style_type = pb.StyleType.STYLE_TYPE_TITLE

    second = note.attribute_run.add()
    second.length = 5
    second.paragraph_style.style_type = pb.StyleType.STYLE_TYPE_TITLE

    attachment = note.attribute_run.add()
    attachment.length = 1
    attachment.attachment_info.attachment_identifier = "att-1"

    trailing = note.attribute_run.add()
    trailing.length = 3
    trailing.paragraph_style.style_type = pb.StyleType.STYLE_TYPE_TITLE
    return note


class TestEnumName(unittest.TestCase):
    """Tests for the private enum-name helper."""

    def test_none_returns_placeholder(self) -> None:
        """A missing value returns the placeholder string."""
        self.assertEqual(_enum_name(pb.StyleType, None), "(none)")

    def test_valid_value_returns_enum_name(self) -> None:
        """A declared enum value maps to its symbolic name."""
        self.assertEqual(
            _enum_name(pb.StyleType, pb.StyleType.STYLE_TYPE_HEADING),
            "STYLE_TYPE_HEADING",
        )

    def test_invalid_value_falls_back_to_str(self) -> None:
        """An undeclared enum number falls back to its decimal string."""
        self.assertEqual(_enum_name(pb.StyleType, 999), "999")


class TestMapAttributeRuns(unittest.TestCase):
    """Tests for mapping each attribute run to its text slice."""

    def test_maps_styled_unstyled_and_attachment_runs(self) -> None:
        """Styled, unstyled, and attachment runs map to expected fields."""
        rows = map_attribute_runs(_control_char_note())

        self.assertEqual(len(rows), 9)

        first = rows[0]
        self.assertEqual(first["index"], 0)
        self.assertEqual(first["utf16_start"], 0)
        self.assertEqual(first["utf16_len"], 2)
        self.assertEqual(first["text"], "ab")
        self.assertEqual(first["style_type"], pb.StyleType.STYLE_TYPE_HEADING)
        self.assertEqual(first["alignment"], pb.Alignment.ALIGNMENT_CENTER)
        self.assertEqual(
            first["writing_direction"],
            pb.WritingDirection.WRITING_DIRECTION_LTR,
        )
        self.assertEqual(first["indent_amount"], 0)
        self.assertFalse(first["has_attachment"])

        unstyled = rows[1]
        self.assertEqual(unstyled["text"], "\n")
        self.assertEqual(unstyled["utf16_start"], 2)
        self.assertIsNone(unstyled["style_type"])
        self.assertIsNone(unstyled["alignment"])
        self.assertIsNone(unstyled["writing_direction"])
        self.assertIsNone(unstyled["indent_amount"])
        self.assertFalse(unstyled["has_attachment"])

        last = rows[8]
        self.assertEqual(last["text"], "ij")
        self.assertEqual(last["utf16_start"], 12)
        self.assertEqual(last["utf16_len"], 2)
        self.assertTrue(last["has_attachment"])

    def test_empty_note_yields_no_rows(self) -> None:
        """A note without runs yields an empty mapping list."""
        self.assertEqual(map_attribute_runs(pb.Note()), [])

    def test_empty_text_with_run_yields_empty_slice(self) -> None:
        """A note with no text still reports a zero-width slice per run."""
        note = pb.Note()
        run = note.attribute_run.add()
        run.length = 0

        rows = map_attribute_runs(note)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "")
        self.assertEqual(rows[0]["utf16_start"], 0)
        self.assertEqual(rows[0]["utf16_len"], 0)
        self.assertIsNone(rows[0]["style_type"])
        self.assertFalse(rows[0]["has_attachment"])


class TestDumpRunsText(unittest.TestCase):
    """Tests for the textual run dump."""

    def test_dump_replaces_control_characters(self) -> None:
        """Every escaped control character gets a readable marker."""
        dumped = dump_runs_text(_control_char_note())

        self.assertTrue(dumped)
        self.assertIn("⏎", dumped)
        self.assertIn("⤶", dumped)
        self.assertIn("␀", dumped)
        self.assertIn("{OBJ}", dumped)
        self.assertIn("[008]", dumped)

    def test_dump_of_empty_note_is_empty(self) -> None:
        """A note without runs dumps to the empty string."""
        self.assertEqual(dump_runs_text(pb.Note()), "")


class TestAnnotateNoteRunsHtml(unittest.TestCase):
    """Tests for the HTML run annotator."""

    def test_annotate_wraps_palette_and_escapes_controls(self) -> None:
        """Runs cycle through the palette and control chars become spans."""
        html_doc = annotate_note_runs_html(_control_char_note())

        self.assertTrue(html_doc.startswith("<!doctype html>"))
        self.assertIn('class="run"', html_doc)
        self.assertIn("<span class=lb>⏎</span>", html_doc)
        self.assertIn("<span class=lb>⤶</span>", html_doc)
        self.assertIn("<span class=obj>{OBJ}</span>", html_doc)
        self.assertIn("<span class=null>␀</span>", html_doc)
        # Nine runs wrap the five-colour palette, repeating the first colour.
        self.assertGreaterEqual(html_doc.count('style="background:#FFF3CD"'), 2)

    def test_annotate_escapes_markup_in_run_text(self) -> None:
        """Markup inside run text is HTML-escaped in the output."""
        note = pb.Note()
        note.note_text = "<b>"
        run = note.attribute_run.add()
        run.length = 3

        html_doc = annotate_note_runs_html(note)

        self.assertIn("&lt;b&gt;", html_doc)
        self.assertIn('class="run"', html_doc)


class TestMapMergedRuns(unittest.TestCase):
    """Tests for run mapping after the renderer merge step."""

    def test_adjacent_runs_merge_and_attachment_starts_new_entry(self) -> None:
        """Identical adjacent runs merge; an attachment starts a new entry."""
        rows = map_merged_runs(_merged_note())

        self.assertEqual(len(rows), 3)
        self.assertEqual([row["utf16_len"] for row in rows], [10, 1, 3])
        self.assertEqual([row["utf16_start"] for row in rows], [0, 10, 11])
        self.assertEqual(
            [row["text"] for row in rows],
            ["HelloWorld", "X", "abc"],
        )
        self.assertEqual(
            [row["has_attachment"] for row in rows],
            [False, True, False],
        )
        self.assertEqual(rows[0]["style_type"], pb.StyleType.STYLE_TYPE_TITLE)
        self.assertEqual(rows[2]["style_type"], pb.StyleType.STYLE_TYPE_TITLE)
        self.assertIsNone(rows[1]["style_type"])

    def test_empty_note_yields_no_merged_rows(self) -> None:
        """A note without runs yields no merged entries."""
        self.assertEqual(map_merged_runs(pb.Note()), [])


if __name__ == "__main__":
    unittest.main()
