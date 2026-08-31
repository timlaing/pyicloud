"""Unit tests for PhotosCloudKitClient raw Photos-specific endpoints."""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
from uuid import UUID

import pytest

from pyicloud.common.cloudkit.client import CloudKitApiError, CloudKitRateLimited
from pyicloud.common.cloudkit.models import (
    CKRecord,
    CKTombstoneRecord,
    CKZoneChangesZoneReq,
    CKZoneID,
)
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.services.photos_cloudkit.client import PhotosCloudKitClient
from pyicloud.services.photos_cloudkit.upload import _local_time_zone

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
ZONES_LIST_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_zones_list_response.json").read_text(encoding="utf-8")
)
DATABASE_CHANGES_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_database_changes_response.json").read_text(encoding="utf-8")
)
ZONE_CHANGES_PAYLOAD = json.loads(
    (FIXTURE_DIR / "photos_zone_changes_response.json").read_text(encoding="utf-8")
)


UPLOAD_CLIENT_UUID = "11111111-2222-3333-4444-555555555555"
UPLOAD_FIXTURE_DIR = FIXTURE_DIR / "photos_upload"
CREATE_UPLOAD_URL_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "create_upload_url_response.json").read_text(encoding="utf-8")
)
SINGLE_FILE_UPLOAD_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "single_file_upload_response.json").read_text(
        encoding="utf-8"
    )
)
PUT_ASSET_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "put_asset_response.json").read_text(encoding="utf-8")
)
UPLOAD_STATUS_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "upload_status_response.json").read_text(encoding="utf-8")
)
UPLOAD_STATUS_UNKNOWN_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "upload_status_unknown_response.json").read_text(
        encoding="utf-8"
    )
)
PUT_ASSET_DUPLICATE_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "put_asset_duplicate_response.json").read_text(
        encoding="utf-8"
    )
)
PUT_ASSET_REJECTED_PAYLOAD = json.loads(
    (UPLOAD_FIXTURE_DIR / "put_asset_rejected_response.json").read_text(
        encoding="utf-8"
    )
)
RESERVED_UPLOAD_URL = CREATE_UPLOAD_URL_PAYLOAD["uploadUrls"][UPLOAD_CLIENT_UUID]


def _upload_client(session: MagicMock) -> PhotosCloudKitClient:
    """Build a client wired to both the CloudKit and photosupload hosts."""

    return PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=session,
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        photos_upload_url="https://photosupload.example.com",
    )


@contextmanager
def _upload_environment(
    *, size: int = 3105, mtime: float = 1787731877.546
) -> Iterator[None]:
    """Pin the file metadata and client UUID the upload flow reads."""

    with (
        patch("pathlib.Path.open", mock_open(read_data=b"jpeg-bytes")),
        patch(
            "pyicloud.services.photos_cloudkit.upload.os.path.getsize",
            return_value=size,
        ),
        patch(
            "pyicloud.services.photos_cloudkit.upload.os.path.getmtime",
            return_value=mtime,
        ),
        patch(
            "pyicloud.services.photos_cloudkit.upload.uuid4",
            return_value=UUID(UPLOAD_CLIENT_UUID),
        ),
    ):
        yield


