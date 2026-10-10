"""Coverage-focused tests for the Invites service internals.

These tests exist to lift branch coverage of ``pyicloud/services/invites/``
(``client.py``, ``service.py``, ``codecs.py``) above 90% as part of
"Ensure all files have >90% branch coverage" (issue #394). They exercise
branches the functional tests in :mod:`tests.test_invites` do not reach:
defensive fallbacks, transport-error translation, dedup paths, and the
raw client's HTTP helpers — all with mocked sessions (no network, no
filesystem access).
"""

# pylint: disable=protected-access

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKModifyOperation,
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKRecord,
    CKTombstoneRecord,
    CKWriteRecord,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CKZoneListResponse,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
)
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.invites import (
    Event,
    EventScope,
    EventShare,
    EventTime,
    InvitesService,
    Rsvp,
    RsvpStatus,
)
from pyicloud.services.invites.client import (
    CloudKitInvitesClient,
    InvitesApiError,
    InvitesAuthError,
    InvitesRateLimited,
)
from pyicloud.services.invites.codecs import (
    decode_integrations,
    decode_json_bytes,
    encode_json_bytes,
)
from pyicloud.services.invites.service import EventNotFound

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "invites"


def load_invites_fixture(name: str) -> Any:
    """Load a synthetic Invites CloudKit fixture."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class CodecsCoverageTest(unittest.TestCase):
    """Branch coverage for ``pyicloud/services/invites/codecs.py``."""

    def test_decode_json_bytes_non_text_types_return_none(self) -> None:
        """Values that are neither str nor bytes fall through to ``None``."""
        self.assertIsNone(decode_json_bytes(cast(Any, 123)))
        self.assertIsNone(decode_json_bytes(cast(Any, ["not", "json"])))

    def test_decode_json_bytes_non_utf8_base64_str_returns_none(self) -> None:
        """A str whose base64 decodes to non-UTF-8 bytes returns None."""
        # "////" is base64 of ``b"\\xff\\xff\\xff"`` — valid base64, bad UTF-8.
        self.assertIsNone(decode_json_bytes("////"))

    def test_decode_json_bytes_non_utf8_invalid_base64_bytes(self) -> None:
        """Bytes that are neither UTF-8 JSON nor valid base64 return None."""
        self.assertIsNone(decode_json_bytes(b"\xff\xff\xff"))

    def test_decode_json_bytes_bytearray_valid_base64_bad_json(self) -> None:
        """A bytearray whose base64 decodes to non-JSON still returns None."""
        # "AAAA" is valid base64 (three NUL bytes) but is not JSON.
        self.assertIsNone(decode_json_bytes(bytearray(b"AAAA")))

    def test_decode_integrations_skips_non_mapping_entries(self) -> None:
        """decode_integrations ignores non-dict entries and keeps iterating."""
        blob = {
            "version": "1",
            "data": [
                "not-a-dict",
                {"type": "com.apple.widget.music"},
                {"type": 99},
            ],
        }
        self.assertEqual(decode_integrations(blob), ("com.apple.widget.music",))


class RawClientCoverageTest(unittest.TestCase):
    """Branch coverage for ``pyicloud/services/invites/client.py``."""

    def setUp(self) -> None:
        self.session = MagicMock()
        self.client = CloudKitInvitesClient(
            "https://example.com/database/1/com.apple.icloud.events/production",
            session=self.session,
            base_params={"remapEnums": True},
        )
        self.private = MagicMock(spec=CloudKitContainerClient)
        self.shared = MagicMock(spec=CloudKitContainerClient)
        for patcher in (
            patch.object(self.client, "_private", self.private),
            patch.object(self.client, "_shared", self.shared),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_client_for_selects_subclient(self) -> None:
        """``_client_for`` maps both scopes and rejects unknown ones."""
        self.assertIs(self.client._client_for("private"), self.private)
        self.assertIs(self.client._client_for("shared"), self.shared)
        public = cast(Any, "public")
        with self.assertRaises(ValueError):
            self.client._client_for(public)

    def test_zones_list_passes_through_both_scopes(self) -> None:
        """zones_list returns the selected scope's response untouched."""
        zone_list = CKZoneListResponse.model_validate({"zones": []})
        self.private.zones_list.return_value = zone_list
        self.shared.zones_list.return_value = zone_list
        self.assertIs(self.client.zones_list("private"), zone_list)
        self.assertIs(self.client.zones_list("shared"), zone_list)

    def test_query_wrapper_forwards_to_scope(self) -> None:
        """query() forwards its keyword arguments to the matched sub-client."""
        resp = CKQueryResponse(records=[])
        self.private.query.return_value = resp
        zone_id = CKZoneIDReq(zoneName="EVENT", zoneType="REGULAR_CUSTOM_ZONE")
        result = self.client.query(
            "private",
            query=CKQueryObject(recordType="EventDetails"),
            zone_id=zone_id,
            desired_keys=["title"],
            results_limit=10,
            continuation="tok",
            zone_wide=True,
        )
        self.assertIs(result, resp)
        self.private.query.assert_called_once()
        forwarded = self.private.query.call_args.kwargs
        self.assertIs(forwarded["zone_id"], zone_id)
        self.assertTrue(forwarded["zone_wide"])
        self.assertEqual(forwarded["results_limit"], 10)
        self.assertEqual(forwarded["desired_keys"], ["title"])

    def test_lookup_wrapper_forwards_to_scope(self) -> None:
        """lookup() forwards record names and zone to the matched sub-client."""
        resp = CKLookupResponse(records=[])
        self.shared.lookup.return_value = resp
        zone_id = CKZoneIDReq(zoneName="EVENT", zoneType="REGULAR_CUSTOM_ZONE")
        result = self.client.lookup(
            "shared", ["EventDetails:X"], zone_id=zone_id, desired_keys=["title"]
        )
        self.assertIs(result, resp)
        self.shared.lookup.assert_called_once_with(
            ["EventDetails:X"], zone_id=zone_id, desired_keys=["title"]
        )

    def test_modify_wrapper_forwards_to_scope(self) -> None:
        """modify() forwards operations and zone to the matched sub-client."""
        resp = CKModifyResponse(records=[])
        self.private.modify.return_value = resp
        zone_id = CKZoneIDReq(zoneName="EVENT", zoneType="REGULAR_CUSTOM_ZONE")
        op = CKModifyOperation(
            operationType="create",
            record=CKWriteRecord(recordName="R", recordType="RSVP"),
        )
        result = self.client.modify(
            "private", operations=[op], zone_id=zone_id, atomic=True
        )
        self.assertIs(result, resp)
        self.private.modify.assert_called_once_with(
            operations=[op], zone_id=zone_id, atomic=True
        )

    def test_changes_wrapper_forwards_to_scope(self) -> None:
        """changes() forwards the zone request to the matched sub-client."""
        zone_req = CKZoneChangesZoneReq.model_validate({
            "zoneID": {"zoneName": "EVENT", "zoneType": "REGULAR_CUSTOM_ZONE"}
        })
        resp = MagicMock()
        self.shared.changes.return_value = resp
        result = self.client.changes("shared", zone_req=zone_req, results_limit=5)
        self.assertIs(result, resp)
        self.shared.changes.assert_called_once_with(zone_req=zone_req, results_limit=5)

    def test_iter_changes_yields_every_page(self) -> None:
        """iter_changes() yields each page produced by the sub-client."""
        zone_req = CKZoneChangesZoneReq.model_validate({
            "zoneID": {"zoneName": "EVENT", "zoneType": "REGULAR_CUSTOM_ZONE"}
        })
        self.shared.iter_changes.return_value = iter([MagicMock(), MagicMock()])
        pages = list(self.client.iter_changes("shared", zone_req=zone_req))
        self.assertEqual(len(pages), 2)
        self.shared.iter_changes.assert_called_once_with(
            zone_req=zone_req, results_limit=None
        )

    def test_wrappers_translate_transport_errors(self) -> None:
        """Each wrapper maps sub-client errors onto the Invites hierarchy."""
        query = CKQueryObject(recordType="EventDetails")
        self.private.query.side_effect = CloudKitAuthError("auth")
        with self.assertRaises(InvitesAuthError):
            self.client.query("private", query=query)

        self.private.query.side_effect = CloudKitRateLimited("429", retry_after=5.0)
        with self.assertRaises(InvitesRateLimited) as ctx:
            self.client.query("private", query=query)
        self.assertEqual(ctx.exception.retry_after, 5.0)

        self.private.query.side_effect = CloudKitApiError("boom", payload={"x": 1})
        with self.assertRaises(InvitesApiError) as api_ctx:
            self.client.query("private", query=query)
        self.assertEqual(api_ctx.exception.payload, {"x": 1})

        self.private.query.side_effect = PyiCloudAPIResponseException("Bad", 429)
        with self.assertRaises(InvitesRateLimited):
            self.client.query("private", query=query)

        zone_id = CKZoneIDReq(zoneName="EVENT", zoneType="REGULAR_CUSTOM_ZONE")
        zone_req = CKZoneChangesZoneReq.model_validate({
            "zoneID": {"zoneName": "EVENT", "zoneType": "REGULAR_CUSTOM_ZONE"}
        })
        self.shared.lookup.side_effect = CloudKitApiError("boom")
        with self.assertRaises(InvitesApiError):
            self.client.lookup("shared", ["EventDetails:X"], zone_id=zone_id)

        self.private.modify.side_effect = CloudKitApiError("boom")
        with self.assertRaises(InvitesApiError):
            self.client.modify("private", operations=[], zone_id=zone_id)

        self.shared.changes.side_effect = CloudKitApiError("boom")
        with self.assertRaises(InvitesApiError):
            self.client.changes("shared", zone_req=zone_req)

        self.shared.iter_changes.side_effect = CloudKitApiError("boom")
        with self.assertRaises(InvitesApiError):
            list(self.client.iter_changes("shared", zone_req=zone_req))

    def test_raise_invites_error_reraises_unknown_exceptions(self) -> None:
        """Exceptions matching no branch are re-raised untouched."""
        error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            CloudKitInvitesClient._raise_invites_error(error)

    def test_download_asset_bytes_proxies_and_translates(self) -> None:
        """download_asset_bytes proxies the private client and maps errors."""
        self.private.download_asset_bytes.return_value = b"data"
        self.assertEqual(
            self.client.download_asset_bytes("https://cvws.example.com/1"), b"data"
        )
        self.private.download_asset_bytes.side_effect = CloudKitApiError("boom")
        with self.assertRaises(InvitesApiError):
            self.client.download_asset_bytes("https://cvws.example.com/1")

    def test_resolve_and_accept_build_short_guid_payloads(self) -> None:
        """resolve()/accept() POST the shortGUIDs payload to the right path."""
        with patch.object(
            self.client, "_post_public", return_value={"results": []}
        ) as post:
            self.client.resolve(["guid-1"])
            post.assert_called_with(
                "/records/resolve", {"shortGUIDs": [{"value": "guid-1"}]}
            )
            self.client.accept(["guid-1", "guid-2"])
            post.assert_called_with(
                "/records/accept",
                {"shortGUIDs": [{"value": "guid-1"}, {"value": "guid-2"}]},
            )

    def test_post_public_success_with_params(self) -> None:
        """A 200 JSON response is returned and base params are urlencoded."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [{"ok": True}]}
        resp.headers = {}
        self.session.post.return_value = resp

        data = self.client._post_public(
            "/records/resolve", {"shortGUIDs": [{"value": "g"}]}
        )

        self.assertEqual(data, {"results": [{"ok": True}]})
        url: str = self.session.post.call_args.args[0]
        self.assertIn("/public/records/resolve", url)
        self.assertIn("remapEnums=true", url)
        self.session.post.assert_called_once()

    def test_post_public_success_without_params(self) -> None:
        """No query string is appended when there are no base params."""
        bare = CloudKitInvitesClient(
            "https://example.com/database/1/com.apple.icloud.events/production",
            session=self.session,
            base_params={},
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        self.session.post.return_value = resp

        bare._post_public("/records/resolve", {})

        url: str = self.session.post.call_args.args[0]
        self.assertEqual(
            url,
            "https://example.com/database/1/com.apple.icloud.events/production"
            "/public/records/resolve",
        )

    def test_post_public_unauthorized(self) -> None:
        """401 / 403 responses raise InvitesAuthError."""
        for code in (401, 403):
            resp = MagicMock()
            resp.status_code = code
            self.session.post.return_value = resp
            with self.subTest(code=code), self.assertRaises(InvitesAuthError):
                self.client._post_public("/records/resolve", {})

    def test_post_public_rate_limited_with_retry_after(self) -> None:
        """429 with a Retry-After header surfaces retry_after as a float."""
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"Retry-After": "5"}
        self.session.post.return_value = resp
        with self.assertRaises(InvitesRateLimited) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertEqual(ctx.exception.retry_after, 5.0)

    def test_post_public_rate_limited_invalid_retry_after(self) -> None:
        """429 with an unparsable Retry-After still raises with retry_after None."""
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"Retry-After": "soon"}
        self.session.post.return_value = resp
        with self.assertRaises(InvitesRateLimited) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertIsNone(ctx.exception.retry_after)

    def test_post_public_rate_limited_no_header(self) -> None:
        """429 without Retry-After raises with retry_after None."""
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {}
        self.session.post.return_value = resp
        with self.assertRaises(InvitesRateLimited) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertIsNone(ctx.exception.retry_after)

    def test_post_public_api_error_json_body(self) -> None:
        """4xx/5xx responses with a JSON body surface the parsed payload."""
        resp = MagicMock()
        resp.status_code = 500
        resp.json.return_value = {"error": "boom"}
        self.session.post.return_value = resp
        with self.assertRaises(InvitesApiError) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertEqual(ctx.exception.payload, {"error": "boom"})

    def test_post_public_api_error_text_body(self) -> None:
        """4xx/5xx responses without JSON surface the raw response text."""
        resp = MagicMock()
        resp.status_code = 400
        resp.json.side_effect = ValueError("no json")
        resp.text = "plain failure"
        self.session.post.return_value = resp
        with self.assertRaises(InvitesApiError) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertEqual(ctx.exception.payload, "plain failure")

    def test_post_public_invalid_json_response(self) -> None:
        """A 200 response whose body is not JSON raises InvitesApiError."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("no json")
        resp.text = "not json"
        self.session.post.return_value = resp
        with self.assertRaises(InvitesApiError) as ctx:
            self.client._post_public("/records/resolve", {})
        self.assertIn("Invalid JSON", str(ctx.exception))
        self.assertEqual(ctx.exception.payload, "not json")

    def test_post_public_non_int_status_code(self) -> None:
        """A non-int status code is treated as 200 (success path)."""
        resp = MagicMock()
        resp.status_code = "200"
        resp.json.return_value = {"ok": True}
        self.session.post.return_value = resp
        self.assertEqual(self.client._post_public("/records/resolve", {}), {"ok": True})

    def test_normalized_params_stringifies_values(self) -> None:
        """Bools become 'true'/'false'; everything else is stringified."""
        client = CloudKitInvitesClient(
            "https://example.com/db",
            session=self.session,
            base_params={"flag": True, "unset": False, "token": "abc"},
        )
        self.assertEqual(
            client._normalized_params(),
            {"flag": "true", "unset": "false", "token": "abc"},
        )


