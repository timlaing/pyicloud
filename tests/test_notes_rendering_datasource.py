"""Tests for the CloudKit-backed notes rendering datasource."""

from __future__ import annotations

import base64
from typing import cast
import unittest
from unittest.mock import patch

from pyicloud.common.cloudkit import CKRecord
from pyicloud.common.cloudkit.models import CKFields
from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
from pyicloud.services.notes.rendering.options import ExportConfig

RECORD_TYPE = "Attachment"


class _RaisingAppearanceConfig:
    """Config stub whose preview appearance lookup raises."""

    @property
    def preview_appearance(self) -> str:
        """Always raise to exercise the defensive config fallback."""
        raise RuntimeError("no appearance")


def _b64(raw: bytes) -> str:
    """Return the base64 text encoding of raw bytes."""
    return base64.b64encode(raw).decode("ascii")


def _string(value: str) -> dict[str, object]:
    """Return a CloudKit STRING field wrapper."""
    return {"type": "STRING", "value": value}


def _encrypted(value: bytes) -> dict[str, object]:
    """Return a CloudKit ENCRYPTED_BYTES field wrapper."""
    return {"type": "ENCRYPTED_BYTES", "value": _b64(value)}


def _asset(url: str) -> dict[str, object]:
    """Return a CloudKit ASSETID field wrapper for a single URL."""
    return {"type": "ASSETID", "value": {"downloadURL": url}}


def _asset_list(*urls: str) -> dict[str, object]:
    """Return a CloudKit ASSETID_LIST field wrapper for the given URLs."""
    return {"type": "ASSETID_LIST", "value": [{"downloadURL": u} for u in urls]}


def _int_list(*values: int) -> dict[str, object]:
    """Return a CloudKit INT64_LIST field wrapper."""
    return {"type": "INT64_LIST", "value": list(values)}


def _record(record_name: str, fields: dict[str, object]) -> CKRecord:
    """Build a validated CKRecord carrying the given fields."""
    return CKRecord.model_validate({
        "recordName": record_name,
        "recordType": RECORD_TYPE,
        "fields": fields,
    })


def _url_of(token: object) -> str | None:
    """Best-effort downloadURL extractor used by appearance tests."""
    if isinstance(token, dict):
        url = token.get("downloadURL")
        return url if isinstance(url, str) else None
    return None


