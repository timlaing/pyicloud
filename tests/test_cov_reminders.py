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
import hashlib
import json
import logging
import plistlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import (
    CKAssetToken,
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
    _decode_due_date_delta_alerts,
    _decode_nsdatecomponents,
    _decode_urgent_presentation_alarms,
    _encode_due_date_delta_alerts,
    _encode_urgent_presentation_alarms,
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
    EarlyReminder,
    EarlyReminderUnit,
    Hashtag,
    ImageAttachment,
    ListRemindersResult,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
    RemindersList,
    UrgentPresentationAlarms,
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


def _write_api(
    *,
    account_id: str | None = None,
) -> tuple[RemindersWriteAPI, MagicMock]:
    """Return a write API backed by a fully-mocked raw CloudKit client."""
    raw = MagicMock()
    mapper = MagicMock()
    return (
        RemindersWriteAPI(lambda: raw, mapper, LOGGER, account_id=account_id),
        raw,
    )


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
        assert fields["Frequency"].value == 0
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


# ---------------------------------------------------------------------------
# Recurrence frequency — wire values are zero-based (#419)
# ---------------------------------------------------------------------------


class TestRecurrenceFrequencyWireValues:
    """``RecurrenceFrequency`` mirrors Apple's zero-based wire encoding."""

    @staticmethod
    def _rule_record(frequency: int | None) -> CKRecord:
        fields: dict[str, Any] = {"Interval": {"type": "INT64", "value": 1}}
        if frequency is not None:
            fields["Frequency"] = {"type": "INT64", "value": frequency}
        return _ck_record("RecurrenceRule", "RecurrenceRule/RR-1", fields)

    def test_wire_values_map_to_frequencies(self) -> None:
        """0..3 are daily, weekly, monthly and yearly on the wire."""
        mapper = RemindersRecordMapper(MagicMock, LOGGER)
        for wire, expected in (
            (0, RecurrenceFrequency.DAILY),
            (1, RecurrenceFrequency.WEEKLY),
            (2, RecurrenceFrequency.MONTHLY),
            (3, RecurrenceFrequency.YEARLY),
        ):
            rule = mapper.record_to_recurrence_rule(self._rule_record(wire))
            assert rule.frequency == expected

    def test_missing_frequency_defaults_to_daily(self) -> None:
        """A rule without a Frequency field reads as daily, not weekly."""
        mapper = RemindersRecordMapper(MagicMock, LOGGER)

        rule = mapper.record_to_recurrence_rule(self._rule_record(None))

        assert rule.frequency == RecurrenceFrequency.DAILY

    def test_daily_rule_writes_wire_zero(self) -> None:
        """A daily rule writes Frequency=0 rather than dropping the update."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        rule = RecurrenceRule(
            id="RecurrenceRule/RR-X",
            reminder_id="",
            frequency=RecurrenceFrequency.WEEKLY,
            record_change_tag="ctag",
        )

        api.update_recurrence_rule(rule, frequency=RecurrenceFrequency.DAILY)

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["Frequency"].value == 0

    def test_create_writes_wire_values(self) -> None:
        """create_recurrence_rule writes int(frequency), the wire value."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")

        api.create_recurrence_rule(reminder, frequency=RecurrenceFrequency.YEARLY)

        create_ops = raw.modify.call_args.kwargs["operations"]
        assert create_ops[1].record.fields["Frequency"].value == 3


# ---------------------------------------------------------------------------
# Early reminders — DueDateDeltaAlertsData (#419)
# ---------------------------------------------------------------------------


def _early_alerts_payload(**overrides: Any) -> dict[str, Any]:
    """Build the JSON Apple stores in DueDateDeltaAlertsData."""
    payload: dict[str, Any] = {
        "reminderIdentifier": "Reminder/R-1",
        "dueDateDeltaAlerts": [
            {
                "dueDateDeltaCount": -3,
                "dueDateDeltaUnit": 1,
                "identifier": "ALERT-1",
                "creationDate": 813646978.64,
                "minimumSupportedAppVersion": 0,
            }
        ],
        "accountIdentifier": "ACCOUNT-1",
        "minimumSupportedVersion": 20230430,
    }
    payload.update(overrides)
    return payload


