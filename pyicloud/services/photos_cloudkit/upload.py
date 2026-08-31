"""Apple's multi-step Photos upload protocol.

Apple withdrew the single-POST ``uploadimagews`` endpoint, which has returned
HTTP 410 Gone since 2026-08-25. An upload now runs as three requests across two
hosts, with a fourth endpoint available for progress:

1. ``POST {photosupload}/photosupload/createUploadUrl`` reserves a per-file
   upload URL, sized from the byte count declared up front.
2. ``POST <reserved url>`` sends the raw bytes to the content host. The URL
   carries its own token, so the request needs no extra headers.
3. ``POST {photosupload}/photosupload/putAsset`` registers the stored bytes and
   returns ``cplMaster`` / ``cplAsset`` directly.
4. ``POST {photosupload}/photosupload/uploadStatus`` reports ingest progress.
   Note that progress is not what gates a usable asset: callers wait for CloudKit
   to index the records, by retrying the lookup.

Step 3 hands back the record names that the old endpoint only exposed through a
follow-up lookup, so callers tracking those IDs save a round trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import ValidationError
from requests import Response

from pyicloud.common.cloudkit.client import CloudKitApiError
from pyicloud.session import PyiCloudSession

from .models import (
    PhotosCreateUploadUrlRequest,
    PhotosCreateUploadUrlResponse,
    PhotosPutAssetFile,
    PhotosPutAssetRequest,
    PhotosPutAssetResult,
    PhotosSingleFileUpload,
    PhotosSingleFileUploadResponse,
    PhotosUploadStatus,
    PhotosUploadStatusRequest,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)

CREATE_UPLOAD_URL_PATH: str = "/photosupload/createUploadUrl"
PUT_ASSET_PATH: str = "/photosupload/putAsset"
UPLOAD_STATUS_PATH: str = "/photosupload/uploadStatus"


def response_json(response: Response, *, context: str) -> Any:
    """Return the parsed JSON body of ``response`` or raise ``CloudKitApiError``.

    Apple's upload hosts answer with either an object or a list depending on the
    step, so the parsed value is returned untyped and validated by the caller.
    """

    code = getattr(response, "status_code", 0)
    if not isinstance(code, int):
        code = 200
    if code >= 400:
        try:
            payload = response.json()
        except Exception:  # pylint: disable=broad-except
            payload = getattr(response, "text", None)
        raise CloudKitApiError(f"{context} failed with HTTP {code}", payload=payload)
    try:
        return response.json()
    except Exception as exc:
        raise CloudKitApiError(
            f"{context} returned invalid JSON",
            payload=getattr(response, "text", None),
        ) from exc


def _local_time_zone() -> tuple[str, int]:
    """Return the local IANA zone id and its offset in web-client minutes.

    Apple's web client sends ``timeZoneOffset`` using JavaScript's
    ``Date.prototype.getTimezoneOffset()`` convention, which is UTC minus local
    time -- so UTC+2 is sent as ``-120``, not ``120``.
    """

    now = datetime.now().astimezone()
    offset = now.utcoffset()
    minutes = -int(offset.total_seconds() // 60) if offset is not None else 0

    zone_id = ""
    try:
        from tzlocal import (  # pylint: disable=import-outside-toplevel
            get_localzone,
        )

        zone_id = str(getattr(get_localzone(), "key", "") or "")
    except Exception:  # pylint: disable=broad-except
        zone_id = ""
    if not zone_id:
        zone_id = str(now.tzname() or timezone.utc.tzname(None) or "UTC")
    return zone_id, minutes


class PhotosUploader:
    """Drives Apple's four-step Photos upload flow against one account."""

    def __init__(
        self,
        *,
        session: PyiCloudSession,
        base_url: str,
        base_params: dict[str, object],
        timeout: tuple[float, float],
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._base_params = base_params
        self._timeout = timeout

    def _url(self, path: str) -> str:
        params = {key: str(value) for key, value in self._base_params.items()}
        return f"{self._base_url}{path}?{urlencode(params)}"

    def create_upload_url(
        self, *, zone_name: str, assets: dict[str, int]
    ) -> dict[str, str]:
        """Reserve one upload URL per client UUID in ``assets``."""

        payload = PhotosCreateUploadUrlRequest(
            zoneName=zone_name, assets=assets
        ).model_dump(mode="json")
        response = self._session.post(
            url=self._url(CREATE_UPLOAD_URL_PATH),
            json=payload,
            timeout=self._timeout,
        )
        data = response_json(response, context="Photos createUploadUrl")
        try:
            parsed = PhotosCreateUploadUrlResponse.model_validate(data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Photos createUploadUrl returned an unexpected payload",
                payload=data,
            ) from exc
        return dict(parsed.uploadUrls)

    def send_bytes(self, url: str, path: Path) -> PhotosSingleFileUpload:
        """Send the file at ``path`` to a reserved upload ``url``."""

        with path.open("rb") as handle:
            response = self._session.post(url=url, data=handle, timeout=self._timeout)
        data = response_json(response, context="Photos singleFileUpload")
        try:
            parsed = PhotosSingleFileUploadResponse.model_validate(data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Photos singleFileUpload returned an unexpected payload",
                payload=data,
            ) from exc
        return parsed.singleFile

    def put_asset(
        self,
        *,
        zone_name: str,
        files: list[PhotosPutAssetFile],
        import_group: str,
        local_time_zone_id: str,
    ) -> list[PhotosPutAssetResult]:
        """Register uploaded bytes as Photos assets."""

        payload = PhotosPutAssetRequest(
            zoneName=zone_name,
            files=files,
            localTimeZoneId=local_time_zone_id,
            importGroup=import_group,
        ).model_dump(mode="json")
        response = self._session.post(
            url=self._url(PUT_ASSET_PATH),
            json=payload,
            timeout=self._timeout,
        )
        data = response_json(response, context="Photos putAsset")
        if not isinstance(data, list):
            raise CloudKitApiError(
                "Photos putAsset returned an unexpected payload", payload=data
            )
        try:
            return [PhotosPutAssetResult.model_validate(item) for item in data]
        except ValidationError as exc:
            raise CloudKitApiError(
                "Photos putAsset returned an unexpected payload", payload=data
            ) from exc

    def upload_status(self, job_ids: list[str]) -> dict[str, PhotosUploadStatus]:
        """Report ingest progress for jobs returned by :meth:`put_asset`.

        Apple answers 200 for job ids it does not recognise, marking those
        entries with ``errorCode`` 404 rather than omitting them, so the result
        maps every requested id.
        """

        payload = PhotosUploadStatusRequest(uploadJobIds=list(job_ids)).model_dump(
            mode="json"
        )
        response = self._session.post(
            url=self._url(UPLOAD_STATUS_PATH),
            json=payload,
            timeout=self._timeout,
        )
        data = response_json(response, context="Photos uploadStatus")
        if not isinstance(data, dict):
            raise CloudKitApiError(
                "Photos uploadStatus returned an unexpected payload", payload=data
            )
        try:
            return {
                key: PhotosUploadStatus.model_validate(value)
                for key, value in data.items()
            }
        except ValidationError as exc:
            raise CloudKitApiError(
                "Photos uploadStatus returned an unexpected payload", payload=data
            ) from exc

    def upload(
        self,
        path: str,
        *,
        zone_name: str,
        import_group: str | None = None,
    ) -> PhotosPutAssetResult:
        """Run the upload flow for one file and return its registration.

        A file iCloud already holds is reported by Apple as a ``409`` rather
        than as a failure, and still carries the existing asset's record names,
        so it is returned like any other result -- check
        :attr:`PhotosPutAssetResult.is_duplicate` to tell the cases apart.

        The asset is registered but not yet queryable when this returns;
        CloudKit needs several seconds to index it.
        """

        upload_path = Path(path)
        client_uuid = str(uuid4())
        size = os.path.getsize(path)

        upload_urls = self.create_upload_url(
            zone_name=zone_name, assets={client_uuid: size}
        )
        target = upload_urls.get(client_uuid)
        if not target:
            raise CloudKitApiError(
                "Photos createUploadUrl did not return an upload URL",
                payload=upload_urls,
            )

        receipt = self.send_bytes(target, upload_path)

        zone_id, offset_minutes = _local_time_zone()
        results = self.put_asset(
            zone_name=zone_name,
            files=[
                PhotosPutAssetFile(
                    fileName=upload_path.name,
                    lastModDate=int(os.path.getmtime(path) * 1000),
                    timeZoneOffset=offset_minutes,
                    singleFileUploadRequest=receipt,
                )
            ],
            import_group=import_group or client_uuid,
            local_time_zone_id=zone_id,
        )
        if not results:
            raise CloudKitApiError("Photos putAsset returned no assets")

        result = results[0]
        status = result.response.status if result.response else None
        if result.is_duplicate:
            _LOGGER.debug(
                "%s is already in zone %s; returning the existing asset",
                upload_path.name,
                zone_name,
            )
            return result
        if status is not None and status >= 400:
            raise CloudKitApiError(
                f"Photos putAsset rejected {upload_path.name} with status {status}",
                payload=result.model_dump(mode="json"),
            )
        _LOGGER.debug(
            "Uploaded %s to zone %s as %s", upload_path.name, zone_name, result.cplAsset
        )
        return result
