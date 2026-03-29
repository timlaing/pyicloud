import argparse
import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

CLI_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "notes_cli.py")


def _load_notes_cli():
    spec = importlib.util.spec_from_file_location(
        "pyicloud_examples_notes_cli", CLI_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNotesCli(unittest.TestCase):
    def _output_dir(self, name):
        path = os.path.join("/tmp/python-test-results", "notes-cli", name)
        os.makedirs(path, exist_ok=True)
        return path

    def test_parse_args_rejects_removed_download_assets_flag(self):
        module = _load_notes_cli()

        with patch.object(
            sys,
            "argv",
            ["notes_cli.py", "--username", "user@example.com", "--download-assets"],
        ):
            with self.assertRaises(SystemExit):
                module.parse_args()

    def test_main_requests_titleencrypted_and_maps_export_config(self):
        module = _load_notes_cli()
        dummy_ckrecord = type("DummyCKRecord", (), {})
        note_record = dummy_ckrecord()
        note_record.recordName = "note-1"

        note_item = SimpleNamespace(id="note-1", title="Wanted", modified_at=None)
        raw = MagicMock()
        raw.lookup.return_value = SimpleNamespace(records=[note_record])

        notes = MagicMock()
        notes.recents.return_value = [note_item]
        notes.iter_all.return_value = []
        notes.raw = raw

        api = MagicMock()
        api.notes = notes

        exporter = MagicMock()
        args = argparse.Namespace(
            username="user@example.com",
            verbose=False,
            cookie_dir="",
            china_mainland=False,
            max_items=1,
            title="Wanted",
            title_contains="",
            output_dir="",
            full_page=True,
            dump_runs=False,
            assets_dir="",
            export_mode="lightweight",
            notes_debug=True,
            preview_appearance="dark",
            pdf_height=777,
        )

        tmpdir = self._output_dir("main-config")
        args.output_dir = tmpdir
        args.assets_dir = os.path.join(tmpdir, "assets")
        exporter.export.return_value = os.path.join(tmpdir, "note.html")

        with (
            patch.object(module, "parse_args", return_value=args),
            patch.object(module, "get_password", return_value="pw"),
            patch.object(module, "PyiCloudService", return_value=api),
            patch.object(module, "ensure_auth"),
            patch.object(module, "decode_and_parse_note", return_value=MagicMock()),
            patch.object(module, "console", MagicMock()),
            patch.object(module, "CKRecord", dummy_ckrecord),
            patch(
                "pyicloud.services.notes.rendering.exporter.NoteExporter",
                return_value=exporter,
            ) as mock_exporter_cls,
        ):
            module.main()

        self.assertEqual(
            raw.lookup.call_args.kwargs["desired_keys"],
            ["TextDataEncrypted", "Attachments", "TitleEncrypted"],
        )

        config = mock_exporter_cls.call_args.kwargs["config"]
        self.assertEqual(config.export_mode, "lightweight")
        self.assertEqual(config.assets_dir, args.assets_dir)
        self.assertTrue(config.full_page)
        self.assertTrue(config.debug)
        self.assertEqual(config.preview_appearance, "dark")
        self.assertEqual(config.pdf_object_height, 777)

    def test_parse_args_rejects_removed_password_flag(self):
        module = _load_notes_cli()

        with patch.object(
            sys,
            "argv",
            ["notes_cli.py", "--username", "user@example.com", "--password", "pw"],
        ):
            with self.assertRaises(SystemExit):
                module.parse_args()

    def test_main_suppresses_note_dumps_without_debug_flags(self):
        module = _load_notes_cli()
        dummy_ckrecord = type("DummyCKRecord", (), {})
        note_record = dummy_ckrecord()
        note_record.recordName = "note-1"

        note_item = SimpleNamespace(id="note-1", title="Wanted", modified_at=None)
        raw = MagicMock()
        raw.lookup.return_value = SimpleNamespace(records=[note_record])

        notes = MagicMock()
        notes.recents.return_value = [note_item]
        notes.iter_all.return_value = []
        notes.raw = raw

        api = MagicMock()
        api.notes = notes

        exporter = MagicMock()
        args = argparse.Namespace(
            username="user@example.com",
            verbose=False,
            cookie_dir="",
            china_mainland=False,
            max_items=1,
            title="Wanted",
            title_contains="",
            output_dir=self._output_dir("main-no-debug"),
            full_page=False,
            dump_runs=False,
            assets_dir="",
            export_mode="lightweight",
            notes_debug=False,
            preview_appearance="light",
            pdf_height=600,
        )
        exporter.export.return_value = os.path.join(args.output_dir, "note.html")
        console = MagicMock()

        with (
            patch.object(module, "parse_args", return_value=args),
            patch.object(module, "get_password", return_value="pw"),
            patch.object(module, "PyiCloudService", return_value=api),
            patch.object(module, "ensure_auth"),
            patch.object(module, "decode_and_parse_note", return_value=MagicMock()),
            patch.object(module, "console", console),
            patch.object(module, "CKRecord", dummy_ckrecord),
            patch(
                "pyicloud.services.notes.rendering.exporter.NoteExporter",
                return_value=exporter,
            ),
        ):
            module.main()

        console.rule.assert_not_called()
        printed = [call.args[0] for call in console.print.call_args_list if call.args]
        self.assertNotIn("proto_note:", printed)

    def test_ensure_auth_uses_security_key_when_fido2_devices_are_available(self):
        module = _load_notes_cli()
        api = MagicMock()
        devices = [object(), object()]
        api.requires_2fa = True
        api.requires_2sa = False
        api.fido2_devices = devices
        api.is_trusted_session = False

        with patch("builtins.input", return_value="1"):
            module.ensure_auth(api)

        api.confirm_security_key.assert_called_once_with(devices[1])
        api.validate_2fa_code.assert_not_called()
        api.trust_session.assert_called_once_with()
