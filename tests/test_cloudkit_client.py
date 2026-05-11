"""Tests for the shared CloudKit container client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKQueryObject, CKZoneIDReq
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitContainerClient,
    CloudKitRateLimited,
    redact_cloudkit_url,
)


def _json_response(payload: dict, *, status_code: int = 200, **attrs):
    response = MagicMock(status_code=status_code, **attrs)
    response.json.return_value = payload
    return response


def test_cloudkit_client_uses_python_bool_params_by_default():
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


def test_cloudkit_client_supports_lowercase_bool_params():
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


def test_cloudkit_client_uses_timeout_override():
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


def test_redact_cloudkit_url_removes_query_and_fragment():
    assert (
        redact_cloudkit_url("https://example.com/path?token=secret&x=1#frag")
        == "https://example.com/path"
    )


def test_cloudkit_client_invokes_debug_hook_on_http_error():
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

    with pytest.raises(CloudKitApiError):
        client.query(
            query=CKQueryObject(recordType="SearchIndexes"),
            zone_id=CKZoneIDReq(zoneName="Notes"),
        )

    assert len(events) == 1
    op, url, payload, response = events[0]
    assert op == "records/query"
    assert "remapEnums=True" in url
    assert payload["query"]["recordType"] == "SearchIndexes"
    assert response is session.post.return_value


def test_cloudkit_client_raises_rate_limited_with_retry_after():
    session = MagicMock()
    session.post.return_value = _json_response(
        {"error": "rate limited"},
        status_code=429,
        headers={"Retry-After": "2.5"},
    )
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.query(
            query=CKQueryObject(recordType="SearchIndexes"),
            zone_id=CKZoneIDReq(zoneName="Notes"),
        )

    assert exc_info.value.retry_after == 2.5


def test_cloudkit_client_download_asset_bytes():
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=200, content=b"asset-bytes")
    client = CloudKitContainerClient("https://example.com/database", session, {})

    assert client.download_asset_bytes("https://example.com/asset") == b"asset-bytes"
    assert session.get.call_args.kwargs["timeout"] == (10.0, 60.0)


def test_cloudkit_client_download_asset_stream():
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


def test_cloudkit_client_download_asset_stream_closes_on_error():
    session = MagicMock()
    response = MagicMock(status_code=500, text="boom")
    session.get.return_value = response
    client = CloudKitContainerClient("https://example.com/database", session, {})

    with pytest.raises(CloudKitApiError):
        list(client.download_asset_stream("https://example.com/asset"))

    response.close.assert_called_once()