def _encode_payload_field(payload: Any) -> dict[str, str]:
    """Encode a payload as the ENCRYPTED_BYTES wire field CloudKit carries."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "type": "ENCRYPTED_BYTES",
        "value": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


class TestEarlyReminderCodecs:
    """The DueDateDeltaAlertsData payload round-trips as base64 JSON."""

    def test_round_trip(self) -> None:
        """Encoded alerts decode back to the same domain values."""
        field = _encode_due_date_delta_alerts(
            reminder_identifier="Reminder/R-1",
            account_identifier="ACCOUNT-1",
            alerts=[
                EarlyReminder(
                    count=-3,
                    unit=EarlyReminderUnit.HOURS,
                    identifier="ALERT-1",
                )
            ],
        )

        payload, alerts = _decode_due_date_delta_alerts(field["value"])

        assert payload["accountIdentifier"] == "ACCOUNT-1"
        assert payload["reminderIdentifier"] == "Reminder/R-1"
        assert len(alerts) == 1
        assert alerts[0].count == -3
        assert alerts[0].unit == EarlyReminderUnit.HOURS
        assert alerts[0].identifier == "ALERT-1"
        assert alerts[0].creation_date is not None

    def test_decode_accepts_bytes_str_and_none(self) -> None:
        """The decoder accepts the shapes the field can arrive in.

        On the wire CloudKit hands the field over already base64-decoded as
        bytes; a ``str`` value is still base64 text.
        """
        payload = _early_alerts_payload()
        raw = json.dumps(payload).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")

        from_bytes, _ = _decode_due_date_delta_alerts(raw)
        from_b64, _ = _decode_due_date_delta_alerts(encoded)

        assert from_bytes["accountIdentifier"] == "ACCOUNT-1"
        assert from_b64["accountIdentifier"] == "ACCOUNT-1"

    def test_undecodable_payload_is_not_fatal(self) -> None:
        """Garbage in the field yields no alerts instead of raising."""
        assert _decode_due_date_delta_alerts(b"\xff\xfe not json") == ({}, ())
        assert _decode_due_date_delta_alerts(json.dumps([1, 2])) == ({}, ())
        assert _decode_due_date_delta_alerts(None) == ({}, ())

    def test_malformed_alert_entries_are_skipped(self) -> None:
        """Entries missing usable values do not fail the whole payload."""
        payload = _early_alerts_payload(
            dueDateDeltaAlerts=[
                {"dueDateDeltaCount": -1, "dueDateDeltaUnit": 1},
                {"dueDateDeltaCount": -2, "dueDateDeltaUnit": 99},
                "not-a-dict",
            ]
        )

        _, alerts = _decode_due_date_delta_alerts(
            _encode_payload_field(payload)["value"]
        )

        assert [alert.count for alert in alerts] == [-1]

    def test_positive_counts_are_normalised(self) -> None:
        """A stray positive count is stored as before-the-due-date."""
        payload = _early_alerts_payload(
            dueDateDeltaAlerts=[{"dueDateDeltaCount": 3, "dueDateDeltaUnit": 2}]
        )

        _, alerts = _decode_due_date_delta_alerts(
            _encode_payload_field(payload)["value"]
        )

        assert alerts[0].count == -3
        assert alerts[0].unit == EarlyReminderUnit.DAYS


class TestEarlyReminderReads:
    """The mapper surfaces early reminders from a reminder record."""

    @staticmethod
    def _mapper() -> RemindersRecordMapper:
        return RemindersRecordMapper(MagicMock, LOGGER)

    def test_early_reminders_from_record(self) -> None:
        """A DueDateDeltaAlertsData field decodes into the domain model."""
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {"DueDateDeltaAlertsData": _encode_payload_field(_early_alerts_payload())},
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert reminder.early_reminders == (
            EarlyReminder(
                count=-3,
                unit=EarlyReminderUnit.HOURS,
                identifier="ALERT-1",
                creation_date=813646978.64,
            ),
        )
        assert reminder.early_reminder_account_id == "ACCOUNT-1"

    def test_no_field_means_no_early_reminders(self) -> None:
        """A reminder without the field has no alerts and no account id."""
        rec = _ck_record("Reminder", "Reminder/R-1", {})

        reminder = self._mapper().record_to_reminder(rec)

        assert not reminder.early_reminders
        assert reminder.early_reminder_account_id is None

    def test_undecodable_field_is_not_fatal(self) -> None:
        """An unreadable payload leaves the reminder readable."""
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {"DueDateDeltaAlertsData": _encode_payload_field("not json {")},
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert not reminder.early_reminders


class TestEarlyReminderWrites:
    """set_early_reminders writes the field with a merged token map."""

    @staticmethod
    def _write_api_with_server(
        server_fields: dict[str, Any] | None = None,
        *,
        account_id: str | None = None,
    ) -> tuple[RemindersWriteAPI, MagicMock]:
        api, raw = _write_api(account_id=account_id)
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        records: list[Any] = []
        if server_fields is not None:
            records.append(_ck_record("Reminder", "Reminder/R-1", server_fields))
        raw.lookup.return_value = MagicMock(records=records)
        return api, raw

    def test_requires_an_account_identifier(self) -> None:
        """Without a signed-in account or learned payload, the write refuses."""
        api, _ = self._write_api_with_server()
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")
        alert = EarlyReminder(count=-1, unit=EarlyReminderUnit.DAYS)

        with pytest.raises(ValueError, match="account identifier"):
            api.set_early_reminders(reminder, [alert])

    def test_falls_back_to_signed_in_account(self) -> None:
        """A fresh reminder uses the signed-in account's directory id."""
        api, raw = self._write_api_with_server(account_id="DSID-123")
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")

        api.set_early_reminders(
            reminder, [EarlyReminder(count=-1, unit=EarlyReminderUnit.DAYS)]
        )

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        payload, _ = _decode_due_date_delta_alerts(
            base64.b64encode(fields["DueDateDeltaAlertsData"].value).decode("ascii")
        )
        assert payload["accountIdentifier"] == "DSID-123"
        assert reminder.early_reminder_account_id == "DSID-123"

    def test_writes_field_and_tokens(self) -> None:
        """The alert payload, token map and local state are all updated."""
        server_map = json.dumps({
            "map": {"dueDate": {"counter": 4, "modificationTime": 1.0}}
        })
        api, raw = self._write_api_with_server({
            "ResolutionTokenMap": {"type": "STRING", "value": server_map},
        })
        reminder = Reminder(
            id="Reminder/R-1",
            list_id="List/L-1",
            title="t",
            record_change_tag="ctag",
            early_reminder_account_id="ACCOUNT-1",
        )

        api.set_early_reminders(
            reminder, [EarlyReminder(count=-1, unit=EarlyReminderUnit.DAYS)]
        )

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        payload, alerts = _decode_due_date_delta_alerts(
            base64.b64encode(fields["DueDateDeltaAlertsData"].value).decode("ascii")
        )
        assert payload["accountIdentifier"] == "ACCOUNT-1"
        assert alerts[0].count == -1
        assert alerts[0].unit == EarlyReminderUnit.DAYS
        tokens = json.loads(fields["ResolutionTokenMap"].value)["map"]
        assert tokens["dueDateDeltaAlertsData"]["counter"] == 1
        assert tokens["lastModifiedDate"]["counter"] == 1
        assert tokens["dueDate"]["counter"] == 4  # untouched tokens survive
        assert reminder.early_reminders
        assert reminder.early_reminder_account_id == "ACCOUNT-1"

    def test_reuses_existing_alert_metadata(self) -> None:
        """A replacement alert keeps the app's identifier and creation date."""
        api, raw = self._write_api_with_server({
            "DueDateDeltaAlertsData": _encode_payload_field(_early_alerts_payload()),
        })
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")

        api.set_early_reminders(
            reminder, [EarlyReminder(count=-3, unit=EarlyReminderUnit.HOURS)]
        )

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        _, alerts = _decode_due_date_delta_alerts(
            base64.b64encode(fields["DueDateDeltaAlertsData"].value).decode("ascii")
        )
        assert alerts[0].identifier == "ALERT-1"
        assert alerts[0].creation_date == 813646978.64
        assert reminder.early_reminder_account_id == "ACCOUNT-1"

    def test_empty_alerts_clear_the_field(self) -> None:
        """Passing no alerts removes them while keeping the account id."""
        api, raw = self._write_api_with_server({
            "DueDateDeltaAlertsData": _encode_payload_field(_early_alerts_payload()),
        })
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")

        api.set_early_reminders(reminder, [])

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        payload, alerts = _decode_due_date_delta_alerts(
            base64.b64encode(fields["DueDateDeltaAlertsData"].value).decode("ascii")
        )
        assert not alerts
        assert payload["accountIdentifier"] == "ACCOUNT-1"
        assert not reminder.early_reminders


