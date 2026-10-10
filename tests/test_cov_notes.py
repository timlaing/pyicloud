"""Branch-coverage tests for the notes client, decoder, and renderers.

This module targets branches in ``client.py``, ``decoding.py``,
``renderer_iface.py``, ``renderer.py`` and ``attachments.py`` that are not
exercised by the existing notes test files (issue #394).
"""

# pylint: disable=protected-access

import base64
import gzip
import os
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import MagicMock, PropertyMock, mock_open, patch
import zlib
from zlib import compress

from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKQueryObject,
    CKQueryResponse,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneID,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitRateLimited,
)
from pyicloud.services.notes.client import (
    CloudKitNotesClient,
    NotesApiError,
    NotesAuthError,
    NotesRateLimited,
)
from pyicloud.services.notes.decoding import BodyDecoder, _b64_to_bytes, _decompress
from pyicloud.services.notes.protobuf import notes_pb2 as pb
from pyicloud.services.notes.rendering.attachments import (
    AttachmentContext,
    render_attachment,
)
from pyicloud.services.notes.rendering.options import ExportConfig
from pyicloud.services.notes.rendering.renderer import (
    NoteRenderer,
    StyleSig,
    _safe_anchor_href,
    _slice_for_run,
    render_note_fragment,
)
from pyicloud.services.notes.rendering.renderer_iface import AttachmentRef


def _sig(**kwargs: Any) -> StyleSig:
    """Build a StyleSig defaulting every field to None and overriding given ones."""
    base: dict[str, Any] = {
        "font_weight": None,
        "underlined": None,
        "strikethrough": None,
        "superscript": None,
        "link": None,
        "color_hex": None,
        "emphasis_style": None,
        "font_size_pt": None,
        "font_name": None,
        "style_type": None,
        "alignment": None,
        "indent_amount": None,
        "block_quote": None,
        "writing_direction": None,
        "checklist_done": None,
        "start_number": None,
        "highlight": None,
        "paragraph_uuid": None,
    }
    base.update(kwargs)
    return StyleSig(**base)


def _sig_from_run(run: pb.AttributeRun) -> StyleSig:
    """Extract a StyleSig without draining the module-level coverage mix."""
    return StyleSig.from_run(run)


def _note(text: str, runs: list[dict[str, Any]]) -> pb.Note:
    """Build a Note from a text and a list of run specifications."""
    note = pb.Note()
    note.note_text = text
    for spec in runs:
        run = note.attribute_run.add()
        run.length = cast(int, spec.get("length", 0))
        if "style" in spec:
            run.paragraph_style.style_type = spec["style"]
        if "indent" in spec:
            run.paragraph_style.indent_amount = spec["indent"]
        if "align" in spec:
            run.paragraph_style.alignment = spec["align"]
        if "wd" in spec:
            run.paragraph_style.writing_direction_paragraph = spec["wd"]
        if "checklist_done" in spec:
            run.paragraph_style.checklist.done = spec["checklist_done"]
        if "start_number" in spec:
            run.paragraph_style.starting_list_item_number = spec["start_number"]
        if "block_quote" in spec:
            run.paragraph_style.block_quote = spec["block_quote"]
        if "para_uuid" in spec:
            run.paragraph_style.paragraph_uuid = spec["para_uuid"]
        if "bold" in spec:
            run.font_weight = spec["bold"]
        if "underlined" in spec:
            run.underlined = spec["underlined"]
        if "strikethrough" in spec:
            run.strikethrough = spec["strikethrough"]
        if "superscript" in spec:
            run.superscript = spec["superscript"]
        if "link" in spec:
            run.link = spec["link"]
        if "color" in spec:
            red, green, blue = spec["color"]
            run.color.red = red
            run.color.green = green
            run.color.blue = blue
        if "font_name" in spec:
            run.font.font_name = spec["font_name"]
        if "font_size" in spec:
            run.font.point_size = spec["font_size"]
        if "emphasis" in spec:
            run.emphasis_style = spec["emphasis"]
        if "highlight" in spec:
            run.highlight_color = spec["highlight"]
        if "attachment" in spec:
            identifier, uti = spec["attachment"]
            run.attachment_info.attachment_identifier = identifier
            if uti is not None:
                run.attachment_info.type_uti = uti
    return note


def _render(
    text: str,
    runs: list[dict[str, Any]],
    *,
    ds: Any = None,
    config: ExportConfig | None = None,
) -> str:
    """Render a note built from text and run specs."""
    return render_note_fragment(_note(text, runs), datasource=ds, config=config)


STM = pb.StyleType


