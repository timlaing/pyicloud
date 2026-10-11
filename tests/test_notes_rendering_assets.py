"""Branch-coverage tests for the Notes HTML exporter asset helpers.

These exercise extension inference in ``download_*_assets``, datasource
hydration, media promotion, and the small pure helpers on the exporter
module. File writes are confined to the framework-approved
``python-test-results`` temp directory (see ``tests/conftest.py``).
"""

# The exporter mirrors CloudKit/protobuf wire names (recordName, downloadURL)
# which pylint rejects as invalid-name.
# pylint: disable=invalid-name
# ISO BMFF box type tokens used as binary fixture headers (cspell).
# cspell:ignore BMFF ftypheic ftypheif ftypmif ftypmsf ftyphevc ftypmp
# cspell:ignore ftypisom ftypqt ftypmoov ftypzzzz

import base64
import os
import tempfile
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import MagicMock, patch

from pyicloud.common.cloudkit import CKRecord
from pyicloud.common.cloudkit.models import CKLookupResponse
from pyicloud.services.notes.protobuf import notes_pb2 as pb
from pyicloud.services.notes.rendering import exporter
from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
from pyicloud.services.notes.rendering.exporter import (
    NoteExporter,
    _attachment_ids_from_record_and_runs,
    _follow_media_references,
    _hydrate_attachment_records,
    _media_field_url,
    _safe_name,
    _wire_media_to_parent,
    build_datasource,
    decode_and_parse_note,
    download_av_assets,
    download_image_assets,
    download_pdf_assets,
    download_vcard_assets,
    render_fragment,
    write_html,
)
from pyicloud.services.notes.rendering.options import ExportConfig

MAGIC_DIR = "python-test-results"

IMAGE_CASES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff\xe0" + b"\x00" * 12, ".jpg"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, ".png"),
    (b"GIF87a" + b"\x00" * 10, ".gif"),
    (b"GIF89a" + b"\x00" * 10, ".gif"),
    (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 4, ".webp"),
    (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 4, ".heic"),
    (b"\x00\x00\x00\x18ftypheif" + b"\x00" * 4, ".heic"),
    (b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 4, ".heic"),
    (b"\x00\x00\x00\x18ftypmsf1" + b"\x00" * 4, ".heic"),
    (b"\x00\x00\x00\x18ftyphevc" + b"\x00" * 4, ".heic"),
    (b"BM" + b"\x00" * 10, ".bmp"),
    (b"II*\x00" + b"\x00" * 8, ".tiff"),
    (b"MM\x00*" + b"\x00" * 8, ".tiff"),
]

AV_CASES: list[tuple[bytes, str]] = [
    (b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 4, ".m4a"),
    (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4, ".m4a"),
    (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4, ".m4a"),
    (b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 4, ".mov"),
    (b"\x00\x00\x00\x18ftypmoov" + b"\x00" * 4, ".mov"),
    (b"\x00\x00\x00\x18ftypzzzz" + b"\x00" * 4, ".mp4"),
    (b"\x00\x00\x00\x00moov" + b"\x00" * 8, ".mov"),
    (b"ID3\x04\x00" + b"\x00" * 8, ".mp3"),
    (b"\xff\xfb" + b"\x00" * 10, ".mp3"),
    (b"\xff\xf3" + b"\x00" * 10, ".mp3"),
    (b"\xff\xf2" + b"\x00" * 10, ".mp3"),
    (b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 4, ".wav"),
    (b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 4, ".avi"),
]


def _workdir(name: str) -> str:
    """Create and return a unique temp work directory the fs guard permits."""
    base = os.path.join(tempfile.gettempdir(), MAGIC_DIR, "notes-assets")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix=f"{name}-", dir=base)


