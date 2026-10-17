"""Offline one-time-link creation through the real client/serializer stack."""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any, cast
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from requests.exceptions import Timeout

from pyicloud.common.cloudkit import CKLookupResponse, CKModifyResponse, CKRecord
from pyicloud.services.invites import (
    Event,
    EventScope,
    InvitesApiError,
    InvitesAuthError,
    InvitesInvitationError,
    InvitesRateLimited,
    InvitesService,
    OneTimeLinkGuest,
    OneTimeLinkInvitation,
)
from tests.fixture_loader import load_fixture

PARTICIPANT_ID = "00000000-0000-4000-8000-000000000003"


def _response(payload: Any, status: int = 200) -> MagicMock:
    response = MagicMock(status_code=status, headers={})
    response.json.return_value = payload
    return response


def _lookup() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        load_fixture("invites", "one_time_link_share_lookup_response.json"),
    )


def _modified() -> dict[str, Any]:
    return cast(
        dict[str, Any], load_fixture("invites", "one_time_link_modify_response.json")
    )


@pytest.fixture(name="service")
def invites_service(monkeypatch: pytest.MonkeyPatch) -> InvitesService:
    """Never call Apple or obtain a live entitlement; use a deterministic UUID."""
    monkeypatch.setattr(
        "pyicloud.services.invites.service.uuid4", lambda: PARTICIPANT_ID
    )
    return InvitesService("https://example.test", MagicMock(), {})


@pytest.fixture(name="event")
def hosted_event() -> Event:
    """An event DTO intentionally lacking both a share and an EventDetails tag."""
    return Event(event_id="EVENT-FIXTURE", scope=EventScope.PRIVATE)


def _invite(service: InvitesService, event: Event) -> OneTimeLinkInvitation:
    return service.invite_via_link(
        event, emails=["guest@example.test"], name="Synthetic guest"
    )