class DecodingCoverageTest(unittest.TestCase):
    """Branches in ``decoding.py``."""

    def test_b64_to_bytes_shapes(self) -> None:
        """All accepted input shapes normalize to raw bytes."""
        self.assertIsNone(_b64_to_bytes(None))
        self.assertEqual(_b64_to_bytes(b"raw"), b"raw")
        self.assertEqual(_b64_to_bytes(bytearray(b"raw")), b"raw")
        self.assertEqual(_b64_to_bytes("aGVsbG8="), b"hello")
        self.assertEqual(_b64_to_bytes("not base64!"), b"not base64!")

    def test_decompress_gzip_and_zlib(self) -> None:
        """Gzip and zlib payloads decompress; raw-deflate falls back."""
        gz = gzip.compress(b"hello")
        zl = compress(b"hello")
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        raw = compressor.compress(b"hello") + compressor.flush()
        self.assertEqual(_decompress(gz), b"hello")
        self.assertEqual(_decompress(zl), b"hello")
        self.assertEqual(_decompress(raw), b"hello")

    def test_decode_none_and_empty(self) -> None:
        """None and empty payloads yield no body."""
        decoder = BodyDecoder()
        self.assertIsNone(decoder.decode(None))
        self.assertIsNone(decoder.decode(b""))

    def test_decode_round_trip_with_attachments_and_dedupe(self) -> None:
        """A valid compressed proto decodes with deduplicated attachment ids."""
        proto = pb.NoteStoreProto()
        note = proto.document.note
        note.note_text = "Hello"
        for _ in range(2):
            run = note.attribute_run.add()
            run.attachment_info.attachment_identifier = "att-1"
            run.attachment_info.type_uti = "public.jpeg"
        payload = base64.b64encode(gzip.compress(proto.SerializeToString())).decode()

        body = BodyDecoder().decode(payload)

        self.assertIsNotNone(body)
        assert body is not None
        self.assertEqual(body.text, "Hello")
        self.assertEqual(
            [(a.identifier, a.type_uti) for a in body.attachment_ids],
            [("att-1", "public.jpeg")],
        )

    def test_decode_without_note_and_with_empty_attachments(self) -> None:
        """A document without a note decodes to an empty body payload."""
        proto = pb.NoteStoreProto()
        payload = base64.b64encode(gzip.compress(proto.SerializeToString())).decode()

        body = BodyDecoder().decode(payload)

        self.assertIsNotNone(body)
        assert body is not None
        self.assertEqual(body.text, "")
        self.assertEqual(body.attachment_ids, [])

    def test_decode_failure_paths(self) -> None:
        """Corrupt payloads fall back to None on decompression and parse errors."""
        self.assertIsNone(BodyDecoder().decode("not-a-payload"))
        garbled = base64.b64encode(b"\x00\x01\x02not-proto").decode()
        self.assertIsNone(BodyDecoder().decode(garbled))


class RendererIfaceCoverageTest(unittest.TestCase):
    """Branches in ``renderer_iface.py``."""

    def test_resolved_uti_uses_datasource(self) -> None:
        """Without a hint the UTI is resolved through the datasource."""
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "public.jpeg"
        ref = AttachmentRef(identifier="att-1")
        self.assertEqual(ref.resolved_uti(ds), "public.jpeg")
        ds.get_attachment_uti.assert_called_once_with("att-1")

    def test_resolved_uti_missing_identifier(self) -> None:
        """A ref without identifier yields None even with a datasource."""
        ref = AttachmentRef(identifier=None)
        self.assertIsNone(ref.resolved_uti(MagicMock()))

    def test_resolved_uti_no_datasource(self) -> None:
        """Without a datasource there is no UTI to resolve."""
        self.assertIsNone(AttachmentRef(identifier="att-1").resolved_uti(None))


# ---------------------------------------------------------------------------
# attachments.py
# ---------------------------------------------------------------------------


