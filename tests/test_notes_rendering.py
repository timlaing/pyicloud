import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from pyicloud.services.notes.rendering.attachments import (
    AttachmentContext,
    _safe_url,
    render_attachment,
)
from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
from pyicloud.services.notes.rendering.exporter import (
    NoteExporter,
    decode_and_parse_note,
    download_image_assets,
)
from pyicloud.services.notes.rendering.options import ExportConfig
from pyicloud.services.notes.rendering.renderer import NoteRenderer, _safe_anchor_href
from pyicloud.services.notes.rendering.table_builder import (
    TableBuilder,
    render_table_from_mergeable,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "note_fixture.json")
with open(FIXTURE_PATH, "r", encoding="utf-8") as fixture_file:
    NOTE_FIXTURE = json.load(fixture_file)


class _Field:
    def __init__(self, value):
        self.value = value


class _Fields:
    def __init__(self, values):
        self.values = values

    def get_value(self, key):
        return self.values.get(key)

    def get_field(self, key):
        if key not in self.values:
            return None
        return _Field(self.values[key])


class _Record:
    def __init__(self, record_name, fields):
        self.recordName = record_name
        self.recordType = "Attachment"
        self.fields = _Fields(fields)


FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "note_fixture.json")
with open(FIXTURE_PATH, "r", encoding="utf-8") as fixture_file:
    NOTE_FIXTURE = json.load(fixture_file)


class _Field:
    def __init__(self, value):
        self.value = value


class _Fields:
    def __init__(self, values):
        self.values = values

    def get_value(self, key):
        return self.values.get(key)

    def get_field(self, key):
        if key not in self.values:
            return None
        return _Field(self.values[key])


class _Record:
    def __init__(self, record_name, fields):
        self.recordName = record_name
        self.recordType = "Attachment"
        self.fields = _Fields(fields)