def test_upload_file_runs_the_four_step_flow() -> None:
    """A successful upload should reserve a URL, send bytes, then register."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: PUT_ASSET_PAYLOAD),
    ]
    client = _upload_client(session)

    with _upload_environment():
        result = client.upload_file("/virtual/new_upload.jpg", zone_name="PrimarySync")

    assert result.cplMaster == PUT_ASSET_PAYLOAD[0]["cplMaster"]
    assert result.cplAsset == PUT_ASSET_PAYLOAD[0]["cplAsset"]
    assert result.uploadJobId == PUT_ASSET_PAYLOAD[0]["uploadJobId"]

    urls = [call.kwargs["url"] for call in session.post.call_args_list]
    assert urls[0].startswith(
        "https://photosupload.example.com/photosupload/createUploadUrl?"
    )
    assert "dsid=12345" in urls[0]
    assert urls[1] == RESERVED_UPLOAD_URL
    assert urls[2].startswith("https://photosupload.example.com/photosupload/putAsset?")

    reserve_body = session.post.call_args_list[0].kwargs["json"]
    assert reserve_body == {
        "zoneName": "PrimarySync",
        "assets": {UPLOAD_CLIENT_UUID: 3105},
    }

    register_body = session.post.call_args_list[2].kwargs["json"]
    assert register_body["zoneName"] == "PrimarySync"
    assert register_body["importGroup"] == UPLOAD_CLIENT_UUID
    assert register_body["files"][0]["fileName"] == "new_upload.jpg"
    assert register_body["files"][0]["lastModDate"] == 1787731877546
    # The receipt from step 2 is echoed back verbatim.
    assert (
        register_body["files"][0]["singleFileUploadRequest"]
        == SINGLE_FILE_UPLOAD_PAYLOAD["singleFile"]
    )
    assert session.post.call_args_list[0].kwargs["timeout"] == (10.0, 60.0)


def test_upload_file_passes_shared_library_zone_through() -> None:
    """Shared Library zones reach Apple rather than being blocked client-side."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: PUT_ASSET_PAYLOAD),
    ]
    client = _upload_client(session)

    with _upload_environment():
        client.upload_file("/virtual/shared.jpg", zone_name="SharedSync-ABCDEF")

    assert session.post.call_args_list[0].kwargs["json"]["zoneName"] == (
        "SharedSync-ABCDEF"
    )
    assert session.post.call_args_list[2].kwargs["json"]["zoneName"] == (
        "SharedSync-ABCDEF"
    )


def test_upload_file_requires_photos_upload_url() -> None:
    """Uploads should fail clearly when the photosupload host is unavailable."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
        upload_url="https://upload.example.com",
        photos_upload_url=None,
    )

    with pytest.raises(CloudKitApiError, match="Photos uploads are not configured"):
        client.upload_file("/virtual/missing_host.jpg", zone_name="PrimarySync")


def test_upload_file_raises_when_no_url_is_reserved() -> None:
    """A reservation that omits our client UUID should not silently succeed."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200, json=lambda: {"uploadUrls": {}}
    )
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(CloudKitApiError, match="did not return an upload URL"),
    ):
        client.upload_file("/virtual/no_url.jpg", zone_name="PrimarySync")


def test_upload_file_raises_when_registration_is_rejected() -> None:
    """A non-success putAsset status should surface as an error."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: PUT_ASSET_REJECTED_PAYLOAD),
    ]
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(CloudKitApiError, match="rejected rejected.jpg with status 503"),
    ):
        client.upload_file("/virtual/rejected.jpg", zone_name="PrimarySync")


def test_upload_file_returns_existing_asset_for_duplicate() -> None:
    """A file iCloud already holds comes back as a result, not an error.

    Verified against a live account: Apple answers 409 with an errorMessage,
    omits isRetryable and uploadJobId, but still supplies both record names.
    """

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: PUT_ASSET_DUPLICATE_PAYLOAD),
    ]
    client = _upload_client(session)

    with _upload_environment():
        result = client.upload_file(
            "/virtual/already_there.jpg", zone_name="PrimarySync"
        )

    assert result.is_duplicate is True
    assert result.cplMaster == PUT_ASSET_DUPLICATE_PAYLOAD[0]["cplMaster"]
    assert result.cplAsset == PUT_ASSET_DUPLICATE_PAYLOAD[0]["cplAsset"]
    assert result.uploadJobId is None
    assert result.response is not None
    assert result.response.isRetryable is None
    assert result.response.errorMessage == "duplicate asset"


def test_upload_file_success_is_not_flagged_as_duplicate() -> None:
    """A normal 200 registration must not be mistaken for a duplicate."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: PUT_ASSET_PAYLOAD),
    ]
    client = _upload_client(session)

    with _upload_environment():
        result = client.upload_file("/virtual/fresh.jpg", zone_name="PrimarySync")

    assert result.is_duplicate is False


