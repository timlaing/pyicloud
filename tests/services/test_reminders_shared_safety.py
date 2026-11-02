"""Offline shared discovery and mutation safety regressions."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKLookupResponse,
    CKModifyResponse,
    CKQueryResponse,
    CKRecord,
    CKZoneChangesResponse,
    CKZoneChangesZone,
    CKZoneID,
    CKZoneListResponse,
    CKZoneListZone,
)
from pyicloud.services.reminders import RemindersService
from pyicloud.services.reminders.exceptions import RemindersApiError, RemindersAuthError
from pyicloud.services.reminders.models import Hashtag, Reminder, RemindersList


def list_record(identifier: str) -> CKRecord:
    """Construct only synthetic list metadata; no account records are read."""
    return CKRecord.model_validate({
        "recordType": "List",
        "recordName": identifier,
        "fields": {"Name": {"type": "STRING", "value": "Synthetic"}},
    })


def changes(records: list[CKRecord | CKErrorItem]) -> CKZoneChangesResponse:
    """Return one finite list-discovery page."""
    return CKZoneChangesResponse(
        zones=[
            CKZoneChangesZone(
                zoneID=CKZoneID(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE"),
                records=list(records),
                syncToken="synthetic",
                moreComing=False,
            )
        ]
    )


@pytest.fixture(name="service")
def service_fixture() -> Any:
    """An entirely mocked private/shared transport with a valid private list."""
    svc = RemindersService("https://example.invalid", MagicMock(), {})
    svc._raw = MagicMock()
    svc._raw.changes.return_value = changes([list_record("List/PRIVATE")])
    svc._shared_raw = MagicMock()
    svc._shared_raw.zones_list.return_value = CKZoneListResponse(zones=[])
    svc._shared_raw.modify.return_value = CKModifyResponse(
        records=[], syncToken="synthetic"
    )
    return svc


def test_shared_zone_listing_failure_retains_private_lists(
    service: Any,
) -> None:
    """Unavailable shares must not make a valid private snapshot unavailable."""
    service._shared_raw.zones_list.side_effect = RemindersApiError("Unavailable")
    assert [item.id for item in service.lists()] == ["List/PRIVATE"]


def test_shared_zone_listing_auth_failure_propagates(service: Any) -> None:
    """Authentication failures are not availability fallbacks."""
    service._shared_raw.zones_list.side_effect = RemindersAuthError("Authentication")
    with pytest.raises(RemindersAuthError, match="Authentication"):
        list(service.lists())


def test_shared_record_error_does_not_hide_later_zones(
    service: Any,
) -> None:
    """A rejected shared page stops only its zone, preserving later shares."""
    service._shared_raw.zones_list.return_value = CKZoneListResponse(
        zones=[
            CKZoneListZone(
                zoneID=CKZoneID(
                    zoneName="Shared",
                    ownerRecordName=owner,
                    zoneType="REGULAR_CUSTOM_ZONE",
                )
            )
            for owner in ["BAD", "GOOD"]
        ]
    )
    service._shared_raw.changes.side_effect = [
        changes([CKErrorItem(serverErrorCode="ACCESS_DENIED", recordName="List/BAD")]),
        changes([list_record("List/GOOD")]),
    ]
    assert [(item.id, item.zone_owner) for item in service.lists()] == [
        ("List/PRIVATE", None),
        ("List/GOOD", "GOOD"),
    ]
    assert service._shared_raw.changes.call_count == 2


@pytest.mark.parametrize(
    ("scope", "name", "owner"),
    [
        ("private", "Shared", "OWNER"),
        ("shared", "Other", "OWNER"),
        ("shared", "Shared", "OTHER"),
    ],
)
def test_linked_child_route_mismatch_rejected_before_writes(
    service: Any,
    scope: str,
    name: str,
    owner: str,
) -> None:
    """Same reminder/child IDs cannot authorize a different scope or zone."""
    reminder = Reminder(
        id="Reminder/R",
        list_id="List/L",
        title="Synthetic",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
        hashtag_ids=["T"],
    )
    child = Hashtag(
        id="Hashtag/T",
        reminder_id="Reminder/R",
        name="Synthetic",
        scope=scope,
        zone_name=name,
        zone_owner=owner,
    )
    with pytest.raises(ValueError, match="different zone route"):
        service.delete_hashtag(reminder, child)
    service._raw.modify.assert_not_called()
    service._shared_raw.modify.assert_not_called()
    assert reminder.hashtag_ids == ["T"]


def test_child_matches_resolved_writer_not_unscoped_parent_defaults(
    service: Any,
) -> None:
    """A cached shared route legitimately overrides an unscoped parent's defaults."""
    context = RemindersList(
        id="List/L",
        title="Synthetic",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
    )
    service._list_zones[
        (context.id, context.scope, context.zone_name, context.zone_owner)
    ] = context
    reminder = Reminder(
        id="Reminder/R", list_id=context.id, title="Synthetic", hashtag_ids=["T"]
    )
    child = Hashtag(
        id="Hashtag/T",
        reminder_id="Reminder/R",
        name="Synthetic",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
    )
    service.delete_hashtag(reminder, child)
    assert (
        service._shared_raw.modify.call_args.kwargs["zone_id"].ownerRecordName
        == "OWNER"
    )
    assert reminder.hashtag_ids == []


