"""Coverage-focused unit tests for the Reminders service internals.

Targets previously-uncovered branches in:

- ``pyicloud/services/reminders/client.py``
- ``pyicloud/services/reminders/_writes.py``
- ``pyicloud/services/reminders/_reads.py``
- ``pyicloud/services/reminders/_protocol.py``

All CloudKit traffic is mocked with ``unittest.mock``; nothing here touches
the network or the filesystem.
"""

# pylint: disable=protected-access

import base64
from datetime import datetime, timezone
import logging
import plistlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import (
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKRecord,
    CKTombstoneRecord,
    CKZoneChangesResponse,
    CKZoneChangesZone,
    CKZoneID,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitRateLimited,
)
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._protocol import (
    _archive_object,
    _as_raw_id,
    _as_record_name,
    _decode_attachment_url,
    _decode_cloudkit_text_value,
    _decode_crdt_document,
    _decode_nsdatecomponents,
    _looks_like_url,
)
from pyicloud.services.reminders._reads import RemindersReadAPI
from pyicloud.services.reminders._writes import RemindersWriteAPI
from pyicloud.services.reminders.client import (
    CloudKitRemindersClient,
    RemindersApiError,
    RemindersAuthError,
)
from pyicloud.services.reminders.models import (
    DateComponents,
    Hashtag,
    ImageAttachment,
    ListRemindersResult,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    RemindersList,
    URLAttachment,
)
from pyicloud.services.reminders.protobuf import reminders_pb2

LOGGER = logging.getLogger(__name__)

