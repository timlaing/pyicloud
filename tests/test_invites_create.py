"""Capture-shaped event creation, including bounded I/O and partial failures."""

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfoNotFoundError

import pytest

from pyicloud.common.cloudkit import CKZoneID, CKZoneListZone, CKZoneModifyOperation
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
)
from pyicloud.services.invites import (
    EventPlace,
    EventScope,
    EventTime,
    InvitesApiError,
    InvitesAuthError,
    InvitesCreationError,
    InvitesEntitlementError,
    InvitesService,
)
from pyicloud.services.invites.codecs import decode_json_bytes
from pyicloud.services.invites.entitlement import CREATE_EVENT_FEATURE, FeatureAccess
from tests.fixture_loader import load_fixture

EVENT_ID = "EVENT-CREATE-FIXTURE"
START = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
END = datetime(2030, 1, 1, 13, tzinfo=timezone.utc)
START_TIME = EventTime(start=START)


def _response(payload: Any, status: int = 200) -> MagicMock:
    response = MagicMock(status_code=status, headers={})
    response.json.return_value = payload
    return response


def _created() -> list[MagicMock]:
    return [
        _response(load_fixture("invites", filename))
        for filename in (
            "event_create_zone_response.json",
            "event_create_details_response.json",
            "event_create_share_response.json",
        )
    ]


def _deleted() -> MagicMock:
    return _response({"zones": [{"zoneID": {"zoneName": EVENT_ID}, "deleted": True}]})


