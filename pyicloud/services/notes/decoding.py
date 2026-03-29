from __future__ import annotations

import base64
import binascii
import gzip
import logging
import zlib
from typing import List, Optional, Union

from .domain import AttachmentId, NoteBody
from .protobuf import notes_pb2

LOGGER = logging.getLogger(__name__)


def _b64_to_bytes(val: Optional[Union[str, bytes, bytearray]]) -> Optional[bytes]:
    """Accepts base64 string OR raw bytes and returns raw bytes."""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        LOGGER.debug("notes.decoder.input_bytes len=%d", len(val))
        return bytes(val)
    if isinstance(val, str):
        try:
            out = base64.b64decode(val, validate=True)
            LOGGER.debug(
                "notes.decoder.input_b64 len=%d -> bytes=%d", len(val), len(out)
            )
            return out
        except binascii.Error:
            # Not valid base64; treat as plain text and encode best-effort
            LOGGER.debug("notes.decoder.input_str_nonb64 len=%d", len(val))
            return val.encode("utf-8", errors="replace")


def _decompress(blob: bytes) -> bytes:
    if len(blob) >= 2 and blob[0] == 0x1F and blob[1] == 0x8B:
        return gzip.decompress(blob)
    try:
        return zlib.decompress(blob)
    except zlib.error:
        return zlib.decompress(blob, -zlib.MAX_WBITS)


class BodyDecoder:
    """Decode TextDataEncrypted (base64, compressed) to NoteBody."""

    def decode(
        self, text_data_encrypted_b64: Optional[Union[str, bytes, bytearray]]
    ) -> Optional[NoteBody]:
        if text_data_encrypted_b64 is None:
            return None
        raw = _b64_to_bytes(text_data_encrypted_b64)
        if not raw:
            return None
        try:
            doc = _decompress(raw)
        except Exception as e:
            LOGGER.debug("notes.decoder.decompress_fail %s", e)
            return None

        try:
            msg = notes_pb2.NoteStoreProto()
            msg.ParseFromString(doc)
            note = getattr(getattr(msg, "document", None), "note", None)
            text = getattr(note, "note_text", None) if note else None

            ids: List[AttachmentId] = []
            if note:
                seen = set()
                for run in getattr(note, "attribute_run", []):
                    ai = getattr(run, "attachment_info", None)
                    if ai and (
                        getattr(ai, "attachment_identifier", None)
                        or getattr(ai, "type_uti", None)
                    ):
                        ident = getattr(ai, "attachment_identifier", "") or ""
                        type_uti = getattr(ai, "type_uti", None) or None
                        key = (ident, type_uti)
                        if key in seen:
                            continue
                        seen.add(key)
                        ids.append(AttachmentId(identifier=ident, type_uti=type_uti))
            LOGGER.debug(
                "notes.decoder.attachments ids=%d note_text=%s",
                len(ids),
                bool(text),
            )

            return NoteBody(bytes=doc, text=text, attachment_ids=ids)
        except Exception as e:
            LOGGER.debug("notes.decoder.proto_parse_fail %s", e)
            return None