def test_completed_list_snapshot_removes_stale_routes(
    service: Any,
) -> None:
    """Revoked shares must not remain ambiguous after a completed fresh snapshot."""
    private = RemindersList(id="List/SAME", title="Private")
    shared = RemindersList(
        id="List/SAME", title="Shared", scope="shared", zone_owner="OWNER"
    )
    service._reads.lists = MagicMock(side_effect=[[private, shared], [private]])
    list(service.lists())
    assert len(service._list_zones) == 2
    list(service.lists())
    assert service._list_route(private.id) == ("private", "Reminders", None)
    assert len(service._list_zones) == 1


def test_partial_list_iteration_does_not_replace_complete_cache(
    service: Any,
) -> None:
    """Closing a partial iteration is not evidence that old lists disappeared."""
    original = RemindersList(id="List/ORIGINAL", title="Original")
    service._list_zones[
        (original.id, original.scope, original.zone_name, original.zone_owner)
    ] = original
    service._reads.lists = MagicMock(
        return_value=[RemindersList(id="List/NEW", title="New")]
    )
    iterator = iter(service.lists())
    assert next(iterator).id == "List/NEW"
    assert [item.id for item in service._list_zones.values()] == [original.id]


def discover_shared(service: Any) -> None:
    """Configure a share discoverable without any private membership data."""
    service._shared_raw.zones_list.return_value = CKZoneListResponse(
        zones=[
            CKZoneListZone(
                zoneID=CKZoneID(
                    zoneName="Shared",
                    ownerRecordName="OWNER",
                    zoneType="REGULAR_CUSTOM_ZONE",
                )
            ),
        ]
    )
    service._shared_raw.changes.return_value = changes([list_record("List/L")])