def _write_file(path: str, data: bytes) -> None:
    """Write bytes to a guard-approved path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


class _AssetWriter:
    """Fake ``download_asset_to`` that writes a payload to disk."""

    def __init__(self, payload: bytes, suffix: str = "") -> None:
        self.payload = payload
        self.suffix = suffix
        self.calls = 0

    def __call__(self, _url: str, subdir: str) -> str:
        """Write the payload and return the saved path."""
        self.calls += 1
        path = os.path.join(str(subdir), f"asset-{self.calls}{self.suffix}")
        _write_file(path, self.payload)
        return path


class _MissingWriter:
    """Fake ``download_asset_to`` that reports a path without writing it."""

    def __call__(self, _url: str, subdir: str) -> str:
        """Return a path that does not exist on disk."""
        return os.path.join(str(subdir), "missing-asset.bin")


def _client_with(writer: Any) -> MagicMock:
    """Return a client mock whose downloads are driven by ``writer``."""
    client = MagicMock()
    client.download_asset_to.side_effect = writer
    return client


def _datasource(
    uti: str, primary: Any = "https://example.com/a.bin", thumb: Any = None
) -> MagicMock:
    """Return a datasource mock answering the download-helper surface."""
    ds = MagicMock()
    ds.get_attachment_uti.return_value = uti
    ds.get_primary_asset_url.return_value = primary
    ds.get_thumbnail_url.return_value = thumb
    return ds


class TestDownloadImageAssets(unittest.TestCase):
    """Coverage for ``download_image_assets`` and its extension inference."""

    def test_infers_image_extensions(self) -> None:
        """Each known image magic maps to the expected file extension."""
        for idx, (payload, expected) in enumerate(IMAGE_CASES):
            with self.subTest(expected=expected, idx=idx):
                work = _workdir(f"img-{idx}")
                ds = _datasource("public.image")
                client = _client_with(_AssetWriter(payload))
                updated = download_image_assets(
                    client,
                    ds,
                    ["img-1"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertTrue(updated["img-1"].endswith(expected))
                ds.set_primary_asset_url.assert_called_once_with(
                    "img-1", updated["img-1"]
                )

    def test_unknown_image_payload_keeps_original_name(self) -> None:
        """An unrecognised payload is kept without an inferred extension."""
        work = _workdir("img-unknown")
        ds = _datasource("public.image")
        client = _client_with(_AssetWriter(b"\x01\x02\x03\x04"))
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertEqual(len(updated), 1)
        self.assertTrue(updated["img-1"].endswith("asset-1"))

    def test_image_already_has_extension(self) -> None:
        """A downloaded file that already ends in the inferred ext is kept."""
        work = _workdir("img-already")
        ds = _datasource("public.image")
        client = _client_with(_AssetWriter(b"\xff\xd8\xff\xe0" + b"\x00" * 12, ".jpg"))
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["img-1"].endswith("asset-1.jpg"))

    def test_image_uses_thumbnail_when_primary_missing(self) -> None:
        """Image downloads fall back to the thumbnail URL when needed."""
        work = _workdir("img-thumb")
        ds = _datasource(
            "public.image", primary=None, thumb="https://example.com/t.png"
        )
        client = _client_with(_AssetWriter(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["img-1"].endswith(".png"))
        self.assertEqual(
            client.download_asset_to.call_args.args[0],
            "https://example.com/t.png",
        )

    def test_image_skips_when_no_usable_url(self) -> None:
        """Image downloads skip attachments without a usable URL."""
        cases: list[tuple[Any, Any]] = [
            (None, None),
            ("assets/local.png", None),
        ]
        for idx, (primary, thumb) in enumerate(cases):
            with self.subTest(idx=idx):
                work = _workdir(f"img-skip-{idx}")
                ds = _datasource("public.image", primary=primary, thumb=thumb)
                client = _client_with(_AssetWriter(b"\x89PNG\r\n\x1a\n"))
                updated = download_image_assets(
                    client,
                    ds,
                    ["img-1"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertEqual(updated, {})
                client.download_asset_to.assert_not_called()

    def test_image_skips_non_image_uti(self) -> None:
        """Non-image UTIs are ignored by the image downloader."""
        work = _workdir("img-non-image")
        ds = _datasource("public.url")
        client = _client_with(_AssetWriter(b"\x89PNG"))
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertEqual(updated, {})
        client.download_asset_to.assert_not_called()

    def test_image_ignores_download_failure(self) -> None:
        """A failed download is swallowed and omitted from the mapping."""
        work = _workdir("img-download-fail")
        ds = _datasource("public.image")
        client = MagicMock()
        client.download_asset_to.side_effect = RuntimeError("boom")
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertEqual(updated, {})

    def test_image_ignores_unreadable_file(self) -> None:
        """A missing downloaded file leaves the original name in place."""
        work = _workdir("img-missing-file")
        ds = _datasource("public.image")
        client = _client_with(_MissingWriter())
        updated = download_image_assets(
            client,
            ds,
            ["img-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["img-1"].endswith("missing-asset.bin"))

    def test_image_ignores_rename_failure(self) -> None:
        """A failing rename falls back to the downloaded path."""
        work = _workdir("img-rename-fail")
        ds = _datasource("public.image")
        client = _client_with(_AssetWriter(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))
        with patch(
            "pyicloud.services.notes.rendering.exporter.os.replace",
            side_effect=OSError("nope"),
        ):
            updated = download_image_assets(
                client,
                ds,
                ["img-1"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            )
        self.assertTrue(updated["img-1"].endswith("asset-1"))


class TestDownloadAvAssets(unittest.TestCase):
    """Coverage for ``download_av_assets`` and its extension inference."""

    def test_infers_av_extensions(self) -> None:
        """Each known audio/video magic maps to the expected extension."""
        for idx, (payload, expected) in enumerate(AV_CASES):
            with self.subTest(expected=expected, idx=idx):
                work = _workdir(f"av-{idx}")
                ds = _datasource("public.audio")
                client = _client_with(_AssetWriter(payload))
                updated = download_av_assets(
                    client,
                    ds,
                    ["av-1"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertTrue(updated["av-1"].endswith(expected))

    def test_av_falls_back_to_uti_extension(self) -> None:
        """Unrecognised payloads fall back to UTI-derived extensions."""
        cases = [
            ("com.apple.m4a-audio", ".m4a"),
            ("com.apple.quicktime-movie", ".mov"),
        ]
        for idx, (uti, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                work = _workdir(f"av-fallback-{idx}")
                ds = _datasource(uti)
                client = _client_with(_AssetWriter(b"\x00" * 16))
                updated = download_av_assets(
                    client,
                    ds,
                    ["av-1"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertTrue(updated["av-1"].endswith(expected))

    def test_av_unknown_payload_without_fallback(self) -> None:
        """An unknown payload with a plain audio UTI keeps its name."""
        work = _workdir("av-unknown")
        ds = _datasource("public.audio")
        client = _client_with(_AssetWriter(b"\x00" * 16))
        updated = download_av_assets(
            client,
            ds,
            ["av-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["av-1"].endswith("asset-1"))

    def test_av_skips_non_media_and_bad_urls(self) -> None:
        """Non-AV UTIs and non-HTTP URLs are skipped."""
        cases = [
            ("public.image", "https://example.com/a"),
            ("public.audio", "assets/local.mp3"),
        ]
        for idx, (uti, primary) in enumerate(cases):
            with self.subTest(uti=uti):
                work = _workdir(f"av-skip-{idx}")
                ds = _datasource(uti, primary=primary)
                client = _client_with(_AssetWriter(b"\x00" * 16))
                updated = download_av_assets(
                    client,
                    ds,
                    ["av-1"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertEqual(updated, {})
                client.download_asset_to.assert_not_called()

    def test_av_ignores_download_and_rename_failures(self) -> None:
        """Download and rename failures are swallowed individually."""
        work = _workdir("av-failures")
        ds = _datasource("public.audio")
        client = MagicMock()
        client.download_asset_to.side_effect = RuntimeError("boom")
        updated = download_av_assets(
            client,
            ds,
            ["av-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertEqual(updated, {})

        client = _client_with(_AssetWriter(b"ID3\x04\x00" + b"\x00" * 8))
        with patch(
            "pyicloud.services.notes.rendering.exporter.os.replace",
            side_effect=OSError("nope"),
        ):
            updated = download_av_assets(
                client,
                ds,
                ["av-1"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            )
        self.assertTrue(updated["av-1"].endswith("asset-1"))

    def test_av_ignores_unreadable_file(self) -> None:
        """A missing downloaded file leaves the original name in place."""
        work = _workdir("av-missing-file")
        ds = _datasource("public.audio")
        client = _client_with(_MissingWriter())
        updated = download_av_assets(
            client,
            ds,
            ["av-1"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["av-1"].endswith("missing-asset.bin"))


class TestDownloadPdfAssets(unittest.TestCase):
    """Coverage for ``download_pdf_assets``."""

    def test_pdf_renamed_when_magic_present(self) -> None:
        """A PDF payload without a .pdf suffix is renamed."""
        work = _workdir("pdf-magic")
        ds = _datasource("com.adobe.pdf", primary="https://example.com/f")
        client = _client_with(_AssetWriter(b"%PDF-1.7\nrest"))
        updated = download_pdf_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith(".pdf"))

    def test_pdf_aliases_are_recognised(self) -> None:
        """The public.pdf and paper.doc.pdf UTIs are treated as PDFs."""
        for idx, uti in enumerate(("public.pdf", "com.apple.paper.doc.pdf")):
            with self.subTest(uti=uti):
                work = _workdir(f"pdf-alias-{idx}")
                ds = _datasource(uti, primary="https://example.com/f")
                client = _client_with(_AssetWriter(b"%PDF-1.4"))
                updated = download_pdf_assets(
                    client,
                    ds,
                    ["a"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertTrue(updated["a"].endswith(".pdf"))

    def test_pdf_not_renamed_without_magic(self) -> None:
        """A payload without the PDF magic keeps its original name."""
        work = _workdir("pdf-no-magic")
        ds = _datasource("com.adobe.pdf", primary="https://example.com/f")
        client = _client_with(_AssetWriter(b"not a pdf"))
        updated = download_pdf_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith("asset-1"))

    def test_pdf_already_has_pdf_extension(self) -> None:
        """A downloaded file already ending in .pdf is kept as-is."""
        work = _workdir("pdf-already")
        ds = _datasource("com.adobe.pdf", primary="https://example.com/f")
        client = _client_with(_AssetWriter(b"%PDF-1.4", suffix=".pdf"))
        updated = download_pdf_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith("asset-1.pdf"))

    def test_pdf_skips_non_pdf_and_bad_urls(self) -> None:
        """Non-PDF UTIs and non-HTTP URLs are skipped."""
        cases = [
            ("public.image", "https://example.com/f"),
            ("com.adobe.pdf", "assets/local.pdf"),
            ("com.adobe.pdf", None),
        ]
        for idx, (uti, primary) in enumerate(cases):
            with self.subTest(idx=idx):
                work = _workdir(f"pdf-skip-{idx}")
                ds = _datasource(uti, primary=primary)
                client = _client_with(_AssetWriter(b"%PDF-1.4"))
                updated = download_pdf_assets(
                    client,
                    ds,
                    ["a"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertEqual(updated, {})
                client.download_asset_to.assert_not_called()

    def test_pdf_ignores_download_rename_and_read_failures(self) -> None:
        """Download, rename, and read failures are swallowed individually."""
        work = _workdir("pdf-failures")
        ds = _datasource("com.adobe.pdf", primary="https://example.com/f")

        client = MagicMock()
        client.download_asset_to.side_effect = RuntimeError("boom")
        self.assertEqual(
            download_pdf_assets(
                client,
                ds,
                ["a"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            ),
            {},
        )

        client = _client_with(_AssetWriter(b"%PDF-1.7"))
        with patch(
            "pyicloud.services.notes.rendering.exporter.os.replace",
            side_effect=OSError("nope"),
        ):
            updated = download_pdf_assets(
                client,
                ds,
                ["a"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            )
        self.assertTrue(updated["a"].endswith("asset-1"))

        client = _client_with(_MissingWriter())
        updated = download_pdf_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith("missing-asset.bin"))


class TestDownloadVcardAssets(unittest.TestCase):
    """Coverage for ``download_vcard_assets``."""

    def test_vcard_appends_extension(self) -> None:
        """A vCard download without a .vcf suffix is renamed."""
        work = _workdir("vcard-append")
        ds = _datasource("public.vcard", primary="https://example.com/c")
        client = _client_with(_AssetWriter(b"BEGIN:VCARD"))
        updated = download_vcard_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith(".vcf"))

    def test_vcard_already_has_extension(self) -> None:
        """A download already ending in .vcf is kept as-is."""
        work = _workdir("vcard-already")
        ds = _datasource("public.vcard", primary="https://example.com/c")
        client = _client_with(_AssetWriter(b"BEGIN:VCARD", suffix=".vcf"))
        updated = download_vcard_assets(
            client,
            ds,
            ["a"],
            assets_dir=os.path.join(work, "assets"),
            out_dir=work,
        )
        self.assertTrue(updated["a"].endswith("asset-1.vcf"))

    def test_vcard_skips_non_vcard_and_bad_urls(self) -> None:
        """Non-vCard UTIs and non-HTTP URLs are skipped."""
        cases = [
            ("public.image", "https://example.com/c"),
            ("public.vcard", "local.vcf"),
        ]
        for idx, (uti, primary) in enumerate(cases):
            with self.subTest(idx=idx):
                work = _workdir(f"vcard-skip-{idx}")
                ds = _datasource(uti, primary=primary)
                client = _client_with(_AssetWriter(b"BEGIN:VCARD"))
                updated = download_vcard_assets(
                    client,
                    ds,
                    ["a"],
                    assets_dir=os.path.join(work, "assets"),
                    out_dir=work,
                )
                self.assertEqual(updated, {})
                client.download_asset_to.assert_not_called()

    def test_vcard_ignores_download_and_rename_failures(self) -> None:
        """Download and rename failures are swallowed individually."""
        work = _workdir("vcard-failures")
        ds = _datasource("public.vcard", primary="https://example.com/c")
        client = MagicMock()
        client.download_asset_to.side_effect = RuntimeError("boom")
        self.assertEqual(
            download_vcard_assets(
                client,
                ds,
                ["a"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            ),
            {},
        )

        client = _client_with(_AssetWriter(b"BEGIN:VCARD"))
        with patch(
            "pyicloud.services.notes.rendering.exporter.os.replace",
            side_effect=OSError("nope"),
        ):
            updated = download_vcard_assets(
                client,
                ds,
                ["a"],
                assets_dir=os.path.join(work, "assets"),
                out_dir=work,
            )
        self.assertTrue(updated["a"].endswith("asset-1"))


class _Fields:
    """Minimal record field container for the exporter tests."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get_value(self, key: str) -> Any:
        """Return a stored value by key."""
        return self.values.get(key)


