"""Unit tests for the CloudKit-based RemindersService record parsing.

Tests all _record_to_*() methods and _decode_crdt_document() using
realistic CKRecord JSON fixtures.
"""
# pylint: disable=protected-access

import base64
import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKLookupRequest,
    CKLookupResponse,
    CKModifyOperation,
    CKModifyRequest,
    CKModifyResponse,
    CKQueryResponse,
    CKRecord,
    CKTombstoneRecord,
    CKWriteRecord,
    CKZoneChangesRequest,
    CKZoneChangesResponse,
    CKZoneChangesZone,
    CKZoneID,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.base import resolve_cloudkit_validation_extra
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._protocol import (
    CRDTDecodeError,
)
from pyicloud.services.reminders._protocol import (
    _decode_crdt_document as decode_crdt_document,
)
from pyicloud.services.reminders._protocol import (
    _encode_crdt_document as encode_crdt_document,
)
from pyicloud.services.reminders._protocol import (
    _generate_resolution_token_map as generate_resolution_token_map,
)
from pyicloud.services.reminders.client import (
    CloudKitRemindersClient,
    RemindersApiError,
    _CloudKitClient,
)
from pyicloud.services.reminders.models import (
    Alarm,
    AlarmWithTrigger,
    Hashtag,
    ImageAttachment,
    ListRemindersResult,
    LocationTrigger,
    Proximity,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    ReminderChangeEvent,
    RemindersList,
    URLAttachment,
)
from pyicloud.services.reminders.service import RemindersService

# ---------------------------------------------------------------------------
# Fixture: a stubbed RemindersService (no network, just parsing)
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """Create a RemindersService with parsing methods but no network."""
    svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
    svc._raw = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ck_record(record_type: str, record_name: str, fields: dict, **extra) -> CKRecord:
    """Build a CKRecord from a raw dict, same as CloudKit JSON on the wire."""
    raw = {
        "recordName": record_name,
        "recordType": record_type,
        "fields": fields,
        **extra,
    }
    return CKRecord.model_validate(raw)


# ---------------------------------------------------------------------------
# Programmatically generated CRDT test blobs
# (versioned_document.Document -> topotext.String -> zlib -> base64)
# ---------------------------------------------------------------------------


def _make_crdt_blob(text: str) -> str:
    """Build a valid reminders CRDT blob: protobuf -> zlib -> base64 string."""
    import base64
    import zlib

    from pyicloud.services.reminders.protobuf import (
        reminders_pb2,
        versioned_document_pb2,
    )

    s = reminders_pb2.String()
    s.string = text

    version = versioned_document_pb2.Version()
    version.serializationVersion = 0
    version.minimumSupportedVersion = 0
    version.data = s.SerializeToString()

    doc = versioned_document_pb2.Document()
    doc.serializationVersion = 0
    doc.version.append(version)

    compressed = zlib.compress(doc.SerializeToString())
    return base64.b64encode(compressed).decode("ascii")


def _make_crdt_version_bytes(text: str) -> bytes:
    """Build the raw, uncompressed versioned_document.Version payload."""
    from pyicloud.services.reminders.protobuf import (
        reminders_pb2,
        versioned_document_pb2,
    )

    s = reminders_pb2.String()
    s.string = text

    version = versioned_document_pb2.Version()
    version.serializationVersion = 0
    version.minimumSupportedVersion = 0
    version.data = s.SerializeToString()
    return version.SerializeToString()


# Pre-built samples for test use
TITLE_DOC_SAMPLES = {
    "Message Benno": _make_crdt_blob("Message Benno"),
    "PRISE EN CHARGE": _make_crdt_blob("PRISE EN CHARGE"),
    "Cancel Hoess": _make_crdt_blob("Cancel Hoess"),
}


def test_reminder_domain_models_are_pydantic_and_mutable():
    reminder = Reminder(id="Reminder/A", list_id="List/A", title="A")

    assert reminder.model_dump()["id"] == "Reminder/A"
    reminder.deleted = True
    assert reminder.deleted is True
    with pytest.raises(ValidationError):
        reminder.priority = "high"
    reminder.priority = 3
    assert reminder.priority == 3
    with pytest.raises(ValidationError):
        Reminder(
            id="Reminder/B",
            list_id="List/B",
            title="B",
            unexpected=True,
        )


def test_list_result_models_are_frozen():
    result = ListRemindersResult(
        reminders=[],
        alarms={},
        triggers={},
        attachments={},
        hashtags={},
        recurrence_rules={},
    )

    with pytest.raises(ValidationError):
        result.reminders = []


def test_location_trigger_radius_must_be_non_negative():
    trigger = LocationTrigger(id="AlarmTrigger/A", alarm_id="Alarm/A")

    with pytest.raises(ValidationError):
        trigger.radius = -10.0


def test_image_attachment_dimensions_and_size_must_be_non_negative():
    attachment = ImageAttachment(id="Attachment/A", reminder_id="Reminder/A")

    with pytest.raises(ValidationError):
        attachment.file_size = -1
    with pytest.raises(ValidationError):
        attachment.width = -1
    with pytest.raises(ValidationError):
        attachment.height = -1


def test_recurrence_rule_domain_constraints_are_enforced():
    rule = RecurrenceRule(id="RecurrenceRule/A", reminder_id="Reminder/A")

    with pytest.raises(ValidationError):
        rule.interval = 0
    with pytest.raises(ValidationError):
        rule.occurrence_count = -1
    with pytest.raises(ValidationError):
        rule.first_day_of_week = 7


def test_protocol_crdt_round_trip():
    encoded = encode_crdt_document("Round trip")

    assert isinstance(encoded, str)
    assert decode_crdt_document(encoded) == "Round trip"


def test_protocol_resolution_token_map_structure():
    payload = json.loads(generate_resolution_token_map(["titleDocument", "completed"]))

    assert set(payload.keys()) == {"map"}
    assert set(payload["map"].keys()) == {"titleDocument", "completed"}
    for token in payload["map"].values():
        assert token["counter"] == 1
        assert isinstance(token["modificationTime"], float)
        assert isinstance(token["replicaID"], str)
        assert token["replicaID"]


def test_mapper_asset_backed_list_membership_download():
    raw = MagicMock()
    raw.download_asset_bytes.return_value = b'["REM-3","Reminder/REM-4"]'
    mapper = RemindersRecordMapper(lambda: raw, logging.getLogger(__name__))
    rec = _ck_record(
        "List",
        "List/LIST-ASSET",
        {
            "Name": {"type": "STRING", "value": "Asset-backed"},
            "ReminderIDsAsset": {
                "type": "ASSETID",
                "value": {
                    "fileChecksum": "abc123",
                    "size": 2,
                    "wrappingKey": "key",
                    "downloadURL": "https://example.test/reminder_ids",
                    "referenceChecksum": "ref",
                    "signature": "sig",
                },
            },
        },
    )

    lst = mapper.record_to_list(rec)

    assert lst.reminder_ids == ["REM-3", "REM-4"]
    raw.download_asset_bytes.assert_called_once_with(
        "https://example.test/reminder_ids"
    )


def test_cloudkit_client_uses_bounded_timeouts():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, json=lambda: {})
    session.get.return_value = MagicMock(status_code=200, content=b"asset-bytes")
    client = _CloudKitClient("https://ckdatabasews.icloud.com", session, {})

    client.post("/records/query", {"query": "payload"})
    client.get_bytes("https://example.test/asset")

    assert session.post.call_args.kwargs["timeout"] == (10.0, 60.0)
    assert session.get.call_args.kwargs["timeout"] == (10.0, 60.0)


def test_resolve_cloudkit_validation_extra_honors_explicit_override(monkeypatch):
    monkeypatch.setenv("PYICLOUD_CK_EXTRA", "forbid")

    assert resolve_cloudkit_validation_extra("allow") == "allow"


def test_reminders_client_allows_unexpected_fields_by_default(monkeypatch):
    monkeypatch.delenv("PYICLOUD_CK_EXTRA", raising=False)
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"records": [], "unexpectedTopLevel": {"present": True}},
    )
    client = CloudKitRemindersClient("https://example.com", session, {})

    response = client.lookup(["Reminder/1"], CKZoneIDReq(zoneName="Reminders"))

    assert isinstance(response, CKLookupResponse)
    assert response.model_extra["unexpectedTopLevel"] == {"present": True}


def test_reminders_client_strict_mode_wraps_validation_error():
    session = MagicMock()
    payload = {"records": [], "unexpectedTopLevel": {"present": True}}
    session.post.return_value = MagicMock(status_code=200, json=lambda: payload)
    client = CloudKitRemindersClient(
        "https://example.com",
        session,
        {},
        validation_extra="forbid",
    )

    with pytest.raises(
        RemindersApiError, match="Lookup response validation failed"
    ) as excinfo:
        client.lookup(["Reminder/1"], CKZoneIDReq(zoneName="Reminders"))

    assert excinfo.value.payload == payload
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_reminders_service_passes_through_validation_override():
    service = RemindersService(
        "https://example.com",
        MagicMock(),
        {},
        cloudkit_validation_extra="ignore",
    )

    assert service._raw._validation_extra == "ignore"


# ---------------------------------------------------------------------------
# Tests: _decode_crdt_document
# ---------------------------------------------------------------------------


class TestDecodeCrdtDocument:
    """Test the shared CRDT document decoder."""

    def test_decode_message_benno(self, service):
        result = service._decode_crdt_document(TITLE_DOC_SAMPLES["Message Benno"])
        assert result == "Message Benno"

    def test_decode_prise_en_charge(self, service):
        result = service._decode_crdt_document(TITLE_DOC_SAMPLES["PRISE EN CHARGE"])
        assert result == "PRISE EN CHARGE"

    def test_decode_cancel_hoess(self, service):
        result = service._decode_crdt_document(TITLE_DOC_SAMPLES["Cancel Hoess"])
        assert result == "Cancel Hoess"

    def test_decode_empty_raises(self, service):
        with pytest.raises(CRDTDecodeError, match="Unable to decode CRDT document"):
            service._decode_crdt_document("")

    def test_decode_malformed_base64_raises(self, service):
        with pytest.raises(
            CRDTDecodeError, match="Invalid base64-encoded CRDT document"
        ):
            service._decode_crdt_document("!!!not-base64!!!")

    def test_decode_bytes_input(self, service):
        """Accept raw bytes as well as base64 string."""
        import base64

        raw = base64.b64decode(TITLE_DOC_SAMPLES["Message Benno"])
        result = service._decode_crdt_document(raw)
        assert result == "Message Benno"

    def test_decode_uncompressed_version_bytes(self, service):
        raw = _make_crdt_version_bytes("Buy groceries")

        result = service._decode_crdt_document(raw)

        assert result == "Buy groceries"


