"""
Pure renderer for Apple Notes (proto3).

Converts a parsed pb.Note into minimal, readable HTML. No I/O.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Tuple, cast
from urllib.parse import urlsplit

from ..protobuf import notes_pb2 as pb
from .attachments import AttachmentContext, render_attachment
from .options import ExportConfig
from .renderer_iface import AttachmentRef, NoteDataSource


class StyleType(IntEnum):
    DEFAULT = -1
    TITLE = 0
    HEADING = 1
    SUBHEADING = 2
    MONOSPACED = 4
    # Observed list styles (from legacy tooling)
    DOTTED_LIST = 100
    DASHED_LIST = 101
    NUMBERED_LIST = 102
    CHECKBOX = 103


def _is_list_style(st: Optional[int]) -> bool:
    return st in (
        StyleType.DOTTED_LIST,
        StyleType.DASHED_LIST,
        StyleType.NUMBERED_LIST,
        StyleType.CHECKBOX,
    )


def _safe_anchor_href(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    candidate = "".join(ch for ch in str(url).strip() if ch >= " " and ch != "\x7f")
    if not candidate:
        return None

    parts = urlsplit(candidate)
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https", "mailto", "tel"}:
        return None
    if scheme in {"http", "https"} and not parts.netloc:
        return None
    if scheme in {"mailto", "tel"} and not (parts.path or parts.netloc):
        return None
    return candidate


_FONT_STACKS = {
    "ComicSansMS": [
        '"Comic Sans MS"',
        '"Comic Sans"',
        '"Chalkboard SE"',
        '"Comic Neue"',
        "cursive",
    ],
    "Comic Sans MS": [
        '"Comic Sans MS"',
        '"Comic Sans"',
        '"Chalkboard SE"',
        '"Comic Neue"',
        "cursive",
    ],
    "HelveticaNeue": ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
    "Helvetica Neue": ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
    "ArialMT": ["Arial", "Helvetica", "sans-serif"],
    "Arial": ["Arial", "Helvetica", "sans-serif"],
    "TimesNewRomanPSMT": ['"Times New Roman"', "Times", "serif"],
    "Times New Roman": ['"Times New Roman"', "Times", "serif"],
    "CourierNewPSMT": ['"Courier New"', "Courier", "monospace"],
    "Courier New": ['"Courier New"', "Courier", "monospace"],
}


def _css_font_stack(name: str) -> str:
    stack = _FONT_STACKS.get(name)
    if stack:
        return ", ".join(stack)
    try:
        safe = name.replace('"', "'")
    except Exception:
        safe = name
    lower = name.lower()
    generic = "sans-serif"
    if "mono" in lower or "courier" in lower or "code" in lower:
        generic = "monospace"
    elif "serif" in lower or "times" in lower or "georgia" in lower:
        generic = "serif"
    elif "comic" in lower or "chalk" in lower or "hand" in lower:
        generic = "cursive"
    return f'"{safe}", {generic}'


@dataclass(frozen=True)
class StyleSig:
    # Inline styling
    font_weight: Optional[int]
    underlined: Optional[int]
    strikethrough: Optional[int]
    superscript: Optional[int]
    link: Optional[str]
    color_hex: Optional[str]
    emphasis_style: Optional[int]  # FIX: added
    font_size_pt: Optional[float]
    font_name: Optional[str]

    # Block/paragraph styling
    style_type: Optional[int]
    alignment: Optional[int]
    indent_amount: Optional[int]
    block_quote: Optional[int]
    writing_direction: Optional[int]
    checklist_done: Optional[int]
    start_number: Optional[int]
    highlight: Optional[int]
    paragraph_uuid: Optional[bytes]

    @staticmethod
    def from_run(run: pb.AttributeRun) -> "StyleSig":
        ps = run.paragraph_style if run.HasField("paragraph_style") else None
        st = align = indent = bq = wd = None
        start_num = None  # default when no paragraph_style/start provided
        fw = getattr(run, "font_weight", None)
        ul = getattr(run, "underlined", None)
        stt = getattr(run, "strikethrough", None)
        sup = getattr(run, "superscript", None)
        link = getattr(run, "link", None)
        emph = getattr(run, "emphasis_style", None)  # FIX
        # Optional font info
        font = run.font if run.HasField("font") else None
        fsz = getattr(font, "point_size", None) if font is not None else None
        fname = getattr(font, "font_name", None) if font is not None else None
        # Optional highlight palette
        hl = getattr(run, "highlight_color", None)
        color_hex = None
        if hasattr(run, "color") and run.HasField("color"):
            try:
                r = getattr(run.color, "red", 0.0)
                g = getattr(run.color, "green", 0.0)
                b = getattr(run.color, "blue", 0.0)
                r8 = max(0, min(255, round(r * 255)))
                g8 = max(0, min(255, round(g * 255)))
                b8 = max(0, min(255, round(b * 255)))
                color_hex = f"#{r8:02X}{g8:02X}{b8:02X}"
            except Exception:
                color_hex = None
        para_uuid = None
        if ps is not None:
            # Use presence checks (proto3 optional) to avoid defaulting to TITLE (0)
            try:
                if ps.HasField("style_type"):
                    st = ps.style_type
            except Exception:
                pass
            try:
                if ps.HasField("alignment"):
                    align = ps.alignment
            except Exception:
                pass
            try:
                if ps.HasField("indent_amount"):
                    indent = ps.indent_amount
                    if isinstance(indent, int) and indent < 0:
                        indent = 0
            except Exception:
                pass
            try:
                if ps.HasField("block_quote"):
                    bq = ps.block_quote
            except Exception:
                pass
            try:
                if ps.HasField("writing_direction_paragraph"):
                    wd = ps.writing_direction_paragraph
            except Exception:
                pass
            try:
                if ps.HasField("paragraph_uuid"):
                    para_uuid = getattr(ps, "paragraph_uuid", None)
            except Exception:
                para_uuid = None
            # Ordered list start number (proto3 optional supports HasField)
            try:
                start_num = (
                    ps.starting_list_item_number
                    if hasattr(ps, "starting_list_item_number")
                    and ps.HasField("starting_list_item_number")
                    else None
                )
            except Exception:
                start_num = None
            try:
                if ps.HasField("checklist"):
                    try:
                        checklist_done = getattr(ps.checklist, "done", None)
                    except Exception:
                        checklist_done = None
                else:
                    checklist_done = None
            except Exception:
                checklist_done = None
        else:
            checklist_done = None
            start_num = None
        return StyleSig(
            font_weight=fw,
            underlined=ul,
            strikethrough=stt,
            superscript=sup,
            link=link,
            color_hex=color_hex,
            emphasis_style=emph,  # FIX
            font_size_pt=fsz,
            font_name=fname,
            style_type=st,
            alignment=align,
            indent_amount=indent,
            block_quote=bq,
            writing_direction=wd,
            checklist_done=checklist_done,
            start_number=start_num,
            highlight=hl,
            paragraph_uuid=para_uuid,
        )

    def same_paragraph_as(self, other: "StyleSig") -> bool:
        # If both runs carry a paragraph UUID and it differs, this is a new paragraph
        if (
            self.paragraph_uuid
            and other.paragraph_uuid
            and self.paragraph_uuid != other.paragraph_uuid
        ):
            return False
        # Treat "neutral" runs (no style_type) as part of an active list paragraph
        # to avoid prematurely closing list items between runs.
        st_self = self.style_type
        st_other = other.style_type
        if _is_list_style(st_self) and st_other is None:
            return True

        return (
            st_self == st_other
            and self.alignment == other.alignment
            and self.indent_amount == other.indent_amount
            and self.block_quote == other.block_quote
            and self.writing_direction == other.writing_direction
            # Checklist item state is part of the paragraph semantics; if it differs,
            # do not merge runs so each item carries its own done/unchecked state.
            and self.checklist_done == other.checklist_done
            # Start number for ordered lists can differ between paragraphs; avoid merging.
            and self.start_number == other.start_number
        )

    def same_inline_as(self, other: "StyleSig") -> bool:
        return (
            self.font_weight == other.font_weight
            and self.underlined == other.underlined
            and self.strikethrough == other.strikethrough
            and self.superscript == other.superscript
            and self.link == other.link
            and self.color_hex == other.color_hex
            and self.emphasis_style == other.emphasis_style
            and self.font_size_pt == other.font_size_pt
            and self.font_name == other.font_name
            and self.highlight == other.highlight
        )

    def same_effective_style(self, other: "StyleSig") -> bool:
        return self.same_paragraph_as(other) and self.same_inline_as(other)


@dataclass
class MergedRun:
    length: int
    sig: StyleSig
    attachment: Optional[AttachmentRef]


def _merge_runs(runs) -> List[MergedRun]:
    out: List[MergedRun] = []
    for r in runs:
        sig = StyleSig.from_run(r)
        attachment = None
        if r.HasField("attachment_info"):
            ai = r.attachment_info
            identifier = getattr(ai, "attachment_identifier", None) or None
            uti_hint = getattr(ai, "type_uti", None) or None
            attachment = AttachmentRef(identifier=identifier, uti_hint=uti_hint)
        if (
            out
            and attachment is None
            and out[-1].attachment is None
            and out[-1].sig.same_effective_style(sig)
        ):
            out[-1].length += r.length
        else:
            out.append(MergedRun(length=r.length, sig=sig, attachment=attachment))
    return out


def _slice_for_run(s: str, start: int, length_units: int) -> Tuple[str, int]:
    end_guess = start + length_units
    while True:
        chunk = s[start:end_guess]
        astrals = sum(1 for ch in chunk if ord(ch) > 0xFFFF)
        new_end = start + length_units + astrals
        if new_end == end_guess:
            return chunk, end_guess
        end_guess = new_end


def render_note_fragment(
    note: pb.Note,
    datasource: Optional[NoteDataSource],
    config: Optional[ExportConfig] = None,
) -> str:
    text = note.note_text or ""
    merged = _merge_runs(note.attribute_run)

    fragments: List[str] = []
    para_tag_open = ""
    para_tag_close = ""
    deferred_breaks = 0
    # strip_leading_break_next = False

    def _emphasis_css(emph_val: Optional[int]) -> List[str]:
        # Use the highlight value from the signature, which may come from emphasis_style or highlight_color
        if emph_val is None:
            return []
        # Map emphasis palette index to CSS variables that adapt to light/dark.
        # Variables are defined in render_note_page().
        idx = int(emph_val)
        if idx not in (1, 2, 3, 4, 5):
            return []
        return [f"background-color:var(--hl{idx}-bg)"]

    i = 0
    list_stack: List[
        dict
    ] = []  # {"indent": int, "tag": str, "li_open": bool, "li_index": Optional[int], "li_has_content": bool}

    def _close_top_list() -> None:
        if not list_stack:
            return
        top = list_stack.pop()
        if top.get("li_open"):
            fragments.append("</li>")
        fragments.append(f"</{top['tag']}>")

    def _close_lists_to(target_indent: int) -> None:
        while list_stack and list_stack[-1]["indent"] > target_indent:
            _close_top_list()

    def _ensure_list(
        indent: int,
        desired_tag: str,
        *,
        start: Optional[int] = None,
        cls: Optional[str] = None,
    ) -> None:
        while list_stack and (
            list_stack[-1]["indent"] > indent
            or (
                list_stack[-1]["indent"] == indent
                and list_stack[-1]["tag"] != desired_tag
            )
        ):
            _close_top_list()
        while (not list_stack) or list_stack[-1]["indent"] < indent:
            if list_stack and not list_stack[-1]["li_open"]:
                fragments.append("<li>")
                list_stack[-1]["li_open"] = True
                list_stack[-1]["li_index"] = len(fragments) - 1
                list_stack[-1]["li_has_content"] = False
            level = (list_stack[-1]["indent"] + 1) if list_stack else 0
            # Use the same list type at all nesting levels for consistency
            tag = desired_tag
            attrs: List[str] = []
            if cls and tag == "ul":
                attrs.append(f'class="{html.escape(cls)}"')
            if start and tag == "ol" and level == indent and int(start) > 1:
                attrs.append(f'start="{int(start)}"')
            attr_text = (" " + " ".join(attrs)) if attrs else ""
            fragments.append(f"<{tag}{attr_text}>")
            list_stack.append(
                {
                    "indent": level,
                    "tag": tag,
                    "li_open": False,
                    "li_index": None,
                    "li_has_content": False,
                }
            )

    def paragraph_open(sig: StyleSig) -> None:
        nonlocal para_tag_open, para_tag_close
        if sig.style_type in (
            StyleType.DOTTED_LIST,
            StyleType.DASHED_LIST,
            StyleType.NUMBERED_LIST,
            StyleType.CHECKBOX,
        ):
            indent = int(sig.indent_amount or 0)
            desired = "ol" if sig.style_type == StyleType.NUMBERED_LIST else "ul"
            cls = "dashed" if sig.style_type == StyleType.DASHED_LIST else None
            _ensure_list(indent, desired, start=sig.start_number, cls=cls)
            # If a list item is already open at this level and had content, that
            # item is complete; close it before starting a new one.
            if list_stack[-1]["li_open"] and list_stack[-1].get("li_has_content"):
                fragments.append("</li>")
                list_stack[-1]["li_open"] = False
            if not list_stack[-1]["li_open"]:
                fragments.append("<li>")
                list_stack[-1]["li_open"] = True
                list_stack[-1]["li_index"] = len(fragments) - 1
                list_stack[-1]["li_has_content"] = False
            if sig.style_type == StyleType.CHECKBOX:
                checked = " checked" if sig.checklist_done == 1 else ""
                fragments.append(f'<input type="checkbox" disabled{checked}> ')
            para_tag_open = "<li>"
            para_tag_close = "</li>"
            return

        _close_lists_to(-1)
        if sig.style_type == StyleType.TITLE:
            tag = "h1"
        elif sig.style_type == StyleType.HEADING:
            tag = "h2"
        elif sig.style_type == StyleType.SUBHEADING:
            tag = "h3"
        elif sig.style_type == StyleType.MONOSPACED:
            tag = "pre"
        else:
            tag = "p"
        if sig.block_quote == 1 and tag == "p":
            tag = "blockquote"
        styles: List[str] = []
        if sig.alignment == 1:
            styles.append("text-align:center")
        elif sig.alignment == 2:
            styles.append("text-align:right")
        elif sig.alignment == 3:
            styles.append("text-align:justify")
        dir_attr = ""
        if sig.writing_direction in (2, 4):
            dir_attr = ' dir="rtl"'
        elif sig.writing_direction in (1, 3):
            dir_attr = ' dir="ltr"'
        style_attr = f' style="{"; ".join(styles)}"' if styles else ""
        para_tag_open = f"<{tag}{style_attr}{dir_attr}>"
        para_tag_close = f"</{tag}>"
        fragments.append(para_tag_open)

    def paragraph_close() -> None:
        nonlocal para_tag_close, deferred_breaks
        deferred_breaks = 0
        if para_tag_close:
            if para_tag_close == "</li>" and list_stack:
                if list_stack[-1]["li_open"]:
                    # If li had no content, drop the opening and skip the closing
                    if not list_stack[-1].get("li_has_content"):
                        idx = list_stack[-1].get("li_index")
                        if isinstance(idx, int) and 0 <= idx < len(fragments):
                            fragments[idx] = ""
                        # Also drop a stray checklist checkbox emitted for an
                        # empty list item. This happens when a trailing
                        # paragraph opens a new CHECKBOX item but carries no
                        # text before the paragraph closes.
                        if (
                            fragments
                            and isinstance(fragments[-1], str)
                            and fragments[-1]
                            .lstrip()
                            .startswith('<input type="checkbox"')
                        ):
                            fragments.pop()
                    else:
                        # Trim trailing <br> before closing the list item
                        while (
                            fragments
                            and isinstance(fragments[-1], str)
                            and fragments[-1] == "<br>"
                        ):
                            fragments.pop()
                        fragments.append(para_tag_close)
                    list_stack[-1]["li_open"] = False
            elif para_tag_close != "</li>":
                fragments.append(para_tag_close)
        para_tag_close = ""

    def _preserve_leading_ws(text: str) -> str:
        # Convert leading spaces/tabs on each line into &nbsp; so indentation is visible
        # while keeping normal whitespace collapsing for the rest of the line.
        out: List[str] = []
        i = 0
        n = len(text)
        while i < n:
            # find end of current line
            j = i
            while j < n and text[j] not in ("\n", "\u2028"):
                j += 1
            line = text[i:j]
            # escape full line
            esc = html.escape(line)
            # measure leading spaces/tabs in original (not escaped)
            k = 0
            prefix: List[str] = []
            for ch in line:
                if ch == " ":
                    prefix.append("&nbsp;")
                    k += 1
                elif ch == "\t":
                    prefix.append("&nbsp;" * 4)
                    k += 1
                else:
                    break
            if prefix:
                esc = "".join(prefix) + esc[k:]
            out.append(esc)
            # line break token
            if j < n:
                out.append("<br>")
                j += 1
            i = j
        return "".join(out)

    def wrap_inline(sig: StyleSig, html_text: str) -> str:
        styles: List[str] = []
        if sig.font_weight in (1, 3):
            styles.append("font-weight:bold")
        if sig.font_weight in (2, 3):
            styles.append("font-style:italic")
        if sig.color_hex:
            styles.append(f"color:{sig.color_hex}")
        styles.extend(_emphasis_css(sig.highlight or sig.emphasis_style))
        if sig.highlight and not sig.emphasis_style:
            try:
                idx = int(sig.highlight)
                if idx in (1, 2, 3, 4, 5):
                    styles.append(f"background-color:var(--hl{idx}-bg)")
            except Exception:
                pass
        if sig.font_size_pt:
            try:
                styles.append(f"font-size:{float(sig.font_size_pt):.0f}pt")
            except Exception:
                pass
        if sig.font_name:
            styles.append(f"font-family:{_css_font_stack(str(sig.font_name))}")
        deco: List[str] = []
        if sig.underlined == 1:
            deco.append("underline")
        if sig.strikethrough == 1:
            deco.append("line-through")
        if deco:
            styles.append(f"text-decoration:{' '.join(deco)}")
        # Use single quotes for the style attribute to safely include quoted
        # font-family names (e.g., "Comic Sans MS") without breaking HTML.
        if styles:
            style_attr = "; ".join(styles)
            styled = f"<span style='{style_attr}'>{html_text}</span>"
        else:
            styled = html_text
        safe_href = _safe_anchor_href(sig.link)
        if safe_href:
            rel = "noopener noreferrer"
            rp = "no-referrer"
            try:
                if config and getattr(config, "link_rel", None):
                    rel = str(config.link_rel)
                if config and getattr(config, "referrer_policy", None):
                    rp = str(config.referrer_policy)
            except Exception:
                pass
            styled = f'<a href="{html.escape(safe_href)}" target="_blank" rel="{html.escape(rel)}" referrerpolicy="{html.escape(rp)}">{styled}</a>'
        if sig.superscript == 1:
            styled = f"<sup>{styled}</sup>"
        elif sig.superscript == -1:
            styled = f"<sub>{styled}</sub>"
        return styled

    total = len(merged)
    prev_sig: Optional[StyleSig] = None
    for idx, mr in enumerate(merged):
        next_mr = merged[idx + 1] if idx + 1 < total else None
        is_para_boundary = next_mr is None or not mr.sig.same_paragraph_as(next_mr.sig)

        if prev_sig is None or not prev_sig.same_paragraph_as(mr.sig):
            if prev_sig is not None:
                # Avoid closing the parent list item when transitioning to a
                # deeper-indented list paragraph; the nested list should remain
                # inside the current <li>.
                close_prev = True
                if _is_list_style(
                    getattr(prev_sig, "style_type", None)
                ) and _is_list_style(getattr(mr.sig, "style_type", None)):
                    prev_indent = int(getattr(prev_sig, "indent_amount", 0) or 0)
                    cur_indent = int(getattr(mr.sig, "indent_amount", 0) or 0)
                    if cur_indent > prev_indent:
                        close_prev = False
                if close_prev:
                    paragraph_close()
            paragraph_open(mr.sig)

        if mr.attachment is not None:
            ident = mr.attachment.identifier or ""
            uti = (mr.attachment.resolved_uti(datasource) or "").lower()
            title = None
            primary = None
            thumb = None
            gz = None
            # Capture preceding text on the same paragraph/line for inline renderers (e.g., calculator)
            prior_text = None
            try:
                # 'i' is the current Python-string index for this run start
                # Collect text since the last explicit line break
                lb_n = text.rfind("\n", 0, i)
                lb_u = text.rfind("\u2028", 0, i)
                lb = max(lb_n, lb_u)
                prior_text = text[(lb + 1) if lb >= 0 else 0 : i]
            except Exception:
                prior_text = None
            if datasource and ident:
                get_title = getattr(datasource, "get_title", None)
                get_p = getattr(datasource, "get_primary_asset_url", None)
                get_t = getattr(datasource, "get_thumbnail_url", None)
                get_m = getattr(datasource, "get_mergeable_gz", None)
                title = (
                    cast(Optional[str], get_title(ident))
                    if callable(get_title)
                    else None
                )
                primary = cast(Optional[str], get_p(ident)) if callable(get_p) else None
                thumb = cast(Optional[str], get_t(ident)) if callable(get_t) else None
                gz = cast(Optional[bytes], get_m(ident)) if callable(get_m) else None

            # Derive link behavior from config
            link_target = (
                "_blank"
                if (config and getattr(config, "link_target_blank", True))
                else None
            )
            link_rel = getattr(config, "link_rel", None) if config else None
            pdf_h = getattr(config, "pdf_object_height", None) if config else None

            ctx = AttachmentContext(
                id=ident,
                uti=uti,
                title=title,
                primary_url=primary,
                thumb_url=thumb,
                mergeable_gz=gz,
                prior_text=prior_text,
                link_target=link_target,
                link_rel=link_rel,
                link_referrerpolicy=getattr(config, "referrer_policy", None)
                if config
                else None,
                pdf_object_height=pdf_h,
            )
            html_att = render_attachment(
                ctx,
                lambda cell_note: render_note_fragment(
                    cell_note, datasource, config=config
                ),
            )
            fragments.append(html_att)
            if list_stack and list_stack[-1]["li_open"]:
                list_stack[-1]["li_has_content"] = True

            i += mr.length
            # We used to set strip_leading_break_next = True here, but that swallows
            # explicit newlines that follow an inline attachment.
            # strip_leading_break_next = True
        else:
            s, i = _slice_for_run(text, i, mr.length)
            s = s.replace("\x00", "\u2400").replace("\ufffc", "")
            # Removed the strip_leading_break_next check
            if list_stack and list_stack[-1]["li_open"]:
                # Inside a list item
                if _is_list_style(mr.sig.style_type):
                    # For any list style, a newline generally means a new sibling item
                    segs = s.split("\n")
                    for k, seg in enumerate(segs):
                        if seg:
                            # If we are strictly inside a list item that is a "spacer" (bulletless),
                            # and we are about to add text, we must close the spacer and start a
                            # real list item so the text gets a bullet.
                            if list_stack and list_stack[-1]["li_open"]:
                                idx = list_stack[-1]["li_index"]
                                if idx is not None and idx < len(fragments):
                                    tag = fragments[idx]
                                    if 'style="list-style-type: none"' in tag:
                                        fragments.append("</li>")
                                        list_stack[-1]["li_open"] = False

                                        # Open new standard item
                                        fragments.append("<li>")
                                        list_stack[-1]["li_open"] = True
                                        list_stack[-1]["li_index"] = len(fragments) - 1
                                        list_stack[-1]["li_has_content"] = False

                                        if mr.sig.style_type == StyleType.CHECKBOX:
                                            checked = (
                                                " checked"
                                                if mr.sig.checklist_done == 1
                                                else ""
                                            )
                                            fragments.append(
                                                f'<input type="checkbox" disabled{checked}> '
                                            )

                            fragments.append(wrap_inline(mr.sig, html.escape(seg)))
                            if seg.strip():
                                list_stack[-1]["li_has_content"] = True
                        else:
                            # Empty segment implies a newline in the source (e.g. \n\n).
                            # Apple Notes renders this as a vertical space (blank line)
                            # but WITHOUT a bullet.
                            # We check if this is a "trailing" newline used for nesting (handled below)
                            # or an actual blank line.
                            pass

                        if k < len(segs) - 1:
                            next_seg = segs[k + 1] if (k + 1) < len(segs) else None
                            # If the newline is the trailing one (next seg empty and last),
                            # keep the current <li> open so a nested list can attach to it.
                            if next_seg == "" and (k + 1) == len(segs) - 1:
                                continue

                            # Otherwise, end current item and start a new sibling item.
                            # If the current item (seg) was empty, we want it to be "bulletless".
                            # But we've already opened the <li> tag at the top of the loop or previous iter.
                            # So we need to retroactively apply style or just insure content forces height?
                            # Actually, we can just close the current <li>.
                            # If it was empty (seg==""), the browser renders an empty bullet point <li></li>.

                            # Correction: We want to hide the bullet for *this* item if it's empty.
                            # But the <li> tag was emitted *before* we processed this segment
                            # (at the end of the previous iteration or start of block).
                            # We can't easily change the opening tag now.

                            # Alternative: Handle the *next* opening tag.

                            fragments.append("</li>")
                            list_stack[-1]["li_open"] = False

                            # open next
                            style_attr = ""
                            # If next segment is empty (and not the trailing nesting case),
                            # it's a blank line. Hide the marker.
                            # We know next_seg is the content of the next item.
                            is_next_empty = next_seg == ""
                            # Caution: if next_seg is "" AND it's the last one, we skipped above.
                            # So if we are here, next_seg might be empty (spacer) or "Text".

                            if is_next_empty:
                                style_attr = ' style="list-style-type: none"'

                            fragments.append(f"<li{style_attr}>")
                            # Track the index of the opening tag
                            list_stack[-1]["li_index"] = len(fragments) - 1
                            list_stack[-1]["li_open"] = True

                            if is_next_empty:
                                # Ensure it has height
                                fragments.append("&nbsp;")

                            list_stack[-1]["li_has_content"] = False

                            # For checklist style, inject a checkbox for each new item
                            # ONLY if it's not a spacer (empty).
                            if (
                                mr.sig.style_type == StyleType.CHECKBOX
                                and not is_next_empty
                            ):
                                checked = (
                                    " checked" if mr.sig.checklist_done == 1 else ""
                                )
                                fragments.append(
                                    f'<input type="checkbox" disabled{checked}> '
                                )
                else:
                    # Non-list paragraphs keep newlines as <br>
                    segs = s.split("\n")
                    for k, seg in enumerate(segs):
                        if seg:
                            fragments.append(wrap_inline(mr.sig, html.escape(seg)))
                            if seg.strip():
                                list_stack[-1]["li_has_content"] = True
                        if k < len(segs) - 1:
                            fragments.append("<br>")
            else:
                if is_para_boundary:
                    s = s.rstrip("\n\u2028")

                # If we are in a list item, check if it was a "spacer" (empty bulletless item)
                # created by a previous run's trailing newlines. If so, and we have text,
                # we should close the spacer and start a new real item.
                if list_stack and list_stack[-1]["li_open"]:
                    # Check if current item is a spacer
                    idx = list_stack[-1]["li_index"]
                    if idx is not None and idx < len(fragments):
                        # A spacer looks like <li style="list-style-type: none">
                        tag = fragments[idx]
                        if 'style="list-style-type: none"' in tag:
                            # Close spacer
                            fragments.append("</li>")
                            list_stack[-1]["li_open"] = False
                            # Open new standard item
                            fragments.append("<li>")
                            list_stack[-1]["li_open"] = True
                            list_stack[-1]["li_index"] = len(fragments) - 1
                            list_stack[-1]["li_has_content"] = False
                            if mr.sig.style_type == StyleType.CHECKBOX:
                                checked = (
                                    " checked" if mr.sig.checklist_done == 1 else ""
                                )
                                fragments.append(
                                    f'<input type="checkbox" disabled{checked}> '
                                )

                if s.replace("\n", "").replace("\u2028", "") == "":
                    deferred_breaks += s.count("\n") + s.count("\u2028")
                else:
                    if deferred_breaks > 0:
                        fragments.append("<br>" * deferred_breaks)
                        deferred_breaks = 0
                    if mr.sig.style_type == StyleType.MONOSPACED:
                        safe = html.escape(s)
                    else:
                        # Preserve leading spaces/tabs per line for visible indentation
                        safe = _preserve_leading_ws(s)
                    fragments.append(wrap_inline(mr.sig, safe))
                    if list_stack and list_stack[-1]["li_open"]:
                        list_stack[-1]["li_has_content"] = list_stack[-1].get(
                            "li_has_content"
                        ) or (s.strip() != "")

        if is_para_boundary:
            # Do not close the current <li> when the next paragraph is a
            # deeper-indented list item. Keeping the parent <li> open ensures
            # the nested <ul>/<ol> is emitted inside the correct item rather
            # than inside an empty sibling <li>.
            should_close = True
            if next_mr is not None:
                cur_st = mr.sig.style_type
                nxt_st = next_mr.sig.style_type
                if _is_list_style(cur_st) and _is_list_style(nxt_st):
                    cur_indent = int(mr.sig.indent_amount or 0)
                    nxt_indent = int(next_mr.sig.indent_amount or 0)
                    if nxt_indent > cur_indent:
                        should_close = False
            if should_close:
                paragraph_close()
        prev_sig = mr.sig

    if prev_sig is not None and para_tag_close:
        paragraph_close()
    _close_lists_to(-1)
    return "".join(fragments)


def render_note_page(title: str, html_fragment: str, extra_css: str = "") -> str:
    return (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{html.escape(title)}</title>"
        "<style>"
        # Highlight palette (light)
        " :root{--hl1-bg:#BA55D333;--hl2-bg:#D5000044;--hl3-bg:#FF6F0022;--hl4-bg:#289C8ECC;--hl5-bg:#2196F333}"
        # Base (light) styles
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.4;background:#fff;color:#000}"
        "pre{white-space:pre-wrap}"
        "blockquote{margin:.5em 0 .5em 1em;padding-left:.8em;border-left:3px solid #ddd}"
        "a{text-decoration:underline}"
        "img{max-width:100%;height:auto}"
        "table{border-collapse:collapse;margin:.5rem 0}"
        "td,th{border:1px solid #ccc;padding:.25rem .5rem;vertical-align:top}"
        "ul.dashed{list-style:none;padding-left:1.2em}"
        "ul.dashed li::before{content:'— ';position:relative;left:-0.6em}"
        # Automatic dark mode via user preference
        "@media (prefers-color-scheme: dark){"
        # Highlight palette (dark) — slightly more opaque/lighter for contrast
        " :root{--hl1-bg:#BA55D380;--hl2-bg:#FF525266;--hl3-bg:#FFB74D55;--hl4-bg:#80CBC480;--hl5-bg:#64B5F680}"
        "body{background:#111;color:#eee}"
        "a{color:#8ab4f8}"
        "blockquote{border-left-color:#444}"
        "td,th{border-color:#555}"
        "img{max-width:100%;height:auto}"
        "pre{background:#1b1b1b;color:#eee}"
        "ul.dashed li::before{color:#bbb}"
        "}"
        f'{extra_css}</style><div class="note-content">{html_fragment}</div>'
    )


class NoteRenderer:
    """Class-based interface for note rendering."""

    def __init__(self, config: Optional[ExportConfig] = None):
        self.config = config or ExportConfig()

    def render(self, note: pb.Note, datasource: Optional[NoteDataSource] = None) -> str:
        """Render the note body to an HTML fragment string."""
        return render_note_fragment(note, datasource, config=self.config)

    def render_full_page(self, title: str, html_fragment: str) -> str:
        """Wrap an HTML fragment in a full page with CSS."""
        return render_note_page(title, html_fragment)
