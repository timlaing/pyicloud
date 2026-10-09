"""Additional coverage for the high-level Notes service internals."""

# pylint: disable=protected-access

from typing import Any, cast
import unittest
from unittest.mock import MagicMock, patch

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKLookupResponse,
    CKQueryResponse,
    CKRecord,
    CKTombstoneRecord,
    CKZoneChangesZone,
    CKZoneID,
)
from pyicloud.services.notes.client import NotesApiError, NotesError
from pyicloud.services.notes.domain import AttachmentId, NoteBody
from pyicloud.services.notes.models.dto import Attachment
from pyicloud.services.notes.service import (
    NoteLockedError,
    NoteNotFound,
    NotesService,
)


def _note_record(
    record_name: str,
    *,
    title: str | None = "Title",
    folder_id: str | None = None,
    deleted: bool = False,
    record_type: str = "Note",
    fields: dict[str, object] | None = None,
) -> CKRecord:
    """Build a minimal Note/SearchIndexes record for tests."""
    wrapped: dict[str, object] = {}
    if title is not None:
        wrapped["TitleEncrypted"] = {
            "type": "STRING",
            "value": title,
            "isEncrypted": True,
        }
    wrapped["ModificationDate"] = {"type": "TIMESTAMP", "value": 1735776000000}
    if folder_id is not None:
        wrapped["Folder"] = {
            "type": "REFERENCE",
            "value": {"recordName": folder_id},
        }
    wrapped["Deleted"] = {"type": "INT64", "value": 1 if deleted else 0}
    if fields:
        wrapped.update(fields)
    return CKRecord.model_validate({
        "recordName": record_name,
        "recordType": record_type,
        "fields": wrapped,
    })


def _attachment_record(record_name: str, *, filename: str = "photo.jpg") -> CKRecord:
    """Build an Attachment record that resolves to a downloadable URL."""
    return CKRecord.model_validate({
        "recordName": record_name,
        "recordType": "Attachment",
        "fields": {
            "Filename": {"type": "STRING", "value": filename},
            "UTI": {"type": "STRING", "value": "public.jpeg"},
            "Size": {"type": "INT64", "value": 10},
            "PrimaryAsset": {
                "type": "ASSETID",
                "value": {"downloadURL": "https://example.test/asset"},
            },
        },
    })


def _zone(
    *records: CKRecord | CKTombstoneRecord | CKErrorItem,
) -> CKZoneChangesZone:
    """Build a single zone-changes zone wrapping the given records."""
    return CKZoneChangesZone(
        zoneID=CKZoneID(zoneName="Notes"),
        syncToken="sync-token",
        records=list(records),
    )


class NotesServiceSetupMixin:
    """Shared helper for constructing a service with a mocked raw client."""

    def _service(self) -> Any:
        """Return a NotesService whose raw CloudKit client is a mock."""
        service = NotesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )
        service._raw = MagicMock()
        return service