# ---------------------------------------------------------------------------
# Tests: _record_to_reminder
# ---------------------------------------------------------------------------


class TestRecordToReminder:
    """Test parsing a Reminder CKRecord."""

    def test_basic_reminder(self, service):
        rec = _ck_record(
            "Reminder",
            "REM-001",
            {
                "TitleDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": TITLE_DOC_SAMPLES["Message Benno"],
                },
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-001", "action": "VALIDATE"},
                },
                "Priority": {"type": "INT64", "value": 0},
                "Completed": {"type": "INT64", "value": 0},
                "Flagged": {"type": "INT64", "value": 0},
                "AllDay": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
        )
        r = service._record_to_reminder(rec)

        assert isinstance(r, Reminder)
        assert r.id == "REM-001"
        assert r.title == "Message Benno"
        assert r.list_id == "LIST-001"
        assert r.priority == 0
        assert r.completed is False
        assert r.flagged is False
        assert r.all_day is False
        assert r.deleted is False
        assert r.alarm_ids == []
        assert r.hashtag_ids == []

    def test_reminder_with_all_fields(self, service):
        completion_date = datetime(2024, 12, 29, tzinfo=timezone.utc)
        created = datetime(2024, 12, 28, tzinfo=timezone.utc)
        modified = datetime(2024, 12, 30, tzinfo=timezone.utc)
        rec = _ck_record(
            "Reminder",
            "REM-002",
            {
                "TitleDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": TITLE_DOC_SAMPLES["PRISE EN CHARGE"],
                },
                "NotesDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": TITLE_DOC_SAMPLES["Cancel Hoess"],
                },
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-002", "action": "VALIDATE"},
                },
                "Priority": {"type": "INT64", "value": 1},
                "Completed": {"type": "INT64", "value": 1},
                "Flagged": {"type": "INT64", "value": 1},
                "AllDay": {"type": "INT64", "value": 1},
                "Deleted": {"type": "INT64", "value": 0},
                "TimeZone": {"type": "STRING", "value": "Europe/Paris"},
                "DueDate": {"type": "TIMESTAMP", "value": 1735488000000},
                "StartDate": {"type": "TIMESTAMP", "value": 1735488000000},
                "CompletionDate": {
                    "type": "TIMESTAMP",
                    "value": int(completion_date.timestamp() * 1000),
                },
                "CreationDate": {
                    "type": "TIMESTAMP",
                    "value": int(created.timestamp() * 1000),
                },
                "LastModifiedDate": {
                    "type": "TIMESTAMP",
                    "value": int(modified.timestamp() * 1000),
                },
                "AlarmIDs": {"type": "STRING_LIST", "value": ["alarm-1", "alarm-2"]},
                "HashtagIDs": {"type": "STRING_LIST", "value": ["hashtag-1"]},
                "AttachmentIDs": {"type": "STRING_LIST", "value": ["attach-1"]},
            },
        )
        r = service._record_to_reminder(rec)

        assert r.title == "PRISE EN CHARGE"
        assert r.desc == "Cancel Hoess"
        assert r.priority == 1
        assert r.completed is True
        assert r.flagged is True
        assert r.all_day is True
        assert r.time_zone == "Europe/Paris"
        assert r.due_date is not None
        assert r.completed_date == completion_date
        assert r.created == created
        assert r.modified == modified
        assert r.alarm_ids == ["alarm-1", "alarm-2"]
        assert r.hashtag_ids == ["hashtag-1"]
        assert r.attachment_ids == ["attach-1"]

    def test_reminder_malformed_title_document_uses_placeholder(self, service):
        rec = _ck_record(
            "Reminder",
            "REM-BAD-TITLE",
            {
                "TitleDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": base64.b64encode(b"not-a-crdt").decode("ascii"),
                },
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-001", "action": "VALIDATE"},
                },
            },
        )

        reminder = service._record_to_reminder(rec)

        assert reminder.title == "Error Decoding Title"

    def test_reminder_falls_back_to_record_audit_timestamps(self, service):
        created = datetime(2024, 12, 28, tzinfo=timezone.utc)
        modified = datetime(2024, 12, 30, tzinfo=timezone.utc)
        rec = _ck_record(
            "Reminder",
            "REM-003",
            {
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-003", "action": "VALIDATE"},
                },
                "Completed": {"type": "INT64", "value": 0},
                "Priority": {"type": "INT64", "value": 0},
                "Flagged": {"type": "INT64", "value": 0},
                "AllDay": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
            created={"timestamp": int(created.timestamp() * 1000)},
            modified={"timestamp": int(modified.timestamp() * 1000)},
        )

        reminder = service._record_to_reminder(rec)
        assert reminder.created == created
        assert reminder.modified == modified

    def test_reminder_with_uncompressed_version_bytes_documents(self, service):
        rec = _ck_record(
            "Reminder",
            "REM-003B",
            {
                "TitleDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": base64.b64encode(
                        _make_crdt_version_bytes("Buy groceries")
                    ).decode("ascii"),
                },
                "NotesDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": base64.b64encode(
                        _make_crdt_version_bytes("Milk, Eggs")
                    ).decode("ascii"),
                },
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-003B", "action": "VALIDATE"},
                },
            },
        )

        reminder = service._record_to_reminder(rec)

        assert reminder.title == "Buy groceries"
        assert reminder.desc == "Milk, Eggs"

    def test_subtask_reminder(self, service):
        """Subtask reminders have a ParentReminder REFERENCE."""
        rec = _ck_record(
            "Reminder",
            "REM-SUBTASK",
            {
                "TitleDocument": {
                    "type": "ENCRYPTED_BYTES",
                    "value": TITLE_DOC_SAMPLES["Cancel Hoess"],
                },
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": "LIST-001"},
                },
                "ParentReminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-PARENT", "action": "VALIDATE"},
                },
                "Priority": {"type": "INT64", "value": 0},
                "Completed": {"type": "INT64", "value": 0},
                "Flagged": {"type": "INT64", "value": 0},
                "AllDay": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
        )
        r = service._record_to_reminder(rec)

        assert r.id == "REM-SUBTASK"
        assert r.parent_reminder_id == "REM-PARENT"
        assert r.title == "Cancel Hoess"


# ---------------------------------------------------------------------------
# Tests: _record_to_list
# ---------------------------------------------------------------------------


class TestRecordToList:
    """Test parsing a List CKRecord."""

    def test_basic_list(self, service):
        rec = _ck_record(
            "List",
            "LIST-001",
            {
                "Name": {"type": "STRING", "value": "pyicloud"},
                "Color": {"type": "STRING", "value": "#FF6600"},
                "Count": {"type": "INT64", "value": 16},
                "IsGroup": {"type": "INT64", "value": 0},
            },
        )
        lst = service._record_to_list(rec)

        assert isinstance(lst, RemindersList)
        assert lst.id == "LIST-001"
        assert lst.title == "pyicloud"
        assert lst.color == "#FF6600"
        assert lst.count == 16
        assert lst.is_group is False

    def test_list_untitled(self, service):
        rec = _ck_record("List", "LIST-002", {})
        lst = service._record_to_list(rec)
        assert lst.title == "Untitled"
        assert lst.count == 0

    def test_list_parses_inline_reminder_ids_json(self, service):
        rec = _ck_record(
            "List",
            "LIST-003",
            {
                "ReminderIDs": {
                    "type": "STRING",
                    "value": '["REM-1","Reminder/REM-2"]',
                }
            },
        )

        lst = service._record_to_list(rec)
        assert lst.reminder_ids == ["REM-1", "REM-2"]
        assert lst.count == 2

    def test_list_falls_back_to_reminder_ids_length_when_count_missing(self, service):
        rec = _ck_record(
            "List",
            "LIST-003A",
            {
                "ReminderIDs": {
                    "type": "STRING",
                    "value": '["REM-1","Reminder/REM-2","REM-3"]',
                }
            },
        )

        lst = service._record_to_list(rec)
        assert lst.reminder_ids == ["REM-1", "REM-2", "REM-3"]
        assert lst.count == 3

    def test_list_falls_back_to_reminder_ids_length_when_count_is_zero(self, service):
        rec = _ck_record(
            "List",
            "LIST-003B",
            {
                "Count": {"type": "INT64", "value": 0},
                "ReminderIDs": {
                    "type": "STRING",
                    "value": '["REM-1","Reminder/REM-2"]',
                },
            },
        )

        lst = service._record_to_list(rec)
        assert lst.reminder_ids == ["REM-1", "REM-2"]
        assert lst.count == 2

    def test_list_parses_asset_backed_reminder_ids_from_downloaded_data(self, service):
        payload = base64.b64encode(b'["REM-1","Reminder/REM-2"]').decode("ascii")
        rec = _ck_record(
            "List",
            "LIST-004",
            {
                "ReminderIDsAsset": {
                    "type": "ASSET",
                    "value": {"downloadedData": payload},
                }
            },
        )

        lst = service._record_to_list(rec)
        assert lst.reminder_ids == ["REM-1", "REM-2"]
        assert lst.count == 2
        service._raw.download_asset_bytes.assert_not_called()

    def test_list_parses_asset_backed_reminder_ids_from_download_url(self, service):
        service._raw.download_asset_bytes.return_value = b'["REM-3","Reminder/REM-4"]'
        rec = _ck_record(
            "List",
            "LIST-005",
            {
                "ReminderIDsAsset": {
                    "type": "ASSET",
                    "value": {"downloadURL": "https://example.com/reminder-ids.json"},
                }
            },
        )

        lst = service._record_to_list(rec)
        assert lst.reminder_ids == ["REM-3", "REM-4"]
        assert lst.count == 2
        service._raw.download_asset_bytes.assert_called_once_with(
            "https://example.com/reminder-ids.json"
        )

    @pytest.mark.parametrize(
        ("asset_value", "download_side_effect"),
        [
            (
                {"downloadedData": base64.b64encode(b"{bad json").decode("ascii")},
                None,
            ),
            (
                {"downloadURL": "https://example.com/reminder-ids.json"},
                RemindersApiError("download failed"),
            ),
        ],
    )
    def test_list_asset_failures_raise(
        self, service, asset_value, download_side_effect
    ):
        if download_side_effect is not None:
            service._raw.download_asset_bytes.side_effect = download_side_effect

        rec = _ck_record(
            "List",
            "LIST-006",
            {
                "ReminderIDsAsset": {
                    "type": "ASSET",
                    "value": asset_value,
                }
            },
        )

        with pytest.raises(RemindersApiError):
            service._record_to_list(rec)