class ServiceCoverageTest(unittest.TestCase):
    """Branch coverage for ``pyicloud/services/invites/service.py``."""

    def setUp(self) -> None:
        self.service = InvitesService(
            service_root="https://example.com",
            session=MagicMock(),
            params={},
        )

    @pytest.fixture(autouse=True)
    def _monkeypatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Expose pytest's monkeypatch fixture to unittest-style tests."""
        self._monkeypatch = monkeypatch

    @staticmethod
    def _event_record(event_id: str) -> CKRecord:
        """A minimal EventDetails record with a decodable time field."""
        return CKRecord.model_validate({
            "recordName": f"EventDetails:{event_id}",
            "recordType": "EventDetails",
            "fields": {
                "title": {"value": f"Event {event_id}", "type": "STRING"},
                "isPublished": {"value": 1, "type": "INT64"},
                "time": {
                    "value": encode_json_bytes({"startSince1970": 1768435200000}),
                    "type": "ENCRYPTED_BYTES",
                },
            },
        })

    @staticmethod
    def _event_with_share(rsvps: tuple[Rsvp, ...] = ()) -> Event:
        """A minimal SHARED-scope event with a share pre-populated."""
        return Event(
            event_id="EVENT-FIXTURE-AAAA",
            scope=EventScope.SHARED,
            time=EventTime(start=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            share=EventShare(
                short_guid="008TESTFIXTUREAAAA",
                public_permission="READ_WRITE",
                current_user_participant_id="PARTICIPANT-FIXTURE-GUEST",
            ),
            rsvps=rsvps,
        )

    @staticmethod
    def _rsvp(record_name: str) -> Rsvp:
        """A minimal RSVP DTO for write-helper tests."""
        return Rsvp(
            record_name=record_name,
            participant_id=record_name,
            name="Guest",
            status=RsvpStatus.GOING,
            record_change_tag="tag",
        )

    def test_events_deduplicates_repeats_within_a_scope(self) -> None:
        """Repeated (scope, event_id) pairs are skipped via the seen set."""
        dup = self._event_record("EVENT-DUP")
        query_resp = CKQueryResponse.model_validate({
            "records": [dup.model_dump(mode="json", exclude_none=True)] * 2
        })
        self._monkeypatch.setattr(
            self.service.raw, "query", MagicMock(return_value=query_resp)
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "zones_list",
            MagicMock(return_value=CKZoneListResponse(zones=[])),
        )

        events = self.service.events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, "EVENT-DUP")

    def test_events_keeps_unprefixed_record_names(self) -> None:
        """An EventDetails record without the prefix keeps its full name."""
        record = CKRecord.model_validate({
            "recordName": "EVENT-RAW-ID",
            "recordType": "EventDetails",
            "fields": {
                "time": {
                    "value": encode_json_bytes({"startSince1970": 1768435200000}),
                    "type": "ENCRYPTED_BYTES",
                }
            },
        })
        query_resp = CKQueryResponse.model_validate({
            "records": [record.model_dump(mode="json", exclude_none=True)]
        })
        self._monkeypatch.setattr(
            self.service.raw, "query", MagicMock(return_value=query_resp)
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "zones_list",
            MagicMock(return_value=CKZoneListResponse(zones=[])),
        )

        events = self.service.events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, "EVENT-RAW-ID")

    def test_events_degrades_when_private_query_fails(self) -> None:
        """A failed private-side query yields no events instead of raising."""
        self._monkeypatch.setattr(
            self.service.raw, "query", MagicMock(side_effect=InvitesApiError("boom"))
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "zones_list",
            MagicMock(return_value=CKZoneListResponse(zones=[])),
        )
        self.assertEqual(self.service.events(), [])

    def test_accept_requires_zone_name(self) -> None:
        """accept() raises InvitesApiError when zoneID.zoneName is missing."""
        self._monkeypatch.setattr(
            self.service.raw,
            "accept",
            MagicMock(return_value={"results": [{"shortGUID": {"value": "g"}}]}),
        )
        with self.assertRaises(InvitesApiError):
            self.service.accept("g")

    def test_accept_raises_when_event_invisible_in_both_scopes(self) -> None:
        """accept() falls back to the other scope then raises EventNotFound."""
        self._monkeypatch.setattr(
            self.service.raw,
            "accept",
            MagicMock(return_value=load_invites_fixture("accept_response.json")),
        )
        self._monkeypatch.setattr(
            self.service, "_fetch_event_full", MagicMock(return_value=None)
        )
        with self.assertRaises(EventNotFound):
            self.service.accept("008TESTFIXTUREAAAA")

    def test_scope_from_db_scope_defaults_to_private(self) -> None:
        """A missing or unrecognized databaseScope defaults to PRIVATE."""
        self.assertIs(self.service._scope_from_db_scope(None), EventScope.PRIVATE)
        self.assertIs(self.service._scope_from_db_scope("OTHER"), EventScope.PRIVATE)
        self.assertIs(self.service._scope_from_db_scope("shared"), EventScope.SHARED)

    def test_find_existing_rsvp_no_match_returns_none(self) -> None:
        """A record_name matching nothing returns None after the whole loop."""
        event = self._event_with_share(
            rsvps=(self._rsvp("PARTICIPANT-A_rsvp"), self._rsvp("PARTICIPANT-B_rsvp"))
        )
        self.assertIsNone(
            self.service._find_existing_rsvp(event, "PARTICIPANT-NOPE_rsvp")
        )

    def test_rsvp_from_modify_response_skips_tombstones(self) -> None:
        """Non-CKRecord entries are skipped when locating the matching RSVP."""
        tombstone = CKTombstoneRecord(recordName="PARTICIPANT-X_rsvp", deleted=True)
        target = CKRecord.model_validate({
            "recordName": "PARTICIPANT-X_rsvp",
            "recordType": "RSVP",
            "fields": {"status": {"value": 3, "type": "INT64"}},
        })
        resp = CKModifyResponse.model_validate({
            "records": [
                tombstone.model_dump(mode="json", exclude_none=True),
                target.model_dump(mode="json", exclude_none=True),
            ]
        })

        rsvp = self.service._rsvp_from_modify_response(resp, "PARTICIPANT-X_rsvp")

        self.assertEqual(rsvp.record_name, "PARTICIPANT-X_rsvp")
        self.assertEqual(rsvp.status, RsvpStatus.GOING)

    def test_rsvp_from_modify_response_missing_record_raises(self) -> None:
        """A modify response without the RSVP record raises InvitesApiError."""
        resp = CKModifyResponse(records=[])
        with self.assertRaises(InvitesApiError):
            self.service._rsvp_from_modify_response(resp, "PARTICIPANT-X_rsvp")

    def test_shared_zone_owner_no_match_returns_none(self) -> None:
        """The owner loop returns None when no zone matches the event id."""
        zones = CKZoneListResponse.model_validate({
            "zones": [
                {"zoneID": {"zoneName": "ZONE-A", "ownerRecordName": "_a"}},
                {"zoneID": {"zoneName": "ZONE-B", "ownerRecordName": "_b"}},
            ]
        })
        self._monkeypatch.setattr(
            self.service.raw, "zones_list", MagicMock(return_value=zones)
        )
        self.assertIsNone(self.service._shared_zone_owner("ZONE-MISSING"))

    def test_fetch_event_full_lookup_failure_returns_none(self) -> None:
        """A failed lookup is treated as a miss, not an error."""
        self._monkeypatch.setattr(
            self.service.raw, "lookup", MagicMock(side_effect=InvitesApiError("boom"))
        )
        self.assertIsNone(
            self.service._fetch_event_full("EVENT-FIXTURE-AAAA", EventScope.PRIVATE)
        )

    def test_fetch_event_full_ignores_other_record_types(self) -> None:
        """Records neither EventDetails nor cloudkit.share are skipped."""
        raw = cast(dict[str, Any], load_invites_fixture("event_lookup_response.json"))
        records: list[Any] = list(raw["records"])
        records.append({
            "recordName": "PARTICIPANT-EXTRA_rsvp",
            "recordType": "RSVP",
            "fields": {"status": {"value": 1, "type": "INT64"}},
        })
        lookup = CKLookupResponse.model_validate({"records": records})
        self._monkeypatch.setattr(
            self.service.raw, "lookup", MagicMock(return_value=lookup)
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "query",
            MagicMock(return_value=CKQueryResponse(records=[])),
        )

        event = self.service._fetch_event_full("EVENT-FIXTURE-AAAA", EventScope.PRIVATE)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_id, "EVENT-FIXTURE-AAAA")

    def test_fetch_event_full_tolerates_rsvp_and_otl_query_failures(self) -> None:
        """Failed RSVP/OTL queries stay empty rather than raising."""
        lookup = CKLookupResponse.model_validate(
            load_invites_fixture("event_lookup_response.json")
        )
        self._monkeypatch.setattr(
            self.service.raw, "lookup", MagicMock(return_value=lookup)
        )
        self._monkeypatch.setattr(
            self.service.raw, "query", MagicMock(side_effect=InvitesApiError("boom"))
        )

        event = self.service._fetch_event_full("EVENT-FIXTURE-AAAA", EventScope.PRIVATE)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.rsvps, ())
        self.assertIsNotNone(event.share)
        assert event.share is not None
        self.assertEqual(event.share.one_time_links, ())

    def test_fetch_event_full_populates_one_time_links(self) -> None:
        """OneTimeLinkGuestInfo records merge into the share's one_time_links."""
        lookup = CKLookupResponse.model_validate(
            load_invites_fixture("event_lookup_response.json")
        )
        otl_response = CKQueryResponse.model_validate({
            "records": [
                {
                    "recordName": "PARTICIPANT-OTL_otl",
                    "recordType": "OneTimeLinkGuestInfo",
                    "fields": {
                        "name": {
                            "value": "Link Guest",
                            "type": "STRING",
                            "isEncrypted": True,
                        },
                        "emails": {
                            "value": ["link@example.com"],
                            "type": "STRING_LIST",
                            "isEncrypted": True,
                        },
                        "phoneNumbers": {
                            "value": ["+15550000000"],
                            "type": "STRING_LIST",
                            "isEncrypted": True,
                        },
                    },
                },
                {
                    "recordName": "PLAIN-OTL-RECORD",
                    "recordType": "OneTimeLinkGuestInfo",
                    "fields": {},
                },
            ]
        })
        self._monkeypatch.setattr(
            self.service.raw, "lookup", MagicMock(return_value=lookup)
        )
        self._monkeypatch.setattr(
            self.service.raw,
            "query",
            MagicMock(side_effect=[CKQueryResponse(records=[]), otl_response]),
        )

        event = self.service._fetch_event_full("EVENT-FIXTURE-AAAA", EventScope.PRIVATE)

        assert event is not None
        assert event.share is not None
        self.assertEqual(len(event.share.one_time_links), 2)
        otl = event.share.one_time_links[0]
        self.assertEqual(otl.participant_id, "PARTICIPANT-OTL")
        self.assertEqual(otl.emails, ("link@example.com",))
        self.assertEqual(otl.phone_numbers, ("+15550000000",))
        # A record without the "_otl" suffix keeps its full name as the id.
        self.assertEqual(
            event.share.one_time_links[1].participant_id, "PLAIN-OTL-RECORD"
        )

    def test_share_from_record_reads_current_participant(self) -> None:
        """currentUserParticipant and unknown type/status fields are handled."""
        share_record = CKRecord.model_validate({
            "recordName": "cloudkit.zoneshare",
            "recordType": "cloudkit.share",
            "shortGUID": "008TESTFIXTUREAAAA",
            "publicPermission": "READ_WRITE",
            "currentUserParticipant": {
                "participantId": "PARTICIPANT-FIXTURE-GUEST",
                "userIdentity": {"userRecordName": "++FAKE="},
                "type": "PUBLIC_USER",
                "acceptanceStatus": "ACCEPTED",
                "permission": "READ_WRITE",
            },
            "participants": [
                {
                    "participantId": "PARTICIPANT-BOGUS",
                    "type": "BOGUS",
                    "acceptanceStatus": "BOGUS",
                    "permission": "READ_ONLY",
                },
                {
                    "participantId": "PARTICIPANT-OWNER",
                    "userIdentity": {"nameComponents": {"givenName": "Owner"}},
                    "type": "OWNER",
                    "acceptanceStatus": "ACCEPTED",
                    "permission": "READ_WRITE",
                },
            ],
        })

        share = self.service._share_from_record(share_record)

        self.assertEqual(share.current_user_participant_id, "PARTICIPANT-FIXTURE-GUEST")
        # Unknown wire values degrade to USER / INVITED instead of raising.
        bogus = share.participants[0]
        self.assertEqual(bogus.type.value, "USER")
        self.assertEqual(bogus.acceptance_status.value, "INVITED")
        self.assertEqual(bogus.permission, "READ_ONLY")

    def test_rsvp_from_record_unknown_status_and_asset_download(self) -> None:
        """Unknown statuses fall back and ASSETID images expose their URL."""
        record = CKRecord.model_validate({
            "recordName": "PARTICIPANT-X_rsvp",
            "recordType": "RSVP",
            "fields": {
                "status": {"value": 99, "type": "INT64", "isEncrypted": True},
                "image": {
                    "value": {"downloadURL": "https://cvws.example.com/1", "size": 10},
                    "type": "ASSETID",
                },
            },
        })

        rsvp = self.service._rsvp_from_record(record)

        self.assertEqual(rsvp.status, RsvpStatus.NO_RESPONSE)
        self.assertEqual(rsvp.image_download_url, "https://cvws.example.com/1")

    def test_first_resolve_result_errors(self) -> None:
        """Missing/empty/non-dict results raise InvitesApiError."""
        with self.assertRaises(InvitesApiError):
            self.service._first_resolve_result({})
        with self.assertRaises(InvitesApiError):
            self.service._first_resolve_result({"results": []})
        with self.assertRaises(InvitesApiError):
            self.service._first_resolve_result({"results": ["nope"]})

    def test_resolved_share_requires_short_guid_and_zone_name(self) -> None:
        """Missing shortGUID.value or zoneID.zoneName raise InvitesApiError."""
        with self.assertRaises(InvitesApiError):
            self.service._resolved_share_from_result({"zoneID": {"zoneName": "Z"}})
        with self.assertRaises(InvitesApiError):
            self.service._resolved_share_from_result({"shortGUID": {"value": "g"}})

    def test_resolved_share_falls_back_when_share_unparsable(self) -> None:
        """A share dict that fails CKRecord validation falls back to "NONE"."""
        result: dict[str, Any] = {
            "shortGUID": {"value": "g"},
            "zoneID": {"zoneName": "Z"},
            "share": {"recordType": "cloudkit.share"},
        }
        resolved = self.service._resolved_share_from_result(result)
        self.assertEqual(resolved.share.short_guid, "g")
        self.assertEqual(resolved.share.public_permission, "NONE")

    def test_resolved_share_falls_back_when_share_not_a_dict(self) -> None:
        """A non-dict share value falls back to an empty EventShare."""
        result: dict[str, Any] = {
            "shortGUID": {"value": "g"},
            "zoneID": {"zoneName": "Z"},
            "share": "nope",
        }
        resolved = self.service._resolved_share_from_result(result)
        self.assertEqual(resolved.share.public_permission, "NONE")

    def test_field_value_dict_fallback(self) -> None:
        """Fields without get_value fall back to a .value attribute lookup."""
        fields: dict[str, Any] = {"title": SimpleNamespace(value="x")}
        self.assertEqual(self.service._field_value(fields, "title"), "x")
        self.assertIsNone(self.service._field_value({}, "missing"))
        # Objects with neither get_value nor get return None.
        self.assertIsNone(self.service._field_value([], "title"))

    def test_field_str_undecodable_bytes(self) -> None:
        """Bytes that are not valid UTF-8 fall back to the default."""
        fields: dict[str, Any] = {"notes": SimpleNamespace(value=b"\xff\xff")}
        self.assertEqual(
            self.service._field_str(fields, "notes", default="fallback"), "fallback"
        )

    def test_field_int_accepts_bool(self) -> None:
        """A boolean field value is coerced through int()."""
        fields: dict[str, Any] = {"maxAttendees": SimpleNamespace(value=True)}
        self.assertEqual(self.service._field_int(fields, "maxAttendees"), 1)

    def test_field_bool_accepts_bool(self) -> None:
        """A boolean field value is returned as-is."""
        fields: dict[str, Any] = {
            "isPublished": SimpleNamespace(value=True),
            "isPrivate": SimpleNamespace(value=False),
        }
        self.assertTrue(self.service._field_bool(fields, "isPublished"))
        self.assertFalse(self.service._field_bool(fields, "isPrivate"))

    @staticmethod
    def _fields(fields: dict[str, Any]) -> Any:
        """Build a CKFields container from a raw field mapping."""
        return CKRecord.model_validate({
            "recordName": "R",
            "recordType": "EventDetails",
            "fields": fields,
        }).fields

    def test_decode_time_missing_or_malformed(self) -> None:
        """_decode_time returns None when the blob is absent or lacks a start."""
        service = self.service
        self.assertIsNone(service._decode_time(self._fields({})))
        self.assertIsNone(
            service._decode_time(
                self._fields({
                    "time": {
                        "value": encode_json_bytes({"nope": 1}),
                        "type": "ENCRYPTED_BYTES",
                    }
                })
            )
        )

    def test_decode_time_overflowing_timestamps(self) -> None:
        """Out-of-range ms values degrade to None rather than raising."""
        huge = 10**30
        service = self.service
        self.assertIsNone(
            service._decode_time(
                self._fields({
                    "time": {
                        "value": encode_json_bytes({"startSince1970": huge}),
                        "type": "ENCRYPTED_BYTES",
                    }
                })
            )
        )
        decoded = service._decode_time(
            self._fields({
                "time": {
                    "value": encode_json_bytes({
                        "startSince1970": 1768435200000,
                        "endSince1970": huge,
                    }),
                    "type": "ENCRYPTED_BYTES",
                }
            })
        )
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertIsNone(decoded.end)

    def test_decode_place_missing(self) -> None:
        """_decode_place returns None when the blob is absent."""
        self.assertIsNone(self.service._decode_place(self._fields({})))

    def test_asset_download_url_variants(self) -> None:
        """_asset_download_url handles mappings, bad values, and None."""
        service = self.service
        # Mapping with a downloadURL string.
        self.assertEqual(
            service._asset_download_url(
                {"image": SimpleNamespace(value={"downloadURL": "http://cvws/1"})},
                "image",
            ),
            "http://cvws/1",
        )
        # Mapping without a downloadURL string.
        self.assertIsNone(
            service._asset_download_url({"image": SimpleNamespace(value={})}, "image")
        )
        # Non-mapping value without a downloadURL attribute.
        self.assertIsNone(
            service._asset_download_url(
                {"image": SimpleNamespace(value=object())}, "image"
            )
        )
        # Missing value.
        self.assertIsNone(service._asset_download_url({}, "image"))


if __name__ == "__main__":
    unittest.main()
