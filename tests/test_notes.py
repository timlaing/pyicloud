"""Tests for the Notes service."""

# pylint: disable=protected-access

from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import tempfile
from typing import Annotated, Any
import unittest
from unittest.mock import MagicMock, mock_open, patch

from pydantic import BaseModel, BeforeValidator, ValidationError
import pytest

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKQueryResponse,
    CKZoneChangesResponse,
)
from pyicloud.common.cloudkit.base import resolve_cloudkit_validation_extra
from pyicloud.common.cloudkit.client import redact_cloudkit_url
from pyicloud.common.cloudkit.models import (
    CKParticipant,
    CKParticipantProtectionInfo,
    CKPCSInfo,
    CKRecord,
    CKUserIdentity,
    _from_millis_or_none,
    _from_secs_or_millis,
)
from pyicloud.services.notes import AttachmentId, Note, NotesService, NoteSummary
from pyicloud.services.notes.client import (
    CloudKitNotesClient,
    NotesApiError,
    NotesAuthError,
    NotesRateLimited,
)
from pyicloud.services.notes.client import NotesError as ClientNotesError
from pyicloud.services.notes.domain import NoteBody
import pyicloud.services.notes.models.cloudkit as notes_cloudkit
from pyicloud.services.notes.rendering.exporter import decode_and_parse_note, write_html
from pyicloud.services.notes.service import NoteNotFound

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
NOTES_FIXTURE_DIR = FIXTURE_DIR / "notes"