_REMINDERS_ZONE_ID = CKZoneID(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE")
_REMINDERS_ZONE_REQ = CKZoneIDReq(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE")


def _ck_record(
    record_type: str,
    record_name: str,
    fields: dict[str, Any] | None = None,
    **extra: Any,
) -> CKRecord:
    """Build a CKRecord from a raw dict, mirroring CloudKit JSON on the wire."""
    return CKRecord.model_validate({
        "recordName": record_name,
        "recordType": record_type,
        "fields": fields or {},
        **extra,
    })


def _minimal_reminder_record(reminder_id: str, list_id: str) -> CKRecord:
    """Build a minimal Reminder CKRecord the mapper can parse."""
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


def _read_api() -> tuple[RemindersReadAPI, MagicMock]:
    """Return a read API backed by a fully-mocked raw CloudKit client."""
    raw = MagicMock()
    mapper = RemindersRecordMapper(lambda: raw, LOGGER)
    return RemindersReadAPI(lambda: raw, mapper, LOGGER), raw


def _write_api() -> tuple[RemindersWriteAPI, MagicMock]:
    """Return a write API backed by a fully-mocked raw CloudKit client."""
    raw = MagicMock()
    mapper = MagicMock()
    return RemindersWriteAPI(lambda: raw, mapper, LOGGER), raw


# ---------------------------------------------------------------------------
# cloudkit.py — CloudKitRemindersClient error routing
# ---------------------------------------------------------------------------


class TestCloudKitRemindersClientErrors:
    """Client methods translate CloudKit errors into Reminders errors."""

    @staticmethod
    def _client() -> tuple[CloudKitRemindersClient, MagicMock]:
        """Return a client with its raw CloudKit client fully mocked."""
        client = CloudKitRemindersClient(
            "https://ckdatabasews.icloud.com",
            MagicMock(),
            {},
        )
        inner = MagicMock()
        client._client = cast(Any, inner)
        return client, inner

    def test_lookup_auth_error_becomes_reminders_auth_error(self) -> None:
        """A CloudKitAuthError from lookup surfaces as a RemindersAuthError."""
        client, inner = self._client()
        inner.lookup.side_effect = CloudKitAuthError("auth failed")

        with pytest.raises(RemindersAuthError, match="auth failed"):
            client.lookup(["Reminder/1"], _REMINDERS_ZONE_REQ)

    def test_lookup_rate_limited_carries_retry_after(self) -> None:
        """A rate-limit error from lookup is wrapped with its retry_after."""
        client, inner = self._client()
        inner.lookup.side_effect = CloudKitRateLimited(
            "slow down",
            retry_after=7.5,
        )

        with pytest.raises(RemindersApiError, match="slow down") as excinfo:
            client.lookup(["Reminder/1"], _REMINDERS_ZONE_REQ)

        assert excinfo.value.payload == {"retry_after": 7.5}

    def test_unrelated_exception_passes_through(self) -> None:
        """Errors outside the CloudKit hierarchy are re-raised unchanged."""
        error = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            CloudKitRemindersClient._raise_reminders_error(error)

    def test_query_success_and_api_error(self) -> None:
        """query() returns the response and wraps CloudKit API failures."""
        client, inner = self._client()
        inner.query.return_value = CKQueryResponse(
            records=[],
            continuationMarker=None,
        )
        query = CKQueryObject(recordType="Reminder")

        result = client.query(query=query, zone_id=_REMINDERS_ZONE_REQ)
        assert result is not None

        inner.query.side_effect = CloudKitApiError(
            "query failed",
            payload={"code": 9},
        )
        with pytest.raises(RemindersApiError, match="query failed") as excinfo:
            client.query(query=query, zone_id=_REMINDERS_ZONE_REQ)
        assert excinfo.value.payload == {"code": 9}

    def test_changes_success_and_api_error(self) -> None:
        """changes() returns the response and wraps CloudKit API failures."""
        client, inner = self._client()
        inner.changes.return_value = CKZoneChangesResponse(zones=[])
        zone_req = MagicMock()

        result = client.changes(zone_req=zone_req)
        assert result is not None

        inner.changes.side_effect = CloudKitApiError("changes failed")
        with pytest.raises(RemindersApiError, match="changes failed"):
            client.changes(zone_req=zone_req)

    def test_modify_success_and_api_error(self) -> None:
        """modify() returns the response and wraps CloudKit API failures."""
        client, inner = self._client()
        inner.modify.return_value = CKModifyResponse(
            records=[],
            syncToken="tok",
        )

        result = client.modify(
            operations=[],
            zone_id=_REMINDERS_ZONE_REQ,
        )
        assert result is not None

        inner.modify.side_effect = CloudKitApiError("modify failed")
        with pytest.raises(RemindersApiError, match="modify failed"):
            client.modify(operations=[], zone_id=_REMINDERS_ZONE_REQ)

    def test_download_asset_bytes_wraps_api_error(self) -> None:
        """download_asset_bytes() wraps CloudKit API failures."""
        client, inner = self._client()
        inner.download_asset_bytes.side_effect = CloudKitApiError("download failed")

        with pytest.raises(RemindersApiError, match="download failed"):
            client.download_asset_bytes("https://example.test/asset")

    def test_current_sync_token_suppresses_api_errors(self) -> None:
        """current_sync_token() swallows CloudKit API/rate-limit failures."""
        client, inner = self._client()
        inner.query_sync_token.side_effect = CloudKitApiError("no token")

        assert client.current_sync_token(zone_id=_REMINDERS_ZONE_REQ) is None

    def test_current_sync_token_propagates_auth_errors(self) -> None:
        """current_sync_token() still surfaces CloudKit auth failures."""
        client, inner = self._client()
        inner.query_sync_token.side_effect = CloudKitAuthError("auth failed")

        with pytest.raises(RemindersAuthError, match="auth failed"):
            client.current_sync_token(zone_id=_REMINDERS_ZONE_REQ)


# ---------------------------------------------------------------------------
# _writes.py — write-path branch coverage
# ---------------------------------------------------------------------------


class TestRemindersWritesBranches:
    """Exercise previously-uncovered branches in the write API."""

    def test_update_treats_naive_dates_as_utc(self) -> None:
        """Updating with naive completed/due dates treats them as UTC."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        reminder = Reminder(
            id="Reminder/NAIVE",
            list_id="List/A",
            title="T",
            completed=True,
            completed_date=datetime(2026, 1, 2, 12, 0),
            due_date=datetime(2026, 1, 1, 10, 0),
            record_change_tag="ctag",
        )

        api.update(reminder)

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["CompletionDate"].value == datetime(
            2026, 1, 2, 12, 0, tzinfo=timezone.utc
        )
        assert fields["DueDate"].value == datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        )
        assert reminder.due_date == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    def test_lookup_created_reminder_matches_record(self) -> None:
        """A matching record from the post-create lookup is mapped."""
        api, raw = _write_api()
        rec = _ck_record("Reminder", "Reminder/A", {})
        recorder = cast(Any, api._mapper)
        raw.lookup.return_value = MagicMock(records=[rec])

        result = api._lookup_created_reminder("Reminder/A")

        assert result is recorder.record_to_reminder.return_value
        recorder.record_to_reminder.assert_called_once_with(rec)

    @pytest.mark.parametrize(
        "records",
        [
            [],
            [_ck_record("Reminder", "Reminder/OTHER", {})],
        ],
    )
    def test_lookup_created_reminder_raises_when_missing(
        self, records: list[CKRecord]
    ) -> None:
        """A post-create lookup without the expected record raises LookupError."""
        api, raw = _write_api()
        raw.lookup.return_value = MagicMock(records=records)

        with pytest.raises(LookupError, match="Reminder not found"):
            api._lookup_created_reminder("Reminder/A")

    def test_create_with_naive_due_date_and_time_zone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create() normalizes a naive due date and persists the time zone."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        created = Reminder(id="Reminder/NEW", list_id="List/A", title="T")
        monkeypatch.setattr(
            api,
            "_lookup_created_reminder",
            MagicMock(return_value=created),
        )

        result = api.create(
            list_id="List/A",
            title="T",
            due_date=datetime(2026, 1, 1, 10, 0),
            time_zone="Europe/Paris",
        )

        assert result is created
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["DueDate"].value == datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        )
        assert fields["TimeZone"].value == "Europe/Paris"

    def test_create_with_aware_due_date_keeps_tz(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create() keeps an already-timezone-aware due date intact."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        monkeypatch.setattr(
            api,
            "_lookup_created_reminder",
            MagicMock(
                return_value=Reminder(id="Reminder/NEW", list_id="List/A", title="T")
            ),
        )

        api.create(
            list_id="List/A",
            title="T",
            due_date=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        )

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["DueDate"].value == datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        )

    def test_update_hashtag_without_reminder_link(self) -> None:
        """Updating a hashtag without a reminder link omits the reference."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        hashtag = Hashtag(
            id="Hashtag/H-NO-PARENT",
            name="old",
            reminder_id="",
            record_change_tag="ctag",
        )

        api.update_hashtag(hashtag, "chores")

        assert hashtag.name == "chores"
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert "Reminder" not in fields

    def test_update_url_attachment_with_uti_only(self) -> None:
        """Updating a URL attachment's UTI only changes that field."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        attachment = URLAttachment(
            id="Attachment/A-UTI",
            reminder_id="Reminder/R",
            url="https://old.example",
            uti="public.url",
            record_change_tag="ctag",
        )

        api.update_attachment(attachment, uti="public.plain-text")

        assert attachment.url == "https://old.example"
        assert attachment.uti == "public.plain-text"
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert "URL" not in fields
        assert fields["UTI"].value == "public.plain-text"

    def test_update_image_attachment_noop_raises(self) -> None:
        """Updating an image attachment with no fields raises ValueError."""
        api, raw = _write_api()
        attachment = ImageAttachment(
            id="Attachment/A-NOOP",
            reminder_id="Reminder/R",
        )

        with pytest.raises(ValueError, match="No attachment fields"):
            api.update_attachment(attachment)

        raw.modify.assert_not_called()

    def test_update_image_attachment_partial_fields(self) -> None:
        """Updating only the image filename leaves the other fields unset."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        attachment = ImageAttachment(
            id="Attachment/A-PARTIAL",
            reminder_id="Reminder/R",
            filename="old.jpg",
            record_change_tag="ctag",
        )

        api.update_attachment(attachment, filename="new.jpg")

        assert attachment.filename == "new.jpg"
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["FileName"].value == "new.jpg"
        assert "UTI" not in fields
        assert "FileSize" not in fields
        assert "Width" not in fields
        assert "Height" not in fields

    def test_update_image_attachment_only_width(self) -> None:
        """Updating only the width leaves the other metadata fields unset."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        attachment = ImageAttachment(
            id="Attachment/A-WIDTH",
            reminder_id="Reminder/R",
            record_change_tag="ctag",
        )

        api.update_attachment(attachment, width=640)

        assert attachment.width == 640
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["Width"].value == 640
        assert "UTI" not in fields
        assert "FileName" not in fields
        assert "FileSize" not in fields
        assert "Height" not in fields

    def test_update_image_attachment_full_metadata(self) -> None:
        """Updating every image metadata field writes them all."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        attachment = ImageAttachment(
            id="Attachment/A-IMG",
            reminder_id="",
            filename="old.jpg",
            file_size=1,
            width=2,
            height=3,
            uti="public.jpeg",
            record_change_tag="ctag",
        )

        api.update_attachment(
            attachment,
            uti="public.png",
            filename="new.png",
            file_size=100,
            width=200,
            height=300,
        )

        assert attachment.filename == "new.png"
        assert attachment.file_size == 100
        assert attachment.width == 200
        assert attachment.height == 300
        assert attachment.uti == "public.png"
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["UTI"].value == "public.png"
        assert fields["FileName"].value == "new.png"
        assert fields["FileSize"].value == 100
        assert fields["Width"].value == 200
        assert fields["Height"].value == 300
        assert fields["Type"].value == "Image"
        assert "Reminder" not in fields

    def test_delete_linked_child_without_reminder_link(self) -> None:
        """Deleting a child without a reminder link omits the reference."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        reminder = Reminder(
            id="Reminder/R",
            list_id="List/A",
            title="T",
            hashtag_ids=["H-1"],
        )
        child = SimpleNamespace(id="Hashtag/H-1", record_change_tag=None)

        api._delete_linked_child(
            reminder=reminder,
            reminder_ids_attr="hashtag_ids",
            child=child,
            prefix="Hashtag",
            record_type="Hashtag",
            field_name="HashtagIDs",
            token_field_name="hashtagIDs",
            operation_name="Delete hashtag",
        )

        assert reminder.hashtag_ids == []
        fields = raw.modify.call_args.kwargs["operations"][1].record.fields
        assert "Reminder" not in fields

    def test_update_recurrence_rule_partial_fields(self) -> None:
        """Updating a recurrence rule writes only the provided fields."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        rule = RecurrenceRule(
            id="RecurrenceRule/RR-X",
            reminder_id="",
            record_change_tag="ctag",
        )

        api.update_recurrence_rule(
            rule,
            frequency=RecurrenceFrequency.DAILY,
            first_day_of_week=1,
        )

        assert rule.frequency == RecurrenceFrequency.DAILY
        assert rule.first_day_of_week == 1
        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["Frequency"].value == 1
        assert fields["FirstDayOfTheWeek"].value == 1
        assert "Interval" not in fields
        assert "OccurrenceCount" not in fields
        assert "Reminder" not in fields


