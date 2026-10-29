"""Tests for the shared CloudKit container client."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import HTTPError

from pyicloud import PyiCloudService
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
    CloudKitZoneNotFoundError,
    redact_cloudkit_url,
)
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.session import PyiCloudSession


def _json_response(
    payload: dict[str, Any], *, status_code: int = 200, **attrs: Any
) -> Any:
    response = MagicMock(status_code=status_code, **attrs)
    response.json.return_value = payload
    return response


def test_cloudkit_client_uses_python_bool_params_by_default() -> None:
    """Query serializes boolean params as Python style by default."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": []})
    client = CloudKitContainerClient(
        "https://example.com/database",
        session,
        {"remapEnums": True},
    )

    client.query(
        query=CKQueryObject(recordType="SearchIndexes"),
        zone_id=CKZoneIDReq(zoneName="Notes"),
    )

    assert "remapEnums=True" in session.post.call_args.args[0]


def test_cloudkit_client_supports_lowercase_bool_params() -> None:
    """Query serializes boolean params as lowercase when requested."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": []})
    client = CloudKitContainerClient(
        "https://example.com/database",
        session,
        {"remapEnums": True},
        bool_param_style="lower",
    )

    client.query(
        query=CKQueryObject(recordType="SearchIndexes"),
        zone_id=CKZoneIDReq(zoneName="Notes"),
    )

    assert "remapEnums=true" in session.post.call_args.args[0]


def test_cloudkit_client_uses_timeout_override() -> None:
    """Query passes the configured timeout override to the request."""
    session = MagicMock()
    session.post.return_value = _json_response({"records": []})
    client = CloudKitContainerClient(
        "https://example.com/database",
        session,
        {},
        timeout=(1.0, 2.0),
    )

    client.query(
        query=CKQueryObject(recordType="SearchIndexes"),
        zone_id=CKZoneIDReq(zoneName="Notes"),
    )

    assert session.post.call_args.kwargs["timeout"] == (1.0, 2.0)


def test_redact_cloudkit_url_removes_query_and_fragment() -> None:
    """Redacting a URL strips its query string and fragment."""
    assert (
        redact_cloudkit_url("https://example.com/path?token=secret&x=1#frag")
        == "https://example.com/path"
    )


def test_cloudkit_client_invokes_debug_hook_on_http_error() -> None:
    """The debug hook is invoked with operation details on HTTP error."""
    events = []
    session = MagicMock()
    session.post.return_value = _json_response(
        {"error": "bad request"},
        status_code=400,
        text="bad request",
    )
    client = CloudKitContainerClient(
        "https://example.com/database",
        session,
        {"remapEnums": True},
        debug_hook=lambda *args: events.append(args),
    )

    query = CKQueryObject(recordType="SearchIndexes")
    zone_id = CKZoneIDReq(zoneName="Notes")

    with pytest.raises(CloudKitApiError):
        client.query(query=query, zone_id=zone_id)

    assert len(events) == 1
    op, url, payload, response = events[0]
    assert op == "records/query"
    assert "remapEnums=True" in url
    assert payload["query"]["recordType"] == "SearchIndexes"
    assert response is session.post.return_value


def test_cloudkit_client_raises_rate_limited_with_retry_after() -> None:
    """A 429 response raises rate limited carrying the retry-after delay."""
    session = MagicMock()
    session.post.return_value = _json_response(
        {"error": "rate limited"},
        status_code=429,
        headers={"Retry-After": "2.5"},
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})

    query = CKQueryObject(recordType="SearchIndexes")
    zone_id = CKZoneIDReq(zoneName="Notes")

    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.query(query=query, zone_id=zone_id)

    assert exc_info.value.retry_after == 2.5


def _zone_error_client(zone_error: dict[str, Any]) -> CloudKitContainerClient:
    session = MagicMock()
    session.post.return_value = _json_response({
        "zones": [{"zoneID": {"zoneName": "Reminders"}, **zone_error}]
    })
    return CloudKitContainerClient("https://example.com/database", session, {})


_ZONE_REQ = CKZoneChangesZoneReq(zoneID=CKZoneID(zoneName="Reminders"))


@pytest.mark.parametrize("code", ["THROTTLED", "TRY_AGAIN_LATER", "ZONE_BUSY"])
def test_cloudkit_client_changes_zone_error_retryable(code: str) -> None:
    """A retryable zone-level error raises rate limited, not a validation error."""
    client = _zone_error_client({
        "serverErrorCode": code,
        "reason": "busy",
        "retryAfter": 3,
    })

    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.changes(zone_req=_ZONE_REQ)

    assert str(exc_info.value) == f"Zone changes failed: {code} (busy)"
    assert exc_info.value.retry_after == 3.0


def test_cloudkit_client_changes_zone_error_without_retry_after() -> None:
    """A retryable zone-level error without retryAfter leaves it unset."""
    client = _zone_error_client({"serverErrorCode": "THROTTLED"})

    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.changes(zone_req=_ZONE_REQ)

    assert str(exc_info.value) == "Zone changes failed: THROTTLED"
    assert exc_info.value.retry_after is None


def test_cloudkit_client_changes_zone_error_auth() -> None:
    """An authentication zone-level error raises an auth error."""
    client = _zone_error_client({"serverErrorCode": "AUTHENTICATION_REQUIRED"})

    with pytest.raises(CloudKitAuthError):
        client.changes(zone_req=_ZONE_REQ)


def test_cloudkit_client_changes_zone_error_other() -> None:
    """Any other zone-level error raises an API error carrying the payload."""
    client = _zone_error_client({
        "serverErrorCode": "BAD_DATABASE_SCOPE",
        "reason": "no such database",
    })

    with pytest.raises(CloudKitApiError) as exc_info:
        client.changes(zone_req=_ZONE_REQ)

    assert str(exc_info.value) == (
        "Zone changes failed: BAD_DATABASE_SCOPE (no such database)"
    )
    payload = exc_info.value.payload
    assert payload is not None
    assert payload["zones"][0]["serverErrorCode"] == "BAD_DATABASE_SCOPE"


def test_cloudkit_client_changes_zone_not_found() -> None:
    """ZONE_NOT_FOUND raises a dedicated zone-not-found error, not a bare one.

    A missing zone is a valid account state (the service was never enabled),
    so callers need to distinguish it from other API failures. It must still
    be catchable as CloudKitApiError with the same message and payload.
    """
    client = _zone_error_client({
        "serverErrorCode": "ZONE_NOT_FOUND",
        "reason": "zone does not exist",
    })

    with pytest.raises(CloudKitZoneNotFoundError) as exc_info:
        client.changes(zone_req=_ZONE_REQ)

    assert isinstance(exc_info.value, CloudKitApiError)
    assert str(exc_info.value) == (
        "Zone changes failed: ZONE_NOT_FOUND (zone does not exist)"
    )
    assert exc_info.value.zone_name == "Reminders"
    payload = exc_info.value.payload
    assert payload is not None
    assert payload["zones"][0]["serverErrorCode"] == "ZONE_NOT_FOUND"


def test_cloudkit_client_changes_zone_not_found_without_zone_id() -> None:
    """A zone-not-found error dict without a zoneID leaves zone_name unset."""
    session = MagicMock()
    session.post.return_value = _json_response({
        "zones": [{"serverErrorCode": "ZONE_NOT_FOUND"}]
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitZoneNotFoundError) as exc_info:
        client.changes(zone_req=_ZONE_REQ)

    assert exc_info.value.zone_name is None


def test_cloudkit_client_http_zone_not_found() -> None:
    """An HTTP error body carrying ZONE_NOT_FOUND raises the dedicated type.

    Queries against a missing zone surface it as a top-level
    ``serverErrorCode`` in an error body rather than a per-zone dictionary.
    """
    session = MagicMock()
    session.post.return_value = _json_response(
        {
            "serverErrorCode": "ZONE_NOT_FOUND",
            "reason": "zone does not exist",
            "zoneID": {"zoneName": "Reminders"},
        },
        status_code=404,
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="reminderList")
    zone_id = CKZoneIDReq(zoneName="Reminders")

    with pytest.raises(CloudKitZoneNotFoundError) as exc_info:
        client.query(query=query, zone_id=zone_id)

    assert str(exc_info.value) == "HTTP 404"
    assert exc_info.value.zone_name == "Reminders"
    assert exc_info.value.payload == {
        "serverErrorCode": "ZONE_NOT_FOUND",
        "reason": "zone does not exist",
        "zoneID": {"zoneName": "Reminders"},
    }


def test_cloudkit_client_http_zone_not_found_without_zone_id() -> None:
    """An HTTP zone-not-found body without a zoneID leaves zone_name unset."""
    session = MagicMock()
    session.post.return_value = _json_response(
        {"serverErrorCode": "ZONE_NOT_FOUND"},
        status_code=404,
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="reminderList")
    zone_id = CKZoneIDReq(zoneName="Reminders")

    with pytest.raises(CloudKitZoneNotFoundError) as exc_info:
        client.query(query=query, zone_id=zone_id)

    assert exc_info.value.zone_name is None


def test_cloudkit_client_http_zone_not_found_via_real_session(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """A real PyiCloudSession 404 still surfaces CloudKitZoneNotFoundError.

    PyiCloudSession raises on a non-OK JSON response before the HTTP layer
    sees it, so the mapping has to survive the session's own error handling
    instead of relying on a mock that hands the response back.
    """
    body = {
        "serverErrorCode": "ZONE_NOT_FOUND",
        "reason": "zone does not exist",
        "zoneID": {"zoneName": "Reminders"},
    }
    response = MagicMock()
    response.status_code = 404
    response.ok = False
    response.reason = "Not Found"
    response.headers = {"Content-Type": "application/json"}
    response.json.return_value = body
    response.raise_for_status.side_effect = HTTPError(response=response)

    with patch.object(PyiCloudSession, "_load_session_data"):
        session = PyiCloudSession(pyicloud_service_working, "", cookie_directory="")
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="reminderList")
    zone_id = CKZoneIDReq(zoneName="Reminders")

    with (
        patch("requests.Session.request", return_value=response),
        patch.object(session, "_save_session_data"),
        pytest.raises(CloudKitZoneNotFoundError) as exc_info,
    ):
        client.query(query=query, zone_id=zone_id)

    assert str(exc_info.value) == "HTTP 404"
    assert exc_info.value.zone_name == "Reminders"
    assert exc_info.value.payload == body
    assert isinstance(exc_info.value, CloudKitApiError)
    assert isinstance(exc_info.value.__cause__, PyiCloudAPIResponseException)


@pytest.mark.parametrize(
    "case",
    ["no_response", "invalid_json", "other_code", "not_a_dict"],
)
def test_cloudkit_client_session_error_without_zone_body_is_reraised(
    case: str,
) -> None:
    """Session errors that are not zone failures propagate unchanged.

    Only a body carrying ZONE_NOT_FOUND is re-mapped to the dedicated type;
    anything else must reach the caller as the original
    PyiCloudAPIResponseException.
    """
    if case == "no_response":
        error = PyiCloudAPIResponseException("Not Found", 404)
    else:
        response = MagicMock()
        if case == "invalid_json":
            response.json.side_effect = ValueError("not json")
        elif case == "other_code":
            response.json.return_value = {"serverErrorCode": "BAD_DATABASE_SCOPE"}
        else:
            response.json.return_value = ["not", "a", "dict"]
        error = PyiCloudAPIResponseException("Not Found", 404, response)
    session = MagicMock()
    session.post.side_effect = error
    client = CloudKitContainerClient("https://example.com/database", session, {})
    query = CKQueryObject(recordType="reminderList")
    zone_id = CKZoneIDReq(zoneName="Reminders")

    with pytest.raises(PyiCloudAPIResponseException) as exc_info:
        client.query(query=query, zone_id=zone_id)

    assert exc_info.value is error


def test_cloudkit_client_iter_changes_zone_error() -> None:
    """Paged zone changes raise on a zone-level error too."""
    client = _zone_error_client({"serverErrorCode": "TRY_AGAIN_LATER"})

    with pytest.raises(CloudKitRateLimited):
        list(client.iter_changes(zone_req=_ZONE_REQ))


def test_cloudkit_client_download_asset_bytes() -> None:
    """Downloading an asset returns its raw bytes with default timeout."""
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=200, content=b"asset-bytes")
    client = CloudKitContainerClient("https://example.com/database", session, {})

    assert client.download_asset_bytes("https://example.com/asset") == b"asset-bytes"
    assert session.get.call_args.kwargs["timeout"] == (10.0, 60.0)


def test_cloudkit_client_download_asset_stream() -> None:
    """Streaming an asset yields non-empty chunks and closes the response."""
    session = MagicMock()
    response = MagicMock(
        status_code=200,
        iter_content=lambda **_: [b"chunk-1", b"", b"chunk-2"],
    )
    session.get.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})

    chunks = list(client.download_asset_stream("https://example.com/asset"))

    assert chunks == [b"chunk-1", b"chunk-2"]
    assert session.get.call_args.kwargs["stream"] is True
    assert session.get.call_args.kwargs["timeout"] == (10.0, 60.0)
    response.close.assert_called_once()


def test_cloudkit_client_download_asset_stream_closes_on_error() -> None:
    """Streaming an asset closes the response when an error occurs."""
    session = MagicMock()
    response = MagicMock(status_code=500, text="boom")
    session.get.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitApiError):
        list(client.download_asset_stream("https://example.com/asset"))

    response.close.assert_called_once()


def _modify_op() -> CKModifyOperation:
    """One trivial update operation, enough to send a modify."""

    return CKModifyOperation(
        operationType="update",
        record=CKWriteRecord(recordName="R1", recordType="Thing"),
    )


def test_a_rejected_record_raises_with_apples_reason() -> None:
    """CloudKit reports write failures per record, inside a 200 response.

    Every caller looked for its record by name, found an error entry instead of
    a CKRecord, and skipped it -- so a rejected write surfaced as a missing
    record or as nothing at all. The reason Apple gave has to reach the caller.
    """

    op = _modify_op()
    zone_id = CKZoneIDReq(zoneName="Z")
    session = MagicMock()
    session.post.return_value = _json_response({
        "records": [
            {
                "recordName": "EventDetails:E1",
                "serverErrorCode": "BAD_REQUEST",
                "reason": (
                    "Attempt to save encrypted data in non encrypted field type"
                ),
            }
        ]
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitApiError) as exc_info:
        client.modify(operations=[op], zone_id=zone_id)

    message = str(exc_info.value)
    assert "BAD_REQUEST" in message
    assert "encrypted data in non encrypted field type" in message
    assert "EventDetails:E1" in message


def test_every_rejected_record_is_named() -> None:
    """A batch that fails in more than one way should say so once."""

    op = _modify_op()
    zone_id = CKZoneIDReq(zoneName="Z")
    session = MagicMock()
    session.post.return_value = _json_response({
        "records": [
            {"recordName": "A", "serverErrorCode": "BAD_REQUEST", "reason": "bad"},
            {"recordName": "B", "serverErrorCode": "CONFLICT"},
        ]
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitApiError) as exc_info:
        client.modify(operations=[op], zone_id=zone_id)

    message = str(exc_info.value)
    assert "2 record(s)" in message
    assert "A: BAD_REQUEST (bad)" in message
    assert "B: CONFLICT" in message


def test_a_rejected_record_with_fields_is_still_detected() -> None:
    """An error entry carrying record fields must not pass as a record.

    CloudKit's per-record error shape is resolved by a union that would pick
    ``CKRecord`` if the entry also carried ``recordType`` and ``fields``. The
    guard inspects the raw payload, so the rejection still surfaces.
    """

    op = _modify_op()
    zone_id = CKZoneIDReq(zoneName="Z")
    session = MagicMock()
    session.post.return_value = _json_response({
        "records": [
            {
                "recordName": "A",
                "recordType": "Thing",
                "fields": {"f": {"type": "INT64", "value": 1}},
                "serverErrorCode": "CONFLICT",
                "reason": "record to insert already exists",
            }
        ]
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitApiError) as exc_info:
        client.modify(operations=[op], zone_id=zone_id)

    message = str(exc_info.value)
    assert "CONFLICT" in message
    assert "record to insert already exists" in message
    assert "A" in message


def test_a_successful_modify_still_returns_its_records() -> None:
    """The guard must not disturb the path that already worked."""

    op = _modify_op()
    zone_id = CKZoneIDReq(zoneName="Z")
    session = MagicMock()
    session.post.return_value = _json_response({
        "records": [
            {"recordName": "R1", "recordType": "Thing", "recordChangeTag": "TAG"}
        ]
    })
    client = CloudKitContainerClient("https://example.com/database", session, {})

    response = client.modify(operations=[op], zone_id=zone_id)

    assert len(response.records) == 1
    assert response.records[0].recordName == "R1"