# ---------------------------------------------------------------------------
# Tests: _record_to_alarm
# ---------------------------------------------------------------------------


class TestRecordToAlarm:
    """Test parsing an Alarm CKRecord."""

    def test_alarm(self, service):
        rec = _ck_record(
            "Alarm",
            "Alarm/ALARM-001",
            {
                "AlarmUID": {"type": "STRING", "value": "ALARM-001"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-001", "action": "VALIDATE"},
                },
                "TriggerID": {"type": "STRING", "value": "TRIGGER-001"},
                "Deleted": {"type": "INT64", "value": 0},
            },
        )
        a = service._record_to_alarm(rec)

        assert isinstance(a, Alarm)
        assert a.id == "Alarm/ALARM-001"
        assert a.alarm_uid == "ALARM-001"
        assert a.reminder_id == "REM-001"
        assert a.trigger_id == "TRIGGER-001"


# ---------------------------------------------------------------------------
# Tests: _record_to_alarm_trigger
# ---------------------------------------------------------------------------


class TestRecordToAlarmTrigger:
    """Test parsing supported AlarmTrigger CKRecords."""

    def test_location_trigger(self, service):
        rec = _ck_record(
            "AlarmTrigger",
            "AlarmTrigger/TRIG-001",
            {
                "Type": {"type": "STRING", "value": "Location"},
                "Title": {"type": "STRING", "value": "Paris"},
                "Address": {"type": "STRING", "value": "Paris, France"},
                "Latitude": {"type": "DOUBLE", "value": 48.8567879},
                "Longitude": {"type": "DOUBLE", "value": 2.3510768},
                "Radius": {"type": "DOUBLE", "value": 8972.70},
                "Proximity": {"type": "INT64", "value": 1},
                "LocationUID": {"type": "STRING", "value": "LOC-UUID-001"},
                "Alarm": {
                    "type": "REFERENCE",
                    "value": {"recordName": "Alarm/ALARM-001", "action": "VALIDATE"},
                },
            },
        )
        t = service._record_to_alarm_trigger(rec)

        assert isinstance(t, LocationTrigger)
        assert t.title == "Paris"
        assert t.address == "Paris, France"
        assert abs(t.latitude - 48.8567879) < 0.0001
        assert abs(t.longitude - 2.3510768) < 0.0001
        assert abs(t.radius - 8972.70) < 0.1
        assert t.proximity == Proximity.ARRIVING
        assert t.alarm_id == "Alarm/ALARM-001"

    def test_location_trigger_leaving(self, service):
        rec = _ck_record(
            "AlarmTrigger",
            "AlarmTrigger/TRIG-002",
            {
                "Type": {"type": "STRING", "value": "Location"},
                "Title": {"type": "STRING", "value": "Home"},
                "Proximity": {"type": "INT64", "value": 2},
                "Alarm": {
                    "type": "REFERENCE",
                    "value": {"recordName": "Alarm/ALARM-002"},
                },
            },
        )
        t = service._record_to_alarm_trigger(rec)

        assert isinstance(t, LocationTrigger)
        assert t.proximity == Proximity.LEAVING

    def test_vehicle_trigger_is_ignored(self, service):
        rec = _ck_record(
            "AlarmTrigger",
            "AlarmTrigger/TRIG-003",
            {
                "Type": {"type": "STRING", "value": "Vehicle"},
                "Event": {"type": "INT64", "value": 1},
                "Alarm": {
                    "type": "REFERENCE",
                    "value": {"recordName": "Alarm/ALARM-003"},
                },
            },
        )
        assert service._record_to_alarm_trigger(rec) is None

    def test_unknown_type_returns_none(self, service):
        rec = _ck_record(
            "AlarmTrigger",
            "AlarmTrigger/TRIG-005",
            {
                "Type": {"type": "STRING", "value": "FutureTriggerType"},
                "Alarm": {
                    "type": "REFERENCE",
                    "value": {"recordName": "Alarm/ALARM-005"},
                },
            },
        )
        assert service._record_to_alarm_trigger(rec) is None


# ---------------------------------------------------------------------------
# Tests: _record_to_attachment
# ---------------------------------------------------------------------------


class TestRecordToAttachment:
    """Test parsing Attachment CKRecords (URL and Image)."""

    def test_url_attachment(self, service):
        rec = _ck_record(
            "Attachment",
            "Attachment/ATT-001",
            {
                "Type": {"type": "STRING", "value": "URL"},
                "URL": {"type": "STRING", "value": "https://discord.gg/CAGYSbyqYk"},
                "UTI": {"type": "STRING", "value": "public.url"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-URL", "action": "VALIDATE"},
                },
            },
        )
        att = service._record_to_attachment(rec)

        assert isinstance(att, URLAttachment)
        assert att.url == "https://discord.gg/CAGYSbyqYk"
        assert att.uti == "public.url"
        assert att.reminder_id == "REM-URL"

    def test_url_attachment_decodes_base64_payload(self, service):
        encoded_url = base64.b64encode(b"https://discord.gg/CAGYSbyqYk").decode("ascii")
        rec = _ck_record(
            "Attachment",
            "Attachment/ATT-001B",
            {
                "Type": {"type": "STRING", "value": "URL"},
                "URL": {"type": "STRING", "value": encoded_url},
                "UTI": {"type": "STRING", "value": "public.url"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-URL", "action": "VALIDATE"},
                },
            },
        )

        att = service._record_to_attachment(rec)

        assert isinstance(att, URLAttachment)
        assert att.url == "https://discord.gg/CAGYSbyqYk"

    def test_url_attachment_falls_back_to_raw_invalid_payload(self, service):
        rec = _ck_record(
            "Attachment",
            "Attachment/ATT-001C",
            {
                "Type": {"type": "STRING", "value": "URL"},
                "URL": {"type": "STRING", "value": "not-base64-at-all"},
                "UTI": {"type": "STRING", "value": "public.url"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-URL", "action": "VALIDATE"},
                },
            },
        )

        att = service._record_to_attachment(rec)

        assert isinstance(att, URLAttachment)
        assert att.url == "not-base64-at-all"

    def test_image_attachment(self, service):
        rec = _ck_record(
            "Attachment",
            "Attachment/ATT-002",
            {
                "Type": {"type": "STRING", "value": "Image"},
                "FileAsset": {
                    "type": "ASSETID",
                    "value": {
                        "fileChecksum": "abc123",
                        "downloadURL": "https://cvws.icloud-content.com/photo.jpeg",
                        "size": 116261,
                    },
                },
                "FileName": {"type": "STRING", "value": "IMG_1234.jpeg"},
                "FileSize": {"type": "INT64", "value": 116261},
                "Width": {"type": "INT64", "value": 1164},
                "Height": {"type": "INT64", "value": 1248},
                "UTI": {"type": "STRING", "value": "public.jpeg"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-IMG", "action": "VALIDATE"},
                },
            },
        )
        att = service._record_to_attachment(rec)

        assert isinstance(att, ImageAttachment)
        assert att.filename == "IMG_1234.jpeg"
        assert att.file_size == 116261
        assert att.width == 1164
        assert att.height == 1248
        assert att.uti == "public.jpeg"
        assert att.reminder_id == "REM-IMG"
        assert "photo.jpeg" in att.file_asset_url

    def test_unknown_type_returns_none(self, service):
        rec = _ck_record(
            "Attachment",
            "Attachment/ATT-003",
            {
                "Type": {"type": "STRING", "value": "UnknownType"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-X"},
                },
            },
        )
        assert service._record_to_attachment(rec) is None


# ---------------------------------------------------------------------------
# Tests: _record_to_hashtag
# ---------------------------------------------------------------------------


class TestRecordToHashtag:
    """Test parsing Hashtag CKRecords."""

    def test_hashtag(self, service):
        rec = _ck_record(
            "Hashtag",
            "Hashtag/HASH-001",
            {
                "Name": {"type": "STRING", "value": "mytag1"},
                "Type": {"type": "INT64", "value": 0},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-TAG", "action": "VALIDATE"},
                },
                "CreationDate": {"type": "TIMESTAMP", "value": 1735488000000},
                "Deleted": {"type": "INT64", "value": 0},
            },
        )
        h = service._record_to_hashtag(rec)

        assert isinstance(h, Hashtag)
        assert h.name == "mytag1"
        assert h.reminder_id == "REM-TAG"
        assert h.created is not None

    def test_hashtag_name_from_encrypted_bytes(self, service):
        rec = _ck_record(
            "Hashtag",
            "Hashtag/HASH-002",
            {
                "Name": {
                    "type": "ENCRYPTED_BYTES",
                    "value": base64.b64encode(b"personal").decode("ascii"),
                },
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-TAG", "action": "VALIDATE"},
                },
            },
        )

        h = service._record_to_hashtag(rec)

        assert h.name == "personal"

    def test_hashtag_name_with_undecodable_bytes_does_not_crash(self, service):
        rec = _ck_record(
            "Hashtag",
            "Hashtag/HASH-003",
            {
                "Name": {
                    "type": "ENCRYPTED_BYTES",
                    "value": base64.b64encode(b"\xa7(\x9c\x96\x8b\x9d\xfa\xea").decode(
                        "ascii"
                    ),
                },
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-TAG", "action": "VALIDATE"},
                },
            },
        )

        h = service._record_to_hashtag(rec)

        assert isinstance(h.name, str)
        assert h.name


# ---------------------------------------------------------------------------
# Tests: STRING_LIST field type handling
# ---------------------------------------------------------------------------


class TestStringListField:
    """Verify STRING_LIST fields are properly parsed by CKRecord."""

    def test_string_list_parsed(self):
        """STRING_LIST should be parsed as CKStringListField, not CKPassthroughField."""
        from pyicloud.common.cloudkit.models import CKStringListField

        rec = _ck_record(
            "Reminder",
            "REM-SL",
            {
                "AlarmIDs": {"type": "STRING_LIST", "value": ["id-1", "id-2", "id-3"]},
            },
        )
        field = rec.fields.get_field("AlarmIDs")
        assert isinstance(field, CKStringListField)
        assert field.value == ["id-1", "id-2", "id-3"]

    def test_empty_string_list(self):
        rec = _ck_record(
            "Reminder",
            "REM-SL2",
            {
                "HashtagIDs": {"type": "STRING_LIST", "value": []},
            },
        )
        field = rec.fields.get_field("HashtagIDs")
        assert field.value == []