class NotesListingCoverageTest(NotesServiceSetupMixin, unittest.TestCase):
    """Coverage for the listing/iteration service methods."""

    def test_recents_returns_early_for_non_positive_limit(self) -> None:
        """A non-positive limit yields nothing without a query."""
        service = self._service()

        self.assertEqual(list(service.recents(limit=0)), [])
        service._raw.query.assert_not_called()

    def test_recents_yields_summaries_and_follows_continuation(self) -> None:
        """Recents yield summaries across continuation pages and skip non-records."""
        service = self._service()
        service._raw.query.side_effect = [
            CKQueryResponse(
                records=[_note_record("Note/1", title=None)],
                continuationMarker="next-token",
            ),
            CKQueryResponse(
                records=[
                    CKTombstoneRecord(
                        recordName="Note/GONE",
                        deleted=True,
                    ),
                    _note_record("Note/2"),
                ]
            ),
        ]

        rows = list(service.recents(limit=5))

        self.assertEqual([row.id for row in rows], ["Note/1", "Note/2"])
        self.assertEqual(service._raw.query.call_count, 2)

    def test_recents_stops_when_limit_reached(self) -> None:
        """Recents stop yielding once the requested limit is reached."""
        service = self._service()
        service._raw.query.side_effect = [
            CKQueryResponse(records=[_note_record("Note/1"), _note_record("Note/2")]),
            CKQueryResponse(records=[_note_record("Note/3")]),
        ]

        rows = list(service.recents(limit=1))

        self.assertEqual([row.id for row in rows], ["Note/1"])
        self.assertEqual(service._raw.query.call_count, 1)

    def test_recents_in_folder_filters_by_folder_and_deleted(self) -> None:
        """recents_in_folder yields only matching, non-deleted notes."""
        service = self._service()
        service._folder_name_cache["Folder/A"] = "Project A"
        service._raw.query.return_value = CKQueryResponse(
            records=[
                _note_record("Note/1", folder_id="Folder/A"),
                _note_record("Note/2", folder_id="Folder/B"),
                _note_record("Note/3", folder_id="Folder/A", deleted=True),
            ]
        )

        rows = list(service.recents_in_folder("Folder/A", limit=10))

        self.assertEqual([row.id for row in rows], ["Note/1"])

    def test_recents_in_folder_stops_at_limit(self) -> None:
        """recents_in_folder stops once its own limit is reached."""
        service = self._service()
        service._folder_name_cache["Folder/A"] = "Project A"
        service._raw.query.return_value = CKQueryResponse(
            records=[
                _note_record("Note/1", folder_id="Folder/A"),
                _note_record("Note/2", folder_id="Folder/A"),
            ]
        )

        rows = list(service.recents_in_folder("Folder/A", limit=1))

        self.assertEqual([row.id for row in rows], ["Note/1"])

    def test_recents_in_folder_returns_early_for_non_positive_limit(self) -> None:
        """A non-positive limit yields no notes from the folder helper."""
        service = self._service()

        self.assertEqual(list(service.recents_in_folder("Folder/A", limit=0)), [])

    def test_iter_all_yields_summaries_from_changes_feed(self) -> None:
        """iter_all without a sync cursor yields note summaries."""
        service = self._service()
        service._raw.changes.return_value = [
            _zone(
                _note_record("Note/1"),
                CKTombstoneRecord(
                    recordName="Note/GONE",
                    deleted=True,
                ),
            )
        ]

        rows = list(service.iter_all())

        self.assertEqual([row.id for row in rows], ["Note/1"])
        self.assertIsNotNone(service._raw.changes.call_args)

    def test_folders_yields_folders_with_optional_subfolder_flag(self) -> None:
        """folders yields NoteFolder entries including the subfolder flag."""
        service = self._service()
        service._raw.query.side_effect = [
            CKQueryResponse(
                records=[
                    _note_record(
                        "Folder/1",
                        title="Inbox",
                        record_type="SearchIndexes",
                        fields={"HasSubfolder": {"type": "INT64", "value": 1}},
                    ),
                    CKTombstoneRecord(recordName="Folder/GONE", deleted=True),
                ],
                continuationMarker="more",
            ),
            CKQueryResponse(
                records=[
                    _note_record(
                        "Folder/2",
                        title="Archive",
                        record_type="SearchIndexes",
                    )
                ]
            ),
        ]

        folders = list(service.folders())

        self.assertEqual([f.id for f in folders], ["Folder/1", "Folder/2"])
        self.assertEqual(folders[0].name, "Inbox")
        self.assertTrue(folders[0].has_subfolders)
        self.assertIsNone(folders[1].has_subfolders)
        self.assertEqual(service._folder_name_cache["Folder/1"], "Inbox")

    def test_in_folder_filters_and_uppercases_folder_match(self) -> None:
        """in_folder yields only matching, non-deleted notes newest-first."""
        service = self._service()
        service._folder_name_cache["Folder/A"] = "Project A"
        service._raw.lookup.return_value = CKLookupResponse(records=[])
        service._raw.changes.return_value = [
            _zone(
                _note_record("Note/1", folder_id="Folder/A"),
                _note_record("Note/2", folder_id="Folder/B", deleted=True),
                _note_record("Note/3", folder_id="Folder/A", deleted=True),
                CKTombstoneRecord(recordName="Note/GONE", deleted=True),
            )
        ]

        rows = list(service.in_folder("Folder/A"))

        self.assertEqual([row.id for row in rows], ["Note/1"])

    def test_in_folder_honors_limit(self) -> None:
        """in_folder stops yielding once the limit is reached."""
        service = self._service()
        service._folder_name_cache["Folder/A"] = "Project A"
        service._raw.changes.return_value = [
            _zone(
                _note_record("Note/1", folder_id="Folder/A"),
                _note_record("Note/2", folder_id="Folder/A"),
            )
        ]

        rows = list(service.in_folder("Folder/A", limit=1))

        self.assertEqual([row.id for row in rows], ["Note/1"])


