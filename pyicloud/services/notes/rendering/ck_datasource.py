"""
CloudKit-backed NoteDataSource implementation.

Provides an in-memory datasource for a single Note that can answer:
  - get_attachment_uti(identifier) -> Optional[str]
  - get_mergeable_gz(identifier)   -> Optional[bytes]
  - (optional) get_primary_asset_url/get_thumbnail_url/get_title

Population is performed by feeding CloudKit records into `add_attachment_record`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from pyicloud.common.cloudkit import CKRecord

from .options import ExportConfig
from .renderer_iface import NoteDataSource

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CloudKitNoteDataSource(NoteDataSource):
    _uti: Dict[str, str] = field(default_factory=dict)
    _mergeable_gz: Dict[str, bytes] = field(default_factory=dict)
    _primary_asset_url: Dict[str, str] = field(default_factory=dict)
    _thumbnail_url: Dict[str, str] = field(default_factory=dict)
    _title: Dict[str, str] = field(default_factory=dict)
    _config: Optional[ExportConfig] = None

    # Minimal protocol
    def get_attachment_uti(self, identifier: str) -> Optional[str]:
        return self._uti.get(identifier)

    def get_mergeable_gz(self, identifier: str) -> Optional[bytes]:
        return self._mergeable_gz.get(identifier)

    # Optional richer protocol
    def get_primary_asset_url(self, identifier: str) -> Optional[str]:
        return self._primary_asset_url.get(identifier)

    def get_thumbnail_url(self, identifier: str) -> Optional[str]:
        return self._thumbnail_url.get(identifier)

    def get_title(self, identifier: str) -> Optional[str]:
        return self._title.get(identifier)

    # Overrides for callers that download assets locally and want to point the
    # renderer at a local path instead of the remote CloudKit URL.
    def set_primary_asset_url(self, identifier: str, url: str) -> None:
        if not identifier or not url:
            return
        self._primary_asset_url[identifier] = url

    def add_attachment_record(self, rec: CKRecord) -> None:
        fields = rec.fields

        # With strict model validation, *Encrypted fields are always bytes.
        def _text_from_bytes(val: Optional[bytes | bytearray]) -> Optional[str]:
            if val is None:
                return None
            try:
                return val.decode("utf-8", "replace")
            except Exception:
                return None

        def _asset_url(obj) -> Optional[str]:
            """Best-effort extractor for CloudKit asset token downloadURL.

            Accepts a mapping (dict-like) or an object with attribute `downloadURL`.
            Returns the URL string if present and non-empty.
            """
            if obj is None:
                return None
            try:
                if isinstance(obj, dict):
                    url = obj.get("downloadURL")
                    return url if isinstance(url, str) and url else None
                url = getattr(obj, "downloadURL", None)
                return url if isinstance(url, str) and url else None
            except Exception:
                return None

        # Attachment logical identifier (if present); otherwise, use recordName
        ident: Optional[str] = None
        for key in ("AttachmentIdentifier", "attachmentIdentifier", "Identifier"):
            raw = getattr(fields.get_field(key) or (), "value", None)
            if isinstance(raw, str) and raw:
                ident = raw
                break
        rec_name = rec.recordName or None
        if not ident and not rec_name:
            return

        # Store under both the logical identifier (if present) and the recordName
        keys: list[str] = []
        if ident:
            keys.append(ident)
        if rec_name and rec_name not in keys:
            keys.append(rec_name)

        if rec_name and rec_name not in keys:
            keys.append(rec_name)

        # UTI (plain or encrypted)

        uti_val: Optional[str] = None
        uti_plain = fields.get_value("UTI") or fields.get_value("AttachmentUTI")
        if isinstance(uti_plain, str) and uti_plain:
            uti_val = uti_plain
        else:
            uti_enc = fields.get_value("UTIEncrypted")  # bytes by invariant
            uti_val = (
                _text_from_bytes(uti_enc)
                if isinstance(uti_enc, (bytes, bytearray))
                else None
            )
        if uti_val:
            for k in keys:
                self._uti[k] = uti_val
        uti_l = (uti_val or "").lower()
        is_url_uti = uti_l == "public.url"
        is_pdf_uti = uti_l in ("com.adobe.pdf", "public.pdf", "com.apple.paper.doc.pdf")
        is_image_uti = uti_l.startswith("public.image") or uti_l in {
            "public.jpeg",
            "public.jpg",
            "public.png",
            "public.heic",
            "public.heif",
            "public.tiff",
            "public.gif",
            "public.bmp",
            "public.webp",
            # Treat Apple Notes sketches as image-like to prefer previews/Media
            "com.apple.paper",
        }

        # Mergeable table (gzipped bytes)
        md = fields.get_value("MergeableDataEncrypted")  # bytes by invariant
        if isinstance(md, (bytes, bytearray)) and md:
            for k in keys:
                self._mergeable_gz[k] = bytes(md)

        # Primary/thumbnail asset URLs from common fields
        pa_val = fields.get_value("PrimaryAsset")
        url = _asset_url(pa_val)
        if url:
            for k in keys:
                self._primary_asset_url[k] = url

        # Some attachments (e.g., com.apple.paper) expose preview images instead.
        # Prefer the first PreviewImages URL; fall back to FallbackImage.
        # Prefer a true PDF for paper/pdf UTIs: FallbackPDF, then PaperAssets
        if is_pdf_uti and not any(k in self._primary_asset_url for k in keys):
            fp_fld = fields.get_field("FallbackPDF")
            url = _asset_url(getattr(fp_fld, "value", None) if fp_fld else None)
            if url:
                for k in keys:
                    self._primary_asset_url[k] = url

        if is_pdf_uti and not any(k in self._primary_asset_url for k in keys):
            pa_list_fld = fields.get_field("PaperAssets")
            try:
                tokens = getattr(pa_list_fld, "value", None) if pa_list_fld else None
                if isinstance(tokens, (list, tuple)) and tokens:
                    url = _asset_url(tokens[0])
                    if url:
                        for k in keys:
                            self._primary_asset_url[k] = url
            except Exception:
                pass

        # Thumbnails/previews: expose as thumbnail_url; for image UTIs, we may also
        # use previews as primary when nothing else is available.
        if not any(k in self._primary_asset_url for k in keys) and is_image_uti:
            pi_fld = fields.get_field("PreviewImages")
            try:
                tokens = getattr(pi_fld, "value", None) if pi_fld else None
                if isinstance(tokens, (list, tuple)) and tokens:
                    # Try to align with PreviewAppearances (0=light, 1=dark)
                    app_fld = fields.get_field("PreviewAppearances")
                    apps = getattr(app_fld, "value", None) if app_fld else None
                    # Prefer config preview appearance when supplied, else env
                    pref = "light"
                    try:
                        if self._config and getattr(
                            self._config, "preview_appearance", None
                        ):
                            pref = str(self._config.preview_appearance).strip().lower()
                    except Exception:
                        pref = "light"
                    pref_code = 1 if pref in ("dark", "1", "true", "yes") else 0
                    selected: Optional[str] = None
                    if isinstance(apps, (list, tuple)) and len(apps) == len(tokens):
                        for idx, app in enumerate(apps):
                            try:
                                code = int(app)
                            except Exception:
                                code = None
                            if code == pref_code:
                                selected = _asset_url(tokens[idx])
                                if selected:
                                    break
                    # Fallback: first valid token
                    if not selected:
                        for token in tokens:
                            selected = _asset_url(token)
                            if selected:
                                break
                    if selected:
                        for k in keys:
                            self._primary_asset_url[k] = selected
            except Exception:
                pass
        if not any(k in self._primary_asset_url for k in keys) and is_image_uti:
            fb_fld = fields.get_field("FallbackImage")
            url = _asset_url(getattr(fb_fld, "value", None) if fb_fld else None)
            if url:
                for k in keys:
                    self._primary_asset_url[k] = url

        # Regardless of PDF or not, also capture previews as thumbnail candidates
        # so callers may show a small preview.
        try:
            pi2_fld = fields.get_field("PreviewImages")
            tokens2 = getattr(pi2_fld, "value", None) if pi2_fld else None
            if isinstance(tokens2, (list, tuple)) and tokens2:
                thumb = _asset_url(tokens2[0])
                if thumb:
                    for k in keys:
                        self._thumbnail_url[k] = thumb
        except Exception:
            pass

        # Older fields: a plain URL string
        url_enc = fields.get_value("URLStringEncrypted")  # bytes by invariant
        if (
            isinstance(url_enc, (bytes, bytearray))
            and url_enc
            and not any(k in self._primary_asset_url for k in keys)
        ):
            dec = _text_from_bytes(bytes(url_enc))
            if dec:
                for k in keys:
                    self._primary_asset_url[k] = dec

        # Title (optional) — attempt several common encrypted fields
        titles_try = [
            fields.get_value("TitleEncrypted"),
            fields.get_value("SummaryEncrypted"),
            fields.get_value("LocalizedTitleEncrypted"),
            fields.get_value("AltTextEncrypted"),
            # Inline tokens sometimes carry a canonical identifier separate from AltText.
            fields.get_value("TokenContentIdentifierEncrypted"),
            # Also try unencrypted fields, just in case
            fields.get_value("Title"),
            fields.get_value("Summary"),
            fields.get_value("AltText"),
        ]
        found_title = False
        for tv in titles_try:
            dec_title = None
            if isinstance(tv, (bytes, bytearray)):
                dec_title = _text_from_bytes(tv)
            elif isinstance(tv, str):
                dec_title = tv

            if dec_title:
                found_title = True
                for k in keys:
                    self._title[k] = dec_title
                break

        # For web links, a URL is still a useful visible label when no richer
        # title is available. Avoid applying this fallback to images/media so
        # signed CloudKit URLs do not leak into rendered labels or alt text.
        if not found_title and is_url_uti:
            # We look for the URLStringEncrypted which we might have already decoded
            url_val = self._primary_asset_url.get(keys[0] if keys else "")
            # Or try URLString raw
            if not url_val:
                url_raw = fields.get_value("URLString")
                if isinstance(url_raw, str) and url_raw:
                    url_val = url_raw

            if url_val:
                # If we have a URL but no title, use the URL as the title
                for k in keys:
                    self._title[k] = url_val

        # Optional thumbnail (best-effort via Media)
        # For types like VCard or Audio, 'Media' might be the only asset source.
        # So if we lack a primary asset, use Media as the primary too.
        media_val = fields.get_value("Media")
        thumb_url = _asset_url(media_val)
        if thumb_url:
            for k in keys:
                self._thumbnail_url[k] = thumb_url
                if k not in self._primary_asset_url:
                    self._primary_asset_url[k] = thumb_url

        try:
            LOGGER.debug(
                "ckds.add_attachment_record",
                extra={
                    "component": "notes",
                    "op": "ckds.add_attachment_record",
                    "record_name": rec.recordName,
                    "identifier": ident,
                    "has_uti": bool(uti_val),
                    "has_mergeable": any(k in self._mergeable_gz for k in keys),
                },
            )
        except Exception:
            pass