# ---------------------------------------------------------------------------
# Urgent presentation alarms (#419)
# ---------------------------------------------------------------------------


class TestUrgentCodecs:
    """The Urgent asset document round-trips."""

    def test_round_trip(self) -> None:
        """An encoded asset decodes back to the same state."""
        data = _encode_urgent_presentation_alarms(
            enabled=True, person_id="PERSON-1", modified_on=123.5
        )

        decoded = _decode_urgent_presentation_alarms(data)

        assert decoded.enabled is True
        assert decoded.person_id == "PERSON-1"
        assert decoded.modified_on == 123.5
        assert decoded.minimum_supported_version == 20251103

    def test_disabled_asset(self) -> None:
        """A disabled asset decodes with enabled False."""
        data = _encode_urgent_presentation_alarms(enabled=False, person_id="PERSON-1")

        assert _decode_urgent_presentation_alarms(data).enabled is False

    def test_non_object_payload_raises(self) -> None:
        """A JSON payload that is not an object is rejected."""
        with pytest.raises(ValueError, match="not a JSON object"):
            _decode_urgent_presentation_alarms(b"[1, 2, 3]")

    def test_missing_account_entry(self) -> None:
        """An asset without an account entry decodes as disabled."""
        data = json.dumps({"minimumSupportedVersion": 1}).encode("utf-8")

        decoded = _decode_urgent_presentation_alarms(data)

        assert decoded.enabled is False
        assert decoded.person_id is None
        assert decoded.minimum_supported_version == 1