@pytest.fixture(name="service")
def invites_service(monkeypatch: pytest.MonkeyPatch) -> InvitesService:
    """Use the real serializer stack with a synthetic cached entitlement."""
    monkeypatch.setattr("pyicloud.services.invites.service.uuid4", lambda: EVENT_ID)

    def zone_info(name: str) -> timezone:
        if name == "Europe/Paris":
            return timezone.utc
        if name.startswith("../"):
            raise ValueError("Invalid relative zone")
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr("pyicloud.services.invites._creation.ZoneInfo", zone_info)
    result = InvitesService("https://example.test", MagicMock(), {})
    result._feature_access = FeatureAccess(  # pylint: disable=protected-access
        feature_key=CREATE_EVENT_FEATURE,
        can_use=True,
        access_token="synthetic-token",
        cache_till=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    return result


@pytest.mark.parametrize("private", [True, False])
@pytest.mark.parametrize("end", [END, None])
def test_create_event_sequence(
    service: InvitesService, private: bool, end: datetime | None
) -> None:
    """A new draft gets a fresh zone, encrypted details, and a plain share."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = _created()
    event = service.create_event(
        "Synthetic event",
        EventTime(start=START, end=end),
        notes="Synthetic notes",
        is_private=private,
        max_attendees=10,
        max_additional_guests_per_rsvp=2,
        place=EventPlace(city="Synthetic city", time_zone_identifier="Europe/Paris"),
    )
    assert event.event_id == EVENT_ID
    assert event.scope is EventScope.PRIVATE
    assert event.record_change_tag == "created-fixture-1"
    assert not event.is_published
    assert event.share is not None
    assert event.share.short_guid == "fixture-created-share"
    calls = session.post.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs["json"] == {
        "operations": [
            {"operationType": "create", "zone": {"zoneID": {"zoneName": EVENT_ID}}}
        ]
    }
    for call in calls:
        assert call.kwargs["timeout"] == (10.0, 60.0)
        assert parse_qs(urlsplit(call.args[0]).query) == {
            "remapEnums": ["true"],
            "getCurrentSyncToken": ["true"],
        }
    assert urlsplit(calls[0].args[0]).path.endswith("/private/zones/modify")
    assert all(
        urlsplit(c.args[0]).path.endswith("/private/records/modify") for c in calls[1:]
    )
    details = calls[1].kwargs["json"]
    share = calls[2].kwargs["json"]
    for payload in (details, share):
        assert payload["atomic"] is True
        assert payload["zoneID"] == {"zoneName": EVENT_ID}
        assert payload["operations"][0]["operationType"] == "create"
    fields = details["operations"][0]["record"]["fields"]
    assert fields["isPrivate"] == {
        "type": "INT64",
        "value": int(private),
        "isEncrypted": True,
    }
    assert fields["maxAttendees"]["value"] == 10
    assert fields["maxAdditionalGuestsPerRSVP"]["value"] == 2
    assert fields["hint"] == {
        "type": "STRING",
        "value": '{"subscriptionAccessToken": "synthetic-token"}',
    }
    assert all(f["isEncrypted"] for k, f in fields.items() if k != "hint")
    preview = share["operations"][0]["record"]
    assert preview["participants"] == preview["blocked"] == preview["requesters"] == []
    assert preview["publicPermission"] == "NONE"
    assert preview["denyAccessRequests"] is True
    assert "hint" not in preview["fields"]
    assert all("isEncrypted" not in f for f in preview["fields"].values())
    assert preview["fields"]["startDate"]["value"] == int(START.timestamp() * 1000)
    assert ("endDate" in preview["fields"]) == (end is not None)
    assert decode_json_bytes(fields["place"]["value"]) == {
        "city": "Synthetic city",
        "timeZoneIdentifier": "Europe/Paris",
    }
    assert fields["time"]["value"] == preview["fields"]["time"]["value"]


def test_create_minimal_defaults(service: InvitesService) -> None:
    """No optional fields are required and no publication operation is sent."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = _created()
    service.create_event("Synthetic event", EventTime(start=START, is_open_ended=True))
    fields = session.post.call_args_list[1].kwargs["json"]["operations"][0]["record"][
        "fields"
    ]
    assert "isPublished" not in fields
    assert "maxAttendees" not in fields
    assert fields["maxAdditionalGuestsPerRSVP"]["value"] == 0
    assert decode_json_bytes(fields["place"]["value"]) == {}
    timing = decode_json_bytes(fields["time"]["value"])
    assert timing is not None
    assert timing["isOpenEnded"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": 1},
        {"time": None},
        {"time": EventTime(start=datetime(2030, 1, 1))},
        {"time": EventTime(start=START, end=datetime(2030, 1, 1, 13))},
        {"time": EventTime(start=END, end=START)},
        {"time": EventTime(start=START, end=END, is_open_ended=True)},
        {"max_attendees": 0},
        {"max_attendees": True},
        {"max_additional_guests_per_rsvp": -1},
        {"max_additional_guests_per_rsvp": 1.5},
        {"max_additional_guests_per_rsvp": None},
        {"notes": None},
        {"is_private": 1},
        {"place": {}},
        {"place": EventPlace(time_zone_identifier="")},
        {"place": EventPlace(time_zone_identifier="not-a-zone")},
        {"place": EventPlace(time_zone_identifier="../invalid")},
    ],
)
def test_invalid_creation_is_rejected_before_network(
    service: InvitesService, changes: dict[str, Any]
) -> None:
    """Invalid requests must not leave orphan zones or even fetch entitlement."""
    kwargs: dict[str, Any] = {
        "title": "Synthetic event",
        "time": EventTime(start=START),
    }
    kwargs.update(changes)
    with pytest.raises(ValueError):
        service.create_event(**kwargs)
    cast(MagicMock, service.session).post.assert_not_called()
    cast(MagicMock, service.session).get.assert_not_called()