def test_upload_status_sends_the_job_ids_envelope() -> None:
    """Apple accepts only {"uploadJobIds": [...]}; every other shape 400s."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200, json=lambda: UPLOAD_STATUS_PAYLOAD
    )
    client = _upload_client(session)

    job_id = next(iter(UPLOAD_STATUS_PAYLOAD))
    statuses = client.upload_status([job_id])

    assert session.post.call_args.kwargs["json"] == {"uploadJobIds": [job_id]}
    assert session.post.call_args.kwargs["url"].startswith(
        "https://photosupload.example.com/photosupload/uploadStatus?"
    )
    assert statuses[job_id].progress == 95
    assert statuses[job_id].is_unknown is False


def test_upload_status_marks_unknown_jobs() -> None:
    """An unrecognised job id comes back as errorCode 404, not as an omission."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200, json=lambda: UPLOAD_STATUS_UNKNOWN_PAYLOAD
    )
    client = _upload_client(session)

    job_id = next(iter(UPLOAD_STATUS_UNKNOWN_PAYLOAD))
    statuses = client.upload_status([job_id])

    assert statuses[job_id].is_unknown is True
    assert statuses[job_id].progress is None


def test_upload_status_requires_photos_upload_url() -> None:
    """Progress lookups need the photosupload host just like uploads do."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
        photos_upload_url=None,
    )

    with pytest.raises(CloudKitApiError, match="Photos uploads are not configured"):
        client.upload_status(["job"])


def test_upload_status_raises_when_payload_is_not_a_mapping() -> None:
    """uploadStatus answers with a mapping keyed by job id."""

    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, json=lambda: [])
    client = _upload_client(session)

    with pytest.raises(
        CloudKitApiError, match="uploadStatus returned an unexpected payload"
    ):
        client.upload_status(["job"])


def test_upload_file_raises_cloudkit_error_for_http_error() -> None:
    """Upload HTTP failures should be raised before response validation."""

    session = MagicMock()
    response = MagicMock(status_code=503, text="upstream unavailable")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(
            CloudKitApiError, match="Photos createUploadUrl failed with HTTP 503"
        ),
    ):
        client.upload_file("/virtual/http_error.jpg", zone_name="PrimarySync")


def test_upload_file_raises_cloudkit_error_for_invalid_json() -> None:
    """Upload responses should fail clearly when Apple returns invalid JSON."""

    session = MagicMock()
    response = MagicMock(status_code=200, text="not-json")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(
            CloudKitApiError, match="Photos createUploadUrl returned invalid JSON"
        ),
    ):
        client.upload_file("/virtual/invalid_json.jpg", zone_name="PrimarySync")


def test_local_time_zone_uses_javascript_offset_sign() -> None:
    """Apple expects getTimezoneOffset() semantics: UTC+2 is sent as -120."""

    zone = timezone(timedelta(hours=2))
    with patch("pyicloud.services.photos_cloudkit.upload.datetime") as mock_datetime:
        mock_datetime.now.return_value.astimezone.return_value = datetime(
            2026, 8, 26, 12, 0, tzinfo=zone
        )
        _, minutes = _local_time_zone()

    assert minutes == -120


def test_upload_file_raises_when_reservation_payload_is_malformed() -> None:
    """A reservation payload of the wrong shape should not be silently ignored."""

    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200, json=lambda: {"uploadUrls": "not-a-mapping"}
    )
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(
            CloudKitApiError, match="createUploadUrl returned an unexpected payload"
        ),
    ):
        client.upload_file("/virtual/malformed.jpg", zone_name="PrimarySync")


def test_upload_file_raises_when_receipt_is_missing() -> None:
    """The content host must return a singleFile receipt to register the asset."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: {"unexpected": True}),
    ]
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(
            CloudKitApiError, match="singleFileUpload returned an unexpected payload"
        ),
    ):
        client.upload_file("/virtual/no_receipt.jpg", zone_name="PrimarySync")


