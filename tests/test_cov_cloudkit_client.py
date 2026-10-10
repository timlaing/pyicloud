"""Branch-coverage tests for the shared CloudKit container client.

Covers previously-missed transport, zone-error, and response-validation
branches in ``pyicloud.common.cloudkit.client``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import (
    CKModifyOperation,
    CKQueryObject,
    CKWriteRecord,
    CKZoneChangesZoneReq,
    CKZoneID,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
)

_ZONE_REQ = CKZoneChangesZoneReq(zoneID=CKZoneID(zoneName="Reminders"))


def _json_response(
    payload: dict[str, Any], *, status_code: int = 200, **attrs: Any
) -> Any:
    response = MagicMock(status_code=status_code, **attrs)
    response.json.return_value = payload
    return response


# ---------------------------------------------------------------------------
# POST transport paths
# ---------------------------------------------------------------------------


def test_post_unauthorized_raises_auth_error() -> None:
    """A 401/403 POST raises CloudKitAuthError."""
    session = MagicMock()
    session.post.return_value = _json_response({}, status_code=403)
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitAuthError, match="HTTP 403"):
        client.raw_post("/records/query", {})


def test_post_rate_limited_without_retry_after_header() -> None:
    """A 429 POST without a Retry-After header leaves retry_after unset."""
    session = MagicMock()
    session.post.return_value = _json_response({}, status_code=429, headers={})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.raw_post("/records/query", {})
    assert exc_info.value.retry_after is None


def test_post_rate_limited_with_invalid_retry_after_header() -> None:
    """A non-numeric Retry-After header is tolerated on a 429 POST."""
    session = MagicMock()
    session.post.return_value = _json_response(
        {}, status_code=429, headers={"Retry-After": "soon"}
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.raw_post("/records/query", {})
    assert exc_info.value.retry_after is None


def test_post_server_error_falls_back_to_response_text() -> None:
    """An undecodable 5xx body is carried as the API error payload."""
    session = MagicMock()
    response = _json_response({}, status_code=500, text="boom")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError) as exc_info:
        client.raw_post("/records/query", {})
    assert exc_info.value.payload == "boom"


def test_post_success_invalid_json_raises_api_error() -> None:
    """A 2xx POST whose body is not JSON raises CloudKitApiError."""
    session = MagicMock()
    response = _json_response({}, status_code=200, text="invalid")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError) as exc_info:
        client.raw_post("/records/query", {})
    assert "Invalid JSON response" in str(exc_info.value)
    assert exc_info.value.payload == "invalid"


def test_debug_hook_failure_is_logged_not_raised() -> None:
    """A raising debug hook is swallowed and logged."""
    session = MagicMock()
    session.post.return_value = _json_response({}, status_code=403)
    client = CloudKitContainerClient(
        "https://example.com/database",
        session,
        {},
        debug_hook=MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(CloudKitAuthError):
        client.raw_post("/records/query", {})


# ---------------------------------------------------------------------------
# Asset GET / stream transport paths
# ---------------------------------------------------------------------------


def test_get_bytes_non_integer_status_code() -> None:
    """A non-integer status code is treated as 200 when downloading."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code="200", content=b"abc")
    client = CloudKitContainerClient("https://example.com/database", session, {})
    assert client.download_asset_bytes("https://example.com/asset") == b"abc"


def test_get_bytes_unauthorized_raises_auth_error() -> None:
    """A 401 asset download raises CloudKitAuthError."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=401)
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitAuthError, match="HTTP 401"):
        client.download_asset_bytes("https://example.com/asset")


def test_get_bytes_rate_limited_without_retry_after() -> None:
    """A 429 asset download without Retry-After leaves retry_after unset."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=429, headers={})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.download_asset_bytes("https://example.com/asset")
    assert exc_info.value.retry_after is None


def test_get_bytes_rate_limited_with_invalid_retry_after() -> None:
    """A non-numeric Retry-After header is tolerated on an asset download."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=429, headers={"Retry-After": "x"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited):
        client.download_asset_bytes("https://example.com/asset")


def test_get_bytes_server_error_raises_api_error() -> None:
    """A 4xx/5xx asset download raises CloudKitApiError."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=404, text="missing")
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError, match="HTTP 404"):
        client.download_asset_bytes("https://example.com/asset")


def test_get_bytes_encodes_text_content() -> None:
    """Text-only asset responses are encoded to UTF-8 bytes."""
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=200, content=None, text="h\u00e9llo"
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})
    assert (
        client.download_asset_bytes("https://example.com/asset")
        == "h\u00e9llo".encode()
    )


def test_get_bytes_invalid_content_raises_api_error() -> None:
    """An asset response without bytes or text raises CloudKitApiError."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=200, content=None, text=123)
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError, match="Invalid asset response"):
        client.download_asset_bytes("https://example.com/asset")


def test_get_stream_non_integer_status_code() -> None:
    """A non-integer status code still streams chunks."""
    session = MagicMock()
    response = MagicMock(status_code="200", iter_content=lambda **_: [b"a", b"b"])
    session.get.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})
    assert list(client.download_asset_stream("https://example.com/asset")) == [
        b"a",
        b"b",
    ]


def test_get_stream_rate_limited_without_retry_after() -> None:
    """A 429 stream without Retry-After leaves retry_after unset."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=429, headers={})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited) as exc_info:
        list(client.download_asset_stream("https://example.com/asset"))
    assert exc_info.value.retry_after is None