def test_entitlement_denial_creates_no_zone(
    service: InvitesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subscription denial remains its specific error and precedes mutation."""
    service._feature_access = None  # pylint: disable=protected-access
    monkeypatch.setattr(
        service.raw,
        "feature_access",
        lambda _: FeatureAccess(feature_key=CREATE_EVENT_FEATURE, can_use=False),
    )
    with pytest.raises(InvitesEntitlementError):
        service.create_event("Synthetic event", START_TIME)
    cast(MagicMock, service.session).post.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        _response({"zones": [{"serverErrorCode": "CONFLICT", "reason": "Exists"}]}),
        _response({"zones": []}),
        _response({}),
        _response({"zones": [{"zoneID": {"zoneName": "another-zone"}}]}),
        _response({}, 401),
        TimeoutError("Uncertain network outcome"),
    ],
)
def test_unconfirmed_zone_is_not_deleted(service: InvitesService, failure: Any) -> None:
    """Do not delete a zone whose creation was not acknowledged, including conflicts."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = [failure]
    with pytest.raises(InvitesCreationError) as caught:
        service.create_event("Synthetic event", START_TIME)
    assert caught.value.event_id == EVENT_ID
    assert caught.value.cleanup_succeeded is None
    assert caught.value.cleanup_error is None
    assert caught.value.__cause__ is not None
    session.post.assert_called_once()


@pytest.mark.parametrize("stage", [1, 2])
@pytest.mark.parametrize(
    "failure",
    [
        _response({"records": [{"serverErrorCode": "CONFLICT", "reason": "Rejected"}]}),
        _response({"records": []}),
        _response({}, 401),
        TimeoutError("Disconnected"),
    ],
)
def test_partial_failure_deletes_only_new_zone(
    service: InvitesService, stage: int, failure: Any
) -> None:
    """Both record creation stages roll back a confirmed, uniquely generated zone."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = [*_created()[:stage], failure, _deleted()]
    with pytest.raises(InvitesCreationError) as caught:
        service.create_event("Synthetic event", START_TIME)
    assert caught.value.cleanup_succeeded is True
    assert caught.value.cleanup_error is None
    assert caught.value.__cause__ is not None
    if isinstance(failure, MagicMock) and failure.status_code == 401:
        assert isinstance(caught.value.__cause__, InvitesAuthError)
    assert session.post.call_args.kwargs["json"] == {
        "operations": [
            {"operationType": "delete", "zone": {"zoneID": {"zoneName": EVENT_ID}}}
        ]
    }


@pytest.mark.parametrize("stage", [1, 2])
def test_malformed_success_also_cleans_up(service: InvitesService, stage: int) -> None:
    """Missing event tags and absent share GUIDs cannot masquerade as success."""
    responses = _created()
    payload = responses[stage].json.return_value
    payload["records"][0].pop("recordChangeTag" if stage == 1 else "shortGUID")
    session = cast(MagicMock, service.session)
    session.post.side_effect = [*responses[: stage + 1], _deleted()]
    with pytest.raises(InvitesCreationError) as caught:
        service.create_event("Synthetic event", START_TIME)
    assert caught.value.cleanup_succeeded is True


@pytest.mark.parametrize("cleanup", [_response({}, 500), _response({"zones": []})])
def test_cleanup_failure_retains_original_error(
    service: InvitesService, cleanup: Any
) -> None:
    """An orphan's ID, original error, and cleanup error all remain available."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_created()[0], _response({}, 401), cleanup]
    with pytest.raises(InvitesCreationError) as caught:
        service.create_event("Synthetic event", START_TIME)
    assert caught.value.event_id == EVENT_ID
    assert caught.value.cleanup_succeeded is False
    assert isinstance(caught.value.__cause__, InvitesAuthError)
    assert isinstance(caught.value.cleanup_error, InvitesApiError)


@pytest.mark.parametrize("field", ["recordName", "recordType", "shortGUID"])
def test_unusable_share_response_rolls_back(
    service: InvitesService, field: str
) -> None:
    """An unrelated record or empty GUID must not produce a usable-looking draft."""
    responses = _created()
    responses[2].json.return_value["records"][0][field] = (
        "" if field == "shortGUID" else "unrelated"
    )
    session = cast(MagicMock, service.session)
    session.post.side_effect = [*responses, _deleted()]
    with pytest.raises(InvitesCreationError) as caught:
        service.create_event("Synthetic event", START_TIME)
    assert caught.value.cleanup_succeeded is True


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (200, {}, CloudKitApiError),
        (
            200,
            {"zones": [{"serverErrorCode": "AUTHENTICATION_REQUIRED"}]},
            CloudKitAuthError,
        ),
        (
            200,
            {"zones": [{"serverErrorCode": "THROTTLED", "retryAfter": 3}]},
            CloudKitRateLimited,
        ),
        (200, {"zones": [{"serverErrorCode": "BAD_REQUEST"}]}, CloudKitApiError),
        (429, {}, CloudKitRateLimited),
    ],
)
def test_common_zone_modify_errors(
    status: int, payload: Any, error: type[Exception]
) -> None:
    """Zone modifications share the common transport and per-zone error mapping."""
    session = MagicMock()
    session.post.return_value = _response(payload, status)
    client = CloudKitContainerClient("https://example.test/private", session, {})
    operation = CKZoneModifyOperation(
        operationType="create",
        zone=CKZoneListZone(zoneID=CKZoneID(zoneName=EVENT_ID)),
    )
    with pytest.raises(error):
        client.zones_modify([operation])