def test_upload_file_raises_when_put_asset_is_not_a_list() -> None:
    """putAsset answers with a list; anything else is treated as an error."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: {"not": "a list"}),
    ]
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(
            CloudKitApiError, match="putAsset returned an unexpected payload"
        ),
    ):
        client.upload_file("/virtual/bad_put.jpg", zone_name="PrimarySync")


def test_upload_file_raises_when_put_asset_registers_nothing() -> None:
    """An empty registration list means the asset never landed."""

    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=200, json=lambda: CREATE_UPLOAD_URL_PAYLOAD),
        MagicMock(status_code=200, json=lambda: SINGLE_FILE_UPLOAD_PAYLOAD),
        MagicMock(status_code=200, json=lambda: []),
    ]
    client = _upload_client(session)

    with (
        _upload_environment(),
        pytest.raises(CloudKitApiError, match="putAsset returned no assets"),
    ):
        client.upload_file("/virtual/empty_put.jpg", zone_name="PrimarySync")


def test_local_time_zone_falls_back_when_tzlocal_is_unavailable() -> None:
    """A tzlocal failure should still yield a usable zone name."""

    with patch("tzlocal.get_localzone", side_effect=RuntimeError("no tzdata")):
        zone_id, _ = _local_time_zone()

    assert zone_id


def test_batch_count_posts_expected_internal_query_payload() -> None:
    """Photos count queries should hit the internal batch endpoint
    with the expected payload."""

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


def test_batch_count_debug_log_omits_cloudkit_query_params(
    caplog: pytest.LogCaptureFixture,
) -> None:
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


def test_download_asset_bytes_redacts_signed_url_in_debug_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    with patch.object(
        client._client._http, "post", return_value=ZONES_LIST_PAYLOAD
    ) as post_mock:
        result = client.zones_list()

    assert result.zones[0].zoneID.zoneName == "PrimarySync"
    assert result.zones[0].syncToken == "SYNC_TOKEN_101"
    assert result.zones[1].zoneID.zoneName == "CustomZone"
    post_mock.assert_called_once_with("/zones/list", {})


def test_database_changes_parses_fixture_payload() -> None:
    """Database changes should validate the changed-zone envelope."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
    )

    with patch.object(
        client._client._http, "post", return_value=DATABASE_CHANGES_PAYLOAD
    ) as post_mock:
        result = client.database_changes(sync_token="SYNC_TOKEN_101")

    assert result.syncToken == "SYNC_TOKEN_102"
    assert [zone.zoneID.zoneName for zone in result.zones] == [
        "PrimarySync",
        "CustomZone",
    ]
    post_mock.assert_called_once_with(
        "/changes/database",
        {"syncToken": "SYNC_TOKEN_101"},
    )


def test_iter_changes_parses_fixture_payload() -> None:
    """Zone changes should yield typed record and tombstone entries from
    fixture JSON."""

    client = PhotosCloudKitClient(
        base_url="https://example.com/database/1/container/production/private",
        session=MagicMock(),
        base_params={"dsid": "12345"},
    )

    with patch.object(client._client._http, "post", return_value=ZONE_CHANGES_PAYLOAD):
        zones = list(
            client.iter_changes(
                zone_req=CKZoneChangesZoneReq(
                    zoneID=CKZoneID(
                        zoneName="PrimarySync",
                        ownerRecordName="OWNER_RECORD_NAME_001",
                        zoneType="REGULAR_CUSTOM_ZONE",
                    ),
                    syncToken="SYNC_TOKEN_102",
                    reverse=False,
                )
            )
        )

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zoneID.zoneName == "PrimarySync"
    assert zone.syncToken == "SYNC_TOKEN_103"
    assert isinstance(zone.records[0], CKRecord)
    assert zone.records[0].recordType == "CPLAsset"
    assert zone.records[0].recordName == "ASSET_RECORD_ID_101"
    assert isinstance(zone.records[1], CKTombstoneRecord)
    assert zone.records[1].deleted is True
    assert zone.records[1].recordName == "ALBUM_RECORD_ID_999"
