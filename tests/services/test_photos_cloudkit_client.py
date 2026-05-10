"""Unit tests for PhotosCloudKitClient raw Photos-specific endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pyicloud.common.cloudkit.client import CloudKitApiError, CloudKitRateLimited
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.services.photos_cloudkit.client import PhotosCloudKitClient

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SKELETAL_UPLOAD_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_upload_skeletal_response.json").read_text(encoding="utf-8")
)
DUPLICATE_UPLOAD_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_upload_duplicate_response.json").read_text(encoding="utf-8")
)
ZONES_LIST_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_zones_list_response.json").read_text(encoding="utf-8")
)
DATABASE_CHANGES_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_database_changes_response.json").read_text(encoding="utf-8")
)
ZONE_CHANGES_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_zone_changes_response.json").read_text(encoding="utf-8")
)


def test_upload_file_returns_skeletal_upload_payload() -> None:
    """Photos uploads should preserve Apple's skeletal record payloads."""

    session = MagicMock()
    session.post.return_value = MagicMock(json=lambda: SKELETAL_UPLOAD_PAYLOAD)
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
    )

    with patch("pathlib.Path.open", mock_open(read_data=b"jpeg-bytes")):
        result = client.upload_file("/virtual/new_upload.jpg", dsid="12345")

    assert [record.recordType for record in result.records] == ["CPLMaster", "CPLAsset"]
    assert [record.recordName for record in result.records] == [
        record["recordName"] for record in SKELETAL_UPLOAD_PAYLOAD["records"]
    ]
    assert session.post.call_args.kwargs["url"].startswith(
        "https://upload.example.com/upload?"
    )
    assert "dsid=12345" in session.post.call_args.kwargs["url"]
    assert "filename=new_upload.jpg" in session.post.call_args.kwargs["url"]
    assert session.post.call_args.kwargs["timeout"] == (10.0, 60.0)


def test_upload_file_returns_duplicate_upload_payload() -> None:
    """Duplicate uploads should preserve Apple's duplicate marker for callers."""

    session = MagicMock()
    session.post.return_value = MagicMock(json=lambda: DUPLICATE_UPLOAD_PAYLOAD)
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
    )

    with patch("pathlib.Path.open", mock_open(read_data=b"jpeg-bytes")):
        result = client.upload_file("/virtual/duplicate_upload.jpg", dsid="12345")

    assert result.isDuplicate is True
    assert result.records[0].recordType == "CPLMaster"
    assert result.records[1].recordType == "CPLAsset"


def test_upload_file_requires_upload_url() -> None:
    """Uploads should fail clearly when the upload endpoint is not configured."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
        upload_url=None,
    )

    with pytest.raises(CloudKitApiError, match="Photos uploads are not configured"):
        client.upload_file("/virtual/missing_upload_url.jpg", dsid="12345")


def test_upload_file_raises_cloudkit_error_for_upload_errors() -> None:
    """Upload error payloads should be normalized into CloudKitApiError."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        json=lambda: {
            "errors": [
                {
                    "code": "TYPE_UNSUPPORTED",
                    "message": "Unsupported file type",
                }
            ]
        }
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
    )

    with (
        patch("pathlib.Path.open", mock_open(read_data=b"png-bytes")),
        pytest.raises(
            CloudKitApiError, match="TYPE_UNSUPPORTED: Unsupported file type"
        ),
    ):
        client.upload_file("/virtual/bad_upload.png", dsid="12345")


def test_upload_file_raises_cloudkit_error_for_http_error() -> None:
    """Upload HTTP failures should be raised before response validation."""

    session = MagicMock()
    response = MagicMock(status_code=503, text="upstream unavailable")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
    )

    with (
        patch("pathlib.Path.open", mock_open(read_data=b"jpeg-bytes")),
        pytest.raises(CloudKitApiError, match="Photos upload failed with HTTP 503"),
    ):
        client.upload_file("/virtual/http_error.jpg", dsid="12345")


def test_upload_file_raises_cloudkit_error_for_invalid_json() -> None:
    """Upload responses should fail clearly when Apple returns invalid JSON."""

    session = MagicMock()
    response = MagicMock(status_code=200, text="not-json")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
    )

    with (
        patch("pathlib.Path.open", mock_open(read_data=b"jpeg-bytes")),
        pytest.raises(CloudKitApiError, match="Photos upload returned invalid JSON"),
    ):
        client.upload_file("/virtual/invalid_json.jpg", dsid="12345")


