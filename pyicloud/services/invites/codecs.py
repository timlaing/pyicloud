"""Codecs for base64-JSON Invites fields.

Several `EventDetails` and `cloudkit.share` fields are typed as ``BYTES`` (or
``ENCRYPTED_BYTES``) on the wire but carry base64-encoded JSON. CloudKit Web
Services hands the inner JSON back decrypted to authenticated sessions, so
decoding is just ``base64 -> utf-8 -> json``.

The decoder is permissive (returns ``None`` for malformed input rather than
raising) so a single malformed record can't poison a page of results.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any, Mapping, Optional, Union

LOGGER = logging.getLogger(__name__)

# Field names that carry base64-encoded JSON. Keep in sync with the
# Invites design doc (see docs/research/invites_service_design.md).
JSON_BYTES_FIELDS: frozenset[str] = frozenset(
    {"time", "place", "background", "style", "integrations"}
)


def decode_json_bytes(value: Optional[Union[str, bytes, bytearray]]) -> Optional[Any]:
    """Decode a base64-encoded JSON blob to its Python value.

    Accepts either a base64 ``str`` (the wire form) or ``bytes``/``bytearray``.
    For ``bytes`` we try parsing as raw UTF-8 JSON first (Pydantic's
    ``Base64Bytes`` decodes the wire base64 for us, so the value reaches
    this codec as already-decoded JSON bytes) and fall back to a second
    base64-decode attempt if that fails (handles callers passing the wire
    base64 form as ``bytes``). Returns ``None`` when ``value`` is missing
    or cannot be decoded.
    """
    if value is None:
        return None

    if isinstance(value, str):
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            LOGGER.debug("invites.codecs.b64_decode_fail len=%d", len(value))
            return None
        return _parse_json_bytes(raw)

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        parsed = _parse_json_bytes(raw, log_failures=False)
        if parsed is not None:
            return parsed
        # Bytes weren't direct JSON — maybe they're the base64 wire form.
        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            LOGGER.debug("invites.codecs.json_parse_fail bytes_len=%d", len(raw))
            return None
        return _parse_json_bytes(decoded)

    return None


def _parse_json_bytes(raw: bytes, *, log_failures: bool = True) -> Optional[Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if log_failures:
            LOGGER.debug("invites.codecs.utf8_decode_fail len=%d", len(raw))
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if log_failures:
            LOGGER.debug("invites.codecs.json_parse_fail text_len=%d", len(text))
        return None


def encode_json_bytes(value: Any) -> str:
    """Encode a Python value as a base64-encoded JSON string for the wire."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_integrations(blob: Optional[Mapping[str, Any]]) -> tuple[str, ...]:
    """Extract the list of widget type strings from a decoded ``integrations`` blob.

    Observed shape: ``{"version": "1", "data": [{"type": "com.apple.widget.weather"}, ...]}``.
    Returns an empty tuple when the blob is missing or malformed.
    """
    if not isinstance(blob, Mapping):
        return ()
    data = blob.get("data")
    if not isinstance(data, list):
        return ()
    out: list[str] = []
    for entry in data:
        if isinstance(entry, Mapping):
            t = entry.get("type")
            if isinstance(t, str):
                out.append(t)
    return tuple(out)