class NotesGetExportCoverageTest(NotesServiceSetupMixin, unittest.TestCase):
    """Coverage for get/export/render service methods."""

    def test_get_with_attachments_resolves_metadata(self) -> None:
        """get(with_attachments=True) resolves attachment metadata."""
        service = self._service()
        note_record = _note_record("Note/1")
        attachment_record = _attachment_record("Att/1")
        service._raw.lookup.side_effect = [
            CKLookupResponse(records=[note_record]),
            CKLookupResponse(records=[attachment_record]),
        ]
        body = NoteBody(
            bytes=b"body",
            text="Hello",
            attachment_ids=[AttachmentId(identifier="Att/1")],
        )

        with patch.object(service, "_decode_note_body", return_value=body):
            note = service.get("Note/1", with_attachments=True)

        self.assertEqual(note.text, "Hello")
        self.assertEqual(note.id, "Note/1")
        self.assertTrue(note.has_attachments)
        attachments = note.attachments
        self.assertIsNotNone(attachments)
        assert attachments is not None
        self.assertEqual(attachments[0].id, "Att/1")

    def test_get_logs_empty_body_decoded(self) -> None:
        """get logs when a decoded body contains no text."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/1")]
        )
        body = NoteBody(bytes=b"body", text=None)

        with patch.object(service, "_decode_note_body", return_value=body):
            note = service.get("Note/1")

        self.assertIsNone(note.text)

    def test_get_raises_note_locked(self) -> None:
        """get raises NoteLockedError for passphrase-protected notes."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[
                _note_record(
                    "Note/1",
                    title="Secret",
                    record_type="PasswordProtectedNote",
                )
            ]
        )

        with self.assertRaises(NoteLockedError):
            service.get("Note/1")

    def test_get_reports_not_found_from_mismatched_lookup(self) -> None:
        """get raises NoteNotFound when lookup returns a different record."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/OTHER")]
        )

        with self.assertRaises(NoteNotFound):
            service.get("Note/1")

    def test_sync_cursor_returns_token(self) -> None:
        """sync_cursor returns the raw client's current sync token."""
        service = self._service()
        service._raw.current_sync_token.return_value = "tok"
        self.assertIs(service.raw, service._raw)

        self.assertEqual(service.sync_cursor(), "tok")
        service._raw.current_sync_token.assert_called_once_with(zone_name="Notes")

    def test_export_note_raises_when_exporter_returns_empty(self) -> None:
        """export_note raises when the exporter reports no output path."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/1")]
        )

        with (
            patch("pyicloud.services.notes.service.NoteExporter") as exporter_cls,
            self.assertRaises(NotesError),
        ):
            exporter_cls.return_value.export.return_value = ""
            service.export_note("Note/1", "out")

    def test_render_note_returns_empty_for_undecodable_note(self) -> None:
        """render_note returns an empty string when the body cannot decode."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/1")]
        )

        with patch(
            "pyicloud.services.notes.service.decode_and_parse_note",
            return_value=None,
        ):
            self.assertEqual(service.render_note("Note/1"), "")