def test_batch_count_posts_expected_internal_query_payload() -> None:
    """Photos count queries should hit the internal batch endpoint with the expected payload."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        json=lambda: {
            "batch": [
                {
                    "records": [
                        {"fields": {"itemCount": {"value": 42}}},
                    ]
                }
            ]
        }
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )

    result = client.batch_count(
        container_id="CPLContainerRelationLiveByPosition:album123",
        zone_id={"zoneName": "PrimarySync"},
    )

    assert result == 42
    assert session.post.call_args.kwargs["headers"] == {CONTENT_TYPE: CONTENT_TYPE_TEXT}
    assert session.post.call_args.kwargs["timeout"] == (10.0, 60.0)
    payload = session.post.call_args.kwargs["json"]
    assert payload["batch"][0]["query"]["recordType"] == "HyperionIndexCountLookup"
    assert payload["batch"][0]["query"]["filterBy"]["fieldValue"]["value"] == [
        "CPLContainerRelationLiveByPosition:album123"
    ]
    assert payload["batch"][0]["zoneID"] == {"zoneName": "PrimarySync"}


def test_batch_count_debug_log_omits_cloudkit_query_params(caplog) -> None:
    """CloudKit request logs should avoid user-identifying query parameters."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        json=lambda: {
            "batch": [
                {
                    "records": [
                        {"fields": {"itemCount": {"value": 42}}},
                    ]
                }
            ]
        }
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )
    caplog.set_level(logging.DEBUG, logger="pyicloud.common.cloudkit.client")

    client.batch_count(
        container_id="CPLContainerRelationLiveByPosition:album123",
        zone_id={"zoneName": "PrimarySync"},
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "CloudKit POST /internal/records/query/batch" in messages
    assert "dsid=12345" not in messages


def test_batch_count_raises_on_malformed_payload() -> None:
    """Malformed count responses should be surfaced as CloudKitApiError."""

    session = MagicMock()
    session.post.return_value = MagicMock(json=lambda: {"batch": []})
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )

    with pytest.raises(CloudKitApiError, match="Photos count query failed"):
        client.batch_count(
            container_id="CPLContainerRelationLiveByPosition:album123",
            zone_id={"zoneName": "PrimarySync"},
        )


def test_batch_count_raises_cloudkit_error_for_validation_failure() -> None:
    """Invalid count response models should be normalized into CloudKitApiError."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        json=lambda: {
            "batch": [
                {
                    "records": [
                        {"fields": {"itemCount": {"value": "not-an-int"}}},
                    ]
                }
            ]
        }
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )

    with pytest.raises(CloudKitApiError, match="Photos count query failed"):
        client.batch_count(
            container_id="CPLContainerRelationLiveByPosition:album123",
            zone_id={"zoneName": "PrimarySync"},
        )


def test_download_asset_bytes_preserves_rate_limit_retry_after() -> None:
    """Asset GET rate limits should expose Retry-After like CloudKit POSTs."""

    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=429,
        headers={"Retry-After": "2.5"},
        text="rate limited",
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )

    with pytest.raises(CloudKitRateLimited) as exc_info:
        client.download_asset_bytes("https://example.com/asset")

    assert exc_info.value.retry_after == 2.5


def test_download_asset_bytes_redacts_signed_url_in_debug_log(caplog) -> None:
    """Asset GET logs should not include signed download URLs."""

    session = MagicMock()
    session.get.return_value = MagicMock(status_code=200, content=b"asset")
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )
    signed_url = "https://cvws.icloud-content.com/asset?dsid=12345&token=secret"
    caplog.set_level(logging.DEBUG, logger="pyicloud.common.cloudkit.client")

    assert client.download_asset_bytes(signed_url) == b"asset"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "CloudKit asset GET <redacted>" in messages
    assert signed_url not in messages
    assert "dsid=12345" not in messages
    assert "token=secret" not in messages


def test_batch_count_raises_cloudkit_error_for_http_error() -> None:
    """Batch count queries should use shared CloudKit HTTP error handling."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=500,
        json=lambda: {"error": "bad"},
    )
    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
    )

    with pytest.raises(CloudKitApiError, match="HTTP 500"):
        client.batch_count(
            container_id="CPLContainerRelationLiveByPosition:album123",
            zone_id={"zoneName": "PrimarySync"},
        )


def test_zones_list_parses_fixture_payload() -> None:
    """Zones list should validate and expose typed zone metadata."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
    )
    client._client._http.post = MagicMock(return_value=ZONES_LIST_PAYLOAD)

    result = client.zones_list()

    assert result.zones[0].zoneID.zoneName == "PrimarySync"
    assert result.zones[0].syncToken == "SYNC_TOKEN_101"
    assert result.zones[1].zoneID.zoneName == "CustomZone"
    client._client._http.post.assert_called_once_with("/zones/list", {})


def test_database_changes_parses_fixture_payload() -> None:
    """Database changes should validate the changed-zone envelope."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
    )
    client._client._http.post = MagicMock(return_value=DATABASE_CHANGES_PAYLOAD)

    result = client.database_changes(sync_token="SYNC_TOKEN_101")

    assert result.syncToken == "SYNC_TOKEN_102"
    assert [zone.zoneID.zoneName for zone in result.zones] == [
        "PrimarySync",
        "CustomZone",
    ]
    client._client._http.post.assert_called_once_with(
        "/changes/database",
        {"syncToken": "SYNC_TOKEN_101"},
    )


def test_iter_changes_parses_fixture_payload() -> None:
    """Zone changes should yield typed record and tombstone entries from fixture JSON."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
    )
    client._client._http.post = MagicMock(return_value=ZONE_CHANGES_PAYLOAD)

    zones = list(
        client.iter_changes(
            zone_req={
                "zoneID": {
                    "zoneName": "PrimarySync",
                    "ownerRecordName": "OWNER_RECORD_NAME_001",
                    "zoneType": "REGULAR_CUSTOM_ZONE",
                },
                "syncToken": "SYNC_TOKEN_102",
                "reverse": False,
            }
        )
    )

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zoneID.zoneName == "PrimarySync"
    assert zone.syncToken == "SYNC_TOKEN_103"
    assert zone.records[0].recordType == "CPLAsset"
    assert zone.records[0].recordName == "ASSET_RECORD_ID_101"
    assert zone.records[1].deleted is True
    assert zone.records[1].recordName == "ALBUM_RECORD_ID_999"
