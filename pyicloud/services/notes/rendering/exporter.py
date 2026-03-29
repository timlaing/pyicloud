"""
Exporter helpers for Apple Notes → HTML.

These functions are thin, testable wrappers around the existing decoding,
datasource hydration, rendering, and file I/O utilities. They are intentionally
pure (no global state) and perform only the minimal work needed by CLI tools
and higher-level APIs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

from rich.console import Console

from pyicloud.common.cloudkit import CKRecord

from ..decoding import BodyDecoder
from ..protobuf import notes_pb2 as pb
from .ck_datasource import CloudKitNoteDataSource
from .options import ExportConfig
from .renderer import NoteRenderer, render_note_fragment, render_note_page

console = Console()

LOGGER = logging.getLogger(__name__)


def decode_and_parse_note(record: CKRecord) -> Optional[pb.Note]:
    """Decode a Note CKRecord's TextDataEncrypted and return a parsed pb.Note.

    Returns None if body is missing or cannot be parsed.
    """
    if not isinstance(record, CKRecord):
        return None
    raw = record.fields.get_value("TextDataEncrypted")
    if not raw:
        return None
    try:
        nb = BodyDecoder().decode(raw)
        if not nb or not getattr(nb, "bytes", None):
            return None
        msg = pb.NoteStoreProto()
        msg.ParseFromString(nb.bytes)
        return getattr(getattr(msg, "document", None), "note", None)
    except Exception:
        return None


def _attachment_ids_from_record_and_runs(record: CKRecord, note: pb.Note) -> List[str]:
    # Collect from Attachments field
    ids: List[str] = []
    fld = record.fields.get_field("Attachments")
    if fld and hasattr(fld, "value"):
        for ref in getattr(fld, "value", []) or []:
            rn = getattr(ref, "recordName", None)
            if rn:
                ids.append(rn)
    # Merge inline run identifiers
    ids_from_runs: List[str] = []
    for rattr in getattr(note, "attribute_run", []) or []:
        if rattr.HasField("attachment_info") and rattr.attachment_info.HasField(
            "attachment_identifier"
        ):
            aid = rattr.attachment_info.attachment_identifier
            if aid:
                ids_from_runs.append(aid)
    seen: set[str] = set()
    merged: List[str] = []
    for a in ids + ids_from_runs:
        if a not in seen:
            seen.add(a)
            merged.append(a)
    return merged


def build_datasource(
    ck_client,
    note_record: CKRecord,
    note: pb.Note,
    config: Optional[ExportConfig] = None,
) -> Tuple[CloudKitNoteDataSource, List[str]]:
    """Build a CloudKit-backed Note datasource for a single note.

    Returns (datasource, attachment_ids) where attachment_ids is the merged list
    of attachment record names to which the datasource has been hydrated.
    """
    ds = CloudKitNoteDataSource(_config=config)
    att_ids = _attachment_ids_from_record_and_runs(note_record, note)
    if att_ids:
        resp = ck_client.lookup(att_ids)  # desired_keys=None → all fields
        media_map: Dict[str, str] = {}  # media_record_name -> parent attachment id
        debug = bool(getattr(config, "debug", False))
        for rec_idx, rec in enumerate(resp.records):
            if debug:
                console.rule(f"rec_idx {rec_idx}")
                console.print(rec)
            if isinstance(rec, CKRecord):
                ds.add_attachment_record(rec)
                # Capture Media reference to follow for full-fidelity images
                try:
                    fld = rec.fields.get_field("Media")
                    ref = getattr(fld, "value", None) if fld else None
                    rn = getattr(ref, "recordName", None)
                    if rn:
                        media_map[rn] = rec.recordName
                except Exception:
                    pass
        # Follow Media references to fetch original asset URLs and wire them to the parent
        if media_map:
            try:
                mresp = ck_client.lookup(list(media_map.keys()))
                if bool(getattr(config, "debug", False)):
                    try:
                        console.rule("media lookup response")
                        console.print(mresp)
                        LOGGER.info("attachment media resp:\n%s", mresp)
                    except Exception:
                        pass
                for mrec in mresp.records:
                    if not isinstance(mrec, CKRecord):
                        continue
                    url: Optional[str] = None
                    # Best-effort: find any field whose value looks like an asset token with downloadURL
                    try:
                        for k in list(getattr(mrec, "fields", ()).keys()):
                            fld = mrec.fields.get_field(k)
                            val = getattr(fld, "value", None)
                            u = getattr(val, "downloadURL", None)
                            if isinstance(u, str) and u:
                                url = u
                                break
                    except Exception:
                        url = None
                    if url:
                        parent = media_map.get(mrec.recordName)
                        if parent:
                            # Only promote Media-derived URLs to primary for image-like attachments.
                            # For 'public.url' (web links) and others, keep the primary_url as the
                            # actual destination, and use previews/Media only as thumbnails.
                            try:
                                parent_uti = (
                                    ds.get_attachment_uti(parent) or ""
                                ).lower()
                            except Exception:
                                parent_uti = ""
                            # Use config-aware predicate to recognize image UTIs (jpeg/png/heic/webp...)
                            conf = config or ExportConfig()
                            is_image = conf.is_image_uti(parent_uti)
                            # Simple heuristic for audio/video promotion
                            is_av = (
                                "audio" in parent_uti
                                or "video" in parent_uti
                                or "movie" in parent_uti
                                or "mpeg" in parent_uti
                            )

                            # Logic update:
                            # 1. If we have no URL yet (e.g. VCard), ALWAYS take the Media URL.
                            # 2. If we have a URL but it's an Image/AV, check if we should "upgrade"
                            #    to the Media URL (e.g. valid preview -> full res).

                            is_media_upgrade = getattr(
                                conf, "prefer_media_for_images", True
                            ) and (is_image or is_av)

                            # Only fetch current if we might need to check for upgrade
                            cur_primary = None
                            try:
                                cur_primary = ds.get_primary_asset_url(parent)
                            except Exception:
                                pass

                            if (not cur_primary) or is_media_upgrade:
                                try:
                                    cur_thumb = ds.get_thumbnail_url(parent)
                                except Exception:
                                    cur_thumb = None

                                # If missing, OR (we want upgrade AND current is likely just a thumbnail)
                                if (not cur_primary) or (
                                    is_media_upgrade and cur_primary == cur_thumb
                                ):
                                    ds.set_primary_asset_url(parent, url)
            except Exception:
                pass
    return ds, att_ids


def download_pdf_assets(
    ck_client,
    ds: CloudKitNoteDataSource,
    att_ids: Iterable[str],
    *,
    assets_dir: str,
    out_dir: str,
    config: Optional[ExportConfig] = None,
) -> Dict[str, str]:
    """Download PDFs for attachments and rewrite datasource URLs to local paths.

    Returns a mapping of attachment id → relative path used in HTML.
    Only applies to PDF UTIs. Files are renamed with `.pdf` extension when the
    magic header is present.
    """
    os.makedirs(assets_dir, exist_ok=True)
    updated: Dict[str, str] = {}

    def _is_pdf_uti(s: Optional[str]) -> bool:
        return bool(
            s
            and s.lower() in ("com.adobe.pdf", "public.pdf", "com.apple.paper.doc.pdf")
        )

    note_subdir = os.path.abspath(assets_dir)
    for aid in att_ids:
        uti = (ds.get_attachment_uti(aid) or "").lower()
        if not _is_pdf_uti(uti):
            continue
        url = ds.get_primary_asset_url(aid)
        if not (url and (url.startswith("http://") or url.startswith("https://"))):
            # Skip thumbnails for PDFs — they are images and will not embed as PDF
            continue
        try:
            saved_path = ck_client.download_asset_to(url, note_subdir)
            resolved = saved_path
            try:
                with open(saved_path, "rb") as fh:
                    head = fh.read(5)
                if head.startswith(b"%PDF-") and not saved_path.lower().endswith(
                    ".pdf"
                ):
                    new_path = saved_path + ".pdf"
                    try:
                        os.replace(saved_path, new_path)
                        resolved = new_path
                    except Exception:
                        resolved = saved_path
            except Exception:
                resolved = saved_path
            rel = os.path.relpath(resolved, start=os.path.abspath(out_dir))
            ds.set_primary_asset_url(aid, rel)
            updated[aid] = rel
        except Exception:
            # Ignore individual download failures; caller can log
            pass
    return updated


def download_image_assets(
    ck_client,
    ds: CloudKitNoteDataSource,
    att_ids: Iterable[str],
    *,
    assets_dir: str,
    out_dir: str,
    config: Optional[ExportConfig] = None,
) -> Dict[str, str]:
    """Download image attachments and rewrite datasource URLs to local paths.

    Returns a mapping of attachment id → relative path used in HTML.
    Applies to common image UTIs (jpeg/png/heic/webp/gif/bmp/tiff...).
    """
    os.makedirs(assets_dir, exist_ok=True)
    updated: Dict[str, str] = {}

    conf = config or ExportConfig()

    def _infer_image_ext(head: bytes) -> Optional[str]:
        try:
            if head.startswith(b"\xff\xd8\xff"):
                return ".jpg"
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return ".png"
            if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
                return ".gif"
            if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
                return ".webp"
            # ISO Base Media File (HEIC/HEIF) — look for ftyp box
            # Common brands: 'heic', 'heif', 'mif1', 'msf1', 'hevc'
            if len(head) >= 12 and head[4:8] == b"ftyp":
                brand = head[8:12]
                if brand in (b"heic", b"heif", b"mif1", b"msf1", b"hevc"):
                    return ".heic"
            if head.startswith(b"BM"):
                return ".bmp"
            # TIFF
            if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
                return ".tiff"
        except Exception:
            pass
        return None

    note_subdir = os.path.abspath(assets_dir)
    for aid in att_ids:
        uti = (ds.get_attachment_uti(aid) or "").lower()
        if not conf.is_image_uti(uti):
            continue
        url = ds.get_primary_asset_url(aid)
        if not url:
            # Fallback to thumbnail for image-like attachments that only expose previews (e.g., com.apple.paper)
            try:
                url = ds.get_thumbnail_url(aid)
            except Exception:
                url = None
        if not (url and (url.startswith("http://") or url.startswith("https://"))):
            # Already local or missing
            continue
        try:
            saved_path = ck_client.download_asset_to(url, note_subdir)
            resolved = saved_path
            try:
                with open(saved_path, "rb") as fh:
                    head = fh.read(16)
                ext = _infer_image_ext(head)
                if ext and not saved_path.lower().endswith(ext):
                    new_path = saved_path + ext
                    try:
                        os.replace(saved_path, new_path)
                        resolved = new_path
                    except Exception:
                        resolved = saved_path
            except Exception:
                resolved = saved_path
            rel = os.path.relpath(resolved, start=os.path.abspath(out_dir))
            ds.set_primary_asset_url(aid, rel)
            updated[aid] = rel
        except Exception:
            # Ignore individual failures; caller can log or continue
            pass
    return updated


def download_av_assets(
    ck_client,
    ds: CloudKitNoteDataSource,
    att_ids: Iterable[str],
    *,
    assets_dir: str,
    out_dir: str,
    config: Optional[ExportConfig] = None,
) -> Dict[str, str]:
    """Download audio/video attachments and rewrite datasource URLs to local paths.

    Returns a mapping of attachment id → relative path used in HTML.
    """
    os.makedirs(assets_dir, exist_ok=True)
    updated: Dict[str, str] = {}

    def _infer_av_ext(head: bytes) -> Optional[str]:
        try:
            # M4A / MP4 / MOV (ISO Base Media)
            if len(head) >= 12 and head[4:8] == b"ftyp":
                brand = head[8:12]
                if brand in (b"M4A ", b"mp42", b"isom"):
                    return ".m4a"
                if brand in (b"qt  ", b"moov"):
                    return ".mov"
                # Fallback for generic MP4/QuickTime
                return ".mp4"

            # QuickTime (moov atom at start)
            if len(head) >= 8 and head[4:8] == b"moov":
                return ".mov"

            # MP3 - ID3v2 container
            if head.startswith(b"ID3"):
                return ".mp3"
            # MP3 - frame sync (approximate)
            if (
                head.startswith(b"\xff\xfb")
                or head.startswith(b"\xff\xf3")
                or head.startswith(b"\xff\xf2")
            ):
                return ".mp3"
            # WAVE
            if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
                return ".wav"
            # AVI
            if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
                return ".avi"
        except Exception:
            pass
        return None

    note_subdir = os.path.abspath(assets_dir)
    for aid in att_ids:
        uti = (ds.get_attachment_uti(aid) or "").lower()
        # Basic check for audio or video
        is_av = (
            "audio" in uti
            or "video" in uti
            or "mpeg" in uti
            or "movie" in uti
            or "quicktime" in uti
        )
        if not is_av:
            continue

        url = ds.get_primary_asset_url(aid)
        if not (url and (url.startswith("http://") or url.startswith("https://"))):
            continue

        try:
            saved_path = ck_client.download_asset_to(url, note_subdir)
            resolved = saved_path
            try:
                with open(saved_path, "rb") as fh:
                    head = fh.read(16)
                ext = _infer_av_ext(head)
                # Fallbacks for common Apple types
                if not ext:
                    if "com.apple.m4a-audio" in uti:
                        ext = ".m4a"
                    elif "quicktime" in uti:
                        ext = ".mov"

                if ext and not saved_path.lower().endswith(ext):
                    new_path = saved_path + ext
                    try:
                        os.replace(saved_path, new_path)
                        resolved = new_path
                    except Exception:
                        resolved = saved_path
            except Exception:
                resolved = saved_path

            rel = os.path.relpath(resolved, start=os.path.abspath(out_dir))
            ds.set_primary_asset_url(aid, rel)
            updated[aid] = rel
        except Exception:
            pass
    return updated


def download_vcard_assets(
    ck_client,
    ds: CloudKitNoteDataSource,
    att_ids: Iterable[str],
    *,
    assets_dir: str,
    out_dir: str,
    config: Optional[ExportConfig] = None,
) -> Dict[str, str]:
    """Download VCard (contact) attachments and rewrite datasource URLs to local paths.

    Returns a mapping of attachment id → relative path used in HTML.
    """
    os.makedirs(assets_dir, exist_ok=True)
    updated: Dict[str, str] = {}
    note_subdir = os.path.abspath(assets_dir)

    for aid in att_ids:
        uti = (ds.get_attachment_uti(aid) or "").lower()
        if "public.vcard" not in uti:
            continue

        url = ds.get_primary_asset_url(aid)
        if not (url and (url.startswith("http://") or url.startswith("https://"))):
            continue

        try:
            saved_path = ck_client.download_asset_to(url, note_subdir)
            resolved = saved_path

            # Ensure .vcf extension
            if not saved_path.lower().endswith(".vcf"):
                new_path = saved_path + ".vcf"
                try:
                    os.replace(saved_path, new_path)
                    resolved = new_path
                except Exception:
                    resolved = saved_path

            rel = os.path.relpath(resolved, start=os.path.abspath(out_dir))
            ds.set_primary_asset_url(aid, rel)
            updated[aid] = rel
        except Exception:
            pass

    return updated


def render_fragment(
    note: pb.Note,
    ds: Optional[CloudKitNoteDataSource],
    config: Optional[ExportConfig] = None,
) -> str:
    return render_note_fragment(note, ds, config=config)


def _safe_name(s: Optional[str]) -> str:
    if not s:
        return "untitled"
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^\w\- ]+", "-", s)
    return s[:60] or "untitled"


def write_html(
    title: str,
    html_fragment: str,
    out_dir: str,
    *,
    full_page: bool = False,
    filename: Optional[str] = None,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    page = render_note_page(title, html_fragment) if full_page else html_fragment
    fname = filename or f"{_safe_name(title)}.html"
    root = os.path.abspath(out_dir)
    path = os.path.abspath(os.path.join(root, fname))
    if os.path.commonpath([root, path]) != root:
        raise ValueError("filename must stay within out_dir")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


class NoteExporter:
    """Orchestrator for exporting notes to HTML with assets."""

    def __init__(self, ck_client, config: Optional[ExportConfig] = None):
        self.client = ck_client
        self.config = config or ExportConfig()
        self.renderer = NoteRenderer(self.config)

    def export(
        self,
        note_record: CKRecord,
        output_dir: str,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Export a single note record to HTML in the output directory.
        Returns the path to the written HTML file, or None if export failed (e.g. no body).
        """
        # 1. Decode
        note = decode_and_parse_note(note_record)
        if not note:
            return None

        # 2. Build Datasource
        ds, att_ids = build_datasource(self.client, note_record, note, self.config)

        # 3. Download Assets when doing archival export
        export_mode = str(getattr(self.config, "export_mode", "archival") or "archival")
        export_mode = export_mode.strip().lower()
        if export_mode == "archival":
            assets_root = getattr(self.config, "assets_dir", None) or os.path.join(
                output_dir, "assets"
            )
            assets_dir = os.path.join(assets_root, note_record.recordName)

            download_pdf_assets(
                self.client,
                ds,
                att_ids,
                assets_dir=assets_dir,
                out_dir=output_dir,
                config=self.config,
            )
            download_image_assets(
                self.client,
                ds,
                att_ids,
                assets_dir=assets_dir,
                out_dir=output_dir,
                config=self.config,
            )
            download_av_assets(
                self.client,
                ds,
                att_ids,
                assets_dir=assets_dir,
                out_dir=output_dir,
                config=self.config,
            )
            download_vcard_assets(
                self.client,
                ds,
                att_ids,
                assets_dir=assets_dir,
                out_dir=output_dir,
                config=self.config,
            )

        # 4. Render
        html_fragment = self.renderer.render(note, ds)

        # 5. Write
        title = "Untitled"
        title_enc = note_record.fields.get_value("TitleEncrypted")
        if title_enc:
            try:
                if isinstance(title_enc, bytes):
                    title = title_enc.decode("utf-8")
                elif isinstance(title_enc, str):
                    title = title_enc
            except Exception:
                pass

        full_page = getattr(self.config, "full_page", None)
        if full_page is None:
            full_page = True

        return write_html(
            title,
            html_fragment,
            output_dir,
            full_page=bool(full_page),
            filename=filename,
        )