class NotesChangesCoverageTest(NotesServiceSetupMixin, unittest.TestCase):
    """Coverage for the change-stream methods."""

    def test_iter_changes_yields_updated_deleted_and_tombstones(self) -> None:
        """iter_changes yields events for every problem record class."""
        service = self._service()
        service._raw.changes.return_value = [
            _zone(
                _note_record("Note/1"),
                _note_record("Note/2", deleted=True),
                CKTombstoneRecord(recordName="Note/GONE", deleted=True),
                CKTombstoneRecord(recordName="", deleted=True),
            )
        ]

        events = list(service.iter_changes())

        self.assertEqual(
            [(event.type, event.note.id) for event in events],
            [
                ("updated", "Note/1"),
                ("deleted", "Note/2"),
                ("deleted", "Note/GONE"),
            ],
        )

    def test_iter_changes_raises_on_error_item(self) -> None:
        """iter_changes raises NotesApiError for per-record CloudKit errors."""
        service = self._service()
        service._raw.changes.return_value = [
            _zone(
                CKErrorItem(
                    serverErrorCode="SERVER_FAILURE",
                    reason="boom",
                    recordName="Note/1",
                )
            )
        ]

        with self.assertRaises(NotesApiError) as ctx:
            list(service.iter_changes())

        self.assertEqual(
            cast(dict[str, object], ctx.exception.payload)["serverErrorCode"],
            "SERVER_FAILURE",
        )

    def test_iter_changes_raises_on_unknown_record_type(self) -> None:
        """iter_changes raises on records it cannot classify."""
        service = self._service()
        service._raw.changes.return_value = [MagicMock(records=[object()])]

        with self.assertRaises(NotesApiError) as ctx:
            list(service.iter_changes())

        self.assertIn("Unexpected record type", str(ctx.exception))

    def test_matches_current_sync_cursor_preflight_paths(self) -> None:
        """The sync-cursor preflight handles stale/matching/failing tokens."""
        service = self._service()
        service._raw.current_sync_token.return_value = "tok-new"
        self.assertFalse(service._matches_current_sync_cursor("tok-old"))

        service._raw.current_sync_token.return_value = "tok-equal"
        self.assertTrue(service._matches_current_sync_cursor("tok-equal"))

        service._raw.current_sync_token.side_effect = NotesApiError("down")
        self.assertFalse(service._matches_current_sync_cursor("tok-any"))