class AttachmentRendererCoverageTest(unittest.TestCase):
    """Branches in ``attachments.py`` renderers."""

    def test_base_attrs_with_and_without_extra(self) -> None:
        """``base_attrs`` merges nothing when no extras are supplied."""
        ctx = AttachmentContext(
            id="a",
            uti="u",
            title=None,
            primary_url=None,
            thumb_url=None,
            mergeable_gz=None,
        )
        self.assertEqual(
            ctx.base_attrs(), {"class": "attachment", "data-uti": "u", "data-id": "a"}
        )
        self.assertEqual(
            ctx.base_attrs({"class": "attachment image"}),
            {"class": "attachment image", "data-uti": "u", "data-id": "a"},
        )

    def test_safe_url_scheme_netloc_edges(self) -> None:
        """Schemeless, netloc-less and empty URLs are rejected."""
        # pylint: disable=import-outside-toplevel,invalid-name
        from pyicloud.services.notes.rendering.attachments import _safe_url

        self.assertIsNone(_safe_url("", allowed_schemes={"https"}))
        self.assertIsNone(_safe_url("https://", allowed_schemes={"https"}))
        self.assertIsNone(_safe_url("mailto:", allowed_schemes={"mailto"}))
        self.assertEqual(
            _safe_url("assets/x.bin", allowed_schemes={"https"}),
            "assets/x.bin",
        )

    def test_image_renderer_uses_primary_url(self) -> None:
        """Image renderers embed the primary asset URL."""
        html = render_attachment(
            AttachmentContext(
                id="i",
                uti="public.image",
                title="Alt",
                primary_url="https://x/i.png",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('src="https://x/i.png"', html)
        self.assertIn('alt="Alt"', html)

    def test_table_renderer_renders_mergeable(self) -> None:
        """Table renderer embeds the HTML table when mergeable bytes exist."""
        with patch(
            "pyicloud.services.notes.rendering.attachments.render_table_from_mergeable",
            return_value="<table><tr><td>x</td></tr></table>",
        ) as mock_table:
            html = render_attachment(
                AttachmentContext(
                    id="t",
                    uti="com.apple.notes.table",
                    title="Tbl",
                    primary_url=None,
                    thumb_url=None,
                    mergeable_gz=b"gz",
                ),
                lambda _: "",
            )
        self.assertEqual(html, "<table><tr><td>x</td></tr></table>")
        mock_table.assert_called_once_with(b"gz", unittest.mock.ANY)

    def test_table_renderer_falls_back_to_link(self) -> None:
        """Table renderer falls back to a link when mergeable rendering is empty."""
        with patch(
            "pyicloud.services.notes.rendering.attachments.render_table_from_mergeable",
            return_value=None,
        ):
            html = render_attachment(
                AttachmentContext(
                    id="t",
                    uti="com.apple.notes.table",
                    title="Tbl",
                    primary_url=None,
                    thumb_url=None,
                    mergeable_gz=b"gz",
                ),
                lambda _: "",
            )
        self.assertIn('class="attachment link"', html)
        self.assertIn("Tbl", html)

    def test_audio_renderer_with_url(self) -> None:
        """Audio renderers emit a controls element when a URL exists."""
        html = render_attachment(
            AttachmentContext(
                id="a",
                uti="com.apple.m4a-audio",
                title=None,
                primary_url="https://x/a.m4a",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn("<audio controls", html)
        self.assertIn('src="https://x/a.m4a"', html)

    def test_video_renderer_with_url(self) -> None:
        """Video renderers emit a controls element when a URL exists."""
        html = render_attachment(
            AttachmentContext(
                id="v",
                uti="public.movie",
                title=None,
                primary_url="https://x/v.mp4",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn("<video", html)
        self.assertIn('src="https://x/v.mp4"', html)

    def test_video_renderer_fallback_without_url(self) -> None:
        """Video renderers fall back to a link without a URL."""
        html = render_attachment(
            AttachmentContext(
                id="v",
                uti="public.video",
                title="Clip",
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('class="attachment link"', html)
        self.assertIn("Clip", html)

    def test_pdf_renderer_embeds_local_pdf(self) -> None:
        """Local PDFs are embedded in an object with a configurable height."""
        html = render_attachment(
            AttachmentContext(
                id="p",
                uti="com.adobe.pdf",
                title="Doc",
                primary_url="assets/doc.pdf",
                thumb_url=None,
                mergeable_gz=None,
                pdf_object_height=800,
            ),
            lambda _: "",
        )
        self.assertIn("<object", html)
        self.assertIn('data="assets/doc.pdf"', html)
        self.assertIn("height:800px", html)

    def test_pdf_renderer_default_height(self) -> None:
        """PDF embeds default to 600px unless configured otherwise."""
        html = render_attachment(
            AttachmentContext(
                id="p",
                uti="public.pdf",
                title="Doc",
                primary_url="assets/doc.pdf",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn("height:600px", html)

    def test_pdf_renderer_remote_url_links(self) -> None:
        """Remote PDF URLs render as download links instead of embeds."""
        html = render_attachment(
            AttachmentContext(
                id="p",
                uti="public.pdf",
                title="Doc",
                primary_url="https://x/doc.pdf",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('class="attachment file"', html)
        self.assertIn('href="https://x/doc.pdf"', html)

    def test_pdf_renderer_without_url(self) -> None:
        """PDFs without a URL render a plain link."""
        html = render_attachment(
            AttachmentContext(
                id="p",
                uti="public.pdf",
                title="Doc",
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('class="attachment file"', html)
        self.assertIn("Doc", html)

    def test_vcard_renderer_with_href(self) -> None:
        """Contacts with a URL get an href."""
        html = render_attachment(
            AttachmentContext(
                id="c",
                uti="public.vcard",
                title="Jane",
                primary_url="https://x/j.vcf",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('href="https://x/j.vcf"', html)
        self.assertIn('class="attachment contact"', html)

    def test_vcard_renderer_without_href(self) -> None:
        """Contacts without a URL render a plain link."""
        html = render_attachment(
            AttachmentContext(
                id="c",
                uti="public.vcard",
                title=None,
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn('class="attachment contact"', html)
        self.assertIn("contact", html)

    def test_hashtag_renderer_keeps_existing_prefix(self) -> None:
        """Hashtags with a leading ``#`` are not double-prefixed."""
        html = render_attachment(
            AttachmentContext(
                id="h",
                uti="com.apple.notes.inlinetextattachment.hashtag",
                title="#tag",
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn(">#tag<", html)
        self.assertIn('data-tag="tag"', html)

    def test_hashtag_renderer_adds_prefix_and_fallback(self) -> None:
        """Hashtags without a prefix gain one; missing titles fall back to the UTI."""
        html = render_attachment(
            AttachmentContext(
                id="h",
                uti="com.apple.notes.inlinetextattachment.hashtag",
                title="tag",
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn(">#tag<", html)

        fallback = render_attachment(
            AttachmentContext(
                id="h",
                uti="com.apple.notes.inlinetextattachment.hashtag",
                title=None,
                primary_url=None,
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )
        self.assertIn("hashtag", fallback)


# ---------------------------------------------------------------------------
# renderer.py helpers
# ---------------------------------------------------------------------------


class RendererHelperCoverageTest(unittest.TestCase):
    """Branches in the renderer's pure helpers."""

    def test_safe_anchor_href_edge_cases(self) -> None:
        """Whitespace-only, netloc-less and path-less URLs are rejected."""
        self.assertIsNone(_safe_anchor_href("   "))
        self.assertIsNone(_safe_anchor_href("https://"))
        self.assertIsNone(_safe_anchor_href("mailto:"))

    def test_slice_for_run_with_astral_chars(self) -> None:
        """Astral characters extend the slice to real character boundaries."""
        text = "a\U0001f600bcd"
        chunk, end = _slice_for_run(text, 0, 3)
        # length_units are UTF-16 code units; the astral char counts twice.
        self.assertEqual(end, 4)
        self.assertEqual(chunk, text[:4])

    def test_css_font_stack_generic_fallbacks(self) -> None:
        """Unknown font names map to generic families by keyword."""
        # pylint: disable=import-outside-toplevel,invalid-name
        from pyicloud.services.notes.rendering.renderer import _css_font_stack

        self.assertIn("monospace", _css_font_stack("MyMonoFont"))
        self.assertIn("monospace", _css_font_stack("CourierCode"))
        self.assertIn("serif", _css_font_stack("Georgia"))
        self.assertIn("serif", _css_font_stack("TimesLike"))
        self.assertIn("cursive", _css_font_stack("ChalkboardHand"))
        self.assertIn("sans-serif", _css_font_stack("AvenirNext"))
        self.assertIn('"', _css_font_stack("Bob's Font"))


class StyleSigCoverageTest(unittest.TestCase):
    """Direct unit tests for ``StyleSig`` semantics."""

    def test_from_run_color_hex(self) -> None:
        """A run with a color produces a hex signature."""
        note = _note("abc", [{"length": 3, "color": (1.0, 0.5, 0.25)}])
        sig = _sig_from_run(note.attribute_run[0])
        self.assertEqual(sig.color_hex, "#FF8040")

    def test_from_run_negative_indent_clamped(self) -> None:
        """Negative indent amounts are clamped to zero."""
        note = _note(
            "abc",
            [{"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": -2}],
        )
        sig = _sig_from_run(note.attribute_run[0])
        self.assertEqual(sig.indent_amount, 0)

    def test_from_run_styles_and_start_number(self) -> None:
        """Style, alignment, direction and starting list number are extracted."""
        note = _note(
            "abc",
            [
                {
                    "length": 3,
                    "style": STM.STYLE_TYPE_NUMBERED_LIST_ITEM,
                    "start_number": 7,
                    "wd": 2,
                }
            ],
        )
        sig = _sig_from_run(note.attribute_run[0])
        self.assertEqual(sig.style_type, STM.STYLE_TYPE_NUMBERED_LIST_ITEM)
        self.assertEqual(sig.start_number, 7)
        self.assertEqual(sig.writing_direction, 2)

    def test_from_run_paragraph_without_style(self) -> None:
        """A paragraph with only alignment has no style_type and no checklist."""
        note = _note("abc", [{"length": 3, "align": 1}])
        sig = _sig_from_run(note.attribute_run[0])
        self.assertIsNone(sig.style_type)
        self.assertIsNone(sig.checklist_done)
        self.assertIsNone(sig.start_number)

    def test_from_run_defensive_presence_errors(self) -> None:
        """Presence checks that raise degrade gracefully (defensive paths)."""
        run = MagicMock()
        run.HasField.return_value = True
        ps = run.paragraph_style
        ps.HasField.side_effect = RuntimeError("boom")
        sig = StyleSig.from_run(run)
        self.assertIsNone(sig.style_type)
        self.assertIsNone(sig.checklist_done)

    def test_same_paragraph_differing_uuids(self) -> None:
        """Differing paragraph UUIDs mark runs as separate paragraphs."""
        left = _sig(paragraph_uuid=b"a")
        right = _sig(paragraph_uuid=b"b")
        self.assertFalse(left.same_paragraph_as(right))

    def test_same_paragraph_list_vs_neutral(self) -> None:
        """Neutral runs are absorbed into an active list paragraph."""
        left = _sig(style_type=STM.STYLE_TYPE_BULLET_LIST_ITEM, indent_amount=0)
        neutral = _sig()
        self.assertTrue(left.same_paragraph_as(neutral))


# ---------------------------------------------------------------------------
# renderer.py end-to-end branches
# ---------------------------------------------------------------------------


class NoteRenderingBranchCoverageTest(unittest.TestCase):
    """End-to-end branches exercised through ``render_note_fragment``."""

    def test_simple_list_with_siblings(self) -> None:
        """Multi-line list runs render as sibling list items."""
        html = _render(
            "one\ntwo",
            [{"length": 7, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0}],
        )
        self.assertEqual(html, "<ul><li>one</li><li>two</li></ul>")

    def test_nested_list_deeper_indent(self) -> None:
        """A deeper-indented item nests inside the active list item."""
        html = _render(
            "onetwo",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 1},
            ],
        )
        self.assertIn("<ul><li>one", html)

    def test_indent_jump_opens_spacer_item(self) -> None:
        """An indent jump from 0 to 2 opens an intermediate spacer item."""
        html = _render(
            "onetwo",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 2},
            ],
        )
        self.assertIn("<li><ul>", html)

    def test_shallower_item_closes_open_list(self) -> None:
        """Returning to a shallower level closes the nested item."""
        html = _render(
            "onetwothree",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 1},
                {"length": 5, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertIn("</ul></li><li>", html)

    def test_neutral_run_renders_inside_list_item(self) -> None:
        """A neutral run sharing a list paragraph renders within the item."""
        html = _render(
            "onetwo",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3},
            ],
        )
        self.assertIn("<li>one", html)
        self.assertIn("two", html)

    def test_deeper_list_after_neutral_run(self) -> None:
        """A deeper list opened after a neutral run gets a fresh item."""
        html = _render(
            "onetwothree",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3},
                {"length": 5, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 1},
            ],
        )
        self.assertIn("three", html)

    def test_empty_checkbox_item_cleanup(self) -> None:
        """An empty checkbox item is dropped without stray markup."""
        html = _render(
            "one",
            [
                {
                    "length": 0,
                    "style": STM.STYLE_TYPE_CHECKLIST_ITEM,
                    "indent": 0,
                    "checklist_done": 1,
                },
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertNotIn('<input type="checkbox"', html)
        self.assertIn("<li>", html)

    def test_empty_bullet_item_cleanup(self) -> None:
        """An empty bullet item is dropped entirely."""
        html = _render(
            "abc",
            [
                {"length": 0, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertIn("<li>abc</li>", html)

    def test_trailing_br_trimmed_in_list_item(self) -> None:
        """Trailing breaks are trimmed before closing a content list item."""
        html = _render(
            "onetwo\n",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 4},
            ],
        )
        self.assertNotIn("<br></li>", html)

    def test_spacer_item_replaced_by_content(self) -> None:
        """A trailing blank-line spacer is replaced once real content arrives."""
        html = _render(
            "a\n\nb",
            [
                {"length": 4, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertIn("b", html)

    def test_spacer_item_keeps_height_when_empty(self) -> None:
        """An empty spacer keeps a non-breaking space for height."""
        html = _render(
            "a\n\n",
            [
                {"length": 3, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 1, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertIn("&nbsp;", html)

    def test_checkbox_items(self) -> None:
        """Checkbox paragraphs emit disabled checkboxes per item."""
        html = _render(
            "x\ny",
            [
                {
                    "length": 3,
                    "style": STM.STYLE_TYPE_CHECKLIST_ITEM,
                    "indent": 0,
                    "checklist_done": 1,
                },
            ],
        )
        self.assertEqual(
            html,
            '<ul><li><input type="checkbox" disabled checked> x</li>'
            '<li><input type="checkbox" disabled checked> y</li></ul>',
        )

    def test_numbered_list_start(self) -> None:
        """Ordered lists carry their starting number."""
        html = _render(
            "one\ntwo",
            [
                {
                    "length": 7,
                    "style": STM.STYLE_TYPE_NUMBERED_LIST_ITEM,
                    "indent": 0,
                    "start_number": 5,
                },
            ],
        )
        self.assertIn("start=", html)

    def test_dashed_list_class(self) -> None:
        """Dashed lists get a marker class."""
        html = _render(
            "one",
            [
                {"length": 3, "style": STM.STYLE_TYPE_DASHED_LIST_ITEM, "indent": 0},
            ],
        )
        self.assertIn('class="dashed"', html)

    def test_heading_styles(self) -> None:
        """Title, heading, subheading and monospaced styles wrap accordingly."""
        self.assertIn(
            "<h1>", _render("abc", [{"length": 3, "style": STM.STYLE_TYPE_TITLE}])
        )
        self.assertIn(
            "<h2>", _render("abc", [{"length": 3, "style": STM.STYLE_TYPE_HEADING}])
        )
        self.assertIn(
            "<h3>", _render("abc", [{"length": 3, "style": STM.STYLE_TYPE_SUBHEADING}])
        )
        self.assertIn(
            "<pre>", _render("abc", [{"length": 3, "style": STM.STYLE_TYPE_MONOSPACED}])
        )

    def test_blockquote_plain_paragraph(self) -> None:
        """A blockquoted plain paragraph renders as a blockquote."""
        html = _render("abc", [{"length": 3, "block_quote": 1}])
        self.assertIn("<blockquote>", html)

    def test_alignments(self) -> None:
        """Alignment values map to text-align styles."""
        html = _render("abc", [{"length": 3, "align": 1}])
        self.assertIn("text-align:center", html)
        html = _render("abc", [{"length": 3, "align": 2}])
        self.assertIn("text-align:right", html)
        html = _render("abc", [{"length": 3, "align": 3}])
        self.assertIn("text-align:justify", html)

    def test_writing_directions(self) -> None:
        """RTL and LTR directions emit dir attributes."""
        html = _render("abc", [{"length": 3, "wd": 2}])
        self.assertIn(' dir="rtl"', html)
        html = _render("abc", [{"length": 3, "wd": 1}])
        self.assertIn(' dir="ltr"', html)

    def test_inline_styles(self) -> None:
        """Bold, italic, underline, strike, super and sub wrap correctly."""
        html = _render("abc", [{"length": 3, "bold": 3}])
        self.assertIn("font-weight:bold", html)
        self.assertIn("font-style:italic", html)
        html = _render("abc", [{"length": 3, "underlined": 1, "strikethrough": 1}])
        self.assertIn("text-decoration:underline line-through", html)
        self.assertIn("<sup>", _render("abc", [{"length": 3, "superscript": 1}]))
        self.assertIn("<sub>", _render("abc", [{"length": 3, "superscript": -1}]))

    def test_inline_color_and_font(self) -> None:
        """Color, font name and size surface in inline styles."""
        html = _render("abc", [{"length": 3, "color": (1.0, 0.0, 0.0)}])
        self.assertIn("color:#FF0000", html)
        html = _render(
            "abc", [{"length": 3, "font_name": "Comic Sans MS", "font_size": 18.0}]
        )
        self.assertIn('font-family:"Comic Sans MS", "Comic Sans"', html)
        self.assertIn("font-size:18pt", html)

    def test_emphasis_and_highlight_palette(self) -> None:
        """Highlight palette indices map to CSS variables."""
        html = _render("abc", [{"length": 3, "emphasis": 3}])
        self.assertIn("background-color:var(--hl3-bg)", html)
        html = _render("abc", [{"length": 3, "highlight": 4}])
        self.assertIn("background-color:var(--hl4-bg)", html)
        html = _render("abc", [{"length": 3, "highlight": 9}])
        self.assertNotIn("--hl9-", html)

    def test_link_with_config_overrides(self) -> None:
        """Config link_rel and referrer_policy override the defaults."""
        config = ExportConfig(link_rel="noopener", referrer_policy="strict-origin")
        html = _render(
            "abc", [{"length": 3, "link": "https://example.com"}], config=config
        )
        self.assertIn('rel="noopener"', html)
        self.assertIn('referrerpolicy="strict-origin"', html)

    def test_link_with_raising_config(self) -> None:
        """Config attribute errors degrade to the default link attributes."""

        class _RaisingConfig:
            """A config whose properties raise on access."""

            @property
            def link_rel(self) -> str:
                """Raise to exercise the defensive fallback."""
                raise RuntimeError("boom")

            @property
            def referrer_policy(self) -> str:
                """Raise to exercise the defensive fallback."""
                raise RuntimeError("boom")

        html = _render(
            "abc",
            [{"length": 3, "link": "https://example.com"}],
            config=cast(Any, _RaisingConfig()),
        )
        self.assertIn('rel="noopener noreferrer"', html)

    def test_blank_lines_produce_breaks(self) -> None:
        """Blank runs accumulate breaks flushed before the next paragraph."""
        html = _render(
            "\n  hi\n\tnext",
            [
                {"length": 1, "style": STM.STYLE_TYPE_TITLE},
                {"length": 9},
            ],
        )
        self.assertIn("<br>", html)
        self.assertIn("&nbsp;&nbsp;hi", html)
        self.assertIn("&nbsp;&nbsp;&nbsp;&nbsp;nex", html)

    def test_attachment_with_and_without_datasource(self) -> None:
        """Attachments render with metadata from a datasource or with defaults."""
        ds = MagicMock()
        ds.get_title.return_value = "Pic"
        ds.get_primary_asset_url.return_value = "https://x/pic.png"
        ds.get_thumbnail_url.return_value = None
        ds.get_mergeable_gz.return_value = None
        html = _render(
            "a",
            [
                {"length": 1, "attachment": ("att-1", "public.image")},
            ],
            ds=ds,
        )
        self.assertIn('src="https://x/pic.png"', html)
        ds.get_title.assert_called_once_with("att-1")

        plain = _render("a", [{"length": 1, "attachment": ("att-1", "public.image")}])
        self.assertIn("data-id=", plain)

    def test_attachment_inside_list_item_marks_content(self) -> None:
        """Attachments inside list items mark the item as having content."""
        html = _render(
            "ab",
            [
                {"length": 1, "style": STM.STYLE_TYPE_BULLET_LIST_ITEM, "indent": 0},
                {"length": 1, "attachment": ("att-1", "public.image")},
            ],
        )
        self.assertIn("<li>a", html)

    def test_render_note_page_and_full_page(self) -> None:
        """The page wrapper and full-page renderer produce a complete page."""
        renderer = NoteRenderer()
        page = renderer.render_full_page("Title", "<p>x</p>")
        self.assertIn("<!doctype html>", page)
        self.assertIn("<title>Title</title>", page)
        self.assertIn("<p>x</p>", page)
        fragment = renderer.render(pb.Note())
        self.assertEqual(fragment, "")


# ---------------------------------------------------------------------------
# client.py branches
# ---------------------------------------------------------------------------


class CloudKitNotesClientCoverageTest(unittest.TestCase):
    """Branches in the low-level CloudKit notes client."""

    def _client(self) -> tuple[CloudKitNotesClient, MagicMock]:
        """Build a client whose backend container client is a mock."""
        client = CloudKitNotesClient("https://example.com", MagicMock(), {})
        inner = MagicMock()
        client._client = cast(Any, inner)
        return client, inner

    def _validation_cause(self) -> ValidationError:
        """Construct a pydantic ValidationError usable as a ``__cause__``."""
        try:
            CKQueryResponse.model_validate(
                {"records": [], "unexpectedTopLevel": 1},
                extra="forbid",
            )
        except ValidationError as exc:
            return exc
        raise AssertionError("unreachable")

    def _api_error_with_cause(self, message: str = "bad") -> CloudKitApiError:
        """An API error chained on a validation failure."""
        exc = CloudKitApiError(message, payload={"x": 1})
        exc.__cause__ = self._validation_cause()
        return exc

    # ---- error translation ----

    def test_raise_notes_error_translations(self) -> None:
        """Each backend error class maps to its notes counterpart."""
        auth_error = CloudKitAuthError("a")
        with self.assertRaises(NotesAuthError):
            CloudKitNotesClient._raise_notes_error(auth_error)

        rate_limited = CloudKitRateLimited("r", retry_after=1.5)
        with self.assertRaises(NotesRateLimited) as rate_ctx:
            CloudKitNotesClient._raise_notes_error(rate_limited)
        self.assertEqual(rate_ctx.exception.retry_after, 1.5)

        api_error = CloudKitApiError("api", payload={"err": 1})
        with self.assertRaises(NotesApiError) as api_ctx:
            CloudKitNotesClient._raise_notes_error(api_error)
        self.assertEqual(api_ctx.exception.payload, {"err": 1})

        plain_error = ValueError("plain")
        with self.assertRaises(ValueError):
            CloudKitNotesClient._raise_notes_error(plain_error)

    def test_query_success(self) -> None:
        """A successful backend query returns the typed response."""
        client, inner = self._client()
        response = CKQueryResponse(records=[])
        inner.query.return_value = response

        out = client.query(
            query=CKQueryObject(recordType="SearchIndexes"),
            zone_id=CKZoneIDReq(zoneName="Notes"),
        )

        self.assertIs(out, response)

    def test_query_validation_error_logs_and_raises(self) -> None:
        """A validation-backed API error logs the failure and raises NotesApiError."""
        client, inner = self._client()
        inner.query.side_effect = self._api_error_with_cause()

        query = CKQueryObject(recordType="SearchIndexes")
        zone_id = CKZoneIDReq(zoneName="Notes")
        with self.assertRaises(NotesApiError) as ctx:
            client.query(query=query, zone_id=zone_id)

        self.assertEqual(ctx.exception.payload, {"x": 1})

    def test_query_translations(self) -> None:
        """Query errors translate auth, rate-limit and API failures."""
        client, inner = self._client()
        inner.query.side_effect = CloudKitAuthError("no")
        query = CKQueryObject(recordType="SearchIndexes")
        zone_id = CKZoneIDReq(zoneName="Notes")
        with self.assertRaises(NotesAuthError):
            client.query(query=query, zone_id=zone_id)

        client, inner = self._client()
        inner.query.side_effect = CloudKitRateLimited("slow", retry_after=2.5)
        query = CKQueryObject(recordType="SearchIndexes")
        zone_id = CKZoneIDReq(zoneName="Notes")
        with self.assertRaises(NotesRateLimited):
            client.query(query=query, zone_id=zone_id)

        client, inner = self._client()
        inner.query.side_effect = CloudKitApiError("server", payload="oops")
        query = CKQueryObject(recordType="SearchIndexes")
        zone_id = CKZoneIDReq(zoneName="Notes")
        with self.assertRaises(NotesApiError) as ctx:
            client.query(query=query, zone_id=zone_id)
        cause = ctx.exception.__cause__
        self.assertIsNotNone(cause)
        assert cause is not None
        self.assertIsNone(cause.__cause__)

    def test_lookup_validation_and_translations(self) -> None:
        """Lookup errors follow the same translation as query errors."""
        client, inner = self._client()
        inner.lookup.side_effect = self._api_error_with_cause()
        with self.assertRaises(NotesApiError):
            client.lookup(["Note/1"])

        client, inner = self._client()
        inner.lookup.side_effect = CloudKitAuthError("no")
        with self.assertRaises(NotesAuthError):
            client.lookup(["Note/1"])

    # ---- changes generator ----

    def test_changes_yields_paged_zones(self) -> None:
        """``changes`` yields every zone page with logging branches."""
        first = CKZoneChangesZone(
            zoneID=CKZoneID(zoneName="Notes"),
            syncToken="t1",
            moreComing=True,
        )
        last = CKZoneChangesZone(
            zoneID=CKZoneID(zoneName="Notes"),
            syncToken="t2",
            moreComing=False,
        )
        client, inner = self._client()
        inner.iter_changes.return_value = iter([first, last])

        zones = list(
            client.changes(
                zone_req=CKZoneChangesZoneReq(zoneID=CKZoneID(zoneName="Notes"))
            )
        )

        self.assertEqual(zones, [first, last])

    def test_changes_error_translations(self) -> None:
        """``changes`` translates backend failures like the other methods."""
        client, inner = self._client()
        inner.iter_changes.side_effect = self._api_error_with_cause()
        zone_req = CKZoneChangesZoneReq(zoneID=CKZoneID(zoneName="Notes"))
        changes = client.changes(zone_req=zone_req)
        with self.assertRaises(NotesApiError):
            list(changes)

        client, inner = self._client()
        inner.iter_changes.side_effect = CloudKitAuthError("no")
        zone_req = CKZoneChangesZoneReq(zoneID=CKZoneID(zoneName="Notes"))
        changes = client.changes(zone_req=zone_req)
        with self.assertRaises(NotesAuthError):
            list(changes)

    # ---- asset helpers ----

    def test_download_asset_to_write_chunks(self) -> None:
        """``download_asset_to`` streams chunks into a new file."""
        client, _ = self._client()
        m = mock_open()
        with (
            patch.object(
                client, "download_asset_stream", return_value=iter([b"a", b"b"])
            ),
            patch("pyicloud.services.notes.client.os.makedirs") as makedirs,
            patch("pyicloud.services.notes.client.open", m),
        ):
            path = client.download_asset_to("https://x/asset", "some/dir")

        makedirs.assert_called_once_with("some/dir", exist_ok=True)
        self.assertTrue(path.startswith("some/dir/icloud-asset-"))
        handle = m()
        handle.write.assert_any_call(b"a")
        handle.write.assert_any_call(b"b")

    def test_download_asset_to_zero_chunks(self) -> None:
        """``download_asset_to`` still finishes when there are no chunks."""
        client, _ = self._client()
        with (
            patch.object(client, "download_asset_stream", return_value=iter([])),
            patch("pyicloud.services.notes.client.os.makedirs"),
            patch("pyicloud.services.notes.client.open", mock_open()),
        ):
            path = client.download_asset_to("https://x/asset", "some/dir")
        self.assertTrue(path.startswith("some/dir/icloud-asset-"))

    # ---- sync tokens ----

    def test_current_sync_token_from_query(self) -> None:
        """A token returned by the cheap query is used directly."""
        client, inner = self._client()
        inner.query_sync_token.return_value = "query-token"

        token = client.current_sync_token(zone_name="Notes")

        self.assertEqual(token, "query-token")

    def test_current_sync_token_query_failure_falls_back(self) -> None:
        """A failing query falls back to the changes endpoint."""
        client, inner = self._client()
        inner.query_sync_token.side_effect = RuntimeError("boom")
        env = SimpleNamespace(zones=[SimpleNamespace(syncToken="changes-token")])
        inner.changes.return_value = env

        token = client.current_sync_token(zone_name="Notes")

        self.assertEqual(token, "changes-token")

    def test_current_sync_token_changes_failure(self) -> None:
        """A failing changes fallback raises NotesApiError."""
        client, inner = self._client()
        inner.query_sync_token.return_value = None
        inner.changes.side_effect = CloudKitApiError("slow", payload={})

        with self.assertRaises(NotesApiError):
            client.current_sync_token(zone_name="Notes")

    def test_current_sync_token_unable_to_obtain(self) -> None:
        """Missing zones and tokens raise ``Unable to obtain sync token``."""
        client, inner = self._client()
        inner.query_sync_token.return_value = None
        inner.changes.return_value = SimpleNamespace(zones=[])

        with self.assertRaisesRegex(NotesApiError, "Unable to obtain sync token"):
            client.current_sync_token(zone_name="Notes")

        client, inner = self._client()
        inner.query_sync_token.return_value = None
        inner.changes.return_value = SimpleNamespace(
            zones=[SimpleNamespace(syncToken=None)]
        )
        with self.assertRaisesRegex(NotesApiError, "Unable to obtain sync token"):
            client.current_sync_token(zone_name="Notes")

    # ---- debug helpers ----

    def test_dump_http_debug_makedirs_failure(self) -> None:
        """A failing makedirs aborts the dump without raising."""
        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch(
                "pyicloud.services.notes.client.os.makedirs",
                side_effect=OSError("no space"),
            ),
            patch("pyicloud.services.notes.client.open", mock_open()),
        ):
            CloudKitNotesClient._dump_http_debug(
                "records/query", "https://x", {"k": "v"}, MagicMock()
            )

    def test_dump_http_debug_write_failures(self) -> None:
        """Dump write failures are swallowed."""
        m = mock_open()
        m().write.side_effect = OSError("disk full")
        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch("pyicloud.services.notes.client.os.makedirs"),
            patch("pyicloud.services.notes.client.open", m),
            patch(
                "pyicloud.services.notes.client.json.dump", side_effect=OSError("boom")
            ),
        ):
            CloudKitNotesClient._dump_http_debug(
                "records/query", "https://x", {"k": "v"}, MagicMock()
            )

    def test_dump_http_debug_truncates_large_body(self) -> None:
        """Oversized response bodies are truncated with a marker."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.text = "x" * 100
        with (
            patch.dict(
                os.environ,
                {"PYICLOUD_NOTES_DEBUG": "1", "PYICLOUD_DEBUG_MAX_BYTES": "10"},
                clear=False,
            ),
            patch("pyicloud.services.notes.client.os.makedirs"),
            patch("pyicloud.services.notes.client.open", mock_open()),
        ):
            CloudKitNotesClient._dump_http_debug("records/query", "https://x", {}, resp)

    def test_dump_http_debug_missing_text(self) -> None:
        """A response without readable text dumps headers only."""
        resp = MagicMock()
        resp.status_code = 204
        resp.headers = {}
        type(resp).text = PropertyMock(
            side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        )
        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch("pyicloud.services.notes.client.os.makedirs"),
            patch("pyicloud.services.notes.client.open", mock_open()),
        ):
            CloudKitNotesClient._dump_http_debug("records/query", "https://x", {}, resp)

    def test_log_validation_failures(self) -> None:
        """Validation dump failures are swallowed and makedirs aborts."""
        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch(
                "pyicloud.services.notes.client.os.makedirs",
                side_effect=OSError("no"),
            ),
            patch("pyicloud.services.notes.client.open", mock_open()),
        ):
            CloudKitNotesClient._log_validation(
                "records.query", {}, self._validation_cause()
            )

        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch("pyicloud.services.notes.client.os.makedirs"),
            patch("pyicloud.services.notes.client.open", mock_open()),
            patch(
                "pyicloud.services.notes.client.json.dump",
                side_effect=OSError("boom"),
            ),
        ):
            CloudKitNotesClient._log_validation(
                "records.query", {}, self._validation_cause()
            )


if __name__ == "__main__":
    unittest.main()