class TestUrgentReads:
    """The mapper surfaces Urgent state from a reminder record."""

    @staticmethod
    def _mapper(raw: MagicMock | None = None) -> RemindersRecordMapper:
        return RemindersRecordMapper(lambda: raw or MagicMock(), LOGGER)

    def test_urgent_from_inline_asset(self) -> None:
        """An asset carrying its bytes decodes without a download."""
        asset = CKAssetToken.model_validate({
            "fileChecksum": "sum",
            "downloadedData": base64.b64encode(
                _encode_urgent_presentation_alarms(enabled=True, person_id="PERSON-1")
            ).decode("ascii"),
        })
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsAsData": {
                    "type": "ASSETID",
                    "value": asset.model_dump(mode="json", exclude_none=True),
                },
                "UrgentPresentationAlarmsChecksum": {
                    "type": "STRING",
                    "value": "abc123",
                    "isEncrypted": True,
                },
            },
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.enabled is True
        assert reminder.urgent.person_id == "PERSON-1"
        assert reminder.urgent.checksum == "abc123"

    def test_urgent_from_download_url(self) -> None:
        """An asset with only a URL is downloaded before decoding."""
        raw = MagicMock()
        raw.download_asset_bytes.return_value = _encode_urgent_presentation_alarms(
            enabled=True, person_id="PERSON-1"
        )
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsAsData": {
                    "type": "ASSETID",
                    "value": {"downloadURL": "https://example.com/urgent"},
                },
            },
        )

        reminder = self._mapper(raw).record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.enabled is True
        raw.download_asset_bytes.assert_called_once_with("https://example.com/urgent")

    def test_asset_without_data_or_url(self) -> None:
        """An asset with neither bytes nor a URL degrades to the checksum."""
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsAsData": {
                    "type": "ASSETID",
                    "value": {"fileChecksum": "sum"},
                },
                "UrgentPresentationAlarmsChecksum": {
                    "type": "STRING",
                    "value": "abc",
                    "isEncrypted": True,
                },
            },
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.enabled is False
        assert reminder.urgent.checksum == "abc"

    def test_undecodable_asset_degrades_to_checksum(self) -> None:
        """Garbage in the asset keeps the reminder readable."""
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsAsData": {
                    "type": "ASSETID",
                    "value": {
                        "downloadedData": base64.b64encode(b"not json").decode("ascii")
                    },
                },
                "UrgentPresentationAlarmsChecksum": {
                    "type": "STRING",
                    "value": "abc",
                    "isEncrypted": True,
                },
            },
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.checksum == "abc"

    def test_download_failure_degrades_to_checksum(self) -> None:
        """A failed asset download keeps the reminder readable."""
        raw = MagicMock()
        raw.download_asset_bytes.side_effect = RemindersApiError("HTTP 404")
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsAsData": {
                    "type": "ASSETID",
                    "value": {"downloadURL": "https://example.com/urgent"},
                },
                "UrgentPresentationAlarmsChecksum": {
                    "type": "STRING",
                    "value": "abc",
                    "isEncrypted": True,
                },
            },
        )

        reminder = self._mapper(raw).record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.enabled is False
        assert reminder.urgent.checksum == "abc"

    def test_no_urgent_fields(self) -> None:
        """A reminder without Urgent fields has urgent None."""
        rec = _ck_record("Reminder", "Reminder/R-1", {})

        assert self._mapper().record_to_reminder(rec).urgent is None

    def test_checksum_without_asset(self) -> None:
        """A checksum alone still surfaces as (disabled) Urgent state."""
        rec = _ck_record(
            "Reminder",
            "Reminder/R-1",
            {
                "UrgentPresentationAlarmsChecksum": {
                    "type": "STRING",
                    "value": "abc",
                    "isEncrypted": True,
                },
            },
        )

        reminder = self._mapper().record_to_reminder(rec)

        assert reminder.urgent is not None
        assert reminder.urgent.checksum == "abc"


