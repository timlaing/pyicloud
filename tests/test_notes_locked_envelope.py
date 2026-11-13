"""Exercise locked Notes archive parsing with synthetic data only."""

import base64
import gzip
from hashlib import pbkdf2_hmac
import plistlib
from typing import Any
from unittest.mock import MagicMock
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
import pytest

from pyicloud.common.cloudkit import CKLookupResponse
from pyicloud.services.notes import NotesService
from pyicloud.services.notes._locked_note import (
    decrypt_locked_note_body,
    parse_locked_note_envelope,
)
from pyicloud.services.notes.encoding import encode_new_note
from pyicloud.services.notes.exceptions import NoteLockedError


def _plist(value: dict[str, Any] | list[Any]) -> bytes:
    return plistlib.dumps(value, fmt=plistlib.PlistFormat.FMT_BINARY)


def _archive() -> dict[str, Any]:
    uid = plistlib.UID
    return {
        "$archiver": "NSKeyedArchiver",
        "$version": 100000,
        "$top": {"root": uid(1)},
        "$objects": [
            "$null",
            {
                "unauthenticatedMetadata": uid(2),
                "metadata": uid(3),
                "wrappedEncryptionKey": uid(4),
                "encryptedData": uid(5),
                "$class": uid(6),
            },
            _plist({
                "passphraseSalt": b"s" * 32,
                "passphraseIterationCount": 20000,
                "passphraseHint": "synthetic hint",
            }),
            _plist({"cipherVersion": 2, "objectIdentifier": "synthetic identifier"}),
            b"k" * 40,
            b"c" * 64,
            {"$classname": "ICCryptoEncryptionObject"},
        ],
    }


def test_parse_locked_note_envelope() -> None:
    """Read only the observed fields and omit sensitive values from repr."""
    archive = _archive()
    envelope = parse_locked_note_envelope(_plist(archive))
    assert envelope.salt == b"s" * 32
    assert envelope.iterations == 20000
    assert envelope.metadata == archive["$objects"][3]
    assert envelope.wrapped_key == b"k" * 40
    assert envelope.encrypted_data == b"c" * 64
    assert repr(envelope) == "LockedNoteEnvelope(iterations=20000)"


@pytest.mark.parametrize(
    "data", [b"not a plist", b"bplist00", b"bplist00" + bytes(1024 * 1024), _plist([])]
)
def test_reject_invalid_locked_plist(data: bytes) -> None:
    """Reject unsupported framing, oversized input, and non-dictionary roots."""
    with pytest.raises(ValueError, match="Unsupported custom-password"):
        parse_locked_note_envelope(data)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("$archiver", "unexpected"),
        ("$version", 1),
        ("$objects", {}),
        ("$objects", []),
        ("$objects", [""] * 33),
        ("$top", []),
        ("$top", {"root": plistlib.UID(90)}),
        ("$top", {"root": 1}),
        ("$top", {"root": plistlib.UID(0)}),
        ("$top", {"root": plistlib.UID(2)}),
    ],
)
def test_reject_invalid_locked_archive(key: str, value: object) -> None:
    """Do not traverse missing, invalid, or unexpected archive references."""
    archive = _archive()
    archive[key] = value
    data = _plist(archive)
    with pytest.raises(ValueError, match="Unsupported custom-password"):
        parse_locked_note_envelope(data)


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (2, _plist({"passphraseSalt": b"s" * 32, "passphraseIterationCount": 0})),
        (2, _plist({"passphraseSalt": b"s" * 32, "passphraseIterationCount": True})),
        (
            2,
            _plist({
                "passphraseSalt": b"s" * 32,
                "passphraseIterationCount": 1_000_001,
            }),
        ),
        (2, _plist({"passphraseSalt": b"short", "passphraseIterationCount": 20000})),
        (2, _plist({"passphraseIterationCount": 20000})),
        (2, _plist({"passphraseSalt": b"s" * 32})),
        (3, _plist({"cipherVersion": 1})),
        (4, b"short"),
        (5, b""),
        (5, "not bytes"),
        (6, {"$classname": "Unexpected"}),
        (6, "not a class"),
    ],
)
def test_reject_invalid_locked_components(index: int, value: object) -> None:
    """Refuse malformed cipher metadata and unreasonable derivation parameters."""
    archive = _archive()
    archive["$objects"][index] = value
    data = _plist(archive)
    with pytest.raises(ValueError, match="Unsupported custom-password"):
        parse_locked_note_envelope(data)