def _set(payload: dict[str, Any], path: Sequence[str | int], value: Any) -> None:
    node: Any = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def test_invitation_serializes_capture_shape(
    service: InvitesService, event: Event
) -> None:
    """Pin operations, fresh tag, encrypted metadata, owner and link request."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_response(_lookup()), _response(_modified())]
    invitation = _invite(service, event)
    expected = load_fixture("invites", "one_time_link_modify_request.json")
    assert session.post.call_args.kwargs["json"] == expected
    assert session.post.call_count == 2
    first, second = session.post.call_args_list
    assert first.kwargs["json"]["records"] == [{"recordName": "cloudkit.zoneshare"}]
    assert urlsplit(first.args[0]).path.endswith("/private/records/lookup")
    assert urlsplit(second.args[0]).path.endswith("/private/records/modify")
    for call in session.post.call_args_list:
        assert call.kwargs["timeout"] == (10.0, 60.0)
        assert parse_qs(urlsplit(call.args[0]).query) == {
            "remapEnums": ["true"],
            "getCurrentSyncToken": ["true"],
        }
    session.get.assert_not_called()
    assert isinstance(invitation, OneTimeLinkGuest)
    assert invitation.participant_id == PARTICIPANT_ID
    assert invitation.record_name == f"{PARTICIPANT_ID}_otl"
    assert invitation.emails == ("guest@example.test",)
    assert invitation.phone_numbers == ()
    assert invitation.name == "Synthetic guest"
    assert invitation.record_change_tag == "guest-created-fixture"
    assert invitation.share_record_change_tag == "share-after-fixture"
    assert invitation.url == "https://www.icloud.com/invites/new-invitation-token"
    assert invitation.short_guid == "new-invitation-token"
    assert invitation.short_guid not in repr(invitation)
    assert "prior-link-token" not in str(invitation)


@pytest.mark.parametrize("mode", ["allow", "ignore", "forbid"])
def test_typed_link_metadata_survives_validation_modes(mode: Any) -> None:
    """Link data must not depend on permissive preservation of untyped extras."""
    lookup = CKLookupResponse.model_validate(_lookup(), extra=mode)
    modified = CKModifyResponse.model_validate(_modified(), extra=mode)
    share = lookup.records[0]
    assert isinstance(share, CKRecord) and share.oneTimeStableUrlInfo is not None
    assert share.oneTimeStableUrlInfo.oneTimeLinks[0].sharingLink == "prior-link-token"
    updated_share = modified.records[1]
    assert (
        isinstance(updated_share, CKRecord)
        and updated_share.oneTimeStableUrlInfo is not None
    )
    assert updated_share.oneTimeStableUrlInfo.oneTimeLinks[0].participantId == [
        PARTICIPANT_ID
    ]


@pytest.mark.parametrize(
    ("emails", "phones"),
    [
        ([" guest@example.test ", "guest@example.test"], []),
        ([], ["+12025550123", " +12025550123 "]),
        (["guest@example.test"], ["+12025550123"]),
    ],
)
def test_contact_normalization_and_empty_name(
    service: InvitesService, event: Event, emails: list[str], phones: list[str]
) -> None:
    """Phone-only support is offline-tested, not a claim of live compatibility."""
    session = cast(MagicMock, service.session)
    modified = _modified()
    fields = modified["records"][0]["fields"]
    expected_emails = list(dict.fromkeys(value.strip() for value in emails))
    expected_phones = list(dict.fromkeys(value.strip() for value in phones))
    fields["name"]["value"] = ""
    fields["emails"]["value"] = expected_emails
    fields["phoneNumbers"]["value"] = expected_phones
    session.post.side_effect = [_response(_lookup()), _response(modified)]
    result = service.invite_via_link(event, emails=emails, phone_numbers=phones)
    assert result.emails == tuple(expected_emails)
    assert result.phone_numbers == tuple(expected_phones)
    assert result.name == ""


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"emails": "guest@example.test"},
        {"emails": None},
        {"emails": [None]},
        {"emails": [""]},
        {"emails": ["not-an-email"]},
        {"emails": ["Guest <guest@example.test>"]},
        {"emails": ["x@y.example\nextra"]},
        {"phone_numbers": "+12025550123"},
        {"phone_numbers": ["2025550123"]},
        {"phone_numbers": ["+0123456789"]},
        {"phone_numbers": ["+1202555012345678"]},
        {"emails": ["guest@example.test"], "name": None},
        {"emails": ["guest@example.test"], "name": "Guest\x00"},
    ],
)
def test_invalid_contacts_fail_before_network(
    service: InvitesService, event: Event, kwargs: dict[str, Any]
) -> None:
    """Bad inputs cannot mutate the event or generate links."""
    with pytest.raises(ValueError):
        service.invite_via_link(event, **kwargs)
    session = cast(MagicMock, service.session)
    session.post.assert_not_called()
    session.get.assert_not_called()


def test_shared_event_rejected(service: InvitesService, event: Event) -> None:
    """Accepted guests cannot create further invitations through this API."""
    with pytest.raises(InvitesApiError, match="hosted"):
        _invite(service, event.model_copy(update={"scope": EventScope.SHARED}))
    cast(MagicMock, service.session).post.assert_not_called()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("records",), []),
        (("records", 0, "recordName"), "unrelated"),
        (("records", 0, "recordType"), "unrelated"),
        (("records", 0, "recordChangeTag"), ""),
        (("records", 0, "participants"), None),
        (("records", 0, "currentUserParticipant"), None),
        (("records", 0, "currentUserParticipant", "type"), "USER"),
        (("records", 0, "currentUserParticipant", "participantId"), ""),
        (("records", 0, "currentUserParticipant", "permission"), "READ_ONLY"),
        (("records", 0, "currentUserParticipant", "acceptanceStatus"), "INVITED"),
        (("records", 0, "currentUserParticipant", "participantId"), "missing-owner"),
        (("records", 0, "participants", 1, "participantId"), "owner-fixture"),
        (("records", 0, "participants", 1, "participantId"), None),
        (("records", 0, "participants", 1, "userIdentity"), None),
        (("records", 0, "participants", 1, "permission"), None),
        (("records", 0, "participants", 1, "type"), None),
        (("records", 0, "participants", 1, "acceptanceStatus"), None),
        (
            ("records", 0, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "participantId"),
            [],
        ),
        (("records", 0, "oneTimeStableUrlInfo"), None),
        (
            ("records", 0, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "shortTokenHash"),
            None,
        ),
        (
            (
                "records",
                0,
                "oneTimeStableUrlInfo",
                "oneTimeLinks",
                0,
                "protectedFullToken",
            ),
            None,
        ),
        (
            (
                "records",
                0,
                "oneTimeStableUrlInfo",
                "oneTimeLinks",
                0,
                "encryptedFullTokenInfo",
            ),
            None,
        ),
    ],
)
def test_unusable_share_is_not_modified(
    service: InvitesService, event: Event, path: Sequence[str | int], value: Any
) -> None:
    """Use a current complete share; do not fall back to an event DTO."""
    payload = _lookup()
    _set(payload, path, value)
    session = cast(MagicMock, service.session)
    session.post.return_value = _response(payload)
    with pytest.raises(InvitesApiError):
        _invite(service, event)
    session.post.assert_called_once()


@pytest.mark.parametrize("collision", ["prior-guest-fixture", "link-only-participant"])
def test_generated_id_collision_sends_no_write(
    service: InvitesService,
    event: Event,
    monkeypatch: pytest.MonkeyPatch,
    collision: str,
) -> None:
    """A generated ID must not accidentally target an existing guest or link."""
    payload = _lookup()
    extra_link = deepcopy(
        payload["records"][0]["oneTimeStableUrlInfo"]["oneTimeLinks"][0]
    )
    extra_link["participantId"] = ["link-only-participant"]
    payload["records"][0]["oneTimeStableUrlInfo"]["oneTimeLinks"].append(extra_link)
    monkeypatch.setattr("pyicloud.services.invites.service.uuid4", lambda: collision)
    session = cast(MagicMock, service.session)
    session.post.return_value = _response(payload)
    with pytest.raises(InvitesApiError, match="already exists"):
        _invite(service, event)
    session.post.assert_called_once()


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, InvitesAuthError), (403, InvitesAuthError), (429, InvitesRateLimited)],
)
def test_lookup_errors_remain_specific(
    service: InvitesService, event: Event, status: int, error: type[Exception]
) -> None:
    """Failures before submission propagate without an uncertain-write wrapper."""
    session = cast(MagicMock, service.session)
    session.post.return_value = _response({}, status)
    with pytest.raises(error):
        _invite(service, event)
    session.post.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        (_response({}, 401), InvitesAuthError),
        (_response({}, 429), InvitesRateLimited),
        (_response({}, 500), InvitesApiError),
        (
            _response({
                "records": [
                    {
                        "recordName": "cloudkit.zoneshare",
                        "serverErrorCode": "CONFLICT",
                        "reason": "Stale tag",
                    }
                ]
            }),
            InvitesApiError,
        ),
        (
            _response({"records": [{"serverErrorCode": "ATOMIC_ERROR"}]}),
            InvitesApiError,
        ),
        (_response({"records": "invalid"}), InvitesApiError),
        (Timeout("Uncertain response"), Timeout),
    ],
)
def test_write_failures_retain_context_without_retry(
    service: InvitesService, event: Event, failure: Any, cause: type[Exception]
) -> None:
    """Original auth/rate-limit/conflict/transport errors remain in the cause chain."""
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_response(_lookup()), failure]
    with pytest.raises(InvitesInvitationError) as caught:
        _invite(service, event)
    assert isinstance(caught.value.__cause__, cause)
    assert caught.value.event_id == event.event_id
    assert caught.value.participant_id == PARTICIPANT_ID
    assert caught.value.record_name == f"{PARTICIPANT_ID}_otl"
    assert "inspect" in str(caught.value)
    assert session.post.call_count == 2


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("records", 0, "recordName"), "unrelated"),
        (("records", 0, "recordChangeTag"), None),
        (("records", 1, "recordChangeTag"), ""),
        (("records", 0, "fields", "name", "value"), "Other guest"),
        (("records", 0, "fields", "emails", "value"), ["other@example.test"]),
        (("records", 0, "fields", "phoneNumbers", "value"), ["+12025550123"]),
        (("records", 1, "participants", 1, "permission"), "READ_ONLY"),
        (("records", 1, "participants", 1, "participantId"), "missing-guest"),
        (("records", 1, "participants", 2, "type"), "OWNER"),
        (("records", 1, "participants", 2, "participantId"), "missing-new-guest"),
        (("records", 1, "participants", 2, "participantId"), "owner-fixture"),
        (("records", 1, "participants"), None),
        (("records", 1, "oneTimeStableUrlInfo"), None),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 1, "participantId"),
            ["missing-prior-link"],
        ),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 1, "sharingLink"),
            "changed-token",
        ),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 1, "shortTokenHash"),
            "changed-hash",
        ),
        (("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "sharingLink"), ""),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "sharingLink"),
            "https://example.test/token",
        ),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "sharingLink"),
            "event-general-token",
        ),
        (
            ("records", 1, "oneTimeStableUrlInfo", "oneTimeLinks", 0, "participantId"),
            ["unrelated"],
        ),
    ],
)
def test_incomplete_or_changed_success_is_not_retried(
    service: InvitesService, event: Event, path: Sequence[str | int], value: Any
) -> None:
    """A successful HTTP status alone cannot prove invitation consistency."""
    payload = _modified()
    _set(payload, path, value)
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_response(_lookup()), _response(payload)]
    with pytest.raises(InvitesInvitationError):
        _invite(service, event)
    assert session.post.call_count == 2


@pytest.mark.parametrize("target", ["records", "new_link", "prior_link", "lookup"])
def test_ambiguous_records_and_links_are_rejected(
    service: InvitesService, event: Event, target: str
) -> None:
    """Do not select an arbitrary first result when identities are duplicated."""
    lookup, modified = _lookup(), _modified()
    if target == "lookup":
        lookup["records"].append(deepcopy(lookup["records"][0]))
    elif target == "records":
        modified["records"].append(deepcopy(modified["records"][0]))
    else:
        links = modified["records"][1]["oneTimeStableUrlInfo"]["oneTimeLinks"]
        links.append(deepcopy(links[0 if target == "new_link" else 1]))
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_response(lookup), _response(modified)]
    with pytest.raises(InvitesApiError):
        _invite(service, event)
    assert session.post.call_count == (1 if target == "lookup" else 2)


@pytest.mark.parametrize("metadata", [None, {"oneTimeLinks": []}])
def test_first_invitation_without_previous_links(
    service: InvitesService, event: Event, metadata: Any
) -> None:
    """Absent/empty link metadata is normal for the first invitation."""
    lookup, modified = _lookup(), _modified()
    lookup["records"][0]["oneTimeStableUrlInfo"] = metadata
    lookup["records"][0]["participants"].pop()
    modified["records"][1]["participants"].pop(1)
    modified["records"][1]["oneTimeStableUrlInfo"]["oneTimeLinks"].pop()
    session = cast(MagicMock, service.session)
    session.post.side_effect = [_response(lookup), _response(modified)]
    assert _invite(service, event).short_guid == "new-invitation-token"


def test_custom_roles_and_second_invitation_use_fresh_share(
    service: InvitesService, event: Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: anonymous guests need their existing link metadata on updates."""
    lookup, first = _lookup(), _modified()
    lookup["records"][0]["participants"][1]["customRole"] = "fixture-role"
    first["records"][1]["participants"][1]["customRole"] = "fixture-role"
    second_lookup = {"records": [deepcopy(first["records"][1])]}
    second_lookup["records"][0]["currentUserParticipant"] = deepcopy(
        lookup["records"][0]["currentUserParticipant"]
    )
    second = deepcopy(first)
    second_id = "00000000-0000-4000-8000-000000000004"
    second["records"][0]["recordName"] = f"{second_id}_otl"
    second["records"][1]["recordChangeTag"] = "share-second-fixture"
    participant = deepcopy(second["records"][1]["participants"][-1])
    participant["participantId"] = second_id
    second["records"][1]["participants"].append(participant)
    second["records"][1]["oneTimeStableUrlInfo"]["oneTimeLinks"].append({
        "participantId": [second_id],
        "sharingLink": "second-invitation-token",
    })
    ids = iter([PARTICIPANT_ID, second_id])
    monkeypatch.setattr("pyicloud.services.invites.service.uuid4", lambda: next(ids))
    session = cast(MagicMock, service.session)
    session.post.side_effect = [
        _response(lookup),
        _response(first),
        _response(second_lookup),
        _response(second),
    ]
    initial = _invite(service, event)
    another = _invite(service, event)
    assert initial.url != another.url
    assert another.share_record_change_tag == "share-second-fixture"
    written_share = session.post.call_args.kwargs["json"]["operations"][1]["record"]
    assert written_share["recordChangeTag"] == "share-after-fixture"
    assert len(written_share["participants"]) == 4
    assert written_share["participants"][1]["customRole"] == "fixture-role"
    written_links = written_share["oneTimeStableUrlInfo"]["oneTimeLinks"]
    assert (
        written_links[:-1]
        == first["records"][1]["oneTimeStableUrlInfo"]["oneTimeLinks"]
    )
    assert written_links[-1] == {"participantId": [second_id]}