# ---------------------------------------------------------------------------
# _reads.py — read-path branch coverage
# ---------------------------------------------------------------------------


class TestRemindersReadsBranches:
    """Exercise previously-uncovered branches in the read API."""

    def test_lists_skips_non_list_records(self) -> None:
        """lists() skips non-List records and tombstones in a zone page."""
        api, raw = _read_api()
        zone = CKZoneChangesZone(
            records=[
                _ck_record(
                    "List", "List/A", {"Name": {"type": "STRING", "value": "A"}}
                ),
                _minimal_reminder_record("Reminder/1", "List/A"),
                CKTombstoneRecord(recordName="Reminder/2", deleted=True),
            ],
            moreComing=False,
            syncToken="tok",
            zoneID=_REMINDERS_ZONE_ID,
        )
        raw.changes.return_value = CKZoneChangesResponse(zones=[zone])

        out = list(api.lists())

        assert [lst.id for lst in out] == ["List/A"]

    def test_reminders_with_list_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """reminders() calls list_reminders once when a list filter is given."""
        api, _ = _read_api()
        reminder = Reminder(id="Reminder/A", list_id="List/A", title="A")
        batch = ListRemindersResult(
            reminders=[reminder],
            alarms={},
            triggers={},
            attachments={},
            hashtags={},
            recurrence_rules={},
        )
        list_reminders = MagicMock(return_value=batch)
        monkeypatch.setattr(api, "list_reminders", list_reminders)

        out = list(api.reminders(list_id="List/A"))

        assert out == [reminder]
        list_reminders.assert_called_once_with(
            list_id="List/A",
            include_completed=True,
            results_limit=200,
        )

    def test_reminders_without_list_filter_uses_lists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reminders() falls back to enumerating lists when no filter is given."""
        api, _ = _read_api()
        reminder = Reminder(id="Reminder/B", list_id="List/B", title="B")
        batch = ListRemindersResult(
            reminders=[reminder],
            alarms={},
            triggers={},
            attachments={},
            hashtags={},
            recurrence_rules={},
        )
        monkeypatch.setattr(
            api,
            "lists",
            MagicMock(return_value=[RemindersList(id="List/B", title="B")]),
        )
        monkeypatch.setattr(api, "list_reminders", MagicMock(return_value=batch))

        out = list(api.reminders())

        assert out == [reminder]

    def test_sync_cursor_raises_when_no_token_available(self) -> None:
        """sync_cursor() raises when neither query nor zone changes yield a token."""
        api, raw = _read_api()
        raw.current_sync_token.return_value = None
        raw.changes.return_value = CKZoneChangesResponse(
            zones=[
                CKZoneChangesZone(
                    records=[],
                    moreComing=False,
                    syncToken="",
                    zoneID=_REMINDERS_ZONE_ID,
                )
            ]
        )

        with pytest.raises(RemindersApiError, match="Unable to obtain sync token"):
            api.sync_cursor()

    def test_iter_changes_skips_unknown_and_foreign_records(self) -> None:
        """iter_changes() skips non-Reminder CKRecords and unknown record kinds."""
        api, raw = _read_api()
        # A plain namespace stands in for the zone page; pydantic model
        # validation would otherwise reject the opaque "record kind" below.
        zone = SimpleNamespace(
            records=[
                _ck_record("List", "List/A", {}),
                SimpleNamespace(recordName="Reminder/MYSTERY"),
            ],
            moreComing=False,
            syncToken="tok",
        )
        raw.changes.return_value = MagicMock(zones=[zone])

        out = list(api.iter_changes(since="tok-0"))

        assert not out

    def test_get_raises_when_lookup_returns_other_records(self) -> None:
        """get() raises LookupError when only other records come back."""
        api, raw = _read_api()
        raw.lookup.return_value = MagicMock(
            records=[_minimal_reminder_record("Reminder/OTHER", "List/A")]
        )

        with pytest.raises(LookupError, match="Reminder not found"):
            api.get("Reminder/MISSING")

    def test_attachments_for_empty_ids_returns_early(self) -> None:
        """attachments_for() returns [] before issuing a lookup for no IDs."""
        api, raw = _read_api()
        reminder = Reminder(id="Reminder/A", list_id="List/A", title="A")

        assert not api.attachments_for(reminder)
        raw.lookup.assert_not_called()

    def test_lookup_related_skips_wrong_types_and_none_mappings(self) -> None:
        """Related-record lookup skips wrong types and unmapped records."""
        api, raw = _read_api()
        reminder = Reminder(
            id="Reminder/A",
            list_id="List/A",
            title="A",
            attachment_ids=["ATT-1", "ATT-2"],
        )
        raw.lookup.return_value = MagicMock(
            records=[
                _minimal_reminder_record("Reminder/A", "List/A"),
                _ck_record(
                    "Attachment",
                    "Attachment/ATT-1",
                    {
                        "Type": {"type": "STRING", "value": "URL"},
                        "URL": {"type": "STRING", "value": "https://x.test"},
                    },
                ),
                _ck_record(
                    "Attachment",
                    "Attachment/ATT-2",
                    {"Type": {"type": "STRING", "value": "FutureType"}},
                ),
            ]
        )

        out = api.attachments_for(reminder)

        assert [att.id for att in out] == ["Attachment/ATT-1"]

    def test_list_reminders_ingests_all_record_kinds(self) -> None:
        """list_reminders() tolerates unmappable and foreign record kinds."""
        api, raw = _read_api()
        raw.query.return_value = CKQueryResponse(
            records=[
                _minimal_reminder_record("Reminder/A", "List/A"),
                _ck_record(
                    "AlarmTrigger",
                    "AlarmTrigger/TRIG-VEH",
                    {
                        "Type": {"type": "STRING", "value": "Vehicle"},
                        "Alarm": {
                            "type": "REFERENCE",
                            "value": {"recordName": "Alarm/A"},
                        },
                    },
                ),
                _ck_record(
                    "Attachment",
                    "Attachment/ATT-X",
                    {"Type": {"type": "STRING", "value": "FutureType"}},
                ),
                _ck_record("List", "List/A", {}),
                CKTombstoneRecord(recordName="Reminder/T", deleted=True),
            ],
            continuationMarker=None,
        )

        result = api.list_reminders(list_id="List/A", include_completed=True)

        assert [r.id for r in result.reminders] == ["Reminder/A"]
        assert result.triggers == {}
        assert result.attachments == {}

    def test_alarms_for_no_ids_returns_early(self) -> None:
        """alarms_for() returns [] when the reminder has no alarm IDs."""
        api, raw = _read_api()
        reminder = Reminder(id="Reminder/A", list_id="List/A", title="A")

        assert api.alarms_for(reminder) == []
        raw.lookup.assert_not_called()

    def test_alarms_for_no_triggers_skips_trigger_lookup(self) -> None:
        """alarms_for() skips the trigger lookup when no alarm has a trigger."""
        api, raw = _read_api()
        raw.lookup.side_effect = [
            MagicMock(
                records=[
                    _ck_record(
                        "Alarm",
                        "Alarm/AL-2",
                        {
                            "AlarmUID": {"type": "STRING", "value": "AL-2"},
                            "Reminder": {
                                "type": "REFERENCE",
                                "value": {"recordName": "Reminder/A"},
                            },
                        },
                    )
                ]
            )
        ]
        reminder = Reminder(
            id="Reminder/A",
            list_id="List/A",
            title="A",
            alarm_ids=["AL-2"],
        )

        out = api.alarms_for(reminder)

        assert raw.lookup.call_count == 1
        assert len(out) == 1
        assert out[0].alarm.id == "Alarm/AL-2"
        assert out[0].trigger is None

    def test_alarms_for_tolerates_foreign_and_unmappable_records(self) -> None:
        """alarms_for() skips foreign records and unmappable triggers."""
        api, raw = _read_api()
        raw.lookup.side_effect = [
            MagicMock(
                records=[
                    _ck_record(
                        "Alarm",
                        "Alarm/AL-1",
                        {
                            "AlarmUID": {"type": "STRING", "value": "AL-1"},
                            "Reminder": {
                                "type": "REFERENCE",
                                "value": {"recordName": "Reminder/A"},
                            },
                            "TriggerID": {"type": "STRING", "value": "TRIG-1"},
                        },
                    ),
                    _ck_record("List", "List/A", {}),
                ]
            ),
            MagicMock(
                records=[
                    _ck_record(
                        "AlarmTrigger",
                        "AlarmTrigger/TRIG-1",
                        {
                            "Type": {"type": "STRING", "value": "Location"},
                            "Alarm": {
                                "type": "REFERENCE",
                                "value": {"recordName": "Alarm/AL-1"},
                            },
                            "Title": {"type": "STRING", "value": "Office"},
                            "Address": {"type": "STRING", "value": "Addr"},
                            "Latitude": {"type": "DOUBLE", "value": 48.0},
                            "Longitude": {"type": "DOUBLE", "value": 2.0},
                            "Radius": {"type": "DOUBLE", "value": 100.0},
                            "Proximity": {"type": "INT64", "value": 1},
                            "LocationUID": {"type": "STRING", "value": "LOC-1"},
                        },
                    ),
                    _minimal_reminder_record("Reminder/A", "List/A"),
                    _ck_record(
                        "AlarmTrigger",
                        "AlarmTrigger/TRIG-VEH",
                        {
                            "Type": {"type": "STRING", "value": "Vehicle"},
                            "Alarm": {
                                "type": "REFERENCE",
                                "value": {"recordName": "Alarm/AL-1"},
                            },
                        },
                    ),
                ]
            ),
        ]
        reminder = Reminder(
            id="Reminder/A",
            list_id="List/A",
            title="A",
            alarm_ids=["AL-1"],
        )

        out = api.alarms_for(reminder)

        assert raw.lookup.call_count == 2
        assert len(out) == 1
        assert out[0].alarm.id == "Alarm/AL-1"
        assert out[0].trigger is not None
        assert out[0].trigger.id == "AlarmTrigger/TRIG-1"


# ---------------------------------------------------------------------------
# _protocol.py — pure protocol helper branches
# ---------------------------------------------------------------------------


class TestRemindersProtocolBranches:
    """Exercise previously-uncovered branches in the protocol helpers."""

    def test_as_record_name_and_raw_id_empty_values(self) -> None:
        """Prefix helpers pass empty values straight through."""
        assert _as_record_name("", "Alarm") == ""
        assert _as_record_name("UUID", "Alarm") == "Alarm/UUID"
        assert _as_record_name("Alarm/UUID", "Alarm") == "Alarm/UUID"
        assert _as_raw_id("", "Alarm") == ""
        assert _as_raw_id("UUID", "Alarm") == "UUID"
        assert _as_raw_id("Alarm/UUID", "Alarm") == "UUID"

    def test_looks_like_url_branches(self) -> None:
        """URL detection handles empty, schemeless, and exotic schemes."""
        assert _looks_like_url("") is False
        assert _looks_like_url("not a url") is False
        assert _looks_like_url("https://example.com") is True
        assert _looks_like_url("mailto:user@example.com") is True
        assert _looks_like_url("ftp://host") is True

    def test_decode_attachment_url_branches(self) -> None:
        """URL decoding handles empty, non-URL base64, and URL base64 values."""
        assert _decode_attachment_url("") == ""
        assert _decode_attachment_url("https://x.test/a") == "https://x.test/a"
        assert _decode_attachment_url("!!!") == "!!!"

        url_b64 = base64.b64encode(b"https://x.test/b").decode("ascii")
        assert _decode_attachment_url(url_b64) == "https://x.test/b"

        text_b64 = base64.b64encode(b"hello world").decode("ascii")
        assert _decode_attachment_url(text_b64) == text_b64

    def test_decode_cloudkit_text_value_branches(self) -> None:
        """Text field decoding handles None, str, bytes, and other values."""
        assert _decode_cloudkit_text_value(None) == ""
        assert _decode_cloudkit_text_value("plain") == "plain"
        assert _decode_cloudkit_text_value(b"bytes") == "bytes"
        assert _decode_cloudkit_text_value(42) == "42"

    def test_archive_object_branching(self) -> None:
        """Archive object resolution handles in-range, out-of-range, and plain."""
        objects: list[object] = [object()]

        assert _archive_object(plistlib.UID(0), objects) is objects[0]
        assert _archive_object(plistlib.UID(9), objects) is None
        assert _archive_object("plain", objects) == "plain"

    @staticmethod
    def _archive_payload(objects: list[object], root: object) -> bytes:
        """Serialize a minimal NSKeyedArchiver payload with a chosen root."""
        archive = {
            "$version": 100000,
            "$archiver": "NSKeyedArchiver",
            "$top": {"root": plistlib.UID(1)},
            "$objects": ["$null", root, *objects],
        }
        return plistlib.dumps(
            archive,
            fmt=plistlib.PlistFormat.FMT_BINARY,
        )

    def test_decode_nsdatecomponents_valid_archive(self) -> None:
        """A well-formed NSDateComponents archive decodes into components."""
        payload = self._archive_payload(
            [
                {"$class": plistlib.UID(2)},
                {"$classname": "NSDateComponents", "$classes": ["NSDateComponents"]},
            ],
            {"NS.year": 2026, "NS.month": 10, "NS.day": 9},
        )

        components = _decode_nsdatecomponents(payload)

        assert components == DateComponents(year=2026, month=10, day=9)

    def test_decode_nsdatecomponents_non_archive_plist(self) -> None:
        """A plist without the NSKeyedArchiver marker yields None."""
        payload = plistlib.dumps({"hello": "world"})

        assert _decode_nsdatecomponents(payload) is None

    def test_decode_nsdatecomponents_bad_objects_layout(self) -> None:
        """An archive whose $objects is not a list yields None."""
        archive = {
            "$version": 100000,
            "$archiver": "NSKeyedArchiver",
            "$top": {"root": plistlib.UID(1)},
            "$objects": "not-a-list",
        }
        payload = plistlib.dumps(archive, fmt=plistlib.PlistFormat.FMT_BINARY)

        assert _decode_nsdatecomponents(payload) is None

    def test_decode_nsdatecomponents_non_dict_root(self) -> None:
        """An archive whose root is not a dict yields None."""
        payload = self._archive_payload([], "$null")

        assert _decode_nsdatecomponents(payload) is None

    def test_decode_nsdatecomponents_empty_root(self) -> None:
        """An archive with an empty root dict yields None."""
        payload = self._archive_payload([], {})

        assert _decode_nsdatecomponents(payload) is None

    def test_decode_nsdatecomponents_non_string_timezone(self) -> None:
        """A non-string timezone name in an archive is dropped, not fatal."""
        payload = self._archive_payload(
            [
                {"$class": plistlib.UID(3)},
                {"NS.year": 2026, "NS.timezone": plistlib.UID(2)},
                {"NS.name": 99, "$class": plistlib.UID(3)},
                {"$classname": "NSTimeZone", "$classes": ["NSTimeZone"]},
            ],
            {"NS.year": 2026, "NS.timezone": plistlib.UID(2)},
        )

        components = _decode_nsdatecomponents(payload)

        assert components is not None
        assert components.year == 2026
        assert components.time_zone is None

    def test_decode_nsdatecomponents_rejects_other_python_types(self) -> None:
        """Values that are neither str nor bytes are rejected up front."""
        assert _decode_nsdatecomponents(None) is None
        assert _decode_nsdatecomponents(12345) is None

    def test_decode_nsdatecomponents_bad_base64(self) -> None:
        """A str payload that is not decodable base64 yields None."""
        assert _decode_nsdatecomponents("a") is None

    def test_decode_crdt_document_bare_string_payload(self) -> None:
        """A bare String protobuf payload decodes through the legacy path."""
        value = reminders_pb2.String()  # type: ignore[attr-defined]
        value.string = "legacy"

        result = _decode_crdt_document(value.SerializeToString())

        assert result == "legacy"