# ---------------------------------------------------------------------------
# Tests: _record_to_recurrence_rule
# ---------------------------------------------------------------------------


class TestRecordToRecurrenceRule:
    """Test parsing RecurrenceRule CKRecords."""

    def test_monthly_recurrence(self, service):
        rec = _ck_record(
            "RecurrenceRule",
            "RecurrenceRule/RR-001",
            {
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-001", "action": "VALIDATE"},
                },
                "Frequency": {"type": "INT64", "value": 3},
                "Interval": {"type": "INT64", "value": 1},
                "OccurrenceCount": {"type": "INT64", "value": 0},
                "FirstDayOfTheWeek": {"type": "INT64", "value": 0},
            },
        )
        rr = service._record_to_recurrence_rule(rec)

        assert isinstance(rr, RecurrenceRule)
        assert rr.id == "RecurrenceRule/RR-001"
        assert rr.reminder_id == "REM-001"
        assert rr.frequency == RecurrenceFrequency.MONTHLY
        assert rr.interval == 1
        assert rr.occurrence_count == 0
        assert rr.first_day_of_week == 0

    def test_weekly_with_occurrence_limit(self, service):
        rec = _ck_record(
            "RecurrenceRule",
            "RecurrenceRule/RR-002",
            {
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-002"},
                },
                "Frequency": {"type": "INT64", "value": 2},
                "Interval": {"type": "INT64", "value": 2},
                "OccurrenceCount": {"type": "INT64", "value": 10},
                "FirstDayOfTheWeek": {"type": "INT64", "value": 2},
            },
        )
        rr = service._record_to_recurrence_rule(rec)

        assert rr.frequency == RecurrenceFrequency.WEEKLY
        assert rr.interval == 2
        assert rr.occurrence_count == 10
        assert rr.first_day_of_week == 2

    def test_unknown_frequency_defaults_to_daily(self, service):
        rec = _ck_record(
            "RecurrenceRule",
            "RecurrenceRule/RR-003",
            {
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": "REM-003"},
                },
                "Frequency": {"type": "INT64", "value": 99},
            },
        )
        rr = service._record_to_recurrence_rule(rec)

        assert rr.frequency == RecurrenceFrequency.DAILY


# ---------------------------------------------------------------------------
# Tests: modify serialization + mutation failure handling
# ---------------------------------------------------------------------------