class TestUrgentWrites:
    """set_urgent uploads the asset and writes both fields."""

    def test_requires_person_id(self) -> None:
        """Without a known person id, the write refuses with guidance."""
        api, _ = _write_api()
        reminder = Reminder(id="Reminder/R-1", list_id="List/L-1", title="t")

        with pytest.raises(ValueError, match="person id"):
            api.set_urgent(reminder, True)

    def test_uploads_asset_and_writes_fields(self) -> None:
        """The asset upload, checksum and token map are all wired up."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        raw.lookup.return_value = MagicMock(records=[])
        raw.upload_asset.return_value = CKAssetToken(
            fileChecksum="fsum", referenceChecksum="reference-checksum", size=12
        )
        reminder = Reminder(
            id="Reminder/R-1", list_id="List/L-1", title="t", record_change_tag="ctag"
        )

        api.set_urgent(reminder, True, person_id="PERSON-1")

        upload = raw.upload_asset.call_args.kwargs
        assert upload["record_type"] == "Reminder"
        assert upload["field_name"] == "UrgentPresentationAlarmsAsData"
        assert upload["record_name"] == "Reminder/R-1"
        asset_payload = _decode_urgent_presentation_alarms(upload["data"])
        assert asset_payload.enabled is True
        assert asset_payload.person_id == "PERSON-1"

        fields = raw.modify.call_args.kwargs["operations"][0].record.fields
        assert fields["UrgentPresentationAlarmsAsData"].value.fileChecksum == "fsum"
        expected_checksum = hashlib.sha512(upload["data"]).hexdigest()
        assert fields["UrgentPresentationAlarmsChecksum"].value == expected_checksum
        assert fields["UrgentPresentationAlarmsChecksum"].unwrap().isEncrypted is True
        tokens = json.loads(fields["ResolutionTokenMap"].value)["map"]
        assert "urgentPresentationAlarmsAsData" in tokens
        assert "urgentPresentationAlarmsChecksum" in tokens
        assert "lastModifiedDate" in tokens

        assert reminder.urgent is not None
        assert reminder.urgent.enabled is True
        assert reminder.urgent.person_id == "PERSON-1"
        assert reminder.urgent.checksum == expected_checksum

    def test_person_id_learned_from_reminder(self) -> None:
        """A known person id on the reminder does not need passing again."""
        api, raw = _write_api()
        raw.modify.return_value = CKModifyResponse(records=[], syncToken="tok")
        raw.lookup.return_value = MagicMock(records=[])
        raw.upload_asset.return_value = CKAssetToken()
        reminder = Reminder(
            id="Reminder/R-1",
            list_id="List/L-1",
            title="t",
            urgent=UrgentPresentationAlarms(enabled=True, person_id="PERSON-1"),
        )

        api.set_urgent(reminder, False)

        assert raw.upload_asset.called
        assert reminder.urgent is not None
        assert reminder.urgent.enabled is False
        assert reminder.urgent.person_id == "PERSON-1"


class TestRemindersClientUploadAsset:
    """The Reminders client surfaces upload_asset with error translation."""

    @staticmethod
    def _client() -> tuple[CloudKitRemindersClient, MagicMock]:
        client = CloudKitRemindersClient(
            "https://ckdatabasews.icloud.com",
            MagicMock(),
            {},
        )
        inner = MagicMock()
        client._client = cast(Any, inner)
        return client, inner

    def test_success(self) -> None:
        """The token from the inner client is returned unchanged."""
        client, inner = self._client()
        token = CKAssetToken(fileChecksum="sum")
        inner.upload_asset.return_value = token

        result = client.upload_asset(
            record_type="Reminder",
            field_name="UrgentPresentationAlarmsAsData",
            record_name="Reminder/R-1",
            data=b"{}",
            zone_id=_REMINDERS_ZONE_REQ,
        )

        assert result is token

    def test_api_error_translated(self) -> None:
        """A CloudKit API failure surfaces as RemindersApiError."""
        client, inner = self._client()
        inner.upload_asset.side_effect = CloudKitApiError("upload failed")

        with pytest.raises(RemindersApiError, match="upload failed"):
            client.upload_asset(
                record_type="Reminder",
                field_name="UrgentPresentationAlarmsAsData",
                record_name="Reminder/R-1",
                data=b"{}",
                zone_id=_REMINDERS_ZONE_REQ,
            )