class TestNoteRendering(unittest.TestCase):
    def setUp(self):
        self.fixture = NOTE_FIXTURE

    def _reconstruct_record(self, data):
        # Helper to rebuild a pseudo-CKRecord from the JSON dict
        # We need to minimally satisfy what build_datasource expects (fields.get_value)
        class MockFields:
            def __init__(self, fields_dict):
                self.d = fields_dict

            def get_value(self, key):
                val = self.d.get(key)
                if isinstance(val, dict) and "__bytes__" in val:
                    import base64

                    return base64.b64decode(val["__bytes__"])
                return val

            def get_field(self, key):
                # Needed for some checks like Attachments
                # For references, we might need more complex reconstruction if the code checks types
                # But let's start simple.
                return None

        rec = Mock()
        rec.recordName = data["recordName"]
        rec.recordType = data["recordType"]
        rec.fields = MockFields(data["fields"])
        return rec

    def test_render_fixture_output(self):
        """Ensure the fixture note renders to HTML without crashing."""
        note_data = self.fixture["note"]
        note_rec = self._reconstruct_record(note_data)

        # Manual decode to skip isinstance check causing issues with simple mocks
        from pyicloud.services.notes.decoding import BodyDecoder
        from pyicloud.services.notes.protobuf import notes_pb2

        raw_cypher = note_rec.fields.get_value("TextDataEncrypted")
        nb = BodyDecoder().decode(raw_cypher)
        self.assertIsNotNone(nb, "Failed to BodyDecoder.decode fixture data")

        msg = notes_pb2.NoteStoreProto()
        msg.ParseFromString(nb.bytes)
        note = getattr(getattr(msg, "document", None), "note", None)

        # Mock datasource hydration
        # We manually populate the datasource with the attachment records from the fixture
        ds = CloudKitNoteDataSource()
        att_data_list = self.fixture["attachments"]
        for att_data in att_data_list:
            att_rec = self._reconstruct_record(att_data)
            ds.add_attachment_record(att_rec)

        renderer = NoteRenderer()
        html = renderer.render(note, datasource=ds)

        # Verify basic structure
        self.assertIn(
            "checklist",
            html.lower(),
            "Should contain checkbox logic if note has checklist",
        )
        # The test note had "pyicloud notes service test" in title, likely not in body.
        # But we expect SOME content.
        self.assertTrue(len(html) > 0)

        print("\n--- Rendered HTML Preview (First 500 chars) ---")
        print(html[:500])
        print("-----------------------------------------------")

    def test_public_url_attachment_keeps_useful_title_and_href(self):
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _Record(
                "url-1",
                {
                    "UTI": "public.url",
                    "SummaryEncrypted": b"Discord Notes Link",
                    "URLStringEncrypted": b"https://discord.example.com/channel/1",
                },
            )
        )

        html = render_attachment(
            AttachmentContext(
                id="url-1",
                uti=ds.get_attachment_uti("url-1") or "",
                title=ds.get_title("url-1"),
                primary_url=ds.get_primary_asset_url("url-1"),
                thumb_url=ds.get_thumbnail_url("url-1"),
                mergeable_gz=ds.get_mergeable_gz("url-1"),
            ),
            lambda _: "",
        )

        self.assertIn("Discord Notes Link", html)
        self.assertIn('href="https://discord.example.com/channel/1"', html)

    def test_image_attachment_does_not_use_signed_url_as_alt_text(self):
        signed_url = "https://cvws.icloud-content.com/B/example-signed-asset"
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _Record(
                "img-1",
                {
                    "UTI": "com.apple.paper",
                    "PreviewImages": [SimpleNamespace(downloadURL=signed_url)],
                },
            )
        )

        self.assertIsNone(ds.get_title("img-1"))

        html = render_attachment(
            AttachmentContext(
                id="img-1",
                uti=ds.get_attachment_uti("img-1") or "",
                title=ds.get_title("img-1"),
                primary_url=ds.get_primary_asset_url("img-1"),
                thumb_url=ds.get_thumbnail_url("img-1"),
                mergeable_gz=ds.get_mergeable_gz("img-1"),
            ),
            lambda _: "",
        )

        self.assertIn(f'src="{signed_url}"', html)
        self.assertNotIn(f'alt="{signed_url}"', html)

    def test_default_renderer_keeps_safe_relative_href(self):
        html = render_attachment(
            AttachmentContext(
                id="att-1",
                uti="com.example.unknown",
                title="Attachment",
                primary_url="assets/note/file.bin",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )

        self.assertIn('href="assets/note/file.bin"', html)

    def test_url_renderer_rejects_unsafe_schemes(self):
        html = render_attachment(
            AttachmentContext(
                id="att-2",
                uti="public.url",
                title="Unsafe",
                primary_url="javascript:alert(1)",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )

        self.assertNotIn("javascript:alert", html)
        self.assertNotIn("href=", html)

    def test_image_renderer_rejects_protocol_relative_urls(self):
        html = render_attachment(
            AttachmentContext(
                id="att-3",
                uti="public.image",
                title="Image",
                primary_url="//evil.example.com/x.png",
                thumb_url=None,
                mergeable_gz=None,
            ),
            lambda _: "",
        )

        self.assertNotIn("src=", html)

    def test_image_renderer_falls_back_to_valid_thumbnail(self):
        html = render_attachment(
            AttachmentContext(
                id="att-4",
                uti="public.image",
                title="Image",
                primary_url="javascript:alert(1)",
                thumb_url="https://example.com/thumb.png",
                mergeable_gz=None,
            ),
            lambda _: "",
        )

        self.assertIn('src="https://example.com/thumb.png"', html)

    def test_render_table_from_mergeable_fails_closed_on_malformed_payload(self):
        self.assertIsNone(
            render_table_from_mergeable(b"not-a-table", lambda _: "<p>x</p>")
        )

    def test_render_table_from_mergeable_uses_later_valid_root_candidate(self):
        class _FakeValue:
            def __init__(self, object_index):
                self.object_index = object_index

        class _FakeMapEntry:
            def __init__(self, key, object_index):
                self.key = key
                self.value = _FakeValue(object_index)

        class _FakeRootEntry:
            def __init__(self, *map_entries):
                self.custom_map = SimpleNamespace(type=0, map_entry=list(map_entries))

            def HasField(self, field_name):
                return field_name == "custom_map"

        class _AxisEntry:
            def __init__(self, total):
                self.total = total

        class _CellEntry:
            def __init__(self, html):
                self.cell_html = html

        class _FakeProto:
            def __init__(self):
                entries = [
                    _FakeRootEntry(
                        _FakeMapEntry(0, 2),
                        _FakeMapEntry(1, 3),
                        _FakeMapEntry(2, 4),
                    ),
                    _FakeRootEntry(
                        _FakeMapEntry(0, 5),
                        _FakeMapEntry(1, 6),
                        _FakeMapEntry(2, 7),
                    ),
                    _AxisEntry(0),
                    _AxisEntry(0),
                    _CellEntry(""),
                    _AxisEntry(1),
                    _AxisEntry(1),
                    _CellEntry("<p>ok</p>"),
                ]
                data = SimpleNamespace(
                    mergeable_data_object_key_item=[
                        "crRows",
                        "crColumns",
                        "cellColumns",
                    ],
                    mergeable_data_object_type_item=["com.apple.notes.ICTable"],
                    mergeable_data_object_uuid_item=[],
                    mergeable_data_object_entry=entries,
                )
                self.mergable_data_object = SimpleNamespace(
                    mergeable_data_object_data=data
                )

            def ParseFromString(self, payload):
                return None

        with (
            patch(
                "pyicloud.services.notes.rendering.table_builder.pb.MergableDataProto",
                _FakeProto,
            ),
            patch.object(
                TableBuilder,
                "parse_rows",
                lambda self, entry: setattr(self.rows, "total", entry.total),
            ),
            patch.object(
                TableBuilder,
                "parse_cols",
                lambda self, entry: setattr(self.cols, "total", entry.total),
            ),
            patch.object(
                TableBuilder,
                "parse_cell_columns",
                lambda self, entry: self.cells.__setitem__(
                    0,
                    [SimpleNamespace(html=entry.cell_html)],
                )
                if self.cells
                else None,
            ),
        ):
            html = render_table_from_mergeable(b"candidate-scan", lambda _: "")

        self.assertIn("<table>", html)
        self.assertIn("<p>ok</p>", html)

    def test_table_builder_caps_large_allocations(self):
        builder = TableBuilder(
            key_items=[],
            type_items=[],
            uuid_items=[],
            entries=[],
            render_note_cb=lambda _: "",
        )
        builder.rows.total = 400
        builder.cols.total = 400

        builder.init_table_buffers()

        self.assertEqual(builder.cells, [])

    def test_safe_anchor_href_allows_only_expected_schemes(self):
        self.assertEqual(
            _safe_anchor_href("https://example.com"), "https://example.com"
        )
        self.assertEqual(
            _safe_anchor_href("mailto:test@example.com"), "mailto:test@example.com"
        )
        self.assertEqual(_safe_anchor_href("tel:+352123456"), "tel:+352123456")
        self.assertIsNone(_safe_anchor_href("javascript:alert(1)"))
        self.assertIsNone(_safe_anchor_href("data:text/html,hi"))

    def test_safe_url_rejects_unsafe_and_protocol_relative_urls(self):
        self.assertEqual(
            _safe_url(" https://example.com/file ", allowed_schemes={"http", "https"}),
            "https://example.com/file",
        )
        self.assertEqual(
            _safe_url("assets/file.png", allowed_schemes={"http", "https"}),
            "assets/file.png",
        )
        self.assertIsNone(
            _safe_url("//evil.example.com/file.png", allowed_schemes={"http", "https"})
        )
        self.assertIsNone(
            _safe_url("javascript:alert(1)", allowed_schemes={"http", "https"})
        )

    def test_export_config_is_image_uti_normalizes_config_values(self):
        config = ExportConfig(
            image_uti_prefixes=("Public.Image",),
            image_uti_exacts=("Com.Apple.Paper",),
        )

        self.assertTrue(config.is_image_uti("public.image"))
        self.assertTrue(config.is_image_uti("com.apple.paper"))

    def test_export_config_is_image_uti_rejects_invalid_config_types(self):
        config = ExportConfig(image_uti_exacts=("public.jpeg", 123))

        with self.assertRaises(TypeError):
            config.is_image_uti("public.jpeg")


class TestNoteExporter(unittest.TestCase):
    def _note_record(self, record_name="note-1", title=b"Example Title"):
        return _Record(record_name, {"TitleEncrypted": title})

    def _output_dir(self, name):
        path = os.path.join(
            tempfile.gettempdir(),
            "python-test-results",
            "notes-rendering",
            name,
        )
        os.makedirs(path, exist_ok=True)
        return path

    def test_export_archival_mode_downloads_assets_into_custom_assets_dir(self):
        client = MagicMock()
        datasource = MagicMock(name="datasource")
        note_record = self._note_record()
        config = ExportConfig(
            export_mode="archival",
            assets_dir=os.path.join(
                tempfile.gettempdir(),
                "python-test-results",
                "notes-rendering",
                "shared-assets",
            ),
        )
        exporter = NoteExporter(client, config=config)

        tmpdir = self._output_dir("archival-mode")
        with (
            patch(
                "pyicloud.services.notes.rendering.exporter.decode_and_parse_note",
                return_value=MagicMock(name="note"),
            ),
            patch(
                "pyicloud.services.notes.rendering.exporter.build_datasource",
                return_value=(datasource, ["att-1"]),
            ),
            patch.object(exporter.renderer, "render", return_value="<p>rendered</p>"),
            patch(
                "pyicloud.services.notes.rendering.exporter.download_pdf_assets"
            ) as mock_pdf,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_image_assets"
            ) as mock_img,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_av_assets"
            ) as mock_av,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_vcard_assets"
            ) as mock_vcard,
        ):
            path = exporter.export(note_record, output_dir=tmpdir, filename="note.html")

        expected_assets_dir = os.path.join(config.assets_dir, "note-1")
        expected = {
            "assets_dir": expected_assets_dir,
            "out_dir": tmpdir,
            "config": config,
        }

        mock_pdf.assert_called_once_with(client, datasource, ["att-1"], **expected)
        mock_img.assert_called_once_with(client, datasource, ["att-1"], **expected)
        mock_av.assert_called_once_with(client, datasource, ["att-1"], **expected)
        mock_vcard.assert_called_once_with(client, datasource, ["att-1"], **expected)

        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("<!doctype html>", html)
        self.assertIn("<title>Example Title</title>", html)

    def test_export_lightweight_mode_skips_downloads_and_writes_fragment(self):
        client = MagicMock()
        datasource = MagicMock(name="datasource")
        note_record = self._note_record(title=b"Fragment Title")
        config = ExportConfig(export_mode="lightweight", full_page=False)
        exporter = NoteExporter(client, config=config)

        tmpdir = self._output_dir("lightweight-mode")
        with (
            patch(
                "pyicloud.services.notes.rendering.exporter.decode_and_parse_note",
                return_value=MagicMock(name="note"),
            ),
            patch(
                "pyicloud.services.notes.rendering.exporter.build_datasource",
                return_value=(datasource, ["att-1"]),
            ),
            patch.object(exporter.renderer, "render", return_value="<p>rendered</p>"),
            patch(
                "pyicloud.services.notes.rendering.exporter.download_pdf_assets"
            ) as mock_pdf,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_image_assets"
            ) as mock_img,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_av_assets"
            ) as mock_av,
            patch(
                "pyicloud.services.notes.rendering.exporter.download_vcard_assets"
            ) as mock_vcard,
        ):
            path = exporter.export(note_record, output_dir=tmpdir, filename="note.html")

        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()

        mock_pdf.assert_not_called()
        mock_img.assert_not_called()
        mock_av.assert_not_called()
        mock_vcard.assert_not_called()
        self.assertEqual(html, "<p>rendered</p>")

    def test_decode_and_parse_note_returns_none_for_invalid_record_type(self):
        self.assertIsNone(decode_and_parse_note(object()))

    def test_download_image_assets_uses_caller_config(self):
        ck_client = MagicMock()
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "com.apple.paper"
        ds.get_primary_asset_url.return_value = (
            "https://cvws.icloud-content.com/B/image"
        )
        ds.get_thumbnail_url.return_value = None

        config = ExportConfig(image_uti_exacts=())

        tmpdir = self._output_dir("download-image-config")
        updated = download_image_assets(
            ck_client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(tmpdir, "assets"),
            out_dir=tmpdir,
            config=config,
        )

        ck_client.download_asset_to.assert_not_called()
        self.assertEqual(updated, {})


if __name__ == "__main__":
    unittest.main()