@pytest.mark.parametrize("explicit", [False, True])
def test_create_routes_shared_list_and_retains_model_route(
    service: Any, explicit: bool
) -> None:
    """String discovery and explicit contexts both create in the shared database."""
    discover_shared(service)
    context = RemindersList(
        id="List/L",
        title="Synthetic",
        scope="shared",
        zone_name="Shared",
        zone_owner="OWNER",
    )
    service._shared_raw.lookup.return_value = CKLookupResponse(
        records=[
            CKRecord.model_validate({
                "recordType": "Reminder",
                "recordName": "Reminder/R",
                "fields": {
                    "List": {
                        "type": "REFERENCE",
                        "value": {"recordName": context.id, "action": "VALIDATE"},
                    }
                },
            })
        ]
    )
    with patch("pyicloud.services.reminders._writes.uuid.uuid4", return_value="R"):
        created = service.create(context if explicit else context.id, "Synthetic")
    for call in [
        service._shared_raw.modify.call_args,
        service._shared_raw.lookup.call_args,
    ]:
        assert (
            call.kwargs["zone_id"].zoneName,
            call.kwargs["zone_id"].ownerRecordName,
        ) == ("Shared", "OWNER")
    assert (created.scope, created.zone_name, created.zone_owner) == (
        "shared",
        "Shared",
        "OWNER",
    )
    service._raw.modify.assert_not_called()
    if explicit:
        service._shared_raw.zones_list.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_unscoped_reminder_discovers_its_shared_route(
    service: Any, operation: str
) -> None:
    """Uncached manually constructed reminders must not write to private scope."""
    discover_shared(service)
    service._shared_raw.lookup.return_value = CKLookupResponse(records=[])
    reminder = Reminder(id="Reminder/R", list_id="List/L", title="Synthetic")
    getattr(service, operation)(reminder)
    assert (
        service._shared_raw.modify.call_args.kwargs["zone_id"].ownerRecordName
        == "OWNER"
    )
    service._raw.modify.assert_not_called()


def test_routing_discovery_ignores_unrelated_malformed_membership(
    service: Any,
) -> None:
    """Zone identity discovery must not decode unrelated membership payloads."""
    malformed = CKRecord.model_validate({
        "recordType": "List",
        "recordName": "List/BROKEN",
        "fields": {
            "Name": {"type": "STRING", "value": "Broken"},
            "ReminderIDs": {"type": "INT64", "value": 42},
        },
    })
    service._raw.changes.return_value = changes([
        malformed,
        list_record("List/PRIVATE"),
    ])
    service._raw.query.return_value = CKQueryResponse(records=[])
    service.list_reminders("List/PRIVATE")
    service._raw.query.assert_called_once()
    service._shared_raw.query.assert_not_called()
    with pytest.raises(RemindersApiError, match="ReminderIDs"):
        list(service.lists())


def test_routing_discovery_preserves_authentication_errors(
    service: Any,
) -> None:
    """Metadata-only routing cannot turn a rejected session into a private query."""
    service._shared_raw.zones_list.side_effect = RemindersAuthError("Authentication")
    with pytest.raises(RemindersAuthError, match="Authentication"):
        service.list_reminders("List/UNKNOWN")
    service._raw.query.assert_not_called()


def test_routing_discovery_keeps_duplicate_id_ambiguity(
    service: Any,
) -> None:
    """Skipping membership decoding must not lose cross-zone identities."""
    discover_shared(service)
    service._raw.changes.return_value = changes([list_record("List/L")])
    with pytest.raises(ValueError, match="Ambiguous list ID"):
        service.list_reminders("List/L")
    service._raw.query.assert_not_called()
    service._shared_raw.query.assert_not_called()


def test_deleted_shared_list_does_not_create_route_ambiguity(service: Any) -> None:
    """Routing-only reads must honor soft deletion without decoding membership."""
    discover_shared(service)
    deleted = list_record("List/PRIVATE").model_dump()
    deleted["fields"]["Deleted"] = {"type": "INT64", "value": 1}
    service._shared_raw.changes.return_value = changes([
        CKRecord.model_validate(deleted)
    ])
    service._raw.query.return_value = CKQueryResponse(records=[])
    service.list_reminders("List/PRIVATE")
    service._raw.query.assert_called_once()
    service._shared_raw.query.assert_not_called()


def test_malformed_shared_list_does_not_hide_later_valid_lists(service: Any) -> None:
    """One bad membership field cannot discard a later valid list on the page."""
    discover_shared(service)
    malformed = list_record("List/BROKEN").model_dump()
    malformed["fields"]["ReminderIDs"] = {"type": "INT64", "value": 42}
    service._shared_raw.changes.return_value = changes([
        CKRecord.model_validate(malformed),
        list_record("List/GOOD"),
    ])
    assert [item.id for item in service.lists()] == ["List/PRIVATE", "List/GOOD"]
    assert not service._list_zones


