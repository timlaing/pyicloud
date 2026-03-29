"""
Debug helpers for mapping AttributeRuns to the exact text slices they cover.

These utilities are intended for troubleshooting renderer issues. They do not
perform any network I/O and can be safely used in tests.
"""

from __future__ import annotations

import html
from typing import Dict, List, Optional

from ..protobuf import notes_pb2 as pb

# We intentionally import the private helper; it's stable within this repo.
from .renderer import StyleSig, _merge_runs, _slice_for_run  # type: ignore


def _enum_name(enum_cls, value: Optional[int]) -> str:
    if value is None:
        return "(none)"
    try:
        return enum_cls.Name(int(value))  # type: ignore[attr-defined]
    except Exception:
        return str(value)


def map_attribute_runs(note: pb.Note) -> List[Dict[str, object]]:
    """Return a list of dictionaries mapping each AttributeRun to its text.

    Each dict contains:
      - index: run index
      - utf16_start: start offset in UTF-16 code units
      - utf16_len: run.length
      - text: Python string slice for the run
      - style_type, alignment, writing_direction, indent_amount
      - has_attachment: whether run carries attachment_info
    """
    text = note.note_text or ""
    pos = 0
    out: List[Dict[str, object]] = []
    for idx, r in enumerate(note.attribute_run):
        seg, pos2 = _slice_for_run(text, pos, r.length)
        ps = r.paragraph_style if r.HasField("paragraph_style") else None
        out.append(
            {
                "index": idx,
                "utf16_start": pos,
                "utf16_len": int(getattr(r, "length", 0) or 0),
                "text": seg,
                "style_type": getattr(ps, "style_type", None)
                if ps is not None
                else None,
                "alignment": getattr(ps, "alignment", None) if ps is not None else None,
                "writing_direction": getattr(ps, "writing_direction_paragraph", None)
                if ps is not None
                else None,
                "indent_amount": getattr(ps, "indent_amount", None)
                if ps is not None
                else None,
                "has_attachment": bool(r.HasField("attachment_info")),
            }
        )
        pos = pos2
    return out


def dump_runs_text(note: pb.Note) -> str:
    """Return a human-readable dump of runs with escaped whitespace markers."""
    rows = []
    for row in map_attribute_runs(note):
        raw = str(row["text"]) if row.get("text") is not None else ""
        # Make control characters explicit to see line boundaries clearly
        pretty = (
            raw.replace("\n", "⏎\n")
            .replace("\u2028", "⤶\n")
            .replace("\x00", "␀")
            .replace("\ufffc", "{OBJ}")
        )
        st_name = _enum_name(pb.StyleType, row.get("style_type"))
        align = _enum_name(pb.Alignment, row.get("alignment"))
        wd = _enum_name(pb.WritingDirection, row.get("writing_direction"))
        indent = row.get("indent_amount")
        rows.append(
            f"[{row['index']:03d}] off={row['utf16_start']:<5} len={row['utf16_len']:<4} "
            f"style={st_name:<26} indent={indent!s:<2} align={align:<16} wd={wd:<8} "
            f"text=“{pretty}”"
        )
    return "\n".join(rows)


def annotate_note_runs_html(note: pb.Note) -> str:
    """Return a small HTML page highlighting each run in a different color.

    Hover tooltips include run index, offsets, and style information.
    """
    palette = [
        "#FFF3CD",  # yellow
        "#D1ECF1",  # cyan
        "#F8D7DA",  # pink
        "#D4EDDA",  # green
        "#E2E3E5",  # gray
    ]
    spans: List[str] = []
    for row in map_attribute_runs(note):
        idx = int(row["index"])  # type: ignore[arg-type]
        bg = palette[idx % len(palette)]
        raw = str(row.get("text", ""))
        tip = (
            f"run {idx} | off={row['utf16_start']} len={row['utf16_len']} | "
            f"{_enum_name(pb.StyleType, row.get('style_type'))} ind={row.get('indent_amount')} | "
            f"{_enum_name(pb.Alignment, row.get('alignment'))} | "
            f"{_enum_name(pb.WritingDirection, row.get('writing_direction'))}"
        )
        safe = (
            html.escape(raw)
            .replace("\u2028", "<span class=lb>⤶</span>")
            .replace("\n", "<span class=lb>⏎</span>")
            .replace("\ufffc", "<span class=obj>{OBJ}</span>")
            .replace("\x00", "<span class=null>␀</span>")
        )
        spans.append(
            f'<span class="run" title="{html.escape(tip)}" style="background:{bg}">{safe}</span>'
        )

    content = "".join(spans)
    return (
        '<!doctype html><meta charset="utf-8">'
        "<style>body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5}"
        ".run{padding:0 .15em;margin:.05em;border-radius:.2em}"
        ".lb{color:#888} .obj{color:#960} .null{color:#c00}"
        "pre{white-space:pre-wrap;border:1px solid #eee;padding:.5em}</style>"
        f"<pre>{content}</pre>"
    )


def map_merged_runs(note: pb.Note) -> List[Dict[str, object]]:
    """Same as map_attribute_runs, but after the renderer's run merge step.

    Useful to understand how the renderer will chunk paragraphs.
    """
    text = note.note_text or ""
    merged = _merge_runs(note.attribute_run)
    out: List[Dict[str, object]] = []
    pos = 0
    for idx, mr in enumerate(merged):
        seg, pos = _slice_for_run(text, pos, mr.length)
        sig: StyleSig = mr.sig
        out.append(
            {
                "index": idx,
                "utf16_start": pos - mr.length,
                "utf16_len": mr.length,
                "text": seg,
                "style_type": getattr(sig, "style_type", None),
                "alignment": getattr(sig, "alignment", None),
                "writing_direction": getattr(sig, "writing_direction", None),
                "indent_amount": getattr(sig, "indent_amount", None),
                "has_attachment": getattr(mr, "attachment", None) is not None,
            }
        )
    return out