def test_get_stream_rate_limited_with_invalid_retry_after() -> None:
    """A non-numeric Retry-After header is tolerated on a stream."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=429, headers={"Retry-After": "x"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitRateLimited):
        list(client.download_asset_stream("https://example.com/asset"))


def test_get_stream_success_without_close_method() -> None:
    """A stream response without a close() method is still handled."""
    session = MagicMock()
    response = MagicMock(status_code=200, iter_content=lambda **_: [b"chunk"])
    response.close = None
    session.get.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})
    assert list(client.download_asset_stream("https://example.com/asset")) == [b"chunk"]


# ---------------------------------------------------------------------------
# Zone change envelopes
# ---------------------------------------------------------------------------


def test_changes_validation_error_when_zones_malformed() -> None:
    """A malformed zones envelope skips zone-error parsing and fails validation."""
    session = MagicMock()
    session.post.return_value = _json_response({"zones": "oops"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError, match="Changes response validation failed"):
        client.changes(zone_req=_ZONE_REQ)


def test_iter_changes_validation_error_when_zones_malformed() -> None:
    """iter_changes surfaces a malformed zones envelope as a validation error."""
    session = MagicMock()
    session.post.return_value = _json_response({"zones": {"zoneName": "x"}})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(CloudKitApiError, match="Changes response validation failed"):
        list(client.iter_changes(zone_req=_ZONE_REQ))


def test_iter_changes_follows_more_coming_pages() -> None:
    """iter_changes advances its sync token while moreComing stays true."""
    session = MagicMock()
    session.post.side_effect = [
        _json_response({
            "zones": [
                {
                    "records": [],
                    "moreComing": True,
                    "syncToken": "token-1",
                    "zoneID": {"zoneName": "Notes"},
                }
            ]
        }),
        _json_response({
            "zones": [
                {
                    "records": [],
                    "moreComing": False,
                    "syncToken": "token-2",
                    "zoneID": {"zoneName": "Notes"},
                }
            ]
        }),
    ]
    client = CloudKitContainerClient("https://example.com/database", session, {})
    zones = list(client.iter_changes(zone_req=_ZONE_REQ))
    assert [zone.syncToken for zone in zones] == ["token-1", "token-2"]
    second_payload = session.post.call_args_list[1].kwargs["json"]
    assert second_payload["zones"][0]["syncToken"] == "token-1"


def test_iter_changes_stops_when_no_zones() -> None:
    """iter_changes returns immediately when the envelope has no zones."""
    session = MagicMock()
    session.post.return_value = _json_response({"zones": []})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    assert not list(client.iter_changes(zone_req=_ZONE_REQ))


# ---------------------------------------------------------------------------
# Query / modify / database methods
# ---------------------------------------------------------------------------


def test_query_zone_wide_with_zone_id_raises() -> None:
    """zone_wide queries reject an explicit zone_id."""
    client = CloudKitContainerClient("https://example.com/database", MagicMock(), {})
    query = CKQueryObject(recordType="SearchIndexes")
    zone_id = CKZoneIDReq(zoneName="Notes")
    with pytest.raises(ValueError, match="zone_id must be omitted"):
        client.query(query=query, zone_id=zone_id, zone_wide=True)


def test_query_requires_zone_id_unless_zone_wide() -> None:
    """Scoped queries require a zone_id."""
    client = CloudKitContainerClient("https://example.com/database", MagicMock(), {})
    query = CKQueryObject(recordType="SearchIndexes")
    with pytest.raises(ValueError, match="zone_id is required"):
        client.query(query=query)


def test_query_validation_error_raises_api_error() -> None:
    """An invalid query response envelope raises CloudKitApiError."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": "not-a-list"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="SearchIndexes")
    zone_id = CKZoneIDReq(zoneName="Notes")
    with pytest.raises(CloudKitApiError, match="Query response validation failed"):
        client.query(query=query, zone_id=zone_id)


def test_modify_success() -> None:
    """modify parses a successful records modify response."""
    session = MagicMock()
    session.post.return_value = _json_response({
        "records": [{"recordName": "rec-1", "recordType": "Test"}],
        "syncToken": "modify-token",
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})
    operation = CKModifyOperation(
        operationType="create",
        record=CKWriteRecord(recordName="rec-1", recordType="Test"),
    )
    result = client.modify(
        operations=[operation],
        zone_id=CKZoneIDReq(zoneName="Notes"),
        atomic=True,
    )
    assert result.records[0].recordName == "rec-1"
    assert result.syncToken == "modify-token"


def test_modify_validation_error_raises_api_error() -> None:
    """An invalid modify response envelope raises CloudKitApiError."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": "nope"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    operation = CKModifyOperation(
        operationType="create",
        record=CKWriteRecord(recordName="rec-1", recordType="Test"),
    )
    zone_id = CKZoneIDReq(zoneName="Notes")
    with pytest.raises(CloudKitApiError, match="Modify response validation failed"):
        client.modify(operations=[operation], zone_id=zone_id)


def test_database_changes_without_sync_token() -> None:
    """database_changes sends an empty payload when no sync token is set."""
    session = MagicMock()
    session.post.return_value = _json_response({
        "zones": [],
        "syncToken": "db-token",
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})
    result = client.database_changes()
    assert result.syncToken == "db-token"
    assert session.post.call_args.kwargs["json"] == {}


def test_database_changes_validation_error_raises_api_error() -> None:
    """An invalid database-changes envelope raises CloudKitApiError."""
    session = MagicMock()
    session.post.return_value = _json_response({"zones": "nope"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    with pytest.raises(
        CloudKitApiError, match="Database changes response validation failed"
    ):
        client.database_changes(sync_token="tok")


def test_query_sync_token_validation_error_raises_api_error() -> None:
    """An invalid sync-token query response raises CloudKitApiError."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": "nope"})
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="SearchIndexes")
    zone_id = CKZoneIDReq(zoneName="Notes")
    with pytest.raises(
        CloudKitApiError, match="Sync token query response validation failed"
    ):
        client.query_sync_token(query=query, zone_id=zone_id)
