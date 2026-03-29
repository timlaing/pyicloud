"""Developer utility for searching, inspecting, and exporting iCloud Notes.

Run:
    uv run python examples/notes_cli.py --username you@example.com ...

This script is built on top of ``api.notes`` for local exploration and export
workflows. It is useful for debugging note selection, rendering, and HTML
exports, but it is not the primary public API for the Notes service.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any, List, Optional

# Ensure pyicloud can be imported when running from examples/ directly.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console
from rich.logging import RichHandler

from pyicloud import PyiCloudService
from pyicloud.common.cloudkit import CKRecord
from pyicloud.exceptions import PyiCloudServiceUnavailable
from pyicloud.services.notes.rendering.exporter import decode_and_parse_note
from pyicloud.services.notes.rendering.options import ExportConfig
from pyicloud.utils import get_password

console = Console()
logger = logging.getLogger("notes.explore")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Developer utility for exploring and exporting iCloud Notes"
    )
    p.add_argument("--username", dest="username", required=True, help="Apple ID")
    p.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Enable verbose logs and detailed output",
    )
    p.add_argument(
        "--cookie-dir",
        dest="cookie_dir",
        default="",
        help="Directory to store session cookies",
    )
    p.add_argument(
        "--china-mainland",
        action="store_true",
        dest="china_mainland",
        default=False,
        help="Set if Apple ID region is China mainland",
    )
    p.add_argument(
        "--max",
        dest="max_items",
        type=int,
        default=20,
        help="How many most recent notes to render (default: 20)",
    )
    p.add_argument(
        "--title",
        dest="title",
        default="",
        help="Only render notes whose title exactly matches this string",
    )
    p.add_argument(
        "--title-contains",
        dest="title_contains",
        default="",
        help="Only render notes whose title contains this substring (case-insensitive)",
    )
    p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=os.path.join("workspace", "notes_html"),
        help="Directory to write rendered HTML output (default: workspace/notes_html)",
    )
    p.add_argument(
        "--full-page",
        dest="full_page",
        action="store_true",
        default=False,
        help="Wrap saved output in a full HTML page; if omitted, save an HTML fragment",
    )
    p.add_argument(
        "--dump-runs",
        dest="dump_runs",
        action="store_true",
        default=False,
        help="Dump attribute runs and write an annotated mapping under workspace/notes_runs",
    )
    p.add_argument(
        "--assets-dir",
        dest="assets_dir",
        default=os.path.join("exports", "assets"),
        help="Directory to store downloaded assets in archival export mode (default: exports/assets)",
    )
    p.add_argument(
        "--export-mode",
        dest="export_mode",
        choices=["archival", "lightweight"],
        default="archival",
        help="Export intent: 'archival' downloads assets for stable, offline HTML (default); 'lightweight' skips downloads for quick previews",
    )
    p.add_argument(
        "--notes-debug",
        dest="notes_debug",
        action="store_true",
        default=False,
        help="Enable verbose Notes/export debug output (datasource, attachments, and rendering)",
    )
    p.add_argument(
        "--preview-appearance",
        dest="preview_appearance",
        choices=["light", "dark"],
        default="light",
        help="Select which preview appearance to prefer for image previews (light/dark)",
    )
    p.add_argument(
        "--pdf-height",
        dest="pdf_height",
        type=int,
        default=600,
        help="Height in pixels for embedded PDF objects (default: 600)",
    )
    return p.parse_args()


def ensure_auth(api: PyiCloudService) -> None:
    if api.requires_2fa:
        fido2_devices = list(api.fido2_devices)
        if fido2_devices:
            logger.info("Security key verification required.")
            for index, _device in enumerate(fido2_devices):
                logger.info("  %d: Security key %d", index, index)
            sel = input("Select security key index [0]: ").strip()
            try:
                idx = int(sel) if sel else 0
            except ValueError:
                idx = 0
            if idx < 0 or idx >= len(fido2_devices):
                logger.warning("Invalid selection; defaulting to security key 0")
                idx = 0
            logger.info("Touch the selected security key to continue.")
            try:
                api.confirm_security_key(fido2_devices[idx])
            except Exception as exc:  # pragma: no cover - live auth path
                raise RuntimeError("Security key verification failed") from exc
        else:
            logger.info("Two-factor authentication required.")
            code = input("Enter the 2FA code: ")
            if not api.validate_2fa_code(code):
                raise RuntimeError("Failed to verify 2FA code")
        if not api.is_trusted_session:
            api.trust_session()
    elif api.requires_2sa:
        logger.info("Two-step authentication required.")
        devices: List[dict[str, Any]] = api.trusted_devices
        if not devices:
            raise RuntimeError("No trusted devices available for 2SA")
        for i, _device in enumerate(devices):
            logger.info("  %d: Trusted device", i)
        sel = input("Select device index [0]: ").strip()
        try:
            idx = int(sel) if sel else 0
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(devices):
            logger.warning("Invalid selection; defaulting to device 0")
            idx = 0
        device = devices[idx]
        if not api.send_verification_code(device):
            raise RuntimeError("Failed to send verification code")
        code = input("Enter verification code: ")
        if not api.validate_verification_code(device, code):
            raise RuntimeError("Failed to verify code")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                markup=True,
                show_time=True,
                log_time_format="%H:%M:%S",
            )
        ],
    )

    args = parse_args()

    import time

    t0 = time.perf_counter()

    def phase(msg: str) -> None:
        try:
            dt = time.perf_counter() - t0
            logger.info("[+%.3fs] %s", dt, msg)
        except Exception:
            logger.info(msg)

    if args.verbose:
        logging.getLogger("pyicloud.services.notes.service").setLevel(logging.DEBUG)
        logging.getLogger("pyicloud.services.notes.client").setLevel(logging.DEBUG)

    debug_dir = os.path.join("workspace", "notes_debug")
    if os.getenv("PYICLOUD_CK_EXTRA") == "forbid":
        logger.info(
            "[yellow]Strict CloudKit validation is enabled[/yellow].\n"
            "Errors and raw payloads may be easier to diagnose under: [bold]%s[/bold]",
            debug_dir,
        )

    phase("bootstrap: starting authentication")
    pw = get_password(args.username)
    api = PyiCloudService(
        apple_id=args.username,
        password=pw,
        china_mainland=args.china_mainland,
        cookie_directory=args.cookie_dir or None,
    )
    ensure_auth(api)
    phase("bootstrap: authentication complete")

    try:
        phase("service: initializing NotesService")
        notes = api.notes
        phase("service: NotesService ready")
    except PyiCloudServiceUnavailable as exc:
        logger.error("Notes service not available: %s", exc)
        return

    max_items = max(1, int(args.max_items))
    out_dir = args.output_dir
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as exc:
        logger.error("Failed to create output directory '%s': %s", out_dir, exc)
        return

    def _safe_name(s: Optional[str]) -> str:
        if not s:
            return "untitled"
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"[^\w\- ]+", "-", s)
        return s[:60] or "untitled"

    def _match_title(title: Optional[str]) -> bool:
        if not title:
            return False
        if args.title and title == args.title:
            return True
        if args.title_contains and args.title_contains.lower() in title.lower():
            return True
        return False

    candidates = []
    if args.title or args.title_contains:
        logger.info("[bold]\nSearching notes by title[/bold]")
        phase(
            "selection: recents-first title search (exact='%s' contains='%s')"
            % (args.title, args.title_contains)
        )
        try:
            window = max(500, max_items * 50)
            seen: set[str] = set()
            for note in notes.recents(limit=window):
                if _match_title(note.title or ""):
                    if note.id not in seen:
                        candidates.append(note)
                        seen.add(note.id)
                    if len(candidates) >= max_items:
                        break
            phase(
                f"selection: recents matched {len(candidates)} candidate(s) in window={window}"
            )

            if len(candidates) < max_items:
                phase("selection: fallback to full feed scan (iter_all)")
                for note in notes.iter_all():
                    if _match_title(note.title or "") and note.id not in seen:
                        candidates.append(note)
                        seen.add(note.id)
                        if len(candidates) >= max_items:
                            break
                phase(f"selection: total matched {len(candidates)} candidate(s)")

            try:
                from datetime import datetime, timezone

                epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
                candidates.sort(key=lambda x: x.modified_at or epoch, reverse=True)
            except Exception:
                pass
        except Exception as exc:
            logger.error("Title search failed, falling back to recents: %s", exc)

    if not candidates:
        logger.info("[bold]\nMost Recent Notes (HTML)[/bold]")
        phase(f"selection: loading {max_items} most recent notes")
        for note in notes.recents(limit=max_items):
            candidates.append(note)
        phase(f"selection: using {len(candidates)} recent note(s)")

    for idx, item in enumerate(candidates):
        phase(f"note[{idx}]: start '{(item.title or 'untitled')}'")
        if args.verbose or args.notes_debug:
            console.rule(f"idx: {idx}")
            console.print(item, end="\n\n")

        ck = notes.raw
        phase(f"note[{idx}]: ck.lookup(TextDataEncrypted,Attachments,TitleEncrypted)")
        resp = ck.lookup(
            [item.id],
            desired_keys=["TextDataEncrypted", "Attachments", "TitleEncrypted"],
        )
        note_rec = None
        for record in resp.records:
            if isinstance(record, CKRecord) and record.recordName == item.id:
                note_rec = record
                break
        if note_rec is None:
            console.print(f"[red]Note lookup returned no CKRecord for {item.id}[/red]")
            continue

        phase(f"note[{idx}]: decode+parse start")
        proto_note = decode_and_parse_note(note_rec)
        phase(f"note[{idx}]: decode+parse ok")
        if args.notes_debug:
            console.print("proto_note:")
            console.print(proto_note, end="\n\n")

        from pyicloud.services.notes.rendering.exporter import NoteExporter

        phase(f"note[{idx}]: exporter init")
        config = ExportConfig(
            debug=bool(args.notes_debug),
            export_mode=str(args.export_mode).strip().lower(),
            assets_dir=args.assets_dir or None,
            full_page=bool(args.full_page),
            preview_appearance=str(args.preview_appearance).strip().lower(),
            pdf_object_height=int(args.pdf_height or 600),
        )
        exporter = NoteExporter(ck, config=config)
        phase(f"note[{idx}]: export start")

        title = item.title or "Apple Note"
        safe = _safe_name(title)
        short_id = (item.id or "note")[:8]
        filename = f"{idx:02d}_{safe}_{short_id}.html"

        try:
            path = exporter.export(note_rec, output_dir=out_dir, filename=filename)
            phase(f"note[{idx}]: export done -> {path}")
            if path:
                console.print(f"[green]Saved:[/green] {path}")
            else:
                console.print("[red]Export returned None (skipped?)[/red]")
        except Exception as exc:
            phase(f"note[{idx}]: export failed: {exc}")
            console.print(f"[red]Export failed:[/red] {exc}")

        if args.dump_runs:
            try:
                from pyicloud.services.notes.rendering.debug_tools import (
                    annotate_note_runs_html,
                    dump_runs_text,
                    map_merged_runs,
                )

                console.rule("attribute runs (utf16 mapping)")
                console.print(dump_runs_text(proto_note))

                merged = map_merged_runs(proto_note)
                console.rule("merged runs (post-merge)")
                lines = []
                for row in merged:
                    raw = str(row.get("text", ""))
                    pretty = (
                        raw.replace("\n", "⏎\n")
                        .replace("\u2028", "⤶\n")
                        .replace("\x00", "␀")
                        .replace("\ufffc", "{OBJ}")
                    )
                    lines.append(
                        f"[{row['index']:03d}] off={row['utf16_start']:<5} len={row['utf16_len']:<4} text=“{pretty}”"
                    )
                console.print("\n".join(lines))

                runs_dir = os.path.join("workspace", "notes_runs")
                os.makedirs(runs_dir, exist_ok=True)
                runs_name = f"{idx:02d}_{_safe_name(item.title)}_{(item.id or 'note')[:8]}_runs.html"
                runs_path = os.path.join(runs_dir, runs_name)
                with open(runs_path, "w", encoding="utf-8") as handle:
                    handle.write(annotate_note_runs_html(proto_note))
                console.print(f"[cyan]Saved runs map:[/cyan] {runs_path}")
            except Exception as exc:
                console.print(f"[red]Failed to dump runs:[/red] {exc}")

    try:
        import time as _time

        logger.info("[+%.3fs] completed", _time.perf_counter() - t0)
    except Exception:
        logger.info("completed")


if __name__ == "__main__":
    main()
