"""Encode Notes using the observed inline topotext format."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from uuid import uuid4
import zlib

from google.protobuf.message import DecodeError

from .models.rich import (
    LIST_STYLES,
    MAX_LIST_INDENT,
    NoteTextRun,
    RichNoteBody,
    is_web_link,
)
from .protobuf import notes_pb2

_END_CLOCK = 0xFFFF_FFFF
MAX_INLINE_NOTE_BYTES = 64 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_REPLICAS = 256
_EMPTY_SNIPPET = "No additional text"
_UNSUPPORTED_DOCUMENT = "Only supported inline plain-text Notes can be edited"
_UNSUPPORTED_RICH_DOCUMENT = "Only supported attachment-free inline Notes can be edited"
_BODY_STYLES = {
    "body": notes_pb2.StyleType.STYLE_TYPE_BODY,
    "heading": notes_pb2.StyleType.STYLE_TYPE_HEADING,
    "subheading": notes_pb2.StyleType.STYLE_TYPE_SUBHEADING,
    "bullet": notes_pb2.StyleType.STYLE_TYPE_BULLET_LIST_ITEM,
    "dash": notes_pb2.StyleType.STYLE_TYPE_DASHED_LIST_ITEM,
    "numbered": notes_pb2.StyleType.STYLE_TYPE_NUMBERED_LIST_ITEM,
    "checklist": notes_pb2.StyleType.STYLE_TYPE_CHECKLIST_ITEM,
}
_LIST_STYLE_TYPES = frozenset(_BODY_STYLES[name] for name in LIST_STYLES)


@dataclass(frozen=True)
class EncodedNote:
    """Base64-encoded Notes fields, kept out of diagnostic output."""

    title: str = field(repr=False)
    snippet: str = field(repr=False)
    body: str = field(repr=False)


@dataclass(frozen=True)
class EncodedTextReplacement:
    """Updated inline body and the image identity that must be preserved."""

    body: str = field(repr=False)
    snippet: str = field(repr=False)
    attachment_id: str | None


@dataclass(frozen=True)
class InlineObject:
    """An existing object marker and the record it references."""

    id: str
    uti: str


TAG_UTI = "com.apple.notes.inlinetextattachment.hashtag"
TABLE_UTI = "com.apple.notes.table"
_IMAGE_TYPES = frozenset({"public.jpeg", "public.png"})
_INLINE_OBJECT_ERROR = "Could not verify the inline objects in this Notes document"


def _utf16_length(text: str) -> int:
    """Return the offset units used by Notes and topotext."""
    return len(text.encode("utf-16-le")) // 2


def _plain_text(title: str, text: str) -> tuple[str, list[notes_pb2.AttributeRun]]:
    """Build the visible document and matching title and body runs."""
    if not title.strip() or title.splitlines() != [title]:
        raise ValueError("A note title must be a non-empty single line")
    lines = [title + "\n"]
    if text:
        body = text if text.endswith("\n") else text + "\n"
        lines += ["\n", *(line + "\n" for line in body.split("\n")[:-1])]
    content = "".join(lines)
    _validate_text(content)
    runs = [
        notes_pb2.AttributeRun(
            length=_utf16_length(line),
            paragraph_style=notes_pb2.ParagraphStyle(
                style_type=(
                    notes_pb2.StyleType.STYLE_TYPE_TITLE
                    if i == 0
                    else notes_pb2.StyleType.STYLE_TYPE_BODY
                ),
                alignment=notes_pb2.Alignment.ALIGNMENT_NATURAL,
            ),
        )
        for i, line in enumerate(lines)
    ]
    return content, runs


def _validate_text(content: str) -> None:
    if any(
        (ord(char) < 32 and char not in "\t\n") or char in "\x7f\ufffc"
        for char in content
    ):
        raise ValueError(
            "Note text contains unsupported control or attachment characters"
        )
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Note text must be valid Unicode") from None
    if size > MAX_INLINE_NOTE_BYTES:
        raise ValueError("Inline note text must not exceed 64 KiB")


def _style_run(run: notes_pb2.AttributeRun, fragment: NoteTextRun) -> None:
    if fragment.bold and fragment.italic:
        run.font_weight = notes_pb2.FontWeight.FONT_WEIGHT_BOLD_ITALIC
    elif fragment.bold:
        run.font_weight = notes_pb2.FontWeight.FONT_WEIGHT_BOLD
    elif fragment.italic:
        run.font_weight = notes_pb2.FontWeight.FONT_WEIGHT_ITALIC
    if fragment.underline:
        run.underlined = 1
    if fragment.strikethrough:
        run.strikethrough = 1
    if fragment.link is not None:
        run.link = fragment.link


def _rich_text(
    title: str, body: RichNoteBody
) -> tuple[str, list[notes_pb2.AttributeRun], str]:
    content, runs = _plain_text(title, "")
    if not body.paragraphs:
        return content, runs, ""
    parts = [content, "\n"]
    runs.append(
        notes_pb2.AttributeRun(
            length=1,
            paragraph_style=notes_pb2.ParagraphStyle(
                style_type=notes_pb2.StyleType.STYLE_TYPE_BODY,
                alignment=notes_pb2.Alignment.ALIGNMENT_NATURAL,
            ),
        )
    )
    for paragraph in body.paragraphs:
        style = notes_pb2.ParagraphStyle(
            style_type=_BODY_STYLES[paragraph.style],
            alignment=notes_pb2.Alignment.ALIGNMENT_NATURAL,
        )
        if paragraph.indent:
            style.indent_amount = paragraph.indent
        if paragraph.style == "checklist":
            style.checklist.uuid = uuid4().bytes
            style.checklist.done = int(bool(paragraph.checked))
        for fragment in paragraph.runs:
            if fragment.text.splitlines() != [fragment.text]:
                raise ValueError("A rich-text run must be a single line")
            _validate_text(fragment.text)
            run = notes_pb2.AttributeRun(
                length=_utf16_length(fragment.text), paragraph_style=style
            )
            _style_run(run, fragment)
            runs.append(run)
            parts.append(fragment.text)
        newline = notes_pb2.AttributeRun(length=1, paragraph_style=style)
        runs.append(newline)
        parts.append("\n")
    content = "".join(parts)
    _validate_text(content)
    return content, runs, "".join(parts[2:])


def _document_parts(
    title: str, text: str | RichNoteBody
) -> tuple[str, list[notes_pb2.AttributeRun], str]:
    if isinstance(text, RichNoteBody):
        return _rich_text(title, text)
    content, runs = _plain_text(title, text)
    return content, runs, text


def _encode_fields(
    title: str, text: str, proto: notes_pb2.NoteStoreProto
) -> EncodedNote:
    snippet = " ".join(text.split())[:200] or _EMPTY_SNIPPET
    return EncodedNote(
        title=base64.b64encode(title.encode("utf-8")).decode("ascii"),
        snippet=base64.b64encode(snippet.encode("utf-8")).decode("ascii"),
        body=base64.b64encode(zlib.compress(proto.SerializeToString())).decode("ascii"),
    )


def encode_new_note(title: str, text: str | RichNoteBody) -> EncodedNote:
    """Build a fresh replica with start, content, and end substrings."""
    content, runs, snippet_text = _document_parts(title, text)
    length = _utf16_length(content)
    proto = notes_pb2.NoteStoreProto(serialization_version=0)
    proto.document.serialization_version = 0
    proto.document.version = 0
    note = proto.document.note
    note.note_text = content
    note.attribute_run.extend(runs)
    for replica, clock, size, children in (
        (0, 0, 0, [1]),
        (1, 0, length, [2]),
        (0, _END_CLOCK, 0, []),
    ):
        identifier = notes_pb2.TextIdentifier(replica_id=replica, clock=clock)
        note.substring.add(
            char_id=identifier, timestamp=identifier, length=size, child=children
        )
    replica_clock = note.timestamp.clock.add(replica_uuid=uuid4().bytes)
    replica_clock.replica_clock.add(clock=length)
    replica_clock.replica_clock.add(clock=1)
    if proto.ByteSize() > _MAX_DOCUMENT_BYTES:
        raise ValueError("The Notes document exceeds the inline size limit")
    return _encode_fields(title, snippet_text, proto)


def _is_plain_run(run: notes_pb2.AttributeRun, index: int) -> bool:
    """Allow split body runs, but no formatting or attachment attributes."""
    style = run.paragraph_style
    return (
        run
        == notes_pb2.AttributeRun(
            length=run.length,
            paragraph_style=notes_pb2.ParagraphStyle(
                style_type=notes_pb2.StyleType.STYLE_TYPE_TITLE
                if index == 0
                else notes_pb2.StyleType.STYLE_TYPE_BODY,
                alignment=notes_pb2.Alignment.ALIGNMENT_NATURAL,
            ),
        )
        and style.HasField("style_type")
        and run.length > 0
    )


def validate_plain_document(compressed: bytes) -> notes_pb2.NoteStoreProto:
    """Read a bounded inline document and reject unsupported formatting/history."""
    return _validate_document(compressed, rich=False)


def validate_rich_document(compressed: bytes) -> notes_pb2.NoteStoreProto:
    """Read a supported attachment-free document with known formatting."""
    return _validate_document(compressed, rich=True)


def _is_rich_run(run: notes_pb2.AttributeRun) -> bool:
    allowed = {
        "length",
        "paragraph_style",
        "font_weight",
        "underlined",
        "strikethrough",
        "link",
    }
    if run.length <= 0 or any(
        field.name not in allowed for field, _ in run.ListFields()
    ):
        return False
    if (
        run.font_weight not in (0, 1, 2, 3)
        or run.underlined not in (0, 1)
        or run.strikethrough not in (0, 1)
        or (
            run.HasField("link") and (len(run.link) > 4096 or not is_web_link(run.link))
        )
    ):
        return False
    if not run.HasField("paragraph_style"):
        return True
    style = run.paragraph_style
    return _is_rich_paragraph(style)


def _is_rich_paragraph(style: notes_pb2.ParagraphStyle) -> bool:
    if style.style_type not in (
        notes_pb2.StyleType.STYLE_TYPE_TITLE,
        *_BODY_STYLES.values(),
    ):
        return False
    expected = notes_pb2.ParagraphStyle(
        style_type=style.style_type, alignment=notes_pb2.Alignment.ALIGNMENT_NATURAL
    )
    if style.HasField("indent_amount"):
        if (
            style.style_type not in _LIST_STYLE_TYPES
            or not 0 <= style.indent_amount <= MAX_LIST_INDENT
        ):
            return False
        expected.indent_amount = style.indent_amount
    if style.style_type == notes_pb2.StyleType.STYLE_TYPE_CHECKLIST_ITEM:
        if len(style.checklist.uuid) != 16 or style.checklist.done not in (0, 1):
            return False
        expected.checklist.uuid = style.checklist.uuid
        expected.checklist.done = style.checklist.done
    return style == expected


def _validate_document(compressed: bytes, *, rich: bool) -> notes_pb2.NoteStoreProto:
    error = _UNSUPPORTED_RICH_DOCUMENT if rich else _UNSUPPORTED_DOCUMENT
    proto = _load_document(compressed, error)
    note = proto.document.note
    if "\ufffc" in note.note_text or not all(
        _is_rich_run(run) if rich else _is_plain_run(run, i)
        for i, run in enumerate(note.attribute_run)
    ):
        raise ValueError(error)
    return proto


def _load_document(compressed: bytes, error: str) -> notes_pb2.NoteStoreProto:
    if len(compressed) > _MAX_DOCUMENT_BYTES:
        raise ValueError(error)
    stream = zlib.decompressobj()
    try:
        data = stream.decompress(compressed, _MAX_DOCUMENT_BYTES + 1)
        if len(data) > _MAX_DOCUMENT_BYTES or not stream.eof or stream.unused_data:
            raise ValueError(error)
        proto = notes_pb2.NoteStoreProto()
        proto.ParseFromString(data)
    except (zlib.error, DecodeError):
        raise ValueError(error) from None
    clone = notes_pb2.NoteStoreProto()
    clone.CopyFrom(proto)
    clone.DiscardUnknownFields()
    version = proto.document
    note = version.note
    if (
        clone != proto
        or proto.serialization_version != 0
        or version.serialization_version != 0
        or version.version != 0
        or not note.attribute_run
        or any(run.length <= 0 for run in note.attribute_run)
        or sum(run.length for run in note.attribute_run)
        != _utf16_length(note.note_text)
    ):
        raise ValueError(error)
    _validate_history(note)
    return proto


def _valid_span(span: notes_pb2.TextSubstring, clocks: list[tuple[int, int]]) -> bool:
    replica = span.char_id.replica_id
    timestamp_replica = span.timestamp.replica_id
    return (
        0 < replica <= len(clocks)
        and 0 < timestamp_replica <= len(clocks)
        and span.length > 0
        and span.char_id.clock + span.length <= clocks[replica - 1][0]
        and span.timestamp.clock < clocks[timestamp_replica - 1][1]
    )


def _validate_history(note: notes_pb2.Note) -> None:
    """Only edit the linear, fully clocked topology observed in Notes captures."""
    replicas = note.timestamp.clock
    uuids = [replica.replica_uuid for replica in replicas]
    if (
        not 0 < len(uuids) < _MAX_REPLICAS
        or uuids != sorted(set(uuids))
        or any(len(uid) != 16 for uid in uuids)
        or any(
            len(replica.replica_clock) != 2
            or any(clock.subclock for clock in replica.replica_clock)
            for replica in replicas
        )
    ):
        raise ValueError(_UNSUPPORTED_DOCUMENT)
    clocks = [(r.replica_clock[0].clock, r.replica_clock[1].clock) for r in replicas]
    spans = note.substring
    start = notes_pb2.TextIdentifier(replica_id=0, clock=0)
    end = notes_pb2.TextIdentifier(replica_id=0, clock=_END_CLOCK)
    if (
        len(spans) < 3
        or spans[0]
        != notes_pb2.TextSubstring(char_id=start, timestamp=start, length=0, child=[1])
        or spans[-1] != notes_pb2.TextSubstring(char_id=end, timestamp=end, length=0)
        or not all(list(span.child) == [i + 1] for i, span in enumerate(spans[:-1]))
        or not all(_valid_span(span, clocks) for span in spans[1:-1])
        or sum(span.length for span in spans if not span.tombstone)
        != _utf16_length(note.note_text)
    ):
        raise ValueError(_UNSUPPORTED_DOCUMENT)
    intervals = sorted(
        (span.char_id.replica_id, span.char_id.clock, span.char_id.clock + span.length)
        for span in spans[1:-1]
    )
    if any(a[0] == b[0] and a[2] > b[1] for a, b in pairwise(intervals)):
        raise ValueError(_UNSUPPORTED_DOCUMENT)


def _add_edit_replica(
    note: notes_pb2.Note, length: int, *, style_clock: int = 1
) -> tuple[int, int]:
    replica = uuid4().bytes
    replicas = list(note.timestamp.clock)
    uuids = [r.replica_uuid for r in replicas]
    if replica in uuids:
        raise ValueError(_UNSUPPORTED_DOCUMENT)
    insert_at = sum(uid < replica for uid in uuids)
    replica_id = insert_at + 1
    next_clock = max(r.replica_clock[0].clock for r in replicas)
    if next_clock + length >= _END_CLOCK or style_clock >= _END_CLOCK:
        raise ValueError(_UNSUPPORTED_DOCUMENT)
    for span in note.substring[1:-1]:
        for identifier in (span.char_id, span.timestamp):
            if identifier.replica_id >= replica_id:
                identifier.replica_id += 1
    note.timestamp.clock.insert(
        insert_at,
        notes_pb2.TextVectorTimestamp.Replica(
            replica_uuid=replica,
            replica_clock=[
                notes_pb2.TextVectorTimestamp.Replica.Clock(clock=next_clock + length),
                notes_pb2.TextVectorTimestamp.Replica.Clock(clock=style_clock + 1),
            ],
        ),
    )
    return replica_id, next_clock


def encode_note_edit(
    compressed: bytes, title: str, text: str | RichNoteBody
) -> EncodedNote:
    """Replace visible text while retaining character and tombstone history."""
    content, runs, snippet_text = _document_parts(title, text)
    validator = (
        validate_rich_document
        if isinstance(text, RichNoteBody)
        else validate_plain_document
    )
    proto = validator(compressed)
    note = proto.document.note
    length = _utf16_length(content)
    replica_id, next_clock = _add_edit_replica(note, length)
    for span in note.substring[1:-1]:
        if not span.tombstone:
            span.tombstone = True
            span.timestamp.CopyFrom(
                notes_pb2.TextIdentifier(replica_id=replica_id, clock=1)
            )
        span.child[0] += 1
    note.substring.insert(
        1,
        notes_pb2.TextSubstring(
            char_id=notes_pb2.TextIdentifier(replica_id=replica_id, clock=next_clock),
            timestamp=notes_pb2.TextIdentifier(replica_id=replica_id, clock=0),
            length=length,
            child=[2],
        ),
    )
    note.note_text = content
    del note.attribute_run[:]
    note.attribute_run.extend(runs)
    if proto.ByteSize() > _MAX_DOCUMENT_BYTES:
        raise ValueError(_UNSUPPORTED_DOCUMENT)
    return _encode_fields(title, snippet_text, proto)


_UNSUPPORTED_ATTACHMENT_EDIT = (
    "Only supported inline Notes with one existing image can be edited"
)


def _image_run(note: notes_pb2.Note) -> InlineObject:
    """Require exactly one verified image; other supported markers may coexist."""
    images = [obj for obj in _inline_objects(note) if obj.uti in _IMAGE_TYPES]
    if len(images) != 1:
        raise ValueError(_UNSUPPORTED_ATTACHMENT_EDIT)
    return images[0]


def _inline_objects(note: notes_pb2.Note) -> tuple[InlineObject, ...]:
    objects: list[InlineObject] = []
    offset = 0
    text = note.note_text.encode("utf-16-le")
    for run in note.attribute_run:
        end = offset + 2 * run.length
        if run.HasField("timestamp"):
            raise ValueError(_INLINE_OBJECT_ERROR)
        if run.HasField("attachment_info"):
            ref = run.attachment_info
            marker = text[offset:end]
            if (
                ref.type_uti not in {*_IMAGE_TYPES, TAG_UTI, TABLE_UTI}
                or not ref.attachment_identifier.strip()
                or (
                    marker != b"\xfc\xff"
                    and (ref.type_uti != TAG_UTI or marker != b"\xfc\xff\n\x00")
                )
            ):
                raise ValueError(_INLINE_OBJECT_ERROR)
            objects.append(InlineObject(ref.attachment_identifier, ref.type_uti))
        offset = end
    if (
        len(objects) != note.note_text.count("\ufffc")
        or len({obj.id for obj in objects}) != len(objects)
        or len(objects) > 64
    ):
        raise ValueError(_INLINE_OBJECT_ERROR)
    return tuple(objects)


def inline_note_objects(compressed: bytes) -> tuple[InlineObject, ...]:
    """Verify an inline document and enumerate its preserved object links."""
    return _inline_objects(
        _load_document(compressed, _INLINE_OBJECT_ERROR).document.note
    )


def append_inline_note_object(compressed: bytes, obj: InlineObject) -> str:
    """Append one linked tag or table without changing prior text or runs."""
    proto = _load_document(compressed, _INLINE_OBJECT_ERROR)
    note = proto.document.note
    existing = _inline_objects(note)
    if (
        obj.uti not in {TAG_UTI, TABLE_UTI}
        or not obj.id.strip()
        or obj.id in {old.id for old in existing}
        or len(existing) >= 64
    ):
        raise ValueError(_INLINE_OBJECT_ERROR)
    separator = (
        "\n"
        if not note.note_text.endswith("\n") or note.note_text.count("\n") == 1
        else ""
    )
    suffix = separator + "\ufffc\n\n"
    replica, clock = _add_edit_replica(note, len(suffix))
    span = notes_pb2.TextSubstring(
        char_id=notes_pb2.TextIdentifier(replica_id=replica, clock=clock),
        timestamp=notes_pb2.TextIdentifier(replica_id=replica, clock=0),
        length=len(suffix),
    )
    _replace_linear_spans(note, [*note.substring[1:-1], span])
    note.note_text += suffix
    if separator:
        note.attribute_run.add(length=1)
    marker_length = 2 if obj.uti == TAG_UTI else 1
    marker = note.attribute_run.add(length=marker_length)
    marker.attachment_info.attachment_identifier = obj.id
    marker.attachment_info.type_uti = obj.uti
    note.attribute_run.add(length=3 - marker_length)
    if (
        len(note.note_text.encode("utf-8")) > MAX_INLINE_NOTE_BYTES
        or proto.ByteSize() > _MAX_DOCUMENT_BYTES
    ):
        raise ValueError(_INLINE_OBJECT_ERROR)
    return base64.b64encode(zlib.compress(proto.SerializeToString())).decode("ascii")


def existing_image_id(compressed: bytes) -> str:
    """Verify the inline image body and return its existing attachment ID."""
    return _image_run(
        _load_document(compressed, _UNSUPPORTED_ATTACHMENT_EDIT).document.note
    ).id


def remove_inline_note_object(compressed: bytes, obj: InlineObject) -> EncodedNote:
    """Tombstone one inline tag or table marker without rewriting other runs."""
    proto = _load_document(compressed, _INLINE_OBJECT_ERROR)
    note = proto.document.note
    if obj.uti not in {TAG_UTI, TABLE_UTI} or obj not in _inline_objects(note):
        raise ValueError(_INLINE_OBJECT_ERROR)
    start = 0
    for index, run in enumerate(note.attribute_run):
        if run.attachment_info.attachment_identifier == obj.id:
            stop = start + 1
            if run.length > 1:
                # Hashtag runs can include a paragraph newline after the marker.
                run.length -= 1
                run.ClearField("attachment_info")
            else:
                del note.attribute_run[index]
            break
        start += run.length
    else:
        raise ValueError(_INLINE_OBJECT_ERROR)
    text = note.note_text.encode("utf-16-le")
    title, _, body = (
        (text[: 2 * start] + text[2 * stop :]).decode("utf-16-le").partition("\n")
    )
    if "\n" not in note.note_text or start <= _utf16_length(title):
        raise ValueError(_INLINE_OBJECT_ERROR)
    _splice_history(note, start, stop, 0)
    note.note_text = f"{title}\n{body}"
    if proto.ByteSize() > _MAX_DOCUMENT_BYTES:
        raise ValueError(_INLINE_OBJECT_ERROR)
    return _encode_fields(title, body.replace("\ufffc", " "), proto)


def _replace_run_lengths(
    note: notes_pb2.Note, start: int, stop: int, length: int
) -> None:
    runs: list[notes_pb2.AttributeRun] = []
    offset = 0
    for run in note.attribute_run:
        end = offset + run.length
        overlap = max(0, min(stop, end) - max(start, offset))
        if overlap and run.HasField("attachment_info"):
            raise ValueError("Find text must not include a linked object")
        updated = run.length - overlap + (length if offset <= start < end else 0)
        if updated:
            copy = notes_pb2.AttributeRun()
            copy.CopyFrom(run)
            copy.length = updated
            runs.append(copy)
        offset = end
    del note.attribute_run[:]
    note.attribute_run.extend(runs)


def _copy_span(
    span: notes_pb2.TextSubstring, offset: int, length: int
) -> notes_pb2.TextSubstring:
    copy = notes_pb2.TextSubstring()
    copy.CopyFrom(span)
    copy.char_id.clock += offset
    copy.length = length
    return copy


def _split_visible_range(
    note: notes_pb2.Note, start: int, stop: int
) -> Iterator[tuple[notes_pb2.TextSubstring, bool]]:
    """Split the existing history at the selected UTF-16 boundaries."""
    offset = 0
    for span in note.substring[1:-1]:
        if span.tombstone:
            yield span, False
            continue
        end = offset + span.length
        if end <= start or offset >= stop:
            yield span, False
        else:
            left = max(start - offset, 0)
            right = min(stop - offset, span.length)
            if left:
                yield _copy_span(span, 0, left), False
            yield _copy_span(span, left, right - left), True
            if right < span.length:
                yield _copy_span(span, right, span.length - right), False
        offset = end


def _replace_linear_spans(
    note: notes_pb2.Note, spans: list[notes_pb2.TextSubstring]
) -> None:
    rewritten = [note.substring[0], *spans, note.substring[-1]]
    for i, span in enumerate(rewritten[:-1], start=1):
        del span.child[:]
        span.child.append(i)
    del note.substring[:]
    note.substring.extend(rewritten)


def _splice_history(note: notes_pb2.Note, start: int, stop: int, length: int) -> None:
    """Split and tombstone only the selected character interval."""
    replica, clock = _add_edit_replica(note, length)
    rewritten: list[notes_pb2.TextSubstring] = []
    inserted = False
    for span, selected in _split_visible_range(note, start, stop):
        if selected:
            if length and not inserted:
                rewritten.append(
                    notes_pb2.TextSubstring(
                        char_id=notes_pb2.TextIdentifier(
                            replica_id=replica, clock=clock
                        ),
                        timestamp=notes_pb2.TextIdentifier(replica_id=replica, clock=0),
                        length=length,
                    )
                )
                inserted = True
            span.tombstone = True
            span.timestamp.CopyFrom(
                notes_pb2.TextIdentifier(replica_id=replica, clock=1)
            )
        rewritten.append(span)
    _replace_linear_spans(note, rewritten)


def _checklist_item_runs(
    note: notes_pb2.Note, item: int
) -> tuple[int, list[notes_pb2.AttributeRun]]:
    items: dict[bytes, tuple[int, int, list[notes_pb2.AttributeRun]]] = {}
    offset = 0
    for run in note.attribute_run:
        style = run.paragraph_style
        if style.HasField("checklist"):
            start, end, runs = items.get(style.checklist.uuid, (offset, offset, []))
            if runs and (
                end != offset
                or runs[0].paragraph_style.checklist.done != style.checklist.done
            ):
                raise ValueError("The checklist item has inconsistent formatting")
            runs.append(run)
            items[style.checklist.uuid] = (start, offset + run.length, runs)
        offset += run.length
    if item > len(items):
        raise ValueError("Checklist item number does not exist")
    start, _, runs = list(items.values())[item - 1]
    return start, runs


def encode_checklist_state(
    compressed: bytes, item: int, *, checked: bool
) -> str | None:
    """Set a checklist item's style without replacing any visible characters."""
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError("Checklist item numbers start at one")
    proto = validate_rich_document(compressed)
    note = proto.document.note
    start, runs = _checklist_item_runs(note, item)
    if runs[0].paragraph_style.checklist.done == checked:
        return None
    stop = start + sum(run.length for run in runs)
    style_clock = max(
        replica.replica_clock[1].clock for replica in note.timestamp.clock
    )
    replica, _ = _add_edit_replica(note, 0, style_clock=style_clock)
    timestamp = notes_pb2.TextIdentifier(replica_id=replica, clock=style_clock)
    spans = []
    for span, selected in _split_visible_range(note, start, stop):
        if selected:
            span.timestamp.CopyFrom(timestamp)
        spans.append(span)
    _replace_linear_spans(note, spans)
    for run in runs:
        run.paragraph_style.checklist.done = int(checked)
    if proto.ByteSize() > _MAX_DOCUMENT_BYTES:
        raise ValueError(_UNSUPPORTED_RICH_DOCUMENT)
    return base64.b64encode(zlib.compress(proto.SerializeToString())).decode("ascii")