@pytest.mark.parametrize("failure", ["zones", "changes", "records"])
def test_incomplete_discovery_preserves_cached_shared_routes(
    service: Any, failure: str
) -> None:
    """Suppressed availability failures cannot replace a complete routing snapshot."""
    discover_shared(service)
    list(service.lists())
    original = dict(service._list_zones)
    if failure == "zones":
        service._shared_raw.zones_list.side_effect = RemindersApiError("Unavailable")
    elif failure == "changes":
        service._shared_raw.changes.side_effect = RemindersApiError("Unavailable")
    else:
        service._shared_raw.changes.return_value = changes([
            CKErrorItem(serverErrorCode="ACCESS_DENIED", recordName="List/L")
        ])
    assert [item.id for item in service.lists()] == ["List/PRIVATE"]
    assert service._list_zones == original
    assert service._list_route("List/L") == ("shared", "Shared", "OWNER")


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_explicit_zone_without_scope_does_not_discover(
    service: Any, operation: str
) -> None:
    """Custom zone values keep the default private scope instead of being discarded."""
    service._raw.modify.return_value = CKModifyResponse(
        records=[], syncToken="synthetic"
    )
    service._raw.lookup.return_value = CKLookupResponse(records=[])
    reminder = Reminder(
        id="Reminder/R",
        list_id="List/L",
        title="Synthetic",
        zone_name="Custom",
        zone_owner="OWNER",
    )
    getattr(service, operation)(reminder)
    zone = service._raw.modify.call_args.kwargs["zone_id"]
    assert (zone.zoneName, zone.ownerRecordName) == ("Custom", "OWNER")
    service._shared_raw.modify.assert_not_called()
    service._raw.changes.assert_not_called()
    service._shared_raw.zones_list.assert_not_called()


def test_interleaved_discovery_cannot_commit_an_earlier_partial_snapshot(
    service: Any,
) -> None:
    """A later successful iterator cannot change the earlier iterator's outcome."""
    discover_shared(service)
    service._shared_raw.zones_list.return_value = CKZoneListResponse(
        zones=[
            CKZoneListZone(
                zoneID=CKZoneID(
                    zoneName="Shared",
                    ownerRecordName=owner,
                    zoneType="REGULAR_CUSTOM_ZONE",
                )
            )
            for owner in ["BAD", "GOOD"]
        ]
    )
    service._shared_raw.changes.side_effect = [
        RemindersApiError("Unavailable"),
        changes([list_record("List/EARLIER")]),
        changes([list_record("List/LATER")]),
        changes([list_record("List/LATER2")]),
    ]
    earlier = iter(service.lists())
    assert next(earlier).id == "List/PRIVATE"
    assert next(earlier).id == "List/EARLIER"
    list(service.lists())
    completed = dict(service._list_zones)
    assert any(item.id == "List/LATER" for item in completed.values())
    assert not list(earlier)
    assert service._list_zones == completed


def test_invalid_shared_count_does_not_hide_later_records_or_zones(
    service: Any,
) -> None:
    """Malformed integer fields are local to the record, not the discovery."""
    discover_shared(service)
    service._shared_raw.zones_list.return_value = CKZoneListResponse(
        zones=[
            CKZoneListZone(
                zoneID=CKZoneID(
                    zoneName="Shared",
                    ownerRecordName=owner,
                    zoneType="REGULAR_CUSTOM_ZONE",
                )
            )
            for owner in ["FIRST", "SECOND"]
        ]
    )
    malformed = list_record("List/BROKEN").model_dump()
    malformed["fields"]["Count"] = {"type": "STRING", "value": "not-a-number"}
    service._shared_raw.changes.side_effect = [
        changes([CKRecord.model_validate(malformed), list_record("List/GOOD")]),
        changes([list_record("List/LATER")]),
    ]
    assert [item.id for item in service.lists()] == [
        "List/PRIVATE",
        "List/GOOD",
        "List/LATER",
    ]
    assert not service._list_zones
