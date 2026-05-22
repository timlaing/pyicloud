"""Tests for the Invites service."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKModifyResponse,
    CKQueryResponse,
)
from pyicloud.services.invites import (
    AcceptanceStatus,
    Event,
    EventScope,
    EventShare,
    EventTime,
    InvitesService,
    OneTimeLinkGuest,
    ParticipantType,
    Rsvp,
    RsvpStatus,
)
from pyicloud.services.invites.client import InvitesApiError
from pyicloud.services.invites.codecs import (
    decode_integrations,
    decode_json_bytes,
    encode_json_bytes,
)
from pyicloud.services.invites.service import EventNotFound

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "invites"


def load_invites_fixture(name: str) -> dict:
    """Load a synthetic Invites CloudKit fixture."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Codecs
# ---------------------------------------------------------------------------


class CodecsTest(unittest.TestCase):
    """Tests for invite codecs."""

    def test_decode_json_bytes_round_trip(self):
        original = {"startSince1970": 1768435200000, "isAllDay": False}
        enc = encode_json_bytes(original)
        self.assertEqual(decode_json_bytes(enc), original)

    def test_decode_json_bytes_none_input(self):
        self.assertIsNone(decode_json_bytes(None))

    def test_decode_json_bytes_invalid_base64(self):
        self.assertIsNone(decode_json_bytes("not base64!!"))

    def test_decode_json_bytes_invalid_json(self):
        # Valid base64, but the decoded payload is not JSON.
        bogus = "bm90LWpzb24="  # base64 of "not-json"
        self.assertIsNone(decode_json_bytes(bogus))

    def test_decode_json_bytes_accepts_bytes(self):
        self.assertEqual(decode_json_bytes(b'{"k": 1}'), {"k": 1})

    def test_decode_json_bytes_accepts_base64_bytes(self):
        # Bytes carrying the base64-encoded wire form decode the same as
        # the str path. Catches callers passing the wire form as bytes.
        encoded = encode_json_bytes({"k": 1}).encode("ascii")
        self.assertEqual(decode_json_bytes(encoded), {"k": 1})

    def test_decode_integrations_extracts_types(self):
        blob = {
            "version": "1",
            "data": [
                {"type": "com.apple.widget.weather"},
                {"type": "com.apple.widget.photos"},
                {"not_type": "ignored"},
            ],
        }
        self.assertEqual(
            decode_integrations(blob),
            ("com.apple.widget.weather", "com.apple.widget.photos"),
        )

    def test_decode_integrations_handles_missing(self):
        self.assertEqual(decode_integrations(None), ())
        self.assertEqual(decode_integrations({}), ())
        self.assertEqual(decode_integrations({"data": "not-a-list"}), ())


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class DtoTest(unittest.TestCase):
    """Tests for invites data transfer objects."""

    def test_rsvp_status_enum(self):
        self.assertEqual(int(RsvpStatus.NO_RESPONSE), 0)
        self.assertEqual(int(RsvpStatus.NOT_GOING), 1)
        self.assertEqual(int(RsvpStatus.MAYBE), 2)
        self.assertEqual(int(RsvpStatus.GOING), 3)

    def test_event_share_url(self):
        share = EventShare(short_guid="008ABC", public_permission="NONE")
        self.assertEqual(share.url, "https://www.icloud.com/invites/008ABC")

    def test_event_is_frozen(self):
        event = Event(
            event_id="x",
            scope=EventScope.PRIVATE,
            time=EventTime(start=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )
        with self.assertRaises(Exception):
            event.title = "mutated"  # type: ignore[misc]

    def test_invites_dtos_serialize(self):
        share = EventShare(short_guid="008XYZ", public_permission="READ_WRITE")
        data = share.model_dump()
        self.assertEqual(data["short_guid"], "008XYZ")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class InvitesServiceTest(unittest.TestCase):
    """Tests for Invites service."""

    def setUp(self):
        self.service = InvitesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )

    def _events_query_response(self) -> CKQueryResponse:
        return CKQueryResponse.model_validate(
            load_invites_fixture("events_query_response.json")
        )

    def _event_lookup_response(self) -> CKLookupResponse:
        return CKLookupResponse.model_validate(
            load_invites_fixture("event_lookup_response.json")
        )

    def _rsvp_query_response(self) -> CKQueryResponse:
        return CKQueryResponse.model_validate(
            load_invites_fixture("rsvp_query_response.json")
        )

    def _otl_empty_response(self) -> CKQueryResponse:
        return CKQueryResponse.model_validate(
            load_invites_fixture("one_time_link_query_empty_response.json")
        )

    def test_events_returns_dtos_from_private_query(self):
        # `events()` queries both private and shared. We return the events from
        # the private side and an empty record set for shared.
        empty = CKQueryResponse(records=[])
        self.service.raw.query = MagicMock(
            side_effect=[self._events_query_response(), empty]
        )

        events = self.service.events()

        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(first.event_id, "EVENT-FIXTURE-AAAA")
        self.assertEqual(first.scope, EventScope.PRIVATE)
        self.assertEqual(first.title, "Event Fixture A")
        self.assertTrue(first.is_published)
        self.assertFalse(first.is_private)
        self.assertFalse(first.is_cancelled)
        self.assertEqual(first.max_attendees, 100)

        # Decoded structured fields
        self.assertIsNotNone(first.time)
        self.assertEqual(
            first.time.start,
            datetime.fromtimestamp(1768435200, tz=timezone.utc),
        )
        self.assertEqual(
            first.time.end,
            datetime.fromtimestamp(1768446000, tz=timezone.utc),
        )
        self.assertFalse(first.time.is_all_day)
        self.assertFalse(first.time.is_open_ended)

        self.assertIsNotNone(first.place)
        self.assertEqual(first.place.city, "Fixture City")
        self.assertEqual(first.place.time_zone_identifier, "Europe/Paris")
        self.assertEqual(first.place.latitude, 48.8566)

        self.assertEqual(
            first.integrations,
            ("com.apple.widget.weather", "com.apple.widget.location"),
        )
        # No share / rsvps in the lightweight list view
        self.assertIsNone(first.share)
        self.assertEqual(first.rsvps, ())

        # Second event has the minimal "tz-only place" form
        second = events[1]
        self.assertEqual(second.event_id, "EVENT-FIXTURE-BBBB")
        self.assertFalse(second.is_published)
        self.assertIsNotNone(second.place)
        assert second.place is not None  # narrow for type checker
        self.assertIsNone(second.place.latitude)
        self.assertEqual(second.place.city, "Fixture City")
        self.assertIsNotNone(second.time)
        assert second.time is not None
        self.assertTrue(second.time.is_open_ended)

    def test_events_merges_private_and_shared_with_dedup(self):
        # Same event appears in both scopes (defensive dedup). Both passes
        # should yield distinct (scope, event_id) keys.
        first_resp = self._events_query_response()
        # Build a shared-scope response with overlapping records.
        second_resp = self._events_query_response()
        self.service.raw.query = MagicMock(side_effect=[first_resp, second_resp])

        events = self.service.events()

        # Two distinct events per scope (private + shared) → 4 total, all unique.
        self.assertEqual(len(events), 4)
        keys = {(e.scope, e.event_id) for e in events}
        self.assertEqual(len(keys), 4)

    def test_event_full_lookup_includes_share_and_rsvps(self):
        # Service tries private first; lookup returns event + share, RSVP
        # query returns one RSVP, OTL query returns empty.
        self.service.raw.lookup = MagicMock(return_value=self._event_lookup_response())
        self.service.raw.query = MagicMock(
            side_effect=[self._rsvp_query_response(), self._otl_empty_response()]
        )

        event = self.service.event("EVENT-FIXTURE-AAAA")

        self.assertEqual(event.event_id, "EVENT-FIXTURE-AAAA")
        self.assertEqual(event.scope, EventScope.PRIVATE)
        self.assertIsNotNone(event.share)
        self.assertEqual(event.share.short_guid, "008TESTFIXTUREAAAA")
        self.assertEqual(
            event.share.url,
            "https://www.icloud.com/invites/008TESTFIXTUREAAAA",
        )
        self.assertEqual(event.share.public_permission, "READ_WRITE")
        self.assertEqual(len(event.share.participants), 2)

        owner = event.share.participants[0]
        self.assertEqual(owner.type, ParticipantType.OWNER)
        self.assertEqual(owner.acceptance_status, AcceptanceStatus.ACCEPTED)
        self.assertEqual(owner.email, "owner@example.com")
        self.assertEqual(owner.given_name, "Owner")

        guest = event.share.participants[1]
        self.assertEqual(guest.type, ParticipantType.PUBLIC_USER)
        self.assertEqual(guest.email, "guest@example.com")

        self.assertEqual(len(event.rsvps), 1)
        rsvp = event.rsvps[0]
        self.assertEqual(rsvp.status, RsvpStatus.GOING)
        self.assertEqual(rsvp.name, "Fixture Guest")
        self.assertEqual(rsvp.message, "Looking forward to it!")
        self.assertEqual(rsvp.num_additional_adults, 1)
        self.assertEqual(rsvp.num_additional_kids, 0)
        self.assertEqual(rsvp.participant_id, "PARTICIPANT-FIXTURE-GUEST")

    def test_event_falls_through_to_shared_on_private_miss(self):
        # Private lookup returns no matching records; shared lookup succeeds.
        empty_lookup = CKLookupResponse(records=[])
        self.service.raw.lookup = MagicMock(
            side_effect=[empty_lookup, self._event_lookup_response()]
        )
        # The shared-path full hydration also issues RSVP + OTL queries.
        self.service.raw.query = MagicMock(
            side_effect=[self._rsvp_query_response(), self._otl_empty_response()]
        )

        event = self.service.event("EVENT-FIXTURE-AAAA")
        self.assertEqual(event.scope, EventScope.SHARED)

    def test_event_missing_raises(self):
        empty_lookup = CKLookupResponse(records=[])
        self.service.raw.lookup = MagicMock(return_value=empty_lookup)
        with self.assertRaises(EventNotFound):
            self.service.event("NO-SUCH-EVENT")

    def test_rsvps_returns_dtos(self):
        self.service.raw.query = MagicMock(return_value=self._rsvp_query_response())
        owner_event = Event(
            event_id="EVENT-FIXTURE-AAAA",
            scope=EventScope.PRIVATE,
            time=EventTime(start=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )

        rsvps = self.service.rsvps(owner_event)

        self.assertEqual(len(rsvps), 1)
        rsvp = rsvps[0]
        self.assertIsInstance(rsvp, Rsvp)
        self.assertEqual(rsvp.status, RsvpStatus.GOING)
        self.assertEqual(rsvp.participant_id, "PARTICIPANT-FIXTURE-GUEST")
        self.assertEqual(rsvp.num_additional_adults, 1)
        # rsvps() uses the event's scope to pick the right sub-client
        self.service.raw.query.assert_called_once()
        call = self.service.raw.query.call_args
        self.assertEqual(call.args[0], "private")

    def test_resolve_returns_resolved_share(self):
        self.service.raw.resolve = MagicMock(
            return_value=load_invites_fixture("resolve_response.json")
        )

        resolved = self.service.resolve("008TESTFIXTUREAAAA")

        self.assertEqual(resolved.short_guid, "008TESTFIXTUREAAAA")
        self.assertEqual(resolved.event_id, "EVENT-FIXTURE-AAAA")
        self.assertEqual(resolved.owner_email, "owner@example.com")
        self.assertEqual(resolved.owner_given_name, "Owner")
        self.assertEqual(resolved.participant_type, "OWNER")
        self.assertEqual(resolved.participant_status, "ACCEPTED")
        self.assertEqual(
            resolved.share.url,
            "https://www.icloud.com/invites/008TESTFIXTUREAAAA",
        )

    def test_accept_returns_full_event(self):
        # accept() POSTs to public, then fetches the full event from SHARED.
        self.service.raw.accept = MagicMock(
            return_value=load_invites_fixture("accept_response.json")
        )
        self.service.raw.lookup = MagicMock(return_value=self._event_lookup_response())
        self.service.raw.query = MagicMock(
            side_effect=[self._rsvp_query_response(), self._otl_empty_response()]
        )

        event = self.service.accept("008TESTFIXTUREAAAA")

        self.assertEqual(event.event_id, "EVENT-FIXTURE-AAAA")
        self.assertEqual(event.scope, EventScope.SHARED)
        self.assertIsNotNone(event.share)
        self.assertEqual(event.share.short_guid, "008TESTFIXTUREAAAA")
        # lookup was called against the shared sub-client first.
        first_lookup_scope = self.service.raw.lookup.call_args_list[0].args[0]
        self.assertEqual(first_lookup_scope, "shared")


# ---------------------------------------------------------------------------
# OneTimeLink DTO construction (the empty fixture case is exercised above;
# this directly tests the dataclass shape).
# ---------------------------------------------------------------------------


class OneTimeLinkGuestTest(unittest.TestCase):
    """Tests for OneTimeLink guest models."""

    def test_default_collections_are_empty_tuples(self):
        otl = OneTimeLinkGuest(
            record_name="PARTICIPANT-X_otl",
            participant_id="PARTICIPANT-X",
        )
        self.assertEqual(otl.emails, ())
        self.assertEqual(otl.phone_numbers, ())
        self.assertEqual(otl.name, "")


# ---------------------------------------------------------------------------
# Service writes (Phase 2)
# ---------------------------------------------------------------------------


def _make_event_with_share(
    *,
    scope: EventScope = EventScope.SHARED,
    rsvps: tuple[Rsvp, ...] = (),
    current_user_participant_id: str = "PARTICIPANT-FIXTURE-GUEST",
) -> Event:
    """Build a minimal Event with a share pre-populated for write tests."""
    share = EventShare(
        short_guid="008TESTFIXTUREAAAA",
        public_permission="READ_WRITE",
        current_user_participant_id=current_user_participant_id,
    )
    return Event(
        event_id="EVENT-FIXTURE-AAAA",
        scope=scope,
        time=EventTime(start=datetime(2026, 1, 15, tzinfo=timezone.utc)),
        share=share,
        rsvps=rsvps,
    )


def _existing_going_rsvp() -> Rsvp:
    return Rsvp(
        record_name="PARTICIPANT-FIXTURE-GUEST_rsvp",
        participant_id="PARTICIPANT-FIXTURE-GUEST",
        name="Fixture Guest",
        status=RsvpStatus.GOING,
        message="Looking forward to it!",
        num_additional_adults=1,
        num_additional_kids=0,
        record_change_tag="rsvpFixture1",
    )


class RsvpWriteTest(unittest.TestCase):
    def setUp(self):
        self.service = InvitesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )
        self.modify_response = CKModifyResponse.model_validate(
            load_invites_fixture("rsvp_modify_response.json")
        )

    def test_rsvp_update_uses_existing_change_tag_and_update_op(self):
        existing = _existing_going_rsvp()
        event = _make_event_with_share(rsvps=(existing,))
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        result = self.service.rsvp(
            event,
            RsvpStatus.MAYBE,
            message="Tentative",
        )

        self.assertIsInstance(result, Rsvp)
        self.assertEqual(result.status, RsvpStatus.MAYBE)
        self.assertEqual(result.record_change_tag, "rsvpFixture2")

        call = self.service.raw.modify.call_args
        self.assertEqual(call.args[0], "shared")
        ops = call.kwargs["operations"]
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op.operationType, "update")
        record = op.record
        self.assertEqual(record.recordName, "PARTICIPANT-FIXTURE-GUEST_rsvp")
        self.assertEqual(record.recordType, "RSVP")
        self.assertEqual(record.recordChangeTag, "rsvpFixture1")
        # Status field wrapped as encrypted INT64.
        status_field = record.fields.get("status")
        self.assertIsNotNone(status_field)
        assert status_field is not None
        self.assertEqual(status_field.value, 2)

    def test_rsvp_first_response_creates_record(self):
        # No existing RSVP in event.rsvps → create op, no recordChangeTag.
        event = _make_event_with_share(rsvps=())
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        self.service.rsvp(event, RsvpStatus.GOING, name="Fixture Guest")

        op = self.service.raw.modify.call_args.kwargs["operations"][0]
        self.assertEqual(op.operationType, "create")
        self.assertIsNone(op.record.recordChangeTag)

    def test_rsvp_not_going_zeros_plus_ones(self):
        existing = _existing_going_rsvp()
        event = _make_event_with_share(rsvps=(existing,))
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        self.service.rsvp(
            event,
            RsvpStatus.NOT_GOING,
            plus_one_adults=2,  # caller-supplied counts must be ignored
            plus_one_kids=1,
        )

        record = self.service.raw.modify.call_args.kwargs["operations"][0].record
        adults = record.fields.get("numAdditionalAdults")
        kids = record.fields.get("numAdditionalKids")
        guests = record.fields.get("numAdditionalGuests")
        assert adults is not None and kids is not None and guests is not None
        self.assertEqual(adults.value, 0)
        self.assertEqual(kids.value, 0)
        self.assertEqual(guests.value, 0)

    def test_rsvp_plus_one_counts_sum_to_total(self):
        event = _make_event_with_share()
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        self.service.rsvp(
            event,
            RsvpStatus.GOING,
            plus_one_adults=2,
            plus_one_kids=3,
        )

        record = self.service.raw.modify.call_args.kwargs["operations"][0].record
        guests = record.fields.get("numAdditionalGuests")
        assert guests is not None
        self.assertEqual(guests.value, 5)

    def test_rsvp_owner_dispatches_to_private_scope(self):
        owner_event = _make_event_with_share(
            scope=EventScope.PRIVATE,
            current_user_participant_id="PARTICIPANT-FIXTURE-OWNER",
        )
        # Tailor the response so the modify helper can locate the owner's record.
        owner_response = self.modify_response.model_copy(deep=True)
        owner_response.records[0].recordName = "PARTICIPANT-FIXTURE-OWNER_rsvp"
        self.service.raw.modify = MagicMock(return_value=owner_response)

        self.service.rsvp(owner_event, RsvpStatus.GOING)

        self.assertEqual(
            self.service.raw.modify.call_args.args[0],
            "private",
        )

    def test_rsvp_omits_name_and_message_when_not_provided(self):
        event = _make_event_with_share()
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        self.service.rsvp(event, RsvpStatus.MAYBE)

        record = self.service.raw.modify.call_args.kwargs["operations"][0].record
        self.assertNotIn("name", record.fields)
        self.assertNotIn("message", record.fields)

    def test_rsvp_includes_name_and_message_when_provided(self):
        event = _make_event_with_share()
        self.service.raw.modify = MagicMock(return_value=self.modify_response)

        self.service.rsvp(
            event,
            RsvpStatus.GOING,
            name="Fixture Guest",
            message="See you there",
        )

        record = self.service.raw.modify.call_args.kwargs["operations"][0].record
        name_field = record.fields.get("name")
        message_field = record.fields.get("message")
        assert name_field is not None and message_field is not None
        self.assertEqual(name_field.value, "Fixture Guest")
        self.assertEqual(message_field.value, "See you there")

    def test_rsvp_raises_when_share_not_loaded(self):
        event = Event(
            event_id="EVENT-FIXTURE-AAAA",
            scope=EventScope.SHARED,
            time=EventTime(start=datetime(2026, 1, 15, tzinfo=timezone.utc)),
            share=None,
        )
        with self.assertRaises(InvitesApiError):
            self.service.rsvp(event, RsvpStatus.GOING)

    def test_rsvp_raises_when_share_has_no_current_participant(self):
        event = _make_event_with_share(current_user_participant_id="")
        with self.assertRaises(InvitesApiError):
            self.service.rsvp(event, RsvpStatus.GOING)

    def test_rsvp_rejects_negative_plus_one_counts(self):
        event = _make_event_with_share()
        # Sentinel modify mock that should never be reached.
        self.service.raw.modify = MagicMock()

        with self.assertRaises(InvitesApiError):
            self.service.rsvp(event, RsvpStatus.GOING, plus_one_adults=-1)
        with self.assertRaises(InvitesApiError):
            self.service.rsvp(event, RsvpStatus.GOING, plus_one_kids=-1)

        # Validation must run before any wire call.
        self.service.raw.modify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