class TestModifySerialization:
    """Ensure request models preserve CloudKit wire shape."""

    def test_double_field_keeps_is_encrypted_in_modify_payload(self):
        trigger_record = CKWriteRecord.model_validate(
            {
                "recordName": "AlarmTrigger/TRIG-DOUBLE",
                "recordType": "AlarmTrigger",
                "fields": {
                    "Latitude": {
                        "type": "DOUBLE",
                        "value": 48.8584,
                        "isEncrypted": True,
                    },
                    "Longitude": {
                        "type": "DOUBLE",
                        "value": 2.2945,
                        "isEncrypted": True,
                    },
                    "Type": {"type": "STRING", "value": "Location"},
                },
            }
        )
        op = CKModifyOperation(operationType="create", record=trigger_record)
        payload = CKModifyRequest(
            operations=[op],
            zoneID=CKZoneIDReq(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE"),
        ).model_dump(mode="json", exclude_none=True)

        fields = payload["operations"][0]["record"]["fields"]
        assert fields["Latitude"]["isEncrypted"] is True
        assert fields["Longitude"]["isEncrypted"] is True

    def test_lookup_request_serializes_desired_keys(self):
        payload = CKLookupRequest(
            records=[],
            zoneID=CKZoneIDReq(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE"),
            desiredKeys=["TitleDocument", "NotesDocument"],
        ).model_dump(mode="json", exclude_none=True)

        assert payload["desiredKeys"] == ["TitleDocument", "NotesDocument"]

    def test_zone_changes_request_serializes_results_limit(self):
        payload = CKZoneChangesRequest(
            zones=[
                {
                    "zoneID": {
                        "zoneName": "Reminders",
                        "zoneType": "REGULAR_CUSTOM_ZONE",
                    }
                }
            ],
            resultsLimit=50,
        ).model_dump(mode="json", exclude_none=True)

        assert payload["resultsLimit"] == 50


class TestMutationErrorHandling:
    """Mutation methods should raise on per-record CloudKit failures."""

    def test_add_location_trigger_raises_on_partial_modify_failure(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                CKErrorItem(
                    serverErrorCode="BAD_REQUEST",
                    reason="Invalid value, expected type ENCRYPTED_BYTES.",
                    recordName="AlarmTrigger/TRIG-FAIL",
                )
            ],
            syncToken="mock-sync-token",
        )

        reminder = Reminder(
            id="Reminder/REM-001",
            list_id="List/LIST-001",
            title="Pick up coffee near Eiffel Tower",
            record_change_tag="mock-change-tag",
            alarm_ids=[],
        )

        with pytest.raises(RemindersApiError, match=r"AlarmTrigger/TRIG-FAIL"):
            svc.add_location_trigger(
                reminder=reminder,
                title="Eiffel Tower",
                address="Paris",
                latitude=48.8584,
                longitude=2.2945,
                radius=150.0,
                proximity=Proximity.ARRIVING,
            )

        assert reminder.alarm_ids == []

    def test_add_location_trigger_validates_radius_before_modify(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/REM-001",
            list_id="List/LIST-001",
            title="Pick up coffee near Eiffel Tower",
            record_change_tag="mock-change-tag",
            alarm_ids=[],
        )

        with pytest.raises(ValidationError):
            svc.add_location_trigger(
                reminder=reminder,
                title="Eiffel Tower",
                address="Paris",
                latitude=48.8584,
                longitude=2.2945,
                radius=-1.0,
                proximity=Proximity.ARRIVING,
            )

        svc._raw.modify.assert_not_called()

    def test_add_location_trigger_normalizes_shorthand_reminder_ids(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        def _ack(
            record_name: str, record_type: str, record_change_tag: str
        ) -> CKRecord:
            return CKRecord.model_validate(
                {
                    "recordName": record_name,
                    "recordType": record_type,
                    "recordChangeTag": record_change_tag,
                    "fields": {},
                }
            )

        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                _ack("Reminder/REM-TRIG", "Reminder", "ctag-rem-new"),
                _ack("Alarm/ALARM-1", "Alarm", "ctag-alarm-new"),
                _ack("AlarmTrigger/TRIG-1", "AlarmTrigger", "ctag-trigger-new"),
            ],
            syncToken="mock-sync",
        )

        with patch(
            "uuid.uuid4",
            side_effect=["ALARM-1", "TRIG-1", "LOC-1", "TOKEN-1", "TOKEN-2"],
        ):
            alarm, trigger = svc.add_location_trigger(
                reminder=Reminder(
                    id="REM-TRIG",
                    list_id="List/LIST-001",
                    title="Reminder",
                    record_change_tag="ctag-rem-old",
                    alarm_ids=[],
                ),
                title="Office",
                address="1 Infinite Loop",
                latitude=37.3318,
                longitude=-122.0312,
                radius=150.0,
                proximity=Proximity.ARRIVING,
            )

        operations = svc._raw.modify.call_args.kwargs["operations"]
        assert operations[0].record.recordName == "Reminder/REM-TRIG"
        assert (
            operations[1].record.fields["Reminder"].value.recordName
            == "Reminder/REM-TRIG"
        )
        assert operations[1].record.parent.recordName == "Reminder/REM-TRIG"
        assert alarm.reminder_id == "Reminder/REM-TRIG"
        assert trigger.id == "AlarmTrigger/TRIG-1"

    def test_create_child_reminder_sets_parent_reference(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(records=[], syncToken="mock")
        expected = Reminder(
            id="Reminder/CHILD-001",
            list_id="List/LIST-001",
            title="Child reminder",
            parent_reminder_id="Reminder/PARENT-001",
        )
        svc._writes._lookup_created_reminder = MagicMock(return_value=expected)

        created = svc.create(
            list_id="List/LIST-001",
            title="Child reminder",
            parent_reminder_id="Reminder/PARENT-001",
        )

        op = svc._raw.modify.call_args.kwargs["operations"][0]
        parent_field = op.record.fields["ParentReminder"]
        assert parent_field.type_tag == "REFERENCE"
        assert parent_field.value.recordName == "Reminder/PARENT-001"
        assert created.parent_reminder_id == "Reminder/PARENT-001"

    def test_create_completed_reminder_sets_completion_date(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(records=[], syncToken="mock")
        expected = Reminder(
            id="Reminder/COMPLETE-001",
            list_id="List/LIST-001",
            title="Completed reminder",
            completed=True,
        )
        svc._writes._lookup_created_reminder = MagicMock(return_value=expected)

        svc.create(
            list_id="List/LIST-001",
            title="Completed reminder",
            completed=True,
        )

        op = svc._raw.modify.call_args.kwargs["operations"][0]
        completion_field = op.record.fields["CompletionDate"]
        assert completion_field.type_tag == "TIMESTAMP"
        assert completion_field.value is not None


class TestAdditionalWriteApis:
    """Validate payload shape and local state updates for newly added write APIs."""

    @staticmethod
    def _ok_modify() -> CKModifyResponse:
        return CKModifyResponse(records=[], syncToken="mock-sync")

    @staticmethod
    def _ack(record_name: str, record_type: str, record_change_tag: str) -> CKRecord:
        return CKRecord.model_validate(
            {
                "recordName": record_name,
                "recordType": record_type,
                "recordChangeTag": record_change_tag,
                "fields": {},
            }
        )

    def test_create_and_delete_hashtag(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = self._ok_modify()

        reminder = Reminder(
            id="Reminder/REM-TAG",
            list_id="List/LIST-001",
            title="Hashtag reminder",
            record_change_tag="ctag-rem",
            hashtag_ids=[],
        )

        hashtag = svc.create_hashtag(reminder, "travel")
        assert hashtag.id.startswith("Hashtag/")
        assert hashtag.name == "travel"
        assert len(reminder.hashtag_ids) == 1

        create_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(create_ops) == 2
        assert create_ops[1].record.recordType == "Hashtag"
        assert create_ops[1].record.fields["Name"].value == "travel"
        assert svc._raw.modify.call_args.kwargs["atomic"] is True

        svc._raw.modify.reset_mock()
        svc._raw.modify.return_value = self._ok_modify()
        svc.delete_hashtag(reminder, hashtag)

        assert reminder.hashtag_ids == []
        delete_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(delete_ops) == 2
        assert delete_ops[1].record.fields["Deleted"].value == 1
        assert svc._raw.modify.call_args.kwargs["atomic"] is True

    def test_delete_hashtag_rejects_mismatched_parent(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/REM-TAG",
            list_id="List/LIST-001",
            title="Hashtag reminder",
            record_change_tag="ctag-rem",
            hashtag_ids=["TAG-1"],
        )
        hashtag = Hashtag(
            id="Hashtag/TAG-1",
            name="travel",
            reminder_id="Reminder/OTHER-REMINDER",
            record_change_tag="ctag-tag",
        )

        with pytest.raises(ValueError, match="Hashtag child"):
            svc.delete_hashtag(reminder, hashtag)

        svc._raw.modify.assert_not_called()
        assert reminder.hashtag_ids == ["TAG-1"]

    def test_create_update_delete_url_attachment(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = self._ok_modify()

        reminder = Reminder(
            id="Reminder/REM-ATT",
            list_id="List/LIST-001",
            title="Attachment reminder",
            record_change_tag="ctag-rem",
            attachment_ids=[],
        )

        attachment = svc.create_url_attachment(
            reminder=reminder,
            url="https://example.com",
            uti="public.url",
        )
        assert attachment.id.startswith("Attachment/")
        assert len(reminder.attachment_ids) == 1
        create_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert create_ops[1].record.recordType == "Attachment"
        assert create_ops[1].record.fields["Type"].value == "URL"
        assert create_ops[1].record.fields["URL"].value == "https://example.com"
        assert create_ops[1].record.fields["URL"].unwrap().isEncrypted is True

        svc._raw.modify.reset_mock()
        svc._raw.modify.return_value = self._ok_modify()
        svc.update_attachment(attachment, url="https://example.org")
        assert attachment.url == "https://example.org"
        update_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(update_ops) == 1
        assert update_ops[0].record.fields["URL"].value == "https://example.org"
        assert update_ops[0].record.fields["URL"].unwrap().isEncrypted is True

        svc._raw.modify.reset_mock()
        svc._raw.modify.return_value = self._ok_modify()
        svc.delete_attachment(reminder, attachment)
        assert reminder.attachment_ids == []
        delete_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(delete_ops) == 2
        assert delete_ops[1].record.fields["Deleted"].value == 1

    def test_delete_attachment_rejects_mismatched_parent(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/REM-ATT",
            list_id="List/LIST-001",
            title="Attachment reminder",
            record_change_tag="ctag-rem",
            attachment_ids=["ATT-1"],
        )
        attachment = URLAttachment(
            id="Attachment/ATT-1",
            reminder_id="Reminder/OTHER-REMINDER",
            url="https://example.com",
            record_change_tag="ctag-att",
        )

        with pytest.raises(ValueError, match="Attachment child"):
            svc.delete_attachment(reminder, attachment)

        svc._raw.modify.assert_not_called()
        assert reminder.attachment_ids == ["ATT-1"]

    def test_create_url_attachment_normalizes_shorthand_reminder_ids(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                self._ack("Reminder/REM-ATT", "Reminder", "ctag-rem-new"),
                self._ack("Attachment/ATT-NEW", "Attachment", "ctag-att-new"),
            ],
            syncToken="mock-sync",
        )

        with patch("uuid.uuid4", return_value="ATT-NEW"):
            attachment = svc.create_url_attachment(
                reminder=Reminder(
                    id="REM-ATT",
                    list_id="List/LIST-001",
                    title="Attachment reminder",
                    record_change_tag="ctag-rem-old",
                    attachment_ids=[],
                ),
                url="https://example.com",
            )

        operations = svc._raw.modify.call_args.kwargs["operations"]
        assert operations[0].record.recordName == "Reminder/REM-ATT"
        assert (
            operations[1].record.fields["Reminder"].value.recordName
            == "Reminder/REM-ATT"
        )
        assert operations[1].record.parent.recordName == "Reminder/REM-ATT"
        assert attachment.reminder_id == "Reminder/REM-ATT"

    def test_update_attachment_rejects_noop(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        attachment = URLAttachment(
            id="Attachment/A-NOOP",
            reminder_id="Reminder/REM-ATT",
            url="https://example.com",
            record_change_tag="ctag-att",
        )

        with pytest.raises(ValueError, match="No attachment fields"):
            svc.update_attachment(attachment)

        svc._raw.modify.assert_not_called()

    def test_update_image_attachment_validates_before_modify(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        attachment = ImageAttachment(
            id="Attachment/A-IMG",
            reminder_id="Reminder/REM-ATT",
            file_asset_url="https://example.com/file.jpg",
            filename="file.jpg",
            file_size=10,
            width=100,
            height=50,
            record_change_tag="ctag-att",
        )

        with pytest.raises(ValidationError):
            svc.update_attachment(attachment, width=-1)

        svc._raw.modify.assert_not_called()

    def test_create_update_delete_recurrence_rule(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = self._ok_modify()

        reminder = Reminder(
            id="Reminder/REM-RR",
            list_id="List/LIST-001",
            title="Recurring reminder",
            record_change_tag="ctag-rem",
            recurrence_rule_ids=[],
        )

        rr = svc.create_recurrence_rule(
            reminder=reminder,
            frequency=RecurrenceFrequency.WEEKLY,
            interval=2,
            occurrence_count=0,
            first_day_of_week=1,
        )
        assert rr.id.startswith("RecurrenceRule/")
        assert rr.frequency == RecurrenceFrequency.WEEKLY
        assert len(reminder.recurrence_rule_ids) == 1
        create_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert create_ops[1].record.recordType == "RecurrenceRule"
        assert create_ops[1].record.fields["Frequency"].value == int(
            RecurrenceFrequency.WEEKLY
        )

        svc._raw.modify.reset_mock()
        svc._raw.modify.return_value = self._ok_modify()
        svc.update_recurrence_rule(rr, interval=3, occurrence_count=5)
        assert rr.interval == 3
        assert rr.occurrence_count == 5
        update_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(update_ops) == 1
        assert update_ops[0].record.fields["Interval"].value == 3
        assert update_ops[0].record.fields["OccurrenceCount"].value == 5

        svc._raw.modify.reset_mock()
        svc._raw.modify.return_value = self._ok_modify()
        svc.delete_recurrence_rule(reminder, rr)
        assert reminder.recurrence_rule_ids == []
        delete_ops = svc._raw.modify.call_args.kwargs["operations"]
        assert len(delete_ops) == 2
        assert delete_ops[1].record.fields["Deleted"].value == 1

    def test_delete_recurrence_rule_rejects_mismatched_parent(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/REM-RR",
            list_id="List/LIST-001",
            title="Recurring reminder",
            record_change_tag="ctag-rem",
            recurrence_rule_ids=["RR-1"],
        )
        recurrence_rule = RecurrenceRule(
            id="RecurrenceRule/RR-1",
            reminder_id="Reminder/OTHER-REMINDER",
            record_change_tag="ctag-rr",
        )

        with pytest.raises(ValueError, match="RecurrenceRule child"):
            svc.delete_recurrence_rule(reminder, recurrence_rule)

        svc._raw.modify.assert_not_called()
        assert reminder.recurrence_rule_ids == ["RR-1"]

    def test_create_recurrence_rule_validates_before_modify(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/REM-RR",
            list_id="List/LIST-001",
            title="Recurring reminder",
            record_change_tag="ctag-rem",
            recurrence_rule_ids=[],
        )

        with pytest.raises(ValidationError):
            svc.create_recurrence_rule(
                reminder=reminder,
                frequency=RecurrenceFrequency.WEEKLY,
                interval=0,
            )

        svc._raw.modify.assert_not_called()

    def test_update_recurrence_rule_rejects_noop(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        recurrence_rule = RecurrenceRule(
            id="RecurrenceRule/RR-NOOP",
            reminder_id="Reminder/REM-RR",
            record_change_tag="ctag-rr",
        )

        with pytest.raises(ValueError, match="No recurrence rule fields"):
            svc.update_recurrence_rule(recurrence_rule)

        svc._raw.modify.assert_not_called()

    def test_delete_marks_reminder_deleted_and_modified(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = self._ok_modify()

        reminder = Reminder(
            id="Reminder/REM-DEL",
            list_id="List/LIST-001",
            title="Delete me",
            record_change_tag="ctag-rem",
            deleted=False,
        )

        svc.delete(reminder)

        assert reminder.deleted is True
        assert reminder.modified is not None
        delete_ops = svc._raw.modify.call_args.kwargs["operations"]
        expected_modified = delete_ops[0].record.fields["LastModifiedDate"].value
        assert reminder.modified == expected_modified

    def test_update_persists_editable_reminder_fields(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[self._ack("Reminder/REM-UPD-ALL", "Reminder", "new-reminder-tag")],
            syncToken="mock-sync",
        )

        due_date = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
        completed_date = datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc)
        reminder = Reminder(
            id="Reminder/REM-UPD-ALL",
            list_id="List/LIST-001",
            title="Reminder",
            desc="Body",
            completed=True,
            completed_date=completed_date,
            due_date=due_date,
            priority=1,
            flagged=True,
            all_day=True,
            time_zone="Europe/Paris",
            parent_reminder_id="PARENT-001",
            record_change_tag="old-reminder-tag",
        )

        svc.update(reminder)

        update_op = svc._raw.modify.call_args.kwargs["operations"][0]
        fields = update_op.record.fields
        assert fields["Completed"].value == 1
        assert fields["CompletionDate"].value == completed_date
        assert fields["Priority"].value == 1
        assert fields["Flagged"].value == 1
        assert fields["AllDay"].value == 1
        assert fields["DueDate"].value == due_date
        assert fields["TimeZone"].value == "Europe/Paris"
        assert fields["ParentReminder"].value.recordName == "Reminder/PARENT-001"
        assert reminder.record_change_tag == "new-reminder-tag"
        assert reminder.modified == fields["LastModifiedDate"].value

    def test_update_normalizes_shorthand_reminder_ids(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[self._ack("Reminder/REM-UPD-SHORT", "Reminder", "new-rem-tag")],
            syncToken="mock-sync",
        )

        reminder = Reminder(
            id="REM-UPD-SHORT",
            list_id="List/LIST-001",
            title="Reminder",
            desc="Body",
            parent_reminder_id="PARENT-001",
            record_change_tag="old-rem-tag",
        )

        svc.update(reminder)

        update_op = svc._raw.modify.call_args.kwargs["operations"][0]
        assert update_op.record.recordName == "Reminder/REM-UPD-SHORT"
        assert (
            update_op.record.fields["ParentReminder"].value.recordName
            == "Reminder/PARENT-001"
        )

    def test_update_can_clear_optional_reminder_fields(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                self._ack("Reminder/REM-UPD-CLEAR", "Reminder", "new-reminder-tag")
            ],
            syncToken="mock-sync",
        )

        reminder = Reminder(
            id="Reminder/REM-UPD-CLEAR",
            list_id="List/LIST-001",
            title="Reminder",
            desc="Body",
            due_date=None,
            time_zone=None,
            parent_reminder_id=None,
            record_change_tag="old-reminder-tag",
        )

        svc.update(reminder)

        update_op = svc._raw.modify.call_args.kwargs["operations"][0]
        fields = update_op.record.fields
        assert fields["DueDate"].value is None
        assert fields["TimeZone"].value is None
        assert fields["ParentReminder"].value is None

        token_map = json.loads(fields["ResolutionTokenMap"].value)
        assert "dueDate" in token_map["map"]
        assert "timeZone" in token_map["map"]
        assert "parentReminder" in token_map["map"]

    def test_update_sets_completion_date_when_marked_completed_without_one(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                self._ack("Reminder/REM-UPD-COMPLETE", "Reminder", "new-reminder-tag")
            ],
            syncToken="mock-sync",
        )

        reminder = Reminder(
            id="Reminder/REM-UPD-COMPLETE",
            list_id="List/LIST-001",
            title="Reminder",
            completed=True,
            completed_date=None,
            record_change_tag="old-reminder-tag",
        )

        svc.update(reminder)

        update_op = svc._raw.modify.call_args.kwargs["operations"][0]
        completion_value = update_op.record.fields["CompletionDate"].value
        assert completion_value is not None
        assert reminder.completed_date == completion_value

    def test_update_clears_completion_date_when_marked_incomplete(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.modify.return_value = CKModifyResponse(
            records=[
                self._ack(
                    "Reminder/REM-UPD-INCOMPLETE",
                    "Reminder",
                    "new-reminder-tag",
                )
            ],
            syncToken="mock-sync",
        )

        reminder = Reminder(
            id="Reminder/REM-UPD-INCOMPLETE",
            list_id="List/LIST-001",
            title="Reminder",
            completed=False,
            completed_date=datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc),
            record_change_tag="old-reminder-tag",
        )

        svc.update(reminder)

        update_op = svc._raw.modify.call_args.kwargs["operations"][0]
        assert update_op.record.fields["CompletionDate"].value is None
        assert reminder.completed_date is None

    def test_create_hashtag_hydrates_record_change_tags(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        def _side_effect(**kwargs):
            reminder_name = kwargs["operations"][0].record.recordName
            hashtag_name = kwargs["operations"][1].record.recordName
            return CKModifyResponse(
                records=[
                    self._ack(reminder_name, "Reminder", "ctag-rem-new"),
                    self._ack(hashtag_name, "Hashtag", "ctag-hash-new"),
                ],
                syncToken="mock-sync",
            )

        svc._raw.modify.side_effect = _side_effect

        reminder = Reminder(
            id="Reminder/REM-TAG-CTAG",
            list_id="List/LIST-001",
            title="Hashtag reminder",
            record_change_tag="ctag-rem-old",
            hashtag_ids=[],
        )
        hashtag = svc.create_hashtag(reminder, "travel")

        assert reminder.record_change_tag == "ctag-rem-new"
        assert hashtag.record_change_tag == "ctag-hash-new"

    def test_create_url_attachment_hydrates_record_change_tags(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        def _side_effect(**kwargs):
            reminder_name = kwargs["operations"][0].record.recordName
            attachment_name = kwargs["operations"][1].record.recordName
            return CKModifyResponse(
                records=[
                    self._ack(reminder_name, "Reminder", "ctag-rem-new"),
                    self._ack(attachment_name, "Attachment", "ctag-att-new"),
                ],
                syncToken="mock-sync",
            )

        svc._raw.modify.side_effect = _side_effect

        reminder = Reminder(
            id="Reminder/REM-ATT-CTAG",
            list_id="List/LIST-001",
            title="Attachment reminder",
            record_change_tag="ctag-rem-old",
            attachment_ids=[],
        )
        attachment = svc.create_url_attachment(
            reminder=reminder, url="https://example.com"
        )

        assert reminder.record_change_tag == "ctag-rem-new"
        assert attachment.record_change_tag == "ctag-att-new"

    def test_create_recurrence_rule_hydrates_record_change_tags(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        def _side_effect(**kwargs):
            reminder_name = kwargs["operations"][0].record.recordName
            recurrence_name = kwargs["operations"][1].record.recordName
            return CKModifyResponse(
                records=[
                    self._ack(reminder_name, "Reminder", "ctag-rem-new"),
                    self._ack(recurrence_name, "RecurrenceRule", "ctag-rr-new"),
                ],
                syncToken="mock-sync",
            )

        svc._raw.modify.side_effect = _side_effect

        reminder = Reminder(
            id="Reminder/REM-RR-CTAG",
            list_id="List/LIST-001",
            title="Recurring reminder",
            record_change_tag="ctag-rem-old",
            recurrence_rule_ids=[],
        )
        rr = svc.create_recurrence_rule(reminder=reminder)

        assert reminder.record_change_tag == "ctag-rem-new"
        assert rr.record_change_tag == "ctag-rr-new"

    def test_update_methods_refresh_record_change_tag(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        def _side_effect(**kwargs):
            operation = kwargs["operations"][0]
            record_name = operation.record.recordName
            record_type = operation.record.recordType
            return CKModifyResponse(
                records=[
                    self._ack(
                        record_name, record_type, f"new-{record_type.lower()}-tag"
                    )
                ],
                syncToken="mock-sync",
            )

        svc._raw.modify.side_effect = _side_effect

        reminder = Reminder(
            id="Reminder/REM-UPD",
            list_id="List/LIST-001",
            title="Reminder",
            desc="Body",
            record_change_tag="old-reminder-tag",
        )
        svc.update(reminder)
        assert reminder.record_change_tag == "new-reminder-tag"

        hashtag = Hashtag(
            id="Hashtag/H-UPD",
            name="old",
            reminder_id=reminder.id,
            record_change_tag="old-hashtag-tag",
        )
        svc.update_hashtag(hashtag, "new")
        assert hashtag.record_change_tag == "new-hashtag-tag"

        attachment = URLAttachment(
            id="Attachment/A-UPD",
            reminder_id=reminder.id,
            url="https://example.com",
            record_change_tag="old-attachment-tag",
        )
        svc.update_attachment(attachment, url="https://example.org")
        assert attachment.record_change_tag == "new-attachment-tag"

        recurrence_rule = RecurrenceRule(
            id="RecurrenceRule/RR-UPD",
            reminder_id=reminder.id,
            record_change_tag="old-recurrencerule-tag",
        )
        svc.update_recurrence_rule(recurrence_rule, interval=2)
        assert recurrence_rule.record_change_tag == "new-recurrencerule-tag"


class TestReminderReadPaths:
    """Validate reminders() and list_reminders() query behavior."""

    LIST_A = "List/LIST-A"
    LIST_B = "List/LIST-B"

    @staticmethod
    def _reminder_record(reminder_id: str, list_id: str) -> CKRecord:
        return _ck_record(
            "Reminder",
            reminder_id,
            {
                "List": {
                    "type": "REFERENCE",
                    "value": {"recordName": list_id, "action": "VALIDATE"},
                },
                "Completed": {"type": "INT64", "value": 0},
                "Priority": {"type": "INT64", "value": 0},
                "Flagged": {"type": "INT64", "value": 0},
                "AllDay": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
            },
        )

    @staticmethod
    def _alarm_record(
        alarm_id: str,
        reminder_id: str,
        trigger_id: str,
    ) -> CKRecord:
        return _ck_record(
            "Alarm",
            alarm_id,
            {
                "AlarmUID": {"type": "STRING", "value": alarm_id.split("/", 1)[1]},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": reminder_id, "action": "VALIDATE"},
                },
                "TriggerID": {"type": "STRING", "value": trigger_id.split("/", 1)[1]},
            },
        )

    @staticmethod
    def _trigger_record(trigger_id: str, alarm_id: str) -> CKRecord:
        return _ck_record(
            "AlarmTrigger",
            trigger_id,
            {
                "Type": {"type": "STRING", "value": "Location"},
                "Alarm": {
                    "type": "REFERENCE",
                    "value": {"recordName": alarm_id, "action": "VALIDATE"},
                },
                "Title": {"type": "STRING", "value": "Test Trigger"},
                "Address": {"type": "STRING", "value": "Test Address"},
                "Latitude": {"type": "DOUBLE", "value": 48.0},
                "Longitude": {"type": "DOUBLE", "value": 2.0},
                "Radius": {"type": "DOUBLE", "value": 100.0},
                "Proximity": {"type": "INT64", "value": 1},
                "LocationUID": {"type": "STRING", "value": "LOC-1"},
            },
        )

    @staticmethod
    def _attachment_record(attachment_id: str, reminder_id: str) -> CKRecord:
        return _ck_record(
            "Attachment",
            attachment_id,
            {
                "Type": {"type": "STRING", "value": "URL"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": reminder_id, "action": "VALIDATE"},
                },
                "URL": {"type": "STRING", "value": "https://example.com"},
                "UTI": {"type": "STRING", "value": "public.url"},
            },
        )

    @staticmethod
    def _hashtag_record(hashtag_id: str, reminder_id: str) -> CKRecord:
        return _ck_record(
            "Hashtag",
            hashtag_id,
            {
                "Name": {"type": "STRING", "value": "tag"},
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": reminder_id, "action": "VALIDATE"},
                },
            },
        )

    @staticmethod
    def _recurrence_rule_record(recurrence_id: str, reminder_id: str) -> CKRecord:
        return _ck_record(
            "RecurrenceRule",
            recurrence_id,
            {
                "Reminder": {
                    "type": "REFERENCE",
                    "value": {"recordName": reminder_id, "action": "VALIDATE"},
                },
                "Frequency": {"type": "INT64", "value": 2},
                "Interval": {"type": "INT64", "value": 1},
                "OccurrenceCount": {"type": "INT64", "value": 0},
                "FirstDayOfTheWeek": {"type": "INT64", "value": 1},
            },
        )

    @staticmethod
    def _changes_response(
        records: list[CKRecord | CKErrorItem | CKTombstoneRecord],
        sync_token: str,
        more_coming: bool,
    ) -> CKZoneChangesResponse:
        return CKZoneChangesResponse(
            zones=[
                CKZoneChangesZone(
                    records=records,
                    moreComing=more_coming,
                    syncToken=sync_token,
                    zoneID=CKZoneID(
                        zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE"
                    ),
                )
            ]
        )

    def test_reminders_aggregates_from_list_reminders(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        rem_a = Reminder(id="Reminder/A", list_id=self.LIST_A, title="A")
        rem_b = Reminder(id="Reminder/B", list_id=self.LIST_B, title="B")

        svc.lists = MagicMock(
            return_value=[
                RemindersList(id=self.LIST_A, title="List A"),
                RemindersList(id=self.LIST_B, title="List B"),
            ]
        )
        svc.list_reminders = MagicMock(
            side_effect=[
                ListRemindersResult(
                    reminders=[rem_a],
                    alarms={},
                    triggers={},
                    attachments={},
                    hashtags={},
                    recurrence_rules={},
                ),
                ListRemindersResult(
                    reminders=[rem_b, rem_a],
                    alarms={},
                    triggers={},
                    attachments={},
                    hashtags={},
                    recurrence_rules={},
                ),  # duplicate across lists -> dedup
            ]
        )

        out = list(svc.reminders())
        assert [r.id for r in out] == ["Reminder/A", "Reminder/B"]
        assert svc.list_reminders.call_count == 2
        assert svc.list_reminders.call_args_list[0].kwargs == {
            "list_id": self.LIST_A,
            "include_completed": True,
            "results_limit": 200,
        }
        assert svc.list_reminders.call_args_list[1].kwargs == {
            "list_id": self.LIST_B,
            "include_completed": True,
            "results_limit": 200,
        }
        assert svc._raw.query.call_count == 0
        assert svc._raw.changes.call_count == 0

    def test_lists_stops_when_changes_returns_no_zones(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.return_value = CKZoneChangesResponse(zones=[])

        out = list(svc.lists())
        assert out == []
        assert svc._raw.changes.call_count == 1

    def test_lists_raises_on_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.return_value = self._changes_response(
            [
                CKErrorItem(
                    serverErrorCode="ACCESS_DENIED",
                    reason="Permission denied",
                    recordName="List/LIST-A",
                )
            ],
            sync_token="tok-1",
            more_coming=False,
        )

        with pytest.raises(RemindersApiError, match="List/LIST-A"):
            list(svc.lists())

    def test_reminders_applies_list_filter(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        rem_a = Reminder(id="Reminder/A", list_id=self.LIST_A, title="A")
        rem_b = Reminder(id="Reminder/B", list_id=self.LIST_A, title="B")

        svc.lists = MagicMock()
        svc.list_reminders = MagicMock(
            return_value=ListRemindersResult(
                reminders=[rem_a, rem_b],
                alarms={},
                triggers={},
                attachments={},
                hashtags={},
                recurrence_rules={},
            )
        )

        out = list(svc.reminders(list_id=self.LIST_A))
        assert [r.id for r in out] == ["Reminder/A", "Reminder/B"]
        svc.list_reminders.assert_called_once_with(
            list_id=self.LIST_A,
            include_completed=True,
            results_limit=200,
        )
        assert svc.lists.call_count == 0
        assert svc._raw.query.call_count == 0
        assert svc._raw.changes.call_count == 0

    def test_list_reminders_enforces_list_scope_for_related_records(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        rem_a = self._reminder_record("Reminder/A", self.LIST_A)
        rem_b = self._reminder_record("Reminder/B", self.LIST_B)

        alarm_a = self._alarm_record(
            "Alarm/ALARM-A",
            "Reminder/A",
            "AlarmTrigger/TRIG-A",
        )
        alarm_b = self._alarm_record(
            "Alarm/ALARM-B",
            "Reminder/B",
            "AlarmTrigger/TRIG-B",
        )

        trig_a = self._trigger_record("AlarmTrigger/TRIG-A", "Alarm/ALARM-A")
        trig_b = self._trigger_record("AlarmTrigger/TRIG-B", "Alarm/ALARM-B")

        att_a = self._attachment_record("Attachment/ATT-A", "Reminder/A")
        att_b = self._attachment_record("Attachment/ATT-B", "Reminder/B")

        tag_a = self._hashtag_record("Hashtag/TAG-A", "Reminder/A")
        tag_b = self._hashtag_record("Hashtag/TAG-B", "Reminder/B")
        rr_a = self._recurrence_rule_record("RecurrenceRule/RR-A", "Reminder/A")
        rr_b = self._recurrence_rule_record("RecurrenceRule/RR-B", "Reminder/B")

        svc._raw.query.return_value = CKQueryResponse(
            records=[
                rem_a,
                rem_b,
                alarm_a,
                alarm_b,
                trig_a,
                trig_b,
                att_a,
                att_b,
                tag_a,
                tag_b,
                rr_a,
                rr_b,
            ],
            continuationMarker=None,
        )

        result = svc.list_reminders(list_id=self.LIST_A, include_completed=True)

        assert isinstance(result, ListRemindersResult)
        assert [r.id for r in result.reminders] == ["Reminder/A"]
        assert set(result.alarms.keys()) == {"Alarm/ALARM-A"}
        assert set(result.triggers.keys()) == {"AlarmTrigger/TRIG-A"}
        assert set(result.attachments.keys()) == {"Attachment/ATT-A"}
        assert set(result.hashtags.keys()) == {"Hashtag/TAG-A"}
        assert set(result.recurrence_rules.keys()) == {"RecurrenceRule/RR-A"}

    def test_list_reminders_paginates_query_results(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        rem_a = self._reminder_record("Reminder/A", self.LIST_A)
        rem_b = self._reminder_record("Reminder/B", self.LIST_A)
        svc._raw.query.side_effect = [
            CKQueryResponse(records=[rem_a], continuationMarker="page-2"),
            CKQueryResponse(records=[rem_b], continuationMarker=None),
        ]

        result = svc.list_reminders(
            list_id=self.LIST_A,
            include_completed=True,
            results_limit=1,
        )

        assert isinstance(result, ListRemindersResult)
        assert {r.id for r in result.reminders} == {"Reminder/A", "Reminder/B"}
        assert svc._raw.query.call_count == 2
        first_call = svc._raw.query.call_args_list[0].kwargs
        second_call = svc._raw.query.call_args_list[1].kwargs
        assert first_call["continuation"] is None
        assert second_call["continuation"] == "page-2"

    def test_list_reminders_raises_on_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.query.return_value = CKQueryResponse(
            records=[
                CKErrorItem(
                    serverErrorCode="REQUEST_FAILED",
                    reason="Backend timeout",
                    recordName="Reminder/FAIL",
                )
            ],
            continuationMarker=None,
        )

        with pytest.raises(RemindersApiError, match="Reminder/FAIL"):
            svc.list_reminders(list_id=self.LIST_A, include_completed=True)

    def test_get_raises_lookup_error_when_missing(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.return_value = MagicMock(records=[])

        with pytest.raises(LookupError, match="Reminder not found"):
            svc.get("Reminder/MISSING")

    def test_get_normalizes_unprefixed_reminder_id(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.return_value = MagicMock(
            records=[self._reminder_record("Reminder/NORMALIZED", self.LIST_A)]
        )

        reminder = svc.get("NORMALIZED")

        assert reminder.id == "Reminder/NORMALIZED"
        assert svc._raw.lookup.call_args.kwargs["record_names"] == [
            "Reminder/NORMALIZED"
        ]

    def test_get_raises_on_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.return_value = MagicMock(
            records=[
                CKErrorItem(
                    serverErrorCode="ACCESS_DENIED",
                    reason="Permission denied",
                    recordName="Reminder/FAIL",
                )
            ]
        )

        with pytest.raises(RemindersApiError, match="Reminder/FAIL"):
            svc.get("Reminder/FAIL")

    @pytest.mark.parametrize(
        (
            "method_name",
            "id_field",
            "raw_id",
            "record_name",
            "record_factory_name",
            "expected_attr",
            "expected_value",
        ),
        [
            (
                "tags_for",
                "hashtag_ids",
                "TAG-1",
                "Hashtag/TAG-1",
                "_hashtag_record",
                "name",
                "tag",
            ),
            (
                "attachments_for",
                "attachment_ids",
                "ATT-1",
                "Attachment/ATT-1",
                "_attachment_record",
                "url",
                "https://example.com",
            ),
            (
                "recurrence_rules_for",
                "recurrence_rule_ids",
                "RR-1",
                "RecurrenceRule/RR-1",
                "_recurrence_rule_record",
                "frequency",
                RecurrenceFrequency.WEEKLY,
            ),
        ],
    )
    def test_lookup_helpers_use_lookup_ids_and_map_records(
        self,
        method_name,
        id_field,
        raw_id,
        record_name,
        record_factory_name,
        expected_attr,
        expected_value,
    ):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            **{id_field: [raw_id]},
        )
        record_factory = getattr(self, record_factory_name)
        svc._raw.lookup.return_value = MagicMock(
            records=[record_factory(record_name, "Reminder/A")]
        )

        out = getattr(svc, method_name)(reminder)
        assert len(out) == 1
        assert out[0].id == record_name
        assert out[0].reminder_id == "Reminder/A"
        assert getattr(out[0], expected_attr) == expected_value
        assert svc._raw.lookup.call_args.kwargs["record_names"] == [record_name]

    @pytest.mark.parametrize(
        ("method_name", "id_field", "raw_id", "record_name"),
        [
            ("tags_for", "hashtag_ids", "TAG-1", "Hashtag/TAG-1"),
            ("attachments_for", "attachment_ids", "ATT-1", "Attachment/ATT-1"),
            (
                "recurrence_rules_for",
                "recurrence_rule_ids",
                "RR-1",
                "RecurrenceRule/RR-1",
            ),
        ],
    )
    def test_lookup_helpers_raise_on_error_item(
        self, method_name, id_field, raw_id, record_name
    ):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.return_value = MagicMock(
            records=[
                CKErrorItem(
                    serverErrorCode="REQUEST_FAILED",
                    reason="Backend timeout",
                    recordName=record_name,
                )
            ]
        )

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            **{id_field: [raw_id]},
        )

        method = getattr(svc, method_name)
        with pytest.raises(RemindersApiError, match=record_name):
            method(reminder)

    def test_alarms_for_returns_typed_rows(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.side_effect = [
            MagicMock(
                records=[
                    self._alarm_record(
                        "Alarm/AL-1",
                        "Reminder/A",
                        "AlarmTrigger/TRIG-1",
                    )
                ]
            ),
            MagicMock(
                records=[
                    self._trigger_record(
                        "AlarmTrigger/TRIG-1",
                        "Alarm/AL-1",
                    )
                ]
            ),
        ]

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            alarm_ids=["AL-1"],
        )

        out = svc.alarms_for(reminder)

        assert len(out) == 1
        assert isinstance(out[0], AlarmWithTrigger)
        assert out[0].alarm.id == "Alarm/AL-1"
        assert out[0].trigger is not None
        assert out[0].alarm.id == "Alarm/AL-1"
        assert out[0].trigger.id == "AlarmTrigger/TRIG-1"

    def test_alarms_for_normalizes_prefixed_trigger_ids(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.side_effect = [
            MagicMock(
                records=[
                    self._alarm_record(
                        "Alarm/AL-1",
                        "Reminder/A",
                        "AlarmTrigger/TRIG-1",
                    )
                ]
            ),
            MagicMock(
                records=[
                    self._trigger_record(
                        "AlarmTrigger/TRIG-1",
                        "Alarm/AL-1",
                    )
                ]
            ),
        ]

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            alarm_ids=["AL-1"],
        )

        out = svc.alarms_for(reminder)

        assert len(out) == 1
        assert out[0].trigger is not None
        assert out[0].trigger.id == "AlarmTrigger/TRIG-1"
        assert svc._raw.lookup.call_args_list[1].kwargs["record_names"] == [
            "AlarmTrigger/TRIG-1"
        ]

    def test_alarms_for_raises_on_alarm_lookup_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.return_value = MagicMock(
            records=[
                CKErrorItem(
                    serverErrorCode="REQUEST_FAILED",
                    reason="Backend timeout",
                    recordName="Alarm/AL-1",
                )
            ]
        )

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            alarm_ids=["AL-1"],
        )

        with pytest.raises(RemindersApiError, match="Alarm/AL-1"):
            svc.alarms_for(reminder)

    def test_alarms_for_raises_on_trigger_lookup_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.lookup.side_effect = [
            MagicMock(
                records=[
                    self._alarm_record(
                        "Alarm/AL-1",
                        "Reminder/A",
                        "AlarmTrigger/TRIG-1",
                    )
                ]
            ),
            MagicMock(
                records=[
                    CKErrorItem(
                        serverErrorCode="REQUEST_FAILED",
                        reason="Backend timeout",
                        recordName="AlarmTrigger/TRIG-1",
                    )
                ]
            ),
        ]

        reminder = Reminder(
            id="Reminder/A",
            list_id=self.LIST_A,
            title="A",
            alarm_ids=["AL-1"],
        )

        with pytest.raises(RemindersApiError, match="AlarmTrigger/TRIG-1"):
            svc.alarms_for(reminder)


class TestReminderDeltaSync:
    """Validate explicit reminder delta-sync APIs."""

    LIST_A = "List/LIST-A"

    @staticmethod
    def _reminder_record(reminder_id: str, *, deleted: bool = False) -> CKRecord:
        return _ck_record(
            "Reminder",
            reminder_id,
            {
                "List": {
                    "type": "REFERENCE",
                    "value": {
                        "recordName": TestReminderDeltaSync.LIST_A,
                        "action": "VALIDATE",
                    },
                },
                "Completed": {"type": "INT64", "value": 0},
                "Priority": {"type": "INT64", "value": 0},
                "Flagged": {"type": "INT64", "value": 0},
                "AllDay": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 1 if deleted else 0},
            },
        )

    @staticmethod
    def _changes_response(
        records: list[CKRecord | CKTombstoneRecord | CKErrorItem],
        sync_token: str,
        more_coming: bool,
    ) -> CKZoneChangesResponse:
        return CKZoneChangesResponse(
            zones=[
                CKZoneChangesZone(
                    records=records,
                    moreComing=more_coming,
                    syncToken=sync_token,
                    zoneID=CKZoneID(
                        zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE"
                    ),
                )
            ]
        )

    def test_sync_cursor_returns_final_paged_token(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.side_effect = [
            self._changes_response([], sync_token="tok-1", more_coming=True),
            self._changes_response([], sync_token="tok-2", more_coming=False),
        ]

        assert svc.sync_cursor() == "tok-2"
        assert svc._raw.changes.call_count == 2
        first_zone_req = svc._raw.changes.call_args_list[0].kwargs["zone_req"]
        second_zone_req = svc._raw.changes.call_args_list[1].kwargs["zone_req"]
        assert first_zone_req.syncToken is None
        assert second_zone_req.syncToken == "tok-1"
        assert first_zone_req.desiredRecordTypes == []
        assert first_zone_req.desiredKeys == []

    def test_iter_changes_emits_updated_deleted_and_tombstone_events(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.return_value = self._changes_response(
            [
                self._reminder_record("Reminder/UPD"),
                self._reminder_record("Reminder/SOFT-DEL", deleted=True),
                CKTombstoneRecord(recordName="Reminder/TOMBSTONE", deleted=True),
            ],
            sync_token="tok-1",
            more_coming=False,
        )

        out = list(svc.iter_changes(since="tok-0"))
        assert out == [
            ReminderChangeEvent(
                type="updated",
                reminder_id="Reminder/UPD",
                reminder=Reminder(
                    id="Reminder/UPD",
                    list_id=self.LIST_A,
                    title="Untitled",
                    completed=False,
                    priority=0,
                    flagged=False,
                    all_day=False,
                    deleted=False,
                ),
            ),
            ReminderChangeEvent(
                type="deleted",
                reminder_id="Reminder/SOFT-DEL",
                reminder=Reminder(
                    id="Reminder/SOFT-DEL",
                    list_id=self.LIST_A,
                    title="Untitled",
                    completed=False,
                    priority=0,
                    flagged=False,
                    all_day=False,
                    deleted=True,
                ),
            ),
            ReminderChangeEvent(
                type="deleted",
                reminder_id="Reminder/TOMBSTONE",
                reminder=None,
            ),
        ]
        zone_req = svc._raw.changes.call_args.kwargs["zone_req"]
        assert zone_req.syncToken == "tok-0"
        assert zone_req.desiredRecordTypes == ["Reminder"]

    def test_iter_changes_paginates(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.side_effect = [
            self._changes_response(
                [self._reminder_record("Reminder/A")],
                sync_token="tok-1",
                more_coming=True,
            ),
            self._changes_response(
                [self._reminder_record("Reminder/B")],
                sync_token="tok-2",
                more_coming=False,
            ),
        ]

        out = list(svc.iter_changes(since="tok-0"))
        assert [event.reminder_id for event in out] == ["Reminder/A", "Reminder/B"]
        assert svc._raw.changes.call_count == 2
        first_zone_req = svc._raw.changes.call_args_list[0].kwargs["zone_req"]
        second_zone_req = svc._raw.changes.call_args_list[1].kwargs["zone_req"]
        assert first_zone_req.syncToken == "tok-0"
        assert second_zone_req.syncToken == "tok-1"

    def test_iter_changes_raises_on_error_item(self):
        svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
        svc._raw = MagicMock()
        svc._raw.changes.return_value = self._changes_response(
            [
                CKErrorItem(
                    serverErrorCode="ACCESS_DENIED",
                    reason="Token expired",
                    recordName="Reminder/FAIL",
                )
            ],
            sync_token="tok-1",
            more_coming=False,
        )

        with pytest.raises(RemindersApiError, match="Reminder/FAIL"):
            list(svc.iter_changes(since="tok-0"))


class TestCloudKitQueryResponseRobustness:
    """Validate query parsing against malformed field values seen in real data."""

    def test_query_response_tolerates_out_of_range_due_date_timestamp(self):
        # Captured variant: DueDate TIMESTAMP can be out-of-range (e.g. year 12177).
        # Parsing should coerce that field to None, not fail the entire response page.
        response = CKQueryResponse.model_validate(
            {
                "records": [
                    {
                        "recordName": "Reminder/GOOD-1",
                        "recordType": "Reminder",
                        "fields": {
                            "List": {
                                "type": "REFERENCE",
                                "value": {
                                    "recordName": "List/LIST-A",
                                    "action": "VALIDATE",
                                },
                            },
                            "Completed": {"type": "INT64", "value": 0},
                            "Priority": {"type": "INT64", "value": 0},
                            "Flagged": {"type": "INT64", "value": 0},
                            "AllDay": {"type": "INT64", "value": 0},
                            "Deleted": {"type": "INT64", "value": 0},
                            "DueDate": {"type": "TIMESTAMP", "value": 1735488000000},
                        },
                    },
                    {
                        "recordName": "Reminder/BAD-DUE-DATE",
                        "recordType": "Reminder",
                        "fields": {
                            "List": {
                                "type": "REFERENCE",
                                "value": {
                                    "recordName": "List/LIST-A",
                                    "action": "VALIDATE",
                                },
                            },
                            "Completed": {"type": "INT64", "value": 0},
                            "Priority": {"type": "INT64", "value": 0},
                            "Flagged": {"type": "INT64", "value": 0},
                            "AllDay": {"type": "INT64", "value": 0},
                            "Deleted": {"type": "INT64", "value": 0},
                            "DueDate": {"type": "TIMESTAMP", "value": 322123125600000},
                        },
                    },
                ],
            }
        )

        assert len(response.records) == 2
        good = response.records[0]
        bad = response.records[1]
        assert isinstance(good, CKRecord)
        assert isinstance(bad, CKRecord)
        assert good.fields.get_value("DueDate") is not None
        assert bad.fields.get_value("DueDate") is None

    def test_query_response_parses_asset_backed_list_field(self):
        payload = base64.b64encode(b'["REM-1","REM-2"]').decode("ascii")
        response = CKQueryResponse.model_validate(
            {
                "records": [
                    {
                        "recordName": "List/LIST-A",
                        "recordType": "List",
                        "fields": {
                            "ReminderIDsAsset": {
                                "type": "ASSET",
                                "value": {"downloadedData": payload},
                            }
                        },
                    }
                ]
            }
        )

        rec = response.records[0]
        assert isinstance(rec, CKRecord)
        asset = rec.fields.get_value("ReminderIDsAsset")
        assert asset is not None
        assert asset.downloadedData == b'["REM-1","REM-2"]'
