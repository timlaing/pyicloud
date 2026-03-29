"""Tests for the Notes service."""

import importlib
import os
import tempfile
import unittest
from datetime import datetime
from typing import Annotated
from unittest.mock import MagicMock, patch

from pydantic import BaseModel, BeforeValidator, ValidationError

from pyicloud.common.cloudkit import CKLookupResponse
from pyicloud.common.cloudkit.base import resolve_cloudkit_validation_extra
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
)
from pyicloud.services.notes.client import NotesError as ClientNotesError
from pyicloud.services.notes.client import (
    _CloudKitClient,
)
from pyicloud.services.notes.rendering.exporter import decode_and_parse_note, write_html
from pyicloud.services.notes.service import NoteNotFound


class NotesServiceTest(unittest.TestCase):
    """Tests for the Notes service."""

    def setUp(self):
        """Set up the test case."""
        self.service = NotesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )

    def test_get_note(self):
        """Test getting a note."""
        self.skipTest("TODO: implement once representative note fixture is available")

    def test_notes_domain_models_are_pydantic(self):
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

    def test_note_has_attachments_is_in_model_dump(self):
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

    def test_notes_domain_models_forbid_unknown_fields(self):
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
                unexpected=True,
            )

    def test_notes_domain_models_are_frozen(self):
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

    def test_resolve_cloudkit_validation_extra_defaults_to_allow(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_cloudkit_validation_extra(), "allow")

    def test_resolve_cloudkit_validation_extra_uses_env(self):
        with patch.dict(os.environ, {"PYICLOUD_CK_EXTRA": "forbid"}, clear=True):
            self.assertEqual(resolve_cloudkit_validation_extra(), "forbid")

    def test_notes_client_allows_unexpected_fields_by_default(self):
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"records": [], "unexpectedTopLevel": {"present": True}},
        )
        client = CloudKitNotesClient(
            "https://example.com",
            session,
            {},
        )

        response = client.lookup(["Note/1"], desired_keys=None)

        self.assertIsInstance(response, CKLookupResponse)
        self.assertEqual(response.model_extra["unexpectedTopLevel"], {"present": True})

    def test_notes_client_uses_bounded_timeouts(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, json=lambda: {})
        session.get.return_value = MagicMock(
            status_code=200, iter_content=lambda **_: []
        )
        client = _CloudKitClient("https://example.com", session, {})

        client.post("/records/query", {"query": "payload"})
        list(client.get_stream("https://example.com/asset"))

        self.assertEqual(session.post.call_args.kwargs["timeout"], (10.0, 60.0))
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10.0, 60.0))

    def test_notes_client_redacts_query_strings_in_logs(self):
        redacted = _CloudKitClient._redact_url(
            "https://example.com/path?token=secret&x=1#frag"
        )
        self.assertEqual(redacted, "https://example.com/path")

    def test_notes_client_strict_mode_wraps_validation_error(self):
        session = MagicMock()
        payload = {"records": [], "unexpectedTopLevel": {"present": True}}
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

    def test_notes_client_explicit_override_wins_over_env(self):
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"records": [], "unexpectedTopLevel": {"present": True}},
        )
        with patch.dict(os.environ, {"PYICLOUD_CK_EXTRA": "forbid"}, clear=True):
            client = CloudKitNotesClient(
                "https://example.com",
                session,
                {},
                validation_extra="allow",
            )

            response = client.lookup(["Note/1"], desired_keys=None)

        self.assertEqual(response.model_extra["unexpectedTopLevel"], {"present": True})

    def test_notes_service_passes_through_validation_override(self):
        service = NotesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
            cloudkit_validation_extra="ignore",
        )

        self.assertEqual(service.raw._validation_extra, "ignore")

    def test_notes_errors_share_client_base_class(self):
        self.assertTrue(issubclass(NoteNotFound, ClientNotesError))

    def test_notes_exporter_module_imports(self):
        module = importlib.import_module("pyicloud.services.notes.rendering.exporter")

        self.assertTrue(hasattr(module, "NoteExporter"))

    def test_notes_service_render_note_uses_lazy_importer(self):
        record = CKRecord.model_validate(
            {"recordName": "Note/1", "recordType": "Note", "fields": {}}
        )
        self.service.raw.lookup = MagicMock(return_value=MagicMock(records=[record]))

        with (
            patch(
                "pyicloud.services.notes.rendering.exporter.decode_and_parse_note",
                return_value=MagicMock(name="note"),
            ),
            patch(
                "pyicloud.services.notes.rendering.exporter.build_datasource",
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

    def test_notes_service_export_note_uses_lazy_importer(self):
        record = CKRecord.model_validate(
            {"recordName": "Note/1", "recordType": "Note", "fields": {}}
        )
        self.service.raw.lookup = MagicMock(return_value=MagicMock(records=[record]))
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

    def test_notes_service_attachment_lookup_prefers_canonical_record_names(self):
        note_record = CKRecord.model_validate(
            {
                "recordName": "Note/1",
                "recordType": "Note",
                "fields": {
                    "Attachments": {
                        "type": "REFERENCE_LIST",
                        "value": [
                            {
                                "recordName": "Attachment/CANONICAL",
                                "action": "VALIDATE",
                            }
                        ],
                    }
                },
            }
        )
        attachment_record = CKRecord.model_validate(
            {
                "recordName": "Attachment/CANONICAL",
                "recordType": "Attachment",
                "fields": {
                    "AttachmentIdentifier": {"type": "STRING", "value": "ALIAS-1"},
                    "AttachmentUTI": {"type": "STRING", "value": "public.url"},
                    "PrimaryAsset": {
                        "type": "ASSETID",
                        "value": {"downloadURL": "https://example.com/file"},
                    },
                },
            }
        )
        self.service.raw.lookup = MagicMock(
            return_value=CKLookupResponse(records=[attachment_record])
        )

        attachments = self.service._resolve_attachments_for_record(
            note_record,
            attachment_ids=[AttachmentId(identifier="ALIAS-1")],
        )

        self.assertEqual(
            self.service.raw.lookup.call_args.args[0],
            ["Attachment/CANONICAL"],
        )
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].id, "Attachment/CANONICAL")
        self.assertIs(self.service._attachment_meta_cache["ALIAS-1"], attachments[0])

    def test_write_html_rejects_filename_escape(self):
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

    def test_decode_and_parse_note_returns_none_on_parse_failure(self):
        record = CKRecord.model_validate(
            {
                "recordName": "Note/1",
                "recordType": "Note",
                "fields": {
                    "TextDataEncrypted": {
                        "type": "ENCRYPTED_BYTES",
                        "value": "aGVsbG8=",
                    }
                },
            }
        )

        with (
            patch(
                "pyicloud.services.notes.rendering.exporter.BodyDecoder.decode",
                return_value=MagicMock(bytes=b"broken"),
            ),
            patch(
                "pyicloud.services.notes.rendering.exporter.pb.NoteStoreProto.ParseFromString",
                side_effect=ValueError("bad proto"),
            ),
        ):
            self.assertIsNone(decode_and_parse_note(record))

    def test_note_body_text_defaults_to_none(self):
        from pyicloud.services.notes.domain import NoteBody

        body = NoteBody(bytes=b"hello")
        self.assertIsNone(body.text)

    def test_shared_cloudkit_signed_string_timestamps_are_tolerated(self):
        created = _from_millis_or_none(" 1735689600000 ")

        self.assertIsNotNone(created)
        self.assertEqual(created.isoformat(), "2025-01-01T00:00:00+00:00")
        self.assertIsNone(_from_secs_or_millis("999999999999999999999999"))

    def test_shared_cloudkit_invalid_timestamp_types_raise_validation_error(self):
        class Demo(BaseModel):
            created: Annotated[datetime, BeforeValidator(_from_millis_or_none)]
            expires: Annotated[datetime, BeforeValidator(_from_secs_or_millis)]

        with self.assertRaises(ValidationError):
            Demo.model_validate(
                {
                    "created": object(),
                    "expires": object(),
                }
            )

    def test_shared_cloudkit_share_allows_encrypted_string_fields(self):
        """Shared cloudkit.share records may expose STRING + isEncrypted fields."""
        record = CKRecord.model_validate(
            {
                "recordName": "Share-123",
                "recordType": "cloudkit.share",
                "fields": {
                    "SnippetEncrypted": {
                        "value": "Shared snippet",
                        "type": "STRING",
                        "isEncrypted": True,
                    }
                },
            }
        )

        self.assertEqual(record.fields.get_value("SnippetEncrypted"), "Shared snippet")
        self.assertEqual(
            NotesService._decode_encrypted(record.fields.get_value("SnippetEncrypted")),
            "Shared snippet",
        )

    def test_shared_cloudkit_share_participant_surfaces_are_typed(self):
        """Shared-record participant and PCS surfaces parse into structured models."""
        record = CKRecord.model_validate(
            {
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
            }
        )

        self.assertIsInstance(record.participants, list)
        self.assertIsInstance(record.participants[0], CKParticipant)
        self.assertIsInstance(record.participants[0].userIdentity, CKUserIdentity)
        self.assertEqual(
            record.participants[0].userIdentity.nameComponents.givenName, "Jacob"
        )
        self.assertIsInstance(
            record.participants[0].protectionInfo, CKParticipantProtectionInfo
        )
        self.assertIsInstance(record.owner, CKParticipant)
        self.assertIsInstance(record.currentUserParticipant, CKParticipant)
        self.assertEqual(
            record.currentUserParticipant.userIdentity.lookupInfo.phoneNumber,
            "352621583784",
        )
        self.assertIsInstance(record.invitedPCS, CKPCSInfo)
        self.assertEqual(record.invitedPCS.pcsChangeTag, "invited-tag")
        self.assertIsInstance(record.selfAddedPCS, CKPCSInfo)
        self.assertEqual(record.selfAddedPCS.pcsChangeTag, "self-tag")

    def test_encrypted_string_fields_without_flag_are_rejected(self):
        """STRING wrappers on *Encrypted fields must carry isEncrypted=true."""
        with self.assertRaises(ValidationError):
            CKRecord.model_validate(
                {
                    "recordName": "Share-123",
                    "recordType": "cloudkit.share",
                    "fields": {
                        "SnippetEncrypted": {
                            "value": "Shared snippet",
                            "type": "STRING",
                        }
                    },
                }
            )

    def test_decode_encrypted_bytes_and_strings(self):
        """Notes encrypted decoder handles both bytes and string field values."""
        self.assertEqual(NotesService._decode_encrypted(b"hello"), "hello")
        self.assertEqual(NotesService._decode_encrypted("bonjour"), "bonjour")


if __name__ == "__main__":
    unittest.main()
