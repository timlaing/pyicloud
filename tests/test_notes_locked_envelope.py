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
from pyicloud.common.cloudkit.client import CloudKitContainerClient
from pyicloud.services.notes import NotesService
from pyicloud.services.notes._locked_note import (
    decrypt_locked_note_body,
    parse_locked_note_envelope,
    rewrite_locked_note_body,
)
from pyicloud.services.notes.encoding import (
    TAG_UTI,
    InlineObject,
    append_inline_note_object,
    encode_new_note,
)
from pyicloud.services.notes.exceptions import (
    NoteLockedError,
    NoteRevisionConflict,
    NotesError,
    NotesWriteUncertain,
)


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


@pytest.mark.parametrize(
    "case",
    [
        "saved",
        "stale",
        "wrong-password",
        "missing-field",
        "extra-field",
        "missing-date",
        "missing-integer",
        "missing-zone",
        "trash",
        "linked",
        "wrong-ack",
        "multiline",
    ],
)
def test_locked_note_edit(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send a guarded encrypted update without plaintext or an unprotected record."""
    encoded = encode_new_note("Synthetic title", "Old body")
    body = encoded.body
    if case == "linked":
        body = append_inline_note_object(
            base64.b64decode(body), InlineObject("tag", TAG_UTI)
        )
    archive = _encrypted_archive(base64.b64decode(body))
    zone = {
        "zoneName": "Notes",
        "zoneType": "REGULAR_CUSTOM_ZONE",
        "ownerRecordName": "synthetic-owner",
    }
    folder_id = "TrashFolder-CloudKit" if case == "trash" else "folder"
    folder: dict[str, Any] = {
        "recordName": folder_id,
        "zoneID": zone,
        "action": "VALIDATE",
    }
    if case == "missing-zone":
        del folder["zoneID"]
    fields: dict[str, Any] = {
        name: {"type": "BYTES", "value": base64.b64encode(b"synthetic").decode()}
        for name in (
            "TitleEncrypted",
            "PasswordHint",
            "CryptoInitializationVector",
            "CryptoSalt",
            "CryptoTag",
            "CryptoWrappedKey",
            "ReplicaIDToNotesVersionDataEncrypted",
            "ReplicaIDToUserIDEncrypted",
        )
    }
    for name in (
        "TitleEncrypted",
        "PasswordHint",
        "ReplicaIDToNotesVersionDataEncrypted",
        "ReplicaIDToUserIDEncrypted",
    ):
        fields[name]["type"] = "ENCRYPTED_BYTES"
    for name in ("CreationDate", "FoldersModificationDate", "ModificationDate"):
        fields[name] = {"type": "TIMESTAMP", "value": 1700000000000}
    for name in (
        "AttachmentViewType",
        "CryptoIterationCount",
        "Deleted",
        "MinimumSupportedNotesVersion",
        "PaperStyleType",
    ):
        fields[name] = {"type": "INT64", "value": 0}
    fields.update(
        Folder={"type": "REFERENCE", "value": folder},
        Folders={"type": "REFERENCE_LIST", "value": [folder]},
        TextDataEncrypted={
            "type": "ENCRYPTED_BYTES",
            "value": base64.b64encode(_plist(archive)).decode(),
        },
    )
    if case == "missing-field":
        del fields["CryptoSalt"]
    if case == "missing-date":
        del fields["CreationDate"]
    if case == "missing-integer":
        del fields["CryptoIterationCount"]
    if case == "extra-field":
        fields["SnippetEncrypted"] = fields["TitleEncrypted"]
    response = CKLookupResponse.model_validate({
        "records": [
            {
                "recordName": "locked-note",
                "recordType": "PasswordProtectedNote",
                "recordChangeTag": "revision",
                "fields": fields,
                "zoneID": zone,
                "parent": {"recordName": folder_id},
            }
        ]
    })
    service = NotesService("https://example.test", MagicMock(), {})
    lookup = MagicMock(return_value=response)
    monkeypatch.setattr(service.raw, "lookup", lookup)
    check_folder = MagicMock()
    monkeypatch.setattr(service, "_verify_note_source_folder", check_folder)

    def acknowledge(
        _path: str, payload: dict[str, Any], **options: Any
    ) -> dict[str, Any]:
        assert options == {"allow_redirects": False, "require_no_retry": True}
        sent = payload["operations"][0]["record"]
        result = response.records[0].model_dump(mode="python", exclude_none=True)
        result["recordChangeTag"] = "new-revision"
        result["fields"] = {
            name: {**fields[name], "value": wrapper["value"]}
            for name, wrapper in sent["fields"].items()
        }
        if case == "wrong-ack":
            result["recordType"] = "Note"
        return {"records": [result]}

    submit = MagicMock(side_effect=acknowledge)
    monkeypatch.setattr(CloudKitContainerClient, "raw_post", submit)
    kwargs = {
        "old": "Old",
        "new": "New\nline" if case == "multiline" else "New",
        "password": "wrong" if case == "wrong-password" else "synthetic passphrase",
        "expected_change_tag": "stale" if case == "stale" else "revision",
    }
    if case not in {"saved", "wrong-ack"}:
        error = {
            "multiline": ValueError,
            "stale": NoteRevisionConflict,
            "wrong-password": NoteLockedError,
            "missing-field": NoteLockedError,
            "extra-field": NotesError,
            "missing-date": NoteLockedError,
            "missing-integer": NoteLockedError,
            "missing-zone": NoteLockedError,
            "trash": NotesError,
            "linked": NoteLockedError,
        }[case]
        with pytest.raises(error):
            service.replace_locked_note_text("locked-note", **kwargs)
        if case == "multiline":
            lookup.assert_not_called()
        submit.assert_not_called()
        return
    if case == "wrong-ack":
        with pytest.raises(NotesWriteUncertain):
            service.replace_locked_note_text("locked-note", **kwargs)
    else:
        result = service.replace_locked_note_text("locked-note", **kwargs)
        assert result.record_change_tag == "new-revision"
    check_folder.assert_called_once_with("folder")
    submit.assert_called_once()
    path, payload = submit.call_args.args
    assert path == "/records/modify"
    (sent,) = payload["operations"]
    assert sent["operationType"] == "update"
    assert sent["record"]["recordType"] == "PasswordProtectedNote"
    assert sent["record"]["recordChangeTag"] == "revision"
    assert sent["record"]["createShortGUID"] is True
    sent_fields = sent["record"]["fields"]
    assert set(sent_fields) == set(fields)
    assert sent_fields["Folder"]["value"] == folder
    rewritten = base64.b64decode(sent_fields["TextDataEncrypted"]["value"])
    assert b"New body" not in rewritten
    parsed = parse_locked_note_envelope(rewritten)
    original = parse_locked_note_envelope(_plist(archive))
    assert (
        parsed.metadata == original.metadata
        and parsed.wrapped_key == original.wrapped_key
    )
    assert parsed.encrypted_data[-48:-16] != original.encrypted_data[-48:-16]
    decrypted = decrypt_locked_note_body(rewritten, "synthetic passphrase")
    assert b"New body" in zlib.decompress(decrypted)


@pytest.mark.parametrize("case", ["nonce-reuse", "oversized"])
def test_rewrite_locked_note_rejects_unsafe_result(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse nonce reuse and archives that would exceed the reader's limit."""
    archive = _plist(_encrypted_archive())
    if case == "nonce-reuse":
        monkeypatch.setattr(
            "pyicloud.services.notes._locked_note.urandom",
            lambda size: bytes(range(32, 32 + size)),
        )
    with pytest.raises(
        ValueError, match="fresh Notes nonce|Unsupported custom-password"
    ):
        rewrite_locked_note_body(
            archive,
            "synthetic passphrase",
            lambda body: bytes(1024 * 1024) if case == "oversized" else body,
        )