class TestIdentifierResolution(unittest.TestCase):
    """Attachment identifier resolution and record-name fallback."""

    def test_attachment_identifier_wins_over_record_name(self) -> None:
        """AttachmentIdentifier is stored under both keys."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "Attachment/1",
                {
                    "AttachmentIdentifier": _string("logical-1"),
                    "UTI": _string("public.image"),
                },
            )
        )
        self.assertEqual(ds.get_attachment_uti("logical-1"), "public.image")
        self.assertEqual(ds.get_attachment_uti("Attachment/1"), "public.image")

    def test_lowercase_attachment_identifier_alias_is_accepted(self) -> None:
        """The lowercase attachmentIdentifier alias is accepted."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "Attachment/2",
                {
                    "attachmentIdentifier": _string("logical-2"),
                    "UTI": _string("public.image"),
                },
            )
        )
        self.assertEqual(ds.get_attachment_uti("logical-2"), "public.image")

    def test_plain_identifier_field_is_accepted(self) -> None:
        """A plain Identifier field is accepted as a fallback."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "Attachment/3",
                {"Identifier": _string("logical-3"), "UTI": _string("public.image")},
            )
        )
        self.assertEqual(ds.get_attachment_uti("logical-3"), "public.image")

    def test_record_name_used_when_identifier_absent(self) -> None:
        """The recordName is used when no identifier field is present."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("Attachment/4", {"UTI": _string("public.image")})
        )
        self.assertEqual(ds.get_attachment_uti("Attachment/4"), "public.image")

    def test_empty_identifier_and_record_name_return_early(self) -> None:
        """No identifier and an empty recordName short-circuit indexing."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("", {"UTI": _string("public.image")}))
        self.assertIsNone(ds.get_attachment_uti(""))
        self.assertIsNone(ds.get_primary_asset_url(""))

    def test_empty_identifier_value_falls_back_to_lowercase_alias(self) -> None:
        """An empty identifier value is skipped in favor of the next alias."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "Attachment/5",
                {
                    "AttachmentIdentifier": _string(""),
                    "attachmentIdentifier": _string("logical-5"),
                    "UTI": _string("public.image"),
                },
            )
        )
        self.assertEqual(ds.get_attachment_uti("logical-5"), "public.image")

    def test_empty_record_name_with_identifier_uses_identifier(self) -> None:
        """An empty recordName adds no extra key when an identifier exists."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "",
                {"Identifier": _string("logical-6"), "UTI": _string("public.image")},
            )
        )
        self.assertEqual(ds.get_attachment_uti("logical-6"), "public.image")


class TestUtiResolution(unittest.TestCase):
    """UTI resolution from plain and encrypted fields."""

    def test_plain_uti_field(self) -> None:
        """A plain UTI field provides the UTI."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("u1", {"UTI": _string("public.image")}))
        self.assertEqual(ds.get_attachment_uti("u1"), "public.image")

    def test_plain_attachment_uti_field(self) -> None:
        """A plain AttachmentUTI field provides the UTI."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("u2", {"AttachmentUTI": _string("com.adobe.pdf")})
        )
        self.assertEqual(ds.get_attachment_uti("u2"), "com.adobe.pdf")

    def test_encrypted_uti_field(self) -> None:
        """An encrypted UTI field is decoded from bytes."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("u3", {"UTIEncrypted": _encrypted(b"com.adobe.pdf")})
        )
        self.assertEqual(ds.get_attachment_uti("u3"), "com.adobe.pdf")

    def test_empty_plain_uti_falls_back_to_encrypted(self) -> None:
        """An empty plain UTI falls through to the encrypted variant."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "u4",
                {"UTI": _string(""), "UTIEncrypted": _encrypted(b"public.png")},
            )
        )
        self.assertEqual(ds.get_attachment_uti("u4"), "public.png")

    def test_absent_uti_is_none(self) -> None:
        """A record without any UTI resolves to None."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("u5", {"Title": _string("no uti")}))
        self.assertIsNone(ds.get_attachment_uti("u5"))


class TestMergeableData(unittest.TestCase):
    """Mergeable (gzipped) table bytes handling."""

    def test_mergeable_data_is_stored(self) -> None:
        """Encrypted mergeable data is stored as raw bytes."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("m1", {"MergeableDataEncrypted": _encrypted(b"raw-gz")})
        )
        self.assertEqual(ds.get_mergeable_gz("m1"), b"raw-gz")

    def test_empty_mergeable_data_is_ignored(self) -> None:
        """Empty mergeable data is not stored."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("m2", {"MergeableDataEncrypted": _encrypted(b"")})
        )
        self.assertIsNone(ds.get_mergeable_gz("m2"))


