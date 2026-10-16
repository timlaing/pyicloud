"""Publication writes through the full Invites client and serializer stack."""

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from pyicloud.services.invites import Event, EventScope, InvitesService
from pyicloud.services.invites.client import (
    InvitesApiError,
    InvitesAuthError,
    InvitesEntitlementError,
    InvitesRateLimited,
)
from pyicloud.services.invites.entitlement import CREATE_EVENT_FEATURE, FeatureAccess
from tests.fixture_loader import load_fixture


def _response(payload: Any, status: int = 200) -> MagicMock:
    response = MagicMock(status_code=status, headers={})
    response.json.return_value = payload
    return response


@pytest.fixture(name="service")
def invites_service() -> InvitesService:
    """Use a fake session and a synthetic cached entitlement; never call Apple."""
    result = InvitesService("https://example.test", MagicMock(), {})
    result._feature_access = FeatureAccess(  # pylint: disable=protected-access
        feature_key=CREATE_EVENT_FEATURE,
        can_use=True,
        access_token="token-fixture",
        cache_till=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    return result


@pytest.fixture(name="event")
def draft_event() -> Event:
    """A hosted draft with a known event change tag."""
    return Event(
        event_id="EVENT-FIXTURE-AAAA",
        scope=EventScope.PRIVATE,
        record_change_tag="fixtureA1",
    )


def _share_lookup(tag: str | None = "shareFixtureA1") -> MagicMock:
    return _response({
        "records": [
            {
                "recordName": "cloudkit.zoneshare",
                "recordType": "cloudkit.share",
                "recordChangeTag": tag,
                "fields": {},
            }
        ]
    })


@pytest.mark.parametrize("published", [True, False])
def test_publish_serializes_atomic_dual_write(
    service: InvitesService, event: Event, published: bool
) -> None:
    """Pin the encryption marker, plain share flag, hint, tags, and query mode."""
    session = cast(MagicMock, service.session)
    payload = load_fixture("invites", "event_publish_modify_response.json")
    expected = load_fixture("invites", "event_publish_modify_request.json")
    for record in payload["records"]:
        record["fields"]["isPublished"]["value"] = int(published)
    for operation in expected["operations"]:
        operation["record"]["fields"]["isPublished"]["value"] = int(published)
    session.post.side_effect = [_share_lookup(), _response(payload)]

    result = service.publish(event, published=published)

    assert result.is_published is published
    assert result.record_change_tag == "fixtureA2"
    assert result.share is None
    assert not result.rsvps
    call = session.post.call_args
    assert call.kwargs["json"] == expected
    assert urlsplit(call.args[0]).path.endswith("/private/records/modify")
    assert parse_qs(urlsplit(call.args[0]).query) == {
        "remapEnums": ["true"],
        "getCurrentSyncToken": ["true"],
    }


def test_publish_default_and_returned_tag_can_be_chained(
    service: InvitesService, event: Event
) -> None:
    """A second write must use both the returned event tag and a fresh share tag."""
    session = cast(MagicMock, service.session)
    first = load_fixture("invites", "event_publish_modify_response.json")
    second = load_fixture("invites", "event_publish_modify_response.json")
    second["records"][0]["recordChangeTag"] = "fixtureA3"
    for record in second["records"]:
        record["fields"]["isPublished"]["value"] = 0
    session.post.side_effect = [
        _share_lookup(),
        _response(first),
        _share_lookup("shareFixtureA2"),
        _response(second),
    ]

    published = service.publish(event)
    restored = service.publish(published, published=False)

    assert published.is_published
    assert not restored.is_published
    assert restored.record_change_tag == "fixtureA3"
    operations = session.post.call_args.kwargs["json"]["operations"]
    assert [op["record"]["recordChangeTag"] for op in operations] == [
        "fixtureA2",
        "shareFixtureA2",
    ]


@pytest.mark.parametrize(
    "change", [{"scope": EventScope.SHARED}, {"record_change_tag": None}]
)
def test_publish_rejects_invalid_event_before_network(
    service: InvitesService, event: Event, change: dict[str, Any]
) -> None:
    """Guests and events lacking optimistic-concurrency metadata cannot publish."""
    with pytest.raises(InvitesApiError):
        service.publish(event.model_copy(update=change))
    cast(MagicMock, service.session).post.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        _response({"records": []}),
        _share_lookup(None),
        _share_lookup(""),
        _response({"reason": "unreadable share"}, status=500),
    ],
)
def test_publish_refuses_to_modify_without_share_tag(
    service: InvitesService, event: Event, response: MagicMock
) -> None:
    """Do not inherit cancellation's event-only fallback for publication."""
    session = cast(MagicMock, service.session)
    session.post.return_value = response
    with pytest.raises(InvitesApiError, match="no changes were sent"):
        service.publish(event)
    session.post.assert_called_once()
    assert session.post.call_args.args[0].split("?")[0].endswith("/records/lookup")


def test_publish_refuses_missing_entitlement(
    service: InvitesService, event: Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entitlement gate applies before any CloudKit modification."""
    service._feature_access = None  # pylint: disable=protected-access
    monkeypatch.setattr(
        service.raw,
        "feature_access",
        lambda _: FeatureAccess(feature_key=CREATE_EVENT_FEATURE, can_use=False),
    )
    with pytest.raises(InvitesEntitlementError):
        service.publish(event)
    cast(MagicMock, service.session).post.assert_not_called()


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (401, {}, InvitesAuthError),
        (429, {}, InvitesRateLimited),
        (
            200,
            {"records": [{"serverErrorCode": "CONFLICT", "reason": "Stale tag"}]},
            InvitesApiError,
        ),
        (200, {"records": []}, InvitesApiError),
    ],
)
def test_publish_propagates_modify_failures(
    service: InvitesService,
    event: Event,
    status: int,
    payload: dict[str, Any],
    error: type[Exception],
) -> None:
    """HTTP 200 with a per-record rejection is not a successful publication."""
    cast(MagicMock, service.session).post.side_effect = [
        _share_lookup(),
        _response(payload, status),
    ]
    with pytest.raises(error):
        service.publish(event)