class _Record:
    """Minimal CloudKit record stand-in used by ``NoteExporter`` tests."""

    def __init__(self, name: str, values: dict[str, Any]) -> None:
        self.recordName = name
        self.recordType = "Note"
        self.fields = _Fields(values)


class _FieldsMap(dict[str, Any]):
    """Dict-like fields map exposing the exporter's ``get_field`` API."""

    def get_field(self, key: str) -> Any:
        """Return the raw stored field."""
        return self.get(key)


class _RaisingFields(dict[str, Any]):
    """Fields map whose ``get_field`` always raises."""

    def get_field(self, key: str) -> Any:
        """Raise to exercise the exporter's defensive branch."""
        raise RuntimeError(f"no field {key}")


def _attachment_refs(names: list[str | None]) -> Any:
    """Build a fake ``Attachments`` field value from record names."""
    return SimpleNamespace(value=[SimpleNamespace(recordName=name) for name in names])


def _media_record(name: str) -> CKRecord:
    """Build a minimal Media record stand-in."""
    return cast(CKRecord, SimpleNamespace(recordName=name))


def _record_with_attachments(names: list[str | None]) -> CKRecord:
    """Build a record exposing only an ``Attachments`` field."""
    field = _attachment_refs(names)
    return cast(
        CKRecord,
        SimpleNamespace(
            fields=SimpleNamespace(
                get_field=lambda key: field if key == "Attachments" else None
            )
        ),
    )