class TestPrimaryAssetResolution(unittest.TestCase):
    """Primary asset URL resolution across asset field shapes."""

    def test_primary_asset_field(self) -> None:
        """A PrimaryAsset token provides the primary URL."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("p1", {"PrimaryAsset": _asset("https://x/primary")})
        )
        self.assertEqual(ds.get_primary_asset_url("p1"), "https://x/primary")

    def test_fallback_pdf_for_com_adobe_pdf(self) -> None:
        """A FallbackPDF is used for the com.adobe.pdf UTI."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p2",
                {"UTI": _string("com.adobe.pdf"), "FallbackPDF": _asset("https://x/f")},
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p2"), "https://x/f")

    def test_fallback_pdf_for_public_pdf(self) -> None:
        """A FallbackPDF is used for the public.pdf UTI."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p3",
                {"UTI": _string("public.pdf"), "FallbackPDF": _asset("https://x/f")},
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p3"), "https://x/f")

    def test_paper_assets_list_for_paper_pdf(self) -> None:
        """The first PaperAssets entry is used for a paper PDF UTI."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p4",
                {
                    "UTI": _string("com.apple.paper.doc.pdf"),
                    "PaperAssets": _asset_list("https://x/a", "https://x/b"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p4"), "https://x/a")

    def test_fallback_pdf_ignored_when_primary_present(self) -> None:
        """A PrimaryAsset wins over FallbackPDF."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p5",
                {
                    "UTI": _string("public.pdf"),
                    "PrimaryAsset": _asset("https://x/primary"),
                    "FallbackPDF": _asset("https://x/fallback"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p5"), "https://x/primary")

    def test_paper_assets_ignored_when_fallback_pdf_present(self) -> None:
        """A FallbackPDF wins over the PaperAssets list."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p6",
                {
                    "UTI": _string("public.pdf"),
                    "FallbackPDF": _asset("https://x/fallback"),
                    "PaperAssets": _asset_list("https://x/a"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p6"), "https://x/fallback")

    def test_empty_paper_assets_list_is_ignored(self) -> None:
        """An empty PaperAssets list leaves the primary URL unset."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p7",
                {"UTI": _string("public.pdf"), "PaperAssets": _asset_list()},
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("p7"))

    def test_non_pdf_does_not_use_fallback_pdf(self) -> None:
        """A FallbackPDF is ignored for non-PDF UTIs."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p8",
                {"UTI": _string("public.url"), "FallbackPDF": _asset("https://x/f")},
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("p8"))

    def test_paper_assets_with_empty_url_is_ignored(self) -> None:
        """A PaperAssets first token without a URL is ignored."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p12",
                {
                    "UTI": _string("public.pdf"),
                    "PaperAssets": {
                        "type": "ASSETID_LIST",
                        "value": [{}],
                    },
                },
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("p12"))

    def test_passthrough_dict_asset_uses_download_url(self) -> None:
        """A dict-shaped asset value is read via its downloadURL key."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p9",
                {
                    "PrimaryAsset": {
                        "type": "FUTURE_ASSET",
                        "value": {"downloadURL": "https://x/dict"},
                    }
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("p9"), "https://x/dict")

    def test_passthrough_dict_asset_without_url_is_ignored(self) -> None:
        """A dict-shaped asset without a string URL is ignored."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p10",
                {"PrimaryAsset": {"type": "FUTURE_ASSET", "value": {}}},
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("p10"))

    def test_passthrough_dict_asset_with_non_string_url_is_ignored(self) -> None:
        """A dict-shaped asset with a non-string URL is ignored."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "p11",
                {
                    "PrimaryAsset": {
                        "type": "FUTURE_ASSET",
                        "value": {"downloadURL": 123},
                    }
                },
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("p11"))


class TestPreviewImageResolution(unittest.TestCase):
    """Preview image selection and appearance matching in records."""

    def test_light_appearance_selects_first_token(self) -> None:
        """The default light appearance selects the light preview token."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i1",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/light", "https://x/dark"),
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i1"), "https://x/light")
        self.assertEqual(ds.get_thumbnail_url("i1"), "https://x/light")

    def test_dark_config_selects_dark_token(self) -> None:
        """A dark preview_appearance config selects the dark token."""
        ds = CloudKitNoteDataSource(_config=ExportConfig(preview_appearance="dark"))
        ds.add_attachment_record(
            _record(
                "i2",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/light", "https://x/dark"),
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i2"), "https://x/dark")

    def test_uppercase_dark_config_is_normalized(self) -> None:
        """An uppercase dark appearance value is normalized."""
        ds = CloudKitNoteDataSource(_config=ExportConfig(preview_appearance="  DARK "))
        ds.add_attachment_record(
            _record(
                "i3",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/light", "https://x/dark"),
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i3"), "https://x/dark")

    def test_boolean_style_dark_config_is_normalized(self) -> None:
        """A truthy appearance value maps to the dark token."""
        ds = CloudKitNoteDataSource(_config=ExportConfig(preview_appearance="YES"))
        ds.add_attachment_record(
            _record(
                "i4",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/light", "https://x/dark"),
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i4"), "https://x/dark")

    def test_mismatched_appearance_falls_back_to_first_token(self) -> None:
        """A missing appearance match falls back to the first valid token."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i5",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/only"),
                    "PreviewAppearances": _int_list(1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i5"), "https://x/only")

    def test_missing_appearances_falls_back_to_first_token(self) -> None:
        """Without PreviewAppearances the first token is used."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i6",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/a", "https://x/b"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i6"), "https://x/a")

    def test_bad_appearance_length_falls_back_to_first_token(self) -> None:
        """An appearance/token length mismatch falls back to the first token."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i7",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/a", "https://x/b"),
                    "PreviewAppearances": _int_list(0),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i7"), "https://x/a")

    def test_invalid_appearance_marker_falls_back_to_first_token(self) -> None:
        """A non-integer appearance marker triggers the first-token fallback."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i8",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/a"),
                    "PreviewAppearances": {"type": "STRING_LIST", "value": ["light"]},
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i8"), "https://x/a")

    def test_empty_matched_preview_falls_back_to_next_token(self) -> None:
        """An empty matched token falls back to the next usable token."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i9",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": {
                        "type": "ASSETID_LIST",
                        "value": [{}, {"downloadURL": "https://x/b"}],
                    },
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i9"), "https://x/b")

    def test_preview_images_ignored_when_primary_present(self) -> None:
        """PreviewImages do not override an existing primary URL."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i10",
                {
                    "UTI": _string("public.image"),
                    "PrimaryAsset": _asset("https://x/primary"),
                    "PreviewImages": _asset_list("https://x/preview"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i10"), "https://x/primary")

    def test_apple_paper_uti_uses_preview_images(self) -> None:
        """The com.apple.paper UTI is treated as image-like."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i11",
                {
                    "UTI": _string("com.apple.paper"),
                    "PreviewImages": _asset_list("https://x/paper"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i11"), "https://x/paper")

    def test_fallback_image_used_for_image_uti(self) -> None:
        """FallbackImage supplies the primary URL for image UTIs."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i12",
                {
                    "UTI": _string("public.image"),
                    "FallbackImage": _asset("https://x/fallback"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i12"), "https://x/fallback")

    def test_fallback_image_ignored_when_preview_selected(self) -> None:
        """A selected preview image wins over FallbackImage."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i13",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/preview"),
                    "FallbackImage": _asset("https://x/fallback"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i13"), "https://x/preview")

    def test_empty_preview_list_is_ignored(self) -> None:
        """An empty PreviewImages list yields no primary URL for non-images."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i14",
                {"UTI": _string("public.url"), "PreviewImages": _asset_list()},
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("i14"))

    def test_config_appearance_error_falls_back_to_light(self) -> None:
        """A raising preview_appearance attribute falls back to light."""
        ds = CloudKitNoteDataSource(
            _config=cast(ExportConfig, _RaisingAppearanceConfig())
        )
        ds.add_attachment_record(
            _record(
                "i15",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": _asset_list("https://x/light", "https://x/dark"),
                    "PreviewAppearances": _int_list(0, 1),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("i15"), "https://x/light")

    def test_all_previews_empty_leaves_primary_unset(self) -> None:
        """Previews with no usable URL leave the primary unset."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "i16",
                {
                    "UTI": _string("public.image"),
                    "PreviewImages": {
                        "type": "ASSETID_LIST",
                        "value": [{}, {}],
                    },
                },
            )
        )
        self.assertIsNone(ds.get_primary_asset_url("i16"))

    def test_preview_images_lookup_error_is_ignored(self) -> None:
        """A failing PreviewImages lookup is swallowed."""
        original = CKFields.get_field

        def fake_get_field(self: CKFields, key: str) -> object:
            """Raise for PreviewImages and delegate otherwise."""
            if key == "PreviewImages":
                raise RuntimeError("boom")
            return original(self, key)

        ds = CloudKitNoteDataSource()
        record = _record(
            "i17",
            {
                "UTI": _string("public.image"),
                "PreviewImages": _asset_list("https://x/preview"),
            },
        )
        with patch.object(CKFields, "get_field", fake_get_field):
            ds.add_attachment_record(record)
        self.assertIsNone(ds.get_primary_asset_url("i17"))

    def test_preview_appearances_lookup_error_is_ignored(self) -> None:
        """A failing PreviewAppearances lookup still uses a preview."""
        original = CKFields.get_field

        def fake_get_field(self: CKFields, key: str) -> object:
            """Raise for PreviewAppearances and delegate otherwise."""
            if key == "PreviewAppearances":
                raise RuntimeError("boom")
            return original(self, key)

        ds = CloudKitNoteDataSource()
        record = _record(
            "i18",
            {
                "UTI": _string("public.image"),
                "PreviewImages": _asset_list("https://x/preview"),
                "PreviewAppearances": _int_list(0),
            },
        )
        with patch.object(CKFields, "get_field", fake_get_field):
            ds.add_attachment_record(record)
        self.assertEqual(ds.get_primary_asset_url("i18"), "https://x/preview")


class TestThumbnailAndMediaResolution(unittest.TestCase):
    """Thumbnail selection from PreviewImages and Media assets."""

    def test_preview_images_set_thumbnail_for_non_image(self) -> None:
        """PreviewImages are captured as a thumbnail for non-image UTIs."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "t1",
                {
                    "UTI": _string("public.pdf"),
                    "PreviewImages": _asset_list("https://x/thumb"),
                },
            )
        )
        self.assertEqual(ds.get_thumbnail_url("t1"), "https://x/thumb")
        self.assertIsNone(ds.get_primary_asset_url("t1"))

    def test_media_sets_thumbnail_and_primary(self) -> None:
        """A Media asset supplies both thumbnail and primary when unset."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("t2", {"Media": _asset("https://x/media")}))
        self.assertEqual(ds.get_thumbnail_url("t2"), "https://x/media")
        self.assertEqual(ds.get_primary_asset_url("t2"), "https://x/media")

    def test_media_does_not_override_existing_primary(self) -> None:
        """A Media asset does not override an existing primary URL."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "t3",
                {
                    "PrimaryAsset": _asset("https://x/primary"),
                    "Media": _asset("https://x/media"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("t3"), "https://x/primary")
        self.assertEqual(ds.get_thumbnail_url("t3"), "https://x/media")


class TestUrlStringHandling(unittest.TestCase):
    """URLString / URLStringEncrypted primary and title handling."""

    def test_url_string_encrypted_sets_primary(self) -> None:
        """An encrypted URL string is decoded into the primary URL."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record("w1", {"URLStringEncrypted": _encrypted(b"https://x/enc")})
        )
        self.assertEqual(ds.get_primary_asset_url("w1"), "https://x/enc")

    def test_url_string_encrypted_ignored_when_primary_present(self) -> None:
        """An encrypted URL string does not override an existing primary."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "w2",
                {
                    "PrimaryAsset": _asset("https://x/primary"),
                    "URLStringEncrypted": _encrypted(b"https://x/enc"),
                },
            )
        )
        self.assertEqual(ds.get_primary_asset_url("w2"), "https://x/primary")

    def test_url_string_used_as_title_for_public_url(self) -> None:
        """A plain URLString becomes the title for public.url attachments."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "w3",
                {
                    "UTI": _string("public.url"),
                    "URLString": _string("https://x/plain"),
                },
            )
        )
        self.assertEqual(ds.get_title("w3"), "https://x/plain")
        self.assertIsNone(ds.get_primary_asset_url("w3"))

    def test_url_string_ignored_as_title_for_non_url(self) -> None:
        """A plain URLString is not used as a title for non-URL UTIs."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "w4",
                {
                    "UTI": _string("public.image"),
                    "URLString": _string("https://x/plain"),
                },
            )
        )
        self.assertIsNone(ds.get_title("w4"))

    def test_primary_url_becomes_title_for_public_url(self) -> None:
        """The decoded encrypted URL becomes the title for public.url."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "w5",
                {
                    "UTI": _string("public.url"),
                    "URLStringEncrypted": _encrypted(b"https://x/enc"),
                },
            )
        )
        self.assertEqual(ds.get_title("w5"), "https://x/enc")

    def test_explicit_title_wins_over_url_fallback(self) -> None:
        """An explicit title prevents the URL title fallback."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "w6",
                {
                    "UTI": _string("public.url"),
                    "URLString": _string("https://x/plain"),
                    "TitleEncrypted": _encrypted(b"Real Title"),
                },
            )
        )
        self.assertEqual(ds.get_title("w6"), "Real Title")


class TestTitleResolution(unittest.TestCase):
    """Title resolution order across encrypted and plain fields."""

    def test_title_encrypted_has_highest_priority(self) -> None:
        """TitleEncrypted outranks every other title candidate."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z1",
                {
                    "TitleEncrypted": _encrypted(b"enc-title"),
                    "SummaryEncrypted": _encrypted(b"enc-summary"),
                    "LocalizedTitleEncrypted": _encrypted(b"enc-localized"),
                    "AltTextEncrypted": _encrypted(b"enc-alt"),
                    "TokenContentIdentifierEncrypted": _encrypted(b"enc-token"),
                    "Title": _string("plain-title"),
                    "Summary": _string("plain-summary"),
                    "AltText": _string("plain-alt"),
                },
            )
        )
        self.assertEqual(ds.get_title("z1"), "enc-title")

    def test_summary_encrypted_used_without_title(self) -> None:
        """SummaryEncrypted is used when TitleEncrypted is absent."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z2",
                {
                    "SummaryEncrypted": _encrypted(b"enc-summary"),
                    "LocalizedTitleEncrypted": _encrypted(b"enc-localized"),
                },
            )
        )
        self.assertEqual(ds.get_title("z2"), "enc-summary")

    def test_localized_title_encrypted_used_without_title_or_summary(self) -> None:
        """LocalizedTitleEncrypted is used when earlier fields are absent."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z3",
                {
                    "LocalizedTitleEncrypted": _encrypted(b"enc-localized"),
                    "AltTextEncrypted": _encrypted(b"enc-alt"),
                },
            )
        )
        self.assertEqual(ds.get_title("z3"), "enc-localized")

    def test_alt_text_encrypted_used_without_earlier_fields(self) -> None:
        """AltTextEncrypted is used when earlier fields are absent."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z4",
                {
                    "AltTextEncrypted": _encrypted(b"enc-alt"),
                    "TokenContentIdentifierEncrypted": _encrypted(b"enc-token"),
                },
            )
        )
        self.assertEqual(ds.get_title("z4"), "enc-alt")

    def test_token_content_identifier_encrypted_used_as_last_encrypted(self) -> None:
        """TokenContentIdentifierEncrypted is the last encrypted candidate."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z5",
                {"TokenContentIdentifierEncrypted": _encrypted(b"enc-token")},
            )
        )
        self.assertEqual(ds.get_title("z5"), "enc-token")

    def test_plain_title_used_after_encrypted_fields(self) -> None:
        """A plain Title is used when no encrypted title is present."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z6",
                {"Title": _string("plain-title"), "Summary": _string("plain-summary")},
            )
        )
        self.assertEqual(ds.get_title("z6"), "plain-title")

    def test_plain_summary_used_without_plain_title(self) -> None:
        """A plain Summary is used when plain Title is absent."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(
            _record(
                "z7",
                {"Summary": _string("plain-summary"), "AltText": _string("plain-alt")},
            )
        )
        self.assertEqual(ds.get_title("z7"), "plain-summary")

    def test_plain_alt_text_used_as_last_resort(self) -> None:
        """A plain AltText is used as the final title fallback."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("z8", {"AltText": _string("plain-alt")}))
        self.assertEqual(ds.get_title("z8"), "plain-alt")

    def test_absent_title_fields_yield_none(self) -> None:
        """A record without any title candidate resolves to None."""
        ds = CloudKitNoteDataSource()
        ds.add_attachment_record(_record("z9", {"UTI": _string("public.image")}))
        self.assertIsNone(ds.get_title("z9"))


class TestMatchPreviewAppearance(unittest.TestCase):
    """Direct tests for the preview appearance matcher."""

    # pylint: disable=protected-access

    def test_none_appearances_return_none(self) -> None:
        """A non-sequence appearances value yields no match."""
        self.assertIsNone(
            CloudKitNoteDataSource._match_preview_appearance(["a"], None, 0, _url_of)
        )

    def test_length_mismatch_returns_none(self) -> None:
        """Mismatched token/appearance lengths yield no match."""
        self.assertIsNone(
            CloudKitNoteDataSource._match_preview_appearance(
                ["a", "b"], [0], 0, _url_of
            )
        )

    def test_matching_appearance_returns_token(self) -> None:
        """The token at the preferred appearance index is returned."""
        result = CloudKitNoteDataSource._match_preview_appearance(
            [{"downloadURL": "https://x/0"}, {"downloadURL": "https://x/1"}],
            [0, 1],
            1,
            _url_of,
        )
        self.assertEqual(result, "https://x/1")

    def test_no_matching_appearance_returns_none(self) -> None:
        """No appearance matching the preference yields no match."""
        self.assertIsNone(
            CloudKitNoteDataSource._match_preview_appearance(
                [{"downloadURL": "https://x/1"}], [1], 0, _url_of
            )
        )

    def test_invalid_appearance_code_returns_none(self) -> None:
        """A non-integer appearance code is skipped."""
        self.assertIsNone(
            CloudKitNoteDataSource._match_preview_appearance(
                [{"downloadURL": "https://x/0"}], ["light"], 0, _url_of
            )
        )

    def test_missing_token_url_returns_none(self) -> None:
        """A matched token without a usable URL yields no match."""
        self.assertIsNone(
            CloudKitNoteDataSource._match_preview_appearance([{}], [0], 0, _url_of)
        )


class TestGettersAndSetters(unittest.TestCase):
    """Default getters and primary URL overrides."""

    def test_getters_default_to_none(self) -> None:
        """All getters return None for unknown identifiers."""
        ds = CloudKitNoteDataSource()
        self.assertIsNone(ds.get_attachment_uti("missing"))
        self.assertIsNone(ds.get_mergeable_gz("missing"))
        self.assertIsNone(ds.get_primary_asset_url("missing"))
        self.assertIsNone(ds.get_thumbnail_url("missing"))
        self.assertIsNone(ds.get_title("missing"))

    def test_set_primary_asset_url_overrides(self) -> None:
        """set_primary_asset_url stores a replacement URL."""
        ds = CloudKitNoteDataSource()
        ds.set_primary_asset_url("a", "https://x/local")
        self.assertEqual(ds.get_primary_asset_url("a"), "https://x/local")

    def test_set_primary_asset_url_noop_without_identifier(self) -> None:
        """An empty identifier makes set_primary_asset_url a no-op."""
        ds = CloudKitNoteDataSource()
        ds.set_primary_asset_url("", "https://x/local")
        self.assertIsNone(ds.get_primary_asset_url(""))

    def test_set_primary_asset_url_noop_without_url(self) -> None:
        """An empty URL makes set_primary_asset_url a no-op."""
        ds = CloudKitNoteDataSource()
        ds.set_primary_asset_url("a", "")
        self.assertIsNone(ds.get_primary_asset_url("a"))


if __name__ == "__main__":
    unittest.main()