class NotesInternalHelperCoverageTest(NotesServiceSetupMixin, unittest.TestCase):
    """Coverage for the NotesService internal helpers."""

    def test_lookup_note_record_found_and_missing(self) -> None:
        """_lookup_note_record returns the record or raises NoteNotFound."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/1")]
        )
        self.assertEqual(
            service._lookup_note_record("Note/1").recordName,
            "Note/1",
        )

        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/OTHER")]
        )
        with self.assertRaises(NoteNotFound):
            service._lookup_note_record("Note/1")

    def test_decode_encrypted_handles_none_str_and_bytes(self) -> None:
        """_decode_encrypted normalizes all supported value shapes."""
        self.assertIsNone(NotesService._decode_encrypted(None))
        self.assertEqual(NotesService._decode_encrypted("text"), "text")
        self.assertEqual(
            NotesService._decode_encrypted(b"caf\xc3\xa9"),
            "café",
        )

    def test_extract_folder_id_prefers_reference_list(self) -> None:
        """_extract_folder_id falls back to the Folders reference list."""
        service = self._service()
        record = _note_record(
            "Note/1",
            fields={
                "Folders": {
                    "type": "REFERENCE_LIST",
                    "value": [{"recordName": "Folder/LIST"}],
                }
            },
        )

        self.assertEqual(service._extract_folder_id(record), "Folder/LIST")

    def test_folder_name_cache_hit(self) -> None:
        """_folder_name returns empty for missing ids and honors its cache."""
        service = self._service()
        self.assertIsNone(service._folder_name(""))
        self.assertIsNone(service._folder_name(None))

        service._folder_name_cache["Folder/1"] = "Cached Name"
        self.assertEqual(service._folder_name("Folder/1"), "Cached Name")
        service._raw.lookup.assert_not_called()

    def test_folder_name_resolves_and_caches(self) -> None:
        """_folder_name resolves an uncached folder via lookup."""
        service = self._service()
        folder_record = _note_record(
            "Folder/1",
            title="Inbox",
            record_type="SearchIndexes",
        )
        service._raw.lookup.return_value = CKLookupResponse(records=[folder_record])

        self.assertEqual(service._folder_name("Folder/1"), "Inbox")
        self.assertEqual(service._folder_name_cache["Folder/1"], "Inbox")

    def test_folder_name_unresolved_and_failure(self) -> None:
        """_folder_name caches None on mismatch and on lookup failure."""
        service = self._service()
        service._raw.lookup.return_value = CKLookupResponse(
            records=[_note_record("Note/1")]
        )
        self.assertIsNone(service._folder_name("Folder/1"))
        self.assertIsNone(service._folder_name_cache["Folder/1"])

        service._raw.lookup.side_effect = NotesApiError("down")
        self.assertIsNone(service._folder_name("Folder/2"))
        self.assertIsNone(service._folder_name_cache["Folder/2"])

    def test_decode_note_body_variants(self) -> None:
        """_decode_note_body handles missing, foreign, failing, and valid payloads."""
        service = self._service()
        bare_record = _note_record("Note/1")
        self.assertIsNone(service._decode_note_body(bare_record))

        encrypted_record = _note_record(
            "Note/1",
            fields={
                "TextDataEncrypted": {
                    "type": "ENCRYPTED_BYTES",
                    "value": "cHJvdG8=",
                }
            },
        )
        with patch("pyicloud.services.notes.service.BodyDecoder") as decoder_cls:
            decoder_cls.return_value.decode.return_value = object()
            self.assertIsNone(service._decode_note_body(encrypted_record))

            body = NoteBody(bytes=b"proto", text="Hello")
            decoder_cls.return_value.decode.return_value = body
            self.assertEqual(
                service._decode_note_body(encrypted_record),
                body,
            )

            decoder_cls.return_value.decode.side_effect = ValueError("bad")
            self.assertIsNone(service._decode_note_body(encrypted_record))

    def test_coerce_keys_accepts_mixed_enums_and_strings(self) -> None:
        """_coerce_keys unwraps enum keys and stringifies the rest."""
        self.assertIsNone(NotesService._coerce_keys(None))
        out = NotesService._coerce_keys(["TitleEncrypted", "TextDataEncrypted"])
        self.assertEqual(out, ["TitleEncrypted", "TextDataEncrypted"])


class NotesAttachmentCoverageTest(NotesServiceSetupMixin, unittest.TestCase):
    """Coverage for attachment resolution and download helpers."""

    def test_resolve_attachments_without_identifiers(self) -> None:
        """Empty attachment identifiers short-circuit resolution."""
        service = self._service()
        bare_record = _note_record("Note/1")

        self.assertEqual(
            service._resolve_attachments_for_record(bare_record),
            [],
        )
        service._raw.lookup.assert_not_called()

    def test_resolve_attachments_caches_and_deduplicates(self) -> None:
        """Attachment resolution deduplicates aliases and caches them."""
        service = self._service()
        attachment_record = _attachment_record("Att/1")
        record = _note_record(
            "Note/1",
            fields={
                "Attachments": {
                    "type": "REFERENCE_LIST",
                    "value": [
                        {"recordName": ""},
                        {"recordName": "Att/1"},
                        {"recordName": "Att/1"},
                    ],
                }
            },
        )
        service._raw.lookup.return_value = CKLookupResponse(records=[attachment_record])

        attachments = service._resolve_attachments_for_record(
            record,
            attachment_ids=[
                AttachmentId(identifier="Att/1"),
                AttachmentId(identifier="Att/1"),
                AttachmentId(identifier=""),
            ],
        )

        self.assertEqual([att.id for att in attachments], ["Att/1"])
        self.assertIs(service._attachment_meta_cache["Att/1"], attachments[0])
        self.assertEqual(service._raw.lookup.call_args.args[0], ["Att/1"])

    def test_resolve_attachments_uses_cache_without_lookup(self) -> None:
        """Cached attachment metadata skips the CloudKit lookup entirely."""
        service = self._service()
        cached = Attachment(
            id="Att/1",
            filename="f.jpg",
            uti="public.jpeg",
            size=10,
            download_url="https://x/asset",
            preview_url=None,
            thumbnail_url=None,
        )
        service._attachment_meta_cache["Att/1"] = cached
        record = _note_record(
            "Note/1",
            fields={
                "Attachments": {
                    "type": "REFERENCE_LIST",
                    "value": [{"recordName": "Att/1"}],
                }
            },
        )

        attachments = service._resolve_attachments_for_record(
            record,
            attachment_ids=None,
        )

        self.assertIs(attachments[0], cached)
        service._raw.lookup.assert_not_called()

    def test_resolve_attachments_handles_lookup_failure(self) -> None:
        """Attachment lookup failures degrade to an empty result."""
        service = self._service()
        record = _note_record(
            "Note/1",
            fields={
                "Attachments": {
                    "type": "REFERENCE_LIST",
                    "value": [{"recordName": "Att/1"}],
                }
            },
        )
        service._raw.lookup.side_effect = NotesApiError("down")

        self.assertEqual(
            service._resolve_attachments_for_record(record, attachment_ids=None),
            [],
        )

    def test_resolve_attachments_skips_unhandled_records(self) -> None:
        """Non-record and nameless entries in lookup results are skipped."""
        service = self._service()
        attachment_record = _attachment_record("Att/1")
        record = _note_record(
            "Note/1",
            fields={
                "Attachments": {
                    "type": "REFERENCE_LIST",
                    "value": [{"recordName": "Att/1"}],
                }
            },
        )
        service._raw.lookup.return_value = CKLookupResponse(
            records=[
                CKTombstoneRecord(recordName="Att/T", deleted=True),
                CKRecord.model_validate({
                    "recordName": "",
                    "recordType": "Attachment",
                    "fields": {},
                }),
                attachment_record,
            ]
        )

        attachments = service._resolve_attachments_for_record(
            record,
            attachment_ids=None,
        )

        self.assertEqual([att.id for att in attachments], ["Att/1"])

    def test_attachment_aliases_include_identifier(self) -> None:
        """Attachment aliases include the AttachmentIdentifier value."""
        record = _note_record(
            "Att/1",
            title=None,
            record_type="Attachment",
            fields={
                "AttachmentIdentifier": {
                    "type": "STRING",
                    "value": "Alias/1",
                }
            },
        )

        aliases = NotesService._attachment_aliases(record, "Att/1")

        self.assertEqual(aliases, ["Att/1", "Alias/1"])

    def test_build_attachment_from_nameless_record(self) -> None:
        """A record without a name cannot become an attachment."""
        service = self._service()
        record = CKRecord.model_validate({
            "recordName": "",
            "recordType": "Attachment",
            "fields": {},
        })

        self.assertIsNone(service._build_attachment_from_record(record))

    def test_coerce_string_bytes_and_none(self) -> None:
        """_coerce_string decodes bytes, keeps strings, and tolerates gaps."""
        byte_record = _note_record(
            "Att/1",
            fields={"Name": {"type": "ENCRYPTED_BYTES", "value": "Y2Fmw6k="}},
        )
        self.assertEqual(
            NotesService._coerce_string(byte_record, ["Filename", "Name"]),
            "café",
        )

        str_record = _note_record(
            "Att/1",
            fields={"Name": {"type": "STRING", "value": "text.txt"}},
        )
        self.assertEqual(
            NotesService._coerce_string(str_record, ["Filename", "Name"]),
            "text.txt",
        )

        bare_record = _note_record("Att/1")
        self.assertIsNone(
            NotesService._coerce_string(bare_record, ["Filename", "Name"]),
        )

        int_record = _note_record(
            "Att/1",
            fields={"Filename": {"type": "INT64", "value": 5}},
        )
        self.assertIsNone(
            NotesService._coerce_string(int_record, ["Filename", "Name"]),
        )

    def test_coerce_int_float_and_string(self) -> None:
        """_coerce_int accepts floats and digit strings."""
        float_record = _note_record(
            "Att/1",
            fields={"Size": {"type": "DOUBLE", "value": 10.5}},
        )
        self.assertEqual(NotesService._coerce_int(float_record, ["Size"]), 10)

        string_record = _note_record(
            "Att/1",
            fields={"FileSize": {"type": "STRING", "value": "42"}},
        )
        self.assertEqual(
            NotesService._coerce_int(string_record, ["Size", "FileSize"]),
            42,
        )

        bare_record = _note_record("Att/1")
        self.assertIsNone(NotesService._coerce_int(bare_record, ["Size"]))

    def test_coerce_asset_url_variants(self) -> None:
        """_coerce_asset_url accepts URLs and tolerates missing ones."""
        good_record = _note_record(
            "Att/1",
            fields={
                "PrimaryAsset": {
                    "type": "ASSETID",
                    "value": {"downloadURL": "https://x/asset"},
                }
            },
        )
        self.assertEqual(
            NotesService._coerce_asset_url(good_record, ["PrimaryAsset"]),
            "https://x/asset",
        )

        bare_record = _note_record("Att/1")
        self.assertIsNone(
            NotesService._coerce_asset_url(bare_record, ["PrimaryAsset"]),
        )

        empty_record = _note_record(
            "Att/1",
            fields={"PrimaryAsset": {"type": "ASSETID", "value": {}}},
        )
        self.assertIsNone(
            NotesService._coerce_asset_url(empty_record, ["PrimaryAsset"]),
        )

    def test_coerce_asset_url_from_list_variants(self) -> None:
        """_coerce_asset_url_from_list reads dict and model tokens."""
        dict_record = _note_record(
            "Att/1",
            fields={
                "PaperAssets": {
                    "type": "ASSET_LIST",
                    "value": [{"downloadURL": "https://x/paper"}],
                }
            },
        )
        self.assertEqual(
            NotesService._coerce_asset_url_from_list(dict_record, "PaperAssets"),
            "https://x/paper",
        )

        empty_record = _note_record(
            "Att/1",
            fields={"PaperAssets": {"type": "ASSET_LIST", "value": []}},
        )
        self.assertIsNone(
            NotesService._coerce_asset_url_from_list(empty_record, "PaperAssets"),
        )

        object_record = _note_record(
            "Att/1",
            fields={
                "PaperAssets": {
                    "type": "ASSET_LIST",
                    "value": [
                        {"downloadURL": None},
                        {"downloadURL": "https://x/second"},
                    ],
                }
            },
        )
        self.assertEqual(
            NotesService._coerce_asset_url_from_list(object_record, "PaperAssets"),
            "https://x/second",
        )

        scalar_record = _note_record(
            "Att/1",
            fields={
                "PaperAssets": {
                    "type": "ASSETID",
                    "value": {"downloadURL": "https://x/single"},
                }
            },
        )
        self.assertIsNone(
            NotesService._coerce_asset_url_from_list(scalar_record, "PaperAssets"),
        )

        string_token_record = _note_record(
            "Att/1",
            fields={
                "PaperAssets": {
                    "type": "ASSET_LIST",
                    "value": ["https://x/string"],
                }
            },
        )
        self.assertIsNone(
            NotesService._coerce_asset_url_from_list(
                string_token_record, "PaperAssets"
            ),
        )

        bare_record = _note_record("Att/1")
        self.assertIsNone(
            NotesService._coerce_asset_url_from_list(bare_record, "PaperAssets"),
        )

    def test_download_attachment_to_success_and_error(self) -> None:
        """Attachment downloads delegate to the raw client or raise."""
        service = self._service()
        service._raw.download_asset_to.return_value = "/tmp/file.jpg"
        attachment = Attachment(
            id="Att/1",
            filename="f.jpg",
            uti="public.jpeg",
            size=10,
            download_url="https://x/asset",
            preview_url=None,
            thumbnail_url=None,
        )

        self.assertEqual(
            service.download_attachment_to(attachment, "/tmp"),
            "/tmp/file.jpg",
        )
        service._raw.download_asset_to.assert_called_once_with(
            "https://x/asset",
            "/tmp",
        )

        url_less = Attachment(
            id="Att/2",
            filename=None,
            uti=None,
            size=None,
            download_url=None,
            preview_url=None,
            thumbnail_url=None,
        )
        with self.assertRaises(NotesApiError):
            service.download_attachment_to(url_less, "/tmp")

    def test_stream_attachment_success_and_error(self) -> None:
        """Attachment streaming yields chunks or raises without a URL."""
        service = self._service()
        service._raw.download_asset_stream.return_value = [b"a", b"b"]
        attachment = Attachment(
            id="Att/1",
            filename="f.jpg",
            uti="public.jpeg",
            size=2,
            download_url="https://x/asset",
            preview_url=None,
            thumbnail_url=None,
        )

        self.assertEqual(
            list(service.stream_attachment(attachment)),
            [b"a", b"b"],
        )
        service._raw.download_asset_stream.assert_called_once_with(
            "https://x/asset",
            chunk_size=65536,
        )

        url_less = Attachment(
            id="Att/2",
            filename=None,
            uti=None,
            size=None,
            download_url=None,
            preview_url=None,
            thumbnail_url=None,
        )
        with self.assertRaises(NotesApiError):
            list(service.stream_attachment(url_less))


if __name__ == "__main__":
    unittest.main()