class TestExporterPureHelpers(unittest.TestCase):
    """Coverage for the exporter's pure and small helper functions."""

    def test_safe_name_cases(self) -> None:
        """``_safe_name`` normalises, sanitises, and bounds titles."""
        cases = [
            (None, "untitled"),
            ("", "untitled"),
            ("   ", "untitled"),
            ("  hello   world ", "hello world"),
            ("a/b:c", "a-b-c"),
            ("x" * 100, "x" * 60),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(_safe_name(value), expected)

    def test_write_html_default_filename_fragment(self) -> None:
        """``write_html`` derives a safe filename and writes a fragment."""
        work = _workdir("write-html-fragment")
        path = write_html("My Note", "<p>x</p>", work, full_page=False)
        self.assertTrue(path.endswith("My Note.html"))
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<p>x</p>")

    def test_write_html_full_page_with_filename(self) -> None:
        """``write_html`` wraps a full page and honours an explicit filename."""
        work = _workdir("write-html-full")
        path = write_html("T", "<p>x</p>", work, full_page=True, filename="c.html")
        self.assertTrue(path.endswith("c.html"))
        with open(path, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("<!doctype html>", html)
        self.assertIn("<title>T</title>", html)

    def test_render_fragment_delegates(self) -> None:
        """``render_fragment`` delegates to the module-level renderer."""
        with patch.object(
            exporter, "render_note_fragment", return_value="<p>f</p>"
        ) as render_mock:
            self.assertEqual(render_fragment(pb.Note(), None), "<p>f</p>")
        render_mock.assert_called_once()

    def test_attachment_ids_merge_and_dedupe(self) -> None:
        """Attachment ids merge record references and inline runs."""
        record = _record_with_attachments(["att-1", "att-2", None])
        note = pb.Note()
        run = note.attribute_run.add()
        run.attachment_info.attachment_identifier = "att-2"
        run2 = note.attribute_run.add()
        run2.attachment_info.attachment_identifier = "att-3"
        # A run with no attachment info must be ignored.
        note.attribute_run.add()

        self.assertEqual(
            _attachment_ids_from_record_and_runs(record, note),
            ["att-1", "att-2", "att-3"],
        )

    def test_attachment_ids_without_attachments_field(self) -> None:
        """Attachment ids come from runs when no Attachments field exists."""
        field = SimpleNamespace()  # no ``value`` attribute
        record = cast(
            CKRecord,
            SimpleNamespace(fields=SimpleNamespace(get_field=lambda key: field)),
        )
        note = pb.Note()

        self.assertEqual(_attachment_ids_from_record_and_runs(record, note), [])

    def test_media_field_url_variants(self) -> None:
        """``_media_field_url`` extracts, skips, and defends URL lookups."""
        good = SimpleNamespace(value=SimpleNamespace(downloadURL="https://x/m"))
        none_url = SimpleNamespace(value=SimpleNamespace(downloadURL=None))
        mrec = SimpleNamespace(fields=_FieldsMap({"Asset": good}))
        self.assertEqual(_media_field_url(cast(CKRecord, mrec)), "https://x/m")

        mrec_none = SimpleNamespace(fields=_FieldsMap({"Asset": none_url}))
        self.assertIsNone(_media_field_url(cast(CKRecord, mrec_none)))

        mrec_raise = SimpleNamespace(fields=_RaisingFields({"Asset": good}))
        self.assertIsNone(_media_field_url(cast(CKRecord, mrec_raise)))

    def test_hydrate_attachment_records(self) -> None:
        """Hydration indexes attachment records and captures Media refs."""
        record = CKRecord.model_validate({
            "recordName": "Attachment/1",
            "recordType": "Attachment",
            "fields": {
                "UTI": {"type": "STRING", "value": "public.image"},
                "Media": {"type": "REFERENCE", "value": {"recordName": "Media/1"}},
            },
        })
        ds = MagicMock()
        media_map = _hydrate_attachment_records(
            ds, cast(CKLookupResponse, SimpleNamespace(records=[record])), None
        )
        self.assertEqual(media_map, {"Media/1": "Attachment/1"})
        ds.add_attachment_record.assert_called_once_with(record)

    def test_hydrate_skips_non_records(self) -> None:
        """Non-record entries are ignored during hydration."""
        ds = MagicMock()
        media_map = _hydrate_attachment_records(
            ds, cast(CKLookupResponse, SimpleNamespace(records=[object()])), None
        )
        self.assertEqual(media_map, {})
        ds.add_attachment_record.assert_not_called()

    def test_follow_media_promotes_url(self) -> None:
        """A Media asset URL is promoted onto its parent attachment."""
        media = CKRecord.model_validate({
            "recordName": "Media/1",
            "recordType": "Media",
            "fields": {
                "Asset": {
                    "type": "ASSETID",
                    "value": {"downloadURL": "https://x/m"},
                }
            },
        })
        client = MagicMock()
        client.lookup.return_value = SimpleNamespace(records=[media])
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "public.jpeg"
        ds.get_primary_asset_url.return_value = "https://x/t"
        ds.get_thumbnail_url.return_value = "https://x/t"

        _follow_media_references(client, ds, {"Media/1": "Attachment/1"}, None)

        ds.set_primary_asset_url.assert_called_once_with("Attachment/1", "https://x/m")

    def test_follow_media_skips_and_swallows_errors(self) -> None:
        """Non-records are skipped and lookup failures are swallowed."""
        client = MagicMock()
        client.lookup.return_value = SimpleNamespace(records=[object()])
        ds = MagicMock()
        _follow_media_references(client, ds, {"Media/1": "Attachment/1"}, None)
        ds.set_primary_asset_url.assert_not_called()

        client = MagicMock()
        client.lookup.side_effect = RuntimeError("boom")
        _follow_media_references(client, MagicMock(), {"Media/1": "a"}, None)

    def test_follow_media_noop_without_map(self) -> None:
        """No lookup happens when there are no Media references."""
        client = MagicMock()
        _follow_media_references(client, MagicMock(), {}, None)
        client.lookup.assert_not_called()

    def test_wire_media_respects_upgrade_policy(self) -> None:
        """Media promotion only rewrites the primary when it should."""
        # Missing parent: nothing to do.
        ds = MagicMock()
        _wire_media_to_parent(ds, {}, _media_record("m"), "u", None)
        ds.set_primary_asset_url.assert_not_called()

        # UTI lookup raises -> treated as unknown, primary is set.
        ds = MagicMock()
        ds.get_attachment_uti.side_effect = RuntimeError("boom")
        ds.get_primary_asset_url.return_value = ""
        _wire_media_to_parent(ds, {"m": "a"}, _media_record("m"), "https://x", None)
        ds.set_primary_asset_url.assert_called_once_with("a", "https://x")

        # A plain web link with an existing primary is left alone.
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "public.url"
        ds.get_primary_asset_url.return_value = "https://x/page"
        _wire_media_to_parent(ds, {"m": "a"}, _media_record("m"), "https://x", None)
        ds.set_primary_asset_url.assert_not_called()

        # Image upgrade disabled leaves an existing primary alone.
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "public.jpeg"
        ds.get_primary_asset_url.return_value = "https://x/old"
        ds.get_thumbnail_url.return_value = "https://x/thumb"
        _wire_media_to_parent(
            ds,
            {"m": "a"},
            _media_record("m"),
            "https://x",
            ExportConfig(prefer_media_for_images=False),
        )
        ds.set_primary_asset_url.assert_not_called()

        # Audio is promoted when the current primary is just the thumbnail.
        ds = MagicMock()
        ds.get_attachment_uti.return_value = "public.audio"
        ds.get_primary_asset_url.return_value = "https://x/old"
        ds.get_thumbnail_url.return_value = "https://x/old"
        _wire_media_to_parent(ds, {"m": "a"}, _media_record("m"), "https://x", None)
        ds.set_primary_asset_url.assert_called_once_with("a", "https://x")


class TestDecodeAndBuildDatasource(unittest.TestCase):
    """Coverage for decoding a Note body and building its datasource."""

    def _note_record(self) -> CKRecord:
        encoded = base64.b64encode(b"x").decode()
        return CKRecord.model_validate({
            "recordName": "Note/1",
            "recordType": "Note",
            "fields": {
                "TextDataEncrypted": {
                    "type": "ENCRYPTED_BYTES",
                    "value": encoded,
                }
            },
        })

    def test_decode_success(self) -> None:
        """A valid body decodes into a parsed ``pb.Note``."""
        proto = pb.NoteStoreProto()
        proto.document.note.note_text = "body"
        payload = proto.SerializeToString()
        decoder = SimpleNamespace(bytes=payload)
        with patch(
            "pyicloud.services.notes.rendering.exporter.BodyDecoder.decode",
            return_value=decoder,
        ):
            note = decode_and_parse_note(self._note_record())
        assert note is not None
        self.assertEqual(note.note_text, "body")

    def test_decode_none_when_decoder_returns_none(self) -> None:
        """A ``None`` decoder result yields ``None``."""
        with patch(
            "pyicloud.services.notes.rendering.exporter.BodyDecoder.decode",
            return_value=None,
        ):
            self.assertIsNone(decode_and_parse_note(self._note_record()))

    def test_decode_none_when_bytes_empty(self) -> None:
        """An empty decoded byte payload yields ``None``."""
        decoder = SimpleNamespace(bytes=b"")
        with patch(
            "pyicloud.services.notes.rendering.exporter.BodyDecoder.decode",
            return_value=decoder,
        ):
            self.assertIsNone(decode_and_parse_note(self._note_record()))

    def test_decode_none_without_body_field(self) -> None:
        """A record without a body field yields ``None``."""
        record = CKRecord.model_validate({
            "recordName": "Note/2",
            "recordType": "Note",
            "fields": {},
        })
        self.assertIsNone(decode_and_parse_note(record))

    def test_build_datasource_without_attachments(self) -> None:
        """No lookup happens when the note has no attachments."""
        client = MagicMock()
        ds, ids = build_datasource(
            client, _record_with_attachments([]), pb.Note(), None
        )
        self.assertEqual(ids, [])
        self.assertIsInstance(ds, CloudKitNoteDataSource)
        client.lookup.assert_not_called()

    def test_build_datasource_hydrates_attachments(self) -> None:
        """Referenced attachments are fetched and indexed."""
        attachment = CKRecord.model_validate({
            "recordName": "Attachment/1",
            "recordType": "Attachment",
            "fields": {"UTI": {"type": "STRING", "value": "public.image"}},
        })
        client = MagicMock()
        client.lookup.return_value = SimpleNamespace(records=[attachment])

        ds, ids = build_datasource(
            client, _record_with_attachments(["Attachment/1"]), pb.Note(), None
        )

        self.assertEqual(ids, ["Attachment/1"])
        self.assertEqual(ds.get_attachment_uti("Attachment/1"), "public.image")


class TestNoteExportPaths(unittest.TestCase):
    """Coverage for the ``NoteExporter.export`` orchestration branches."""

    def _out(self, name: str) -> str:
        return _workdir(f"export-{name}")

    def test_export_returns_none_without_body(self) -> None:
        """Export returns ``None`` when the body cannot be decoded."""
        exporter_inst = NoteExporter(MagicMock())
        with patch.object(exporter, "decode_and_parse_note", return_value=None):
            self.assertIsNone(
                exporter_inst.export(
                    cast(CKRecord, _Record("n", {})), self._out("nobody")
                )
            )

    def test_export_defaults_full_page_and_string_title(self) -> None:
        """A string title is used and ``full_page=None`` defaults to a page."""
        exporter_inst = NoteExporter(
            MagicMock(), config=ExportConfig(export_mode="lightweight")
        )
        record = cast(CKRecord, _Record("n", {"TitleEncrypted": "Str Title"}))
        work = self._out("string-title")
        with (
            patch.object(exporter, "decode_and_parse_note", return_value=pb.Note()),
            patch.object(exporter, "build_datasource", return_value=(MagicMock(), [])),
            patch.object(exporter_inst.renderer, "render", return_value="<p>x</p>"),
        ):
            path = exporter_inst.export(record, work)

        assert path is not None
        with open(path, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("<!doctype html>", html)
        self.assertIn("<title>Str Title</title>", html)

    def test_export_bytes_title_and_fragment(self) -> None:
        """A bytes title is decoded and a fragment is written when asked."""
        exporter_inst = NoteExporter(
            MagicMock(),
            config=ExportConfig(export_mode="lightweight", full_page=False),
        )
        record = cast(CKRecord, _Record("n", {"TitleEncrypted": b"Bytes Title"}))
        work = self._out("bytes-title")
        with (
            patch.object(exporter, "decode_and_parse_note", return_value=pb.Note()),
            patch.object(exporter, "build_datasource", return_value=(MagicMock(), [])),
            patch.object(exporter_inst.renderer, "render", return_value="<p>x</p>"),
        ):
            path = exporter_inst.export(record, work, filename="out.html")

        assert path is not None
        self.assertTrue(path.endswith("out.html"))
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "<p>x</p>")

    def test_export_falls_back_on_bad_title_bytes(self) -> None:
        """An undecodable title falls back to ``Untitled``."""
        exporter_inst = NoteExporter(
            MagicMock(), config=ExportConfig(export_mode="lightweight")
        )
        record = cast(CKRecord, _Record("n", {"TitleEncrypted": b"\xff\xfe"}))
        work = self._out("bad-title")
        with (
            patch.object(exporter, "decode_and_parse_note", return_value=pb.Note()),
            patch.object(exporter, "build_datasource", return_value=(MagicMock(), [])),
            patch.object(exporter_inst.renderer, "render", return_value="<p>x</p>"),
        ):
            path = exporter_inst.export(record, work)

        assert path is not None
        with open(path, encoding="utf-8") as handle:
            self.assertIn("<title>Untitled</title>", handle.read())