def _encrypted_archive(plaintext: bytes = b"synthetic note body") -> dict[str, Any]:
    archive = _archive()
    objects = archive["$objects"]
    key = bytes(range(32))
    nonce = bytes(range(32, 64))
    wrapping_key = pbkdf2_hmac("sha256", b"synthetic passphrase", b"s" * 32, 20000)
    objects[4] = aes_key_wrap(wrapping_key, key)
    ciphertext_and_tag = AESGCM(key).encrypt(nonce, plaintext, objects[3])
    objects[5] = ciphertext_and_tag[:-16] + nonce + ciphertext_and_tag[-16:]
    return archive


def test_decrypt_locked_note_body() -> None:
    """Use the inner metadata verbatim and split the browser's nonce and tag."""
    assert (
        decrypt_locked_note_body(_plist(_encrypted_archive()), "synthetic passphrase")
        == b"synthetic note body"
    )


@pytest.mark.parametrize(
    "part", ["password", "unicode", "metadata", "ciphertext", "nonce", "tag"]
)
def test_locked_note_authentication_failure(part: str) -> None:
    """Reject wrong passwords and modified authenticated inputs without disclosure."""
    archive = _encrypted_archive()
    objects = archive["$objects"]
    password = {
        "password": "wrong synthetic passphrase",
        "unicode": chr(0xD800),
    }.get(part, "synthetic passphrase")
    if part == "metadata":
        objects[3] = _plist({"cipherVersion": 2, "objectIdentifier": "altered"})
    elif part in {"ciphertext", "nonce", "tag"}:
        framed = bytearray(objects[5])
        framed[{"ciphertext": 0, "nonce": -48, "tag": -1}[part]] ^= 1
        objects[5] = bytes(framed)
    data = _plist(archive)
    with pytest.raises(
        ValueError, match="^Could not authenticate the locked Notes body$"
    ):
        decrypt_locked_note_body(data, password)


@pytest.mark.parametrize(
    "case",
    [
        "zlib",
        "gzip",
        "raw",
        "missing-password",
        "wrong-password",
        "attachments",
        "missing-body",
        "unsupported-envelope",
        "bad-document",
        "oversized",
        "truncated",
        "trailing",
        "other-zone",
    ],
)
def test_read_locked_note(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Open only supported, authenticated documents and never send the password."""
    plaintext = base64.b64decode(
        encode_new_note("Synthetic title", "Synthetic text").body
    )
    document = zlib.decompress(plaintext)
    if case == "gzip":
        plaintext = gzip.compress(document)
    elif case == "raw":
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        plaintext = compressor.compress(document) + compressor.flush()
    elif case == "bad-document":
        plaintext = b"not a document"
    elif case == "oversized":
        plaintext = zlib.compress(bytes(2 * 1024 * 1024 + 1))
    elif case == "truncated":
        plaintext = plaintext[:-1]
    elif case == "trailing":
        plaintext += b"trailing"
    archive = _encrypted_archive(plaintext)
    if case == "unsupported-envelope":
        archive["$objects"][3] = _plist({"cipherVersion": 1})
    fields: dict[str, Any] = {}
    if case != "missing-body":
        fields["TextDataEncrypted"] = {
            "type": "ENCRYPTED_BYTES",
            "value": base64.b64encode(_plist(archive)).decode(),
        }
    response = CKLookupResponse.model_validate({
        "records": [
            {
                "recordName": "locked-note",
                "recordType": "PasswordProtectedNote",
                "recordChangeTag": "revision",
                "fields": fields,
                "zoneID": {"zoneName": "Other" if case == "other-zone" else "Notes"},
            }
        ]
    })
    service = NotesService("https://example.test", MagicMock(), {})
    lookup = MagicMock(return_value=response)
    monkeypatch.setattr(service.raw, "lookup", lookup)
    password = {"missing-password": None, "wrong-password": "incorrect"}.get(
        case, "synthetic passphrase"
    )
    if case in {"zlib", "gzip", "raw"}:
        note = service.get("locked-note", password=password)
        assert note.is_locked
        assert note.record_change_tag == "revision"
        assert note.text is not None
        assert "Synthetic text" in note.text
        assert note.attachments is None
    else:
        with pytest.raises(NoteLockedError) as caught:
            service.get(
                "locked-note", password=password, with_attachments=case == "attachments"
            )
        assert "synthetic passphrase" not in str(caught.value)
        assert caught.value.__cause__ is None
    lookup.assert_called_once()
    assert lookup.call_args.kwargs["record_names"] == ["locked-note"]
    assert "password" not in lookup.call_args.kwargs