def load_notes_fixture(name: str) -> Any:
    """Load a synthetic Notes CloudKit fixture."""
    return json.loads((NOTES_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _resolve_cloudkit_shim_attr(name: str) -> Any:
    """Resolve an older Notes model name from the CloudKit compatibility shim."""
    return getattr(notes_cloudkit, name)


class NotesServiceTest(unittest.TestCase):
    """Tests for the Notes service."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.service = NotesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )

    @pytest.fixture(autouse=True)
    def _monkeypatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Expose pytest's monkeypatch fixture to unittest-style tests."""
        self._monkeypatch = monkeypatch

    def test_get_note(self) -> None:
        """Test getting a note."""
        note_response = CKLookupResponse.model_validate(
            load_notes_fixture("notes_lookup_note_response.json")
        )
        folder_response = CKLookupResponse.model_validate(
            load_notes_fixture("notes_query_folders_response.json")
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "lookup",
            MagicMock(side_effect=[note_response, folder_response]),
        )

        note = self.service.get("Note/NOTE-FIXTURE")

        self.assertEqual(note.id, "Note/NOTE-FIXTURE")
        self.assertEqual(note.title, "Synthetic note")
        self.assertEqual(note.snippet, "Synthetic snippet")
        self.assertEqual(note.folder_id, "Folder/FOLDER-FIXTURE")
        self.assertEqual(note.folder_name, "Synthetic Folder")
        self.assertFalse(note.is_deleted)

    def test_notes_domain_models_are_pydantic(self) -> None:
        """Notes public models expose Pydantic serialization."""
        summary = NoteSummary(
            id="note-1",
            title="Hello",
            snippet="World",
            modified_at=None,
            folder_id="folder-1",
            folder_name="Inbox",
            is_deleted=False,
            is_locked=False,
        )
        attachment_id = AttachmentId(identifier="att-1", type_uti="public.jpeg")

        self.assertEqual(summary.model_dump()["id"], "note-1")
        self.assertEqual(attachment_id.model_dump()["type_uti"], "public.jpeg")

    def test_notes_cloudkit_models_module_is_compatibility_shim(self) -> None:
        """Older Notes CloudKit model imports resolve to the common models."""
        self.assertIs(notes_cloudkit.CKRecord, CKRecord)
        ck_record_type = _resolve_cloudkit_shim_attr("CKRecordType")
        ck_desired_key = _resolve_cloudkit_shim_attr("CKDesiredKey")
        self.assertEqual(ck_record_type.Note.value, "Note")
        self.assertEqual(
            ck_desired_key.TITLE_ENCRYPTED.value,
            "TitleEncrypted",
        )

    def test_note_has_attachments_is_in_model_dump(self) -> None:
        """Note serialization includes the has_attachments field."""
        note = Note(
            id="note-1",
            title="Hello",
            snippet="World",
            modified_at=None,
            folder_id="folder-1",
            folder_name="Inbox",
            is_deleted=False,
            is_locked=False,
            text="Body",
            attachments=[],
        )

        self.assertFalse(note.model_dump()["has_attachments"])

    def test_notes_domain_models_forbid_unknown_fields(self) -> None:
        """NoteSummary rejects unexpected fields at validation time."""
        extra_field: dict[str, object] = {"unexpected": True}
        with self.assertRaises(ValidationError):
            NoteSummary(
                id="note-1",
                title="Hello",
                snippet="World",
                modified_at=None,
                folder_id="folder-1",
                folder_name="Inbox",
                is_deleted=False,
                is_locked=False,
                **extra_field,
            )

    def test_notes_domain_models_are_frozen(self) -> None:
        """Notes domain models reject attribute mutation."""
        summary = NoteSummary(
            id="note-1",
            title="Hello",
            snippet="World",
            modified_at=None,
            folder_id="folder-1",
            folder_name="Inbox",
            is_deleted=False,
            is_locked=False,
        )

        with self.assertRaises(ValidationError):
            summary.title = "Updated"

    def test_resolve_cloudkit_validation_extra_defaults_to_allow(self) -> None:
        """Validation extra defaults to 'allow' when unset."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_cloudkit_validation_extra(), "allow")

    def test_resolve_cloudkit_validation_extra_uses_env(self) -> None:
        """Validation extra reads the PYICLOUD_CK_EXTRA environment variable."""
        with patch.dict(os.environ, {"PYICLOUD_CK_EXTRA": "forbid"}, clear=True):
            self.assertEqual(resolve_cloudkit_validation_extra(), "forbid")

    def test_notes_client_allows_unexpected_fields_by_default(self) -> None:
        """CloudKitNotesClient tolerates unexpected fields by default."""
        session = MagicMock()
        payload = {
            **load_notes_fixture("notes_lookup_note_response.json"),
            "unexpectedTopLevel": {"present": True},
        }
        session.post.return_value = MagicMock(
            status_code=200,
            json=lambda: payload,
        )
        client = CloudKitNotesClient(
            "https://example.com",
            session,
            {},
        )

        response = client.lookup(["Note/1"], desired_keys=None)

        self.assertIsInstance(response, CKLookupResponse)
        assert response.model_extra is not None
        self.assertEqual(response.model_extra["unexpectedTopLevel"], {"present": True})

    def test_notes_client_uses_bounded_timeouts(self) -> None:
        """CloudKitNotesClient applies bounded request timeouts."""
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"records": []},
        )
        session.get.return_value = MagicMock(
            status_code=200, iter_content=lambda **_: []
        )
        client = CloudKitNotesClient("https://example.com", session, {})

        client.lookup(["Note/1"], desired_keys=None)
        list(client.download_asset_stream("https://example.com/asset"))

        self.assertEqual(session.post.call_args.kwargs["timeout"], (10.0, 60.0))
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10.0, 60.0))

    def test_notes_client_redacts_query_strings_in_logs(self) -> None:
        """CloudKit URLs have their query strings redacted for logging."""
        redacted = redact_cloudkit_url("https://example.com/path?token=secret&x=1#frag")
        self.assertEqual(redacted, "https://example.com/path")

    def test_notes_client_asset_stream_translates_auth_errors(self) -> None:
        """Asset streaming raises NotesAuthError on 403 responses."""
        session = MagicMock()
        session.get.return_value = MagicMock(status_code=403)
        client = CloudKitNotesClient("https://example.com", session, {})

        with self.assertRaisesRegex(NotesAuthError, "HTTP 403"):
            list(client.download_asset_stream("https://example.com/asset"))

    def test_notes_client_asset_stream_translates_rate_limits(self) -> None:
        """Asset streaming raises NotesRateLimited on 429 responses."""
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=429,
            headers={"Retry-After": "2.5"},
        )
        client = CloudKitNotesClient("https://example.com", session, {})

        with self.assertRaisesRegex(NotesRateLimited, "HTTP 429") as ctx:
            list(client.download_asset_stream("https://example.com/asset"))

        self.assertEqual(ctx.exception.retry_after, 2.5)

    def test_notes_client_asset_stream_translates_api_errors(self) -> None:
        """Asset streaming raises NotesApiError on server errors."""
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=500,
            text="server error",
        )
        client = CloudKitNotesClient("https://example.com", session, {})

        with self.assertRaisesRegex(NotesApiError, "HTTP 500 on asset GET") as ctx:
            list(client.download_asset_stream("https://example.com/asset"))

        self.assertEqual(ctx.exception.payload, "server error")

    def test_notes_client_strict_mode_wraps_validation_error(self) -> None:
        """Strict mode wraps validation failures in a NotesApiError."""
        session = MagicMock()
        payload = {
            **load_notes_fixture("notes_lookup_note_response.json"),
            "unexpectedTopLevel": {"present": True},
        }
        session.post.return_value = MagicMock(status_code=200, json=lambda: payload)
        client = CloudKitNotesClient(
            "https://example.com",
            session,
            {},
            validation_extra="forbid",
        )

        with self.assertRaisesRegex(
            NotesApiError, "Lookup response validation failed"
        ) as ctx:
            client.lookup(["Note/1"], desired_keys=None)

        self.assertEqual(ctx.exception.payload, payload)
        self.assertIsInstance(ctx.exception.__cause__, ValidationError)

    def test_notes_client_debug_validation_logging_is_preserved(self) -> None:
        """Debug logging writes validation dumps on failure."""
        session = MagicMock()
        payload = {
            **load_notes_fixture("notes_lookup_note_response.json"),
            "unexpectedTopLevel": {"present": True},
        }
        session.post.return_value = MagicMock(status_code=200, json=lambda: payload)
        client = CloudKitNotesClient(
            "https://example.com",
            session,
            {},
            validation_extra="forbid",
        )

        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch("os.makedirs"),
            patch("builtins.open", mock_open()) as mocked_open,
            self.assertRaises(NotesApiError),
        ):
            client.lookup(["Note/1"], desired_keys=None)

        opened_paths = [call.args[0] for call in mocked_open.call_args_list]
        self.assertTrue(
            any(
                path.endswith("_records.lookup_validation.json")
                for path in opened_paths
            )
        )

    def test_notes_client_debug_hook_writes_http_dumps(self) -> None:
        """Debug logging writes HTTP request and response dumps."""
        session = MagicMock()
        payload = {"reason": "bad request"}
        session.post.return_value = MagicMock(
            status_code=400,
            headers={},
            json=lambda: payload,
            text="bad request",
        )
        client = CloudKitNotesClient(
            "https://example.com",
            session,
            {"remapEnums": True},
        )

        with (
            patch.dict(os.environ, {"PYICLOUD_NOTES_DEBUG": "1"}, clear=False),
            patch("os.makedirs"),
            patch("builtins.open", mock_open()) as mocked_open,
            self.assertRaises(NotesApiError),
        ):
            client.lookup(["Note/1"], desired_keys=None)

        opened_paths = [call.args[0] for call in mocked_open.call_args_list]
        self.assertTrue(
            any(
                path.endswith("_records.lookup_http_request.json")
                for path in opened_paths
            )
        )
        self.assertTrue(
            any(
                path.endswith("_records.lookup_http_response.txt")
                for path in opened_paths
            )
        )

    def test_notes_client_current_sync_token_falls_back_to_changes(self) -> None:
        """Current sync token falls back to the changes endpoint on empty query."""
        session = MagicMock()
        query_payload = load_notes_fixture(
            "notes_current_sync_token_query_empty_response.json"
        )
        changes_payload = load_notes_fixture(
            "notes_current_sync_token_changes_response.json"
        )
        session.post.side_effect = [
            MagicMock(status_code=200, json=lambda: query_payload),
            MagicMock(status_code=200, json=lambda: changes_payload),
        ]
        client = CloudKitNotesClient("https://example.com", session, {})

        token = client.current_sync_token(zone_name="Notes")

        self.assertEqual(token, "notes-changes-sync-token-fixture")
        self.assertEqual(session.post.call_count, 2)

    def test_notes_changes_zone_fixture_parses_mixed_records(self) -> None:
        """Zone changes response parses mixed live and deleted records."""
        response = CKZoneChangesResponse.model_validate(
            load_notes_fixture("notes_changes_zone_response.json")
        )

        self.assertEqual(len(response.zones), 1)
        records = response.zones[0].records
        self.assertEqual(len(records), 3)
        first_record = records[0]
        self.assertIsInstance(first_record, CKRecord)
        assert isinstance(first_record, CKRecord)
        self.assertEqual(first_record.recordType, "Note")
        self.assertEqual(
            getattr(records[2], "recordName", None),
            "Note/NOTE-DELETED-FIXTURE",
        )
        self.assertTrue(getattr(records[2], "deleted", False))

    def test_notes_client_explicit_override_wins_over_env(self) -> None:
        """An explicit validation_extra override takes precedence over the env."""
        session = MagicMock()
        payload = {
            **load_notes_fixture("notes_lookup_note_response.json"),
            "unexpectedTopLevel": {"present": True},
        }
        session.post.return_value = MagicMock(
            status_code=200,
            json=lambda: payload,
        )
        with patch.dict(os.environ, {"PYICLOUD_CK_EXTRA": "forbid"}, clear=True):
            client = CloudKitNotesClient(
                "https://example.com",
                session,
                {},
                validation_extra="allow",
            )

            response = client.lookup(["Note/1"], desired_keys=None)

        assert response.model_extra is not None
        self.assertEqual(response.model_extra["unexpectedTopLevel"], {"present": True})

    def test_notes_service_passes_through_validation_override(self) -> None:
        """NotesService forwards the validation_extra override to its client."""
        service = NotesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
            cloudkit_validation_extra="ignore",
        )

        self.assertEqual(service.raw._validation_extra, "ignore")

    def test_notes_errors_share_client_base_class(self) -> None:
        """NoteNotFound subclasses the notes client error base."""
        self.assertTrue(issubclass(NoteNotFound, ClientNotesError))

    def test_notes_exporter_module_imports(self) -> None:
        """The notes exporter module imports successfully."""
        module = importlib.import_module("pyicloud.services.notes.rendering.exporter")

        self.assertTrue(hasattr(module, "NoteExporter"))

    def test_notes_service_render_note_delegates_to_exporter_modules(self) -> None:
        """Render note delegates to the top-level ex/importer modules."""
        record = CKRecord.model_validate({
            "recordName": "Note/1",
            "recordType": "Note",
            "fields": {},
        })
        self._monkeypatch.setattr(
            self.service.raw,
            "lookup",
            MagicMock(return_value=MagicMock(records=[record])),
        )

        with (
            patch(
                "pyicloud.services.notes.service.decode_and_parse_note",
                return_value=MagicMock(name="note"),
            ),
            patch(
                "pyicloud.services.notes.service.build_datasource",
                return_value=(MagicMock(name="datasource"), []),
            ),
            patch(
                "pyicloud.services.notes.rendering.renderer.NoteRenderer.render",
                return_value="<p>rendered</p>",
            ) as mock_render,
        ):
            rendered = self.service.render_note("Note/1")

        self.assertEqual(rendered, "<p>rendered</p>")
        mock_render.assert_called_once()

    def test_notes_service_export_note_uses_lazy_importer(self) -> None:
        """Export note delegates to the lazily imported exporter modules."""
        record = CKRecord.model_validate({
            "recordName": "Note/1",
            "recordType": "Note",
            "fields": {},
        })
        self._monkeypatch.setattr(
            self.service.raw,
            "lookup",
            MagicMock(return_value=MagicMock(records=[record])),
        )
        output_dir = os.path.join(
            tempfile.gettempdir(),
            "python-test-results",
            "notes-export",
        )
        output_path = os.path.join(output_dir, "note.html")

        with patch(
            "pyicloud.services.notes.rendering.exporter.NoteExporter.export",
            return_value=output_path,
        ) as mock_export:
            exported = self.service.export_note("Note/1", output_dir)

        self.assertEqual(exported, output_path)
        mock_export.assert_called_once()

    def test_iter_all_skips_changes_when_sync_cursor_is_current(self) -> None:
        """iter_all skips changes when the sync cursor is already current."""
        self.service._raw = MagicMock()
        self.service._raw.current_sync_token.return_value = "tok-current"

        rows = list(self.service.iter_all(since="tok-current"))

        self.assertEqual(rows, [])
        self.service._raw.current_sync_token.assert_called_once_with(zone_name="Notes")
        self.service._raw.changes.assert_not_called()

    def test_iter_changes_skips_changes_when_sync_cursor_is_current(self) -> None:
        """iter_changes skips changes when the sync cursor is already current."""
        self.service._raw = MagicMock()
        self.service._raw.current_sync_token.return_value = "tok-current"

        rows = list(self.service.iter_changes(since="tok-current"))

        self.assertEqual(rows, [])
        self.service._raw.current_sync_token.assert_called_once_with(zone_name="Notes")
        self.service._raw.changes.assert_not_called()

    def test_iter_all_uses_changes_when_sync_cursor_is_not_current(self) -> None:
        """iter_all falls back to changes when the sync cursor is stale."""
        self.service._raw = MagicMock()
        self.service._raw.current_sync_token.return_value = "tok-other"
        self.service._raw.changes.return_value = []

        rows = list(self.service.iter_all(since="tok-stale"))

        self.assertEqual(rows, [])
        self.service._raw.current_sync_token.assert_called_once_with(zone_name="Notes")
        self.service._raw.changes.assert_called_once()

    def test_notes_service_attachment_lookup_prefers_canonical_record_names(
        self,
    ) -> None:
        """Attachment lookup resolves canonical record names for aliases."""
        note_record = CKLookupResponse.model_validate(
            load_notes_fixture("notes_lookup_note_response.json")
        ).records[0]
        assert isinstance(note_record, CKRecord)
        attachment_record = CKLookupResponse.model_validate(
            load_notes_fixture("notes_lookup_attachment_response.json")
        ).records[0]
        lookup_mock = MagicMock(
            return_value=CKLookupResponse(records=[attachment_record])
        )
        self._monkeypatch.setattr(self.service.raw, "lookup", lookup_mock)

        attachments = self.service._resolve_attachments_for_record(
            note_record,
            attachment_ids=[AttachmentId(identifier="ATTACHMENT-ALIAS-FIXTURE")],
        )

        self.assertEqual(
            lookup_mock.call_args.args[0],
            ["Attachment/ATTACHMENT-FIXTURE"],
        )
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].id, "Attachment/ATTACHMENT-FIXTURE")
        self.assertEqual(
            attachments[0].download_url, "https://example.test/notes/asset"
        )
        self.assertIs(
            self.service._attachment_meta_cache["ATTACHMENT-ALIAS-FIXTURE"],
            attachments[0],
        )

    def test_notes_service_folders_uses_supported_desired_keys(self) -> None:
        """Folder listing should not depend on nonexistent Notes desired-key enums."""

        query_mock = MagicMock(
            return_value=CKQueryResponse.model_validate(
                load_notes_fixture("notes_query_folders_response.json")
            )
        )
        self._monkeypatch.setattr(self.service.raw, "query", query_mock)

        folders = list(self.service.folders())

        self.assertEqual(
            query_mock.call_args.kwargs["desired_keys"],
            ["TitleEncrypted", "HasSubfolder"],
        )
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].id, "Folder/FOLDER-FIXTURE")
        self.assertEqual(folders[0].name, "Synthetic Folder")
        self.assertTrue(folders[0].has_subfolders)

    def test_notes_service_folders_treats_subfolder_flag_as_optional(self) -> None:
        """Folder listing should still work when Apple omits the subfolder flag."""

        folder_record = CKRecord.model_validate({
            "recordName": "Folder/2",
            "recordType": "SearchIndexes",
            "fields": {
                "TitleEncrypted": {
                    "type": "STRING",
                    "value": "Personal",
                    "isEncrypted": True,
                },
            },
        })
        self._monkeypatch.setattr(
            self.service.raw,
            "query",
            MagicMock(
                return_value=MagicMock(records=[folder_record], continuationMarker=None)
            ),
        )

        folders = list(self.service.folders())

        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].name, "Personal")
        self.assertIsNone(folders[0].has_subfolders)

    def test_write_html_rejects_filename_escape(self) -> None:
        """write_html rejects filenames that escape the output directory."""
        out_dir = os.path.join(
            tempfile.gettempdir(),
            "python-test-results",
            "notes-export-write-html",
        )
        with self.assertRaisesRegex(ValueError, "filename must stay within out_dir"):
            write_html(
                "Title",
                "<p>rendered</p>",
                out_dir,
                filename="../escape.html",
            )

    def test_decode_and_parse_note_returns_none_on_parse_failure(self) -> None:
        """decode_and_parse_note returns None when parsing the body fails."""
        record = CKRecord.model_validate({
            "recordName": "Note/1",
            "recordType": "Note",
            "fields": {
                "TextDataEncrypted": {
                    "type": "ENCRYPTED_BYTES",
                    "value": "aGVsbG8=",
                }
            },
        })

        with (
            patch(
                "pyicloud.services.notes.rendering.exporter.BodyDecoder.decode",
                return_value=MagicMock(bytes=b"broken"),
            ),
            patch(
                "pyicloud.services.notes.rendering.exporter.pb."
                "NoteStoreProto.ParseFromString",
                side_effect=ValueError("bad proto"),
            ),
        ):
            self.assertIsNone(decode_and_parse_note(record))

    def test_note_body_text_defaults_to_none(self) -> None:
        """NoteBody.text defaults to None when no body text is present."""
        body = NoteBody(bytes=b"hello")
        self.assertIsNone(body.text)

    def test_shared_cloudkit_signed_string_timestamps_are_tolerated(self) -> None:
        """CloudKit timestamps tolerate signed-string and whitespace forms."""
        created = _from_millis_or_none(" 1735689600000 ")

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.isoformat(), "2025-01-01T00:00:00+00:00")
        self.assertIsNone(_from_secs_or_millis("999999999999999999999999"))

    def test_shared_cloudkit_invalid_timestamp_types_raise_validation_error(
        self,
    ) -> None:
        """Invalid timestamp types raise a validation error."""

        class Demo(BaseModel):
            """A model using CloudKit timestamp validators."""

            created: Annotated[datetime, BeforeValidator(_from_millis_or_none)]
            expires: Annotated[datetime, BeforeValidator(_from_secs_or_millis)]

        with self.assertRaises(ValidationError):
            Demo.model_validate({
                "created": object(),
                "expires": object(),
            })

    def test_shared_cloudkit_share_allows_encrypted_string_fields(self) -> None:
        """Shared cloudkit.share records may expose STRING + isEncrypted fields."""
        record = CKRecord.model_validate({
            "recordName": "Share-123",
            "recordType": "cloudkit.share",
            "fields": {
                "SnippetEncrypted": {
                    "value": "Shared snippet",
                    "type": "STRING",
                    "isEncrypted": True,
                }
            },
        })

        self.assertEqual(record.fields.get_value("SnippetEncrypted"), "Shared snippet")
        self.assertEqual(
            NotesService._decode_encrypted(record.fields.get_value("SnippetEncrypted")),
            "Shared snippet",
        )

    def test_shared_cloudkit_share_participant_surfaces_are_typed(self) -> None:
        """Shared-record participant and PCS surfaces parse into structured models."""
        record = CKRecord.model_validate({
            "recordName": "Share-123",
            "recordType": "cloudkit.share",
            "publicPermission": "NONE",
            "participants": [
                {
                    "participantId": "owner-1",
                    "userIdentity": {
                        "userRecordName": "_owner",
                        "nameComponents": {
                            "givenName": "Jacob",
                            "familyName": "Arnould",
                        },
                        "lookupInfo": {
                            "emailAddress": "jacob@example.com",
                        },
                    },
                    "type": "OWNER",
                    "acceptanceStatus": "ACCEPTED",
                    "permission": "READ_WRITE",
                    "customRole": "",
                    "isApprovedRequester": False,
                    "orgUser": False,
                    "publicKeyVersion": 1,
                    "outOfNetworkPrivateKey": "",
                    "outOfNetworkKeyType": 0,
                    "protectionInfo": {
                        "bytes": "aGVsbG8=",
                        "pcsChangeTag": "owner-tag",
                    },
                }
            ],
            "requesters": [],
            "blocked": [],
            "owner": {
                "participantId": "owner-1",
                "userIdentity": {
                    "userRecordName": "_owner",
                },
                "type": "OWNER",
                "permission": "READ_WRITE",
                "protectionInfo": {
                    "bytes": "aGVsbG8=",
                    "pcsChangeTag": "owner-tag",
                },
            },
            "currentUserParticipant": {
                "participantId": "user-1",
                "userIdentity": {
                    "userRecordName": "_user",
                    "lookupInfo": {
                        "phoneNumber": "352621583784",
                    },
                },
                "type": "ADMINISTRATOR",
                "acceptanceStatus": "ACCEPTED",
                "permission": "READ_WRITE",
                "protectionInfo": {
                    "bytes": "d29ybGQ=",
                    "pcsChangeTag": "user-tag",
                },
            },
            "invitedPCS": {
                "bytes": "aW52aXRlZA==",
                "pcsChangeTag": "invited-tag",
            },
            "selfAddedPCS": {
                "bytes": "c2VsZg==",
                "pcsChangeTag": "self-tag",
            },
            "fields": {
                "SnippetEncrypted": {
                    "value": "Shared snippet",
                    "type": "STRING",
                    "isEncrypted": True,
                }
            },
        })

        participants = record.participants
        assert participants is not None
        self.assertIsInstance(participants, list)
        participant = participants[0]
        self.assertIsInstance(participant, CKParticipant)
        identity = participant.userIdentity
        assert identity is not None
        self.assertIsInstance(identity, CKUserIdentity)
        components = identity.nameComponents
        assert components is not None
        self.assertEqual(components.givenName, "Jacob")
        self.assertIsInstance(participant.protectionInfo, CKParticipantProtectionInfo)

        owner = record.owner
        assert owner is not None
        self.assertIsInstance(owner, CKParticipant)

        current_user = record.currentUserParticipant
        assert current_user is not None
        self.assertIsInstance(current_user, CKParticipant)
        current_identity = current_user.userIdentity
        assert current_identity is not None
        current_lookup = current_identity.lookupInfo
        assert current_lookup is not None
        self.assertEqual(current_lookup.phoneNumber, "352621583784")

        invited_pcs = record.invitedPCS
        assert invited_pcs is not None
        self.assertIsInstance(invited_pcs, CKPCSInfo)
        self.assertEqual(invited_pcs.pcsChangeTag, "invited-tag")

        self_added_pcs = record.selfAddedPCS
        assert self_added_pcs is not None
        self.assertIsInstance(self_added_pcs, CKPCSInfo)
        self.assertEqual(self_added_pcs.pcsChangeTag, "self-tag")

    def test_encrypted_string_fields_without_flag_are_rejected(self) -> None:
        """STRING wrappers on *Encrypted fields must carry isEncrypted=true."""
        with self.assertRaises(ValidationError):
            CKRecord.model_validate({
                "recordName": "Share-123",
                "recordType": "cloudkit.share",
                "fields": {
                    "SnippetEncrypted": {
                        "value": "Shared snippet",
                        "type": "STRING",
                    }
                },
            })

    def test_decode_encrypted_bytes_and_strings(self) -> None:
        """Notes encrypted decoder handles both bytes and string field values."""
        self.assertEqual(NotesService._decode_encrypted(b"hello"), "hello")
        self.assertEqual(NotesService._decode_encrypted("bonjour"), "bonjour")


if __name__ == "__main__":
    unittest.main()