def replace_inline_note_text(
    compressed: bytes, old: str, new: str
) -> EncodedTextReplacement:
    """Replace one unambiguous inline match, preserving unrelated runs."""
    for text in (old, new):
        if not text or text.splitlines() != [text]:
            raise ValueError("Provide non-empty single-line text for the replacement")
        _validate_text(text)
    if old == new:
        raise ValueError("The replacement text must differ from the original")
    proto = _load_document(compressed, _UNSUPPORTED_ATTACHMENT_EDIT)
    note = proto.document.note
    images = [obj.id for obj in _inline_objects(note) if obj.uti in _IMAGE_TYPES]
    if len(images) > 1:
        raise ValueError(_UNSUPPORTED_ATTACHMENT_EDIT)
    body_offset = note.note_text.find("\n") + 1
    index = note.note_text.find(old, body_offset)
    if not body_offset or index == -1 or note.note_text.find(old, index + 1) != -1:
        raise ValueError("Find text must occur exactly once in the note body")
    start = _utf16_length(note.note_text[:index])
    stop = start + _utf16_length(old)
    length = _utf16_length(new)
    _replace_run_lengths(note, start, stop, length)
    _splice_history(note, start, stop, length)
    note.note_text = note.note_text[:index] + new + note.note_text[index + len(old) :]
    if (
        len(note.note_text.encode("utf-8")) > MAX_INLINE_NOTE_BYTES
        or proto.ByteSize() > _MAX_DOCUMENT_BYTES
    ):
        raise ValueError(_UNSUPPORTED_ATTACHMENT_EDIT)
    body = note.note_text[body_offset:].replace("\ufffc", " ")
    encoded = _encode_fields(note.note_text[: body_offset - 1], body, proto)
    return EncodedTextReplacement(
        encoded.body, encoded.snippet, images[0] if images else None
    )
