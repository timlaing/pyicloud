"""Photos-specific CloudKit client helpers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NoReturn

from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKDatabaseChangesResponse,
    CKLookupResponse,
    CKModifyOperation,
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CKZoneListResponse,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
    CloudKitZoneNotFoundError,
    session_zone_not_found,
)
from pyicloud.common.cloudkit.errors import (
    RATE_LIMITED_STATUS,
    UNAUTHORIZED_STATUSES,
    retry_after_of,
    status_of,
)
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.session import PyiCloudSession

from .exceptions import (
    PhotosApiError,
    PhotosAuthError,
    PhotosRateLimited,
    PhotosZoneNotFoundError,
)
from .models import (
    PhotosBatchCountFilter,
    PhotosBatchCountQuery,
    PhotosBatchCountRequest,
    PhotosBatchCountRequestBatch,
    PhotosBatchCountResponse,
    PhotosBatchCountStringListValue,
    PhotosPutAssetResult,
    PhotosUploadStatus,
)
from .upload import PhotosUploader


class PhotosCloudKitClient:
    """Photos container adapter on top of the generic CloudKit client."""

    def __init__(
        self,
        *,
        base_url: str,
        session: PyiCloudSession,
        base_params: dict[str, object],
        upload_url: str | None = None,
        photos_upload_url: str | None = None,
    ) -> None:
        self._session = session
        self._upload_url = upload_url
        self._photos_upload_url = photos_upload_url
        self._base_params = base_params
        self._client = CloudKitContainerClient(base_url, session, base_params)
        self._uploader: PhotosUploader | None = None
        if photos_upload_url:
            self._uploader = PhotosUploader(
                session=session,
                base_url=photos_upload_url,
                base_params=base_params,
                timeout=self._client.timeout,
            )

    @staticmethod
    def _raise_photos_error(exc: Exception) -> NoReturn:
        """Re-raise a raw CloudKit or session error as its Photos counterpart.

        Mirrors the other CloudKit services' clients: callers of the Photos
        service see Photos errors rather than the shared CloudKit hierarchy.
        """
        cause = exc.__cause__ or exc
        if isinstance(exc, CloudKitAuthError):
            raise PhotosAuthError(str(exc)) from cause
        if isinstance(exc, CloudKitRateLimited):
            raise PhotosRateLimited(str(exc), retry_after=exc.retry_after) from cause
        if isinstance(exc, CloudKitZoneNotFoundError):
            raise PhotosZoneNotFoundError(
                str(exc), exc.payload, zone_name=exc.zone_name
            ) from cause
        if isinstance(exc, CloudKitApiError):
            raise PhotosApiError(
                str(exc),
                payload=exc.payload,
                status=exc.status if hasattr(exc, "status") else None,
            ) from cause
        if isinstance(exc, PyiCloudAPIResponseException):
            # The CloudKit HTTP layer maps 4xx onto the errors above, but
            # never gets the chance: PyiCloudSession raises on a non-ok
            # response first, so the transport error arrives here
            # untranslated and callers catching PhotosServiceException miss
            # it. Check auth/rate-limit first so a ZONE_NOT_FOUND body
            # cannot mask an auth error or lose Retry-After.
            status = status_of(exc)
            if status in UNAUTHORIZED_STATUSES:
                raise PhotosAuthError(str(exc)) from cause
            if status == RATE_LIMITED_STATUS:
                raise PhotosRateLimited(
                    str(exc), retry_after=retry_after_of(exc)
                ) from cause
            zone_error = session_zone_not_found(exc)
            if zone_error is not None:
                raise PhotosZoneNotFoundError(
                    str(zone_error),
                    zone_error.payload,
                    zone_name=zone_error.zone_name,
                ) from cause
            raise PhotosApiError(str(exc), status=status) from cause
        raise exc

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        results_limit: int | None = None,
        continuation: str | None = None,
        desired_keys: list[str] | None = None,
    ) -> CKQueryResponse:
        """Run a CloudKit query against a Photos zone."""
        try:
            return self._client.query(
                query=query,
                zone_id=zone_id,
                results_limit=results_limit,
                continuation=continuation,
                desired_keys=desired_keys,
            )
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def iter_changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
    ) -> Iterator[CKZoneChangesZone]:
        """Yield CloudKit record changes in the given zone."""
        try:
            yield from self._client.iter_changes(zone_req=zone_req)
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def modify(
        self,
        *,
        operations: list[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: bool | None = None,
    ) -> CKModifyResponse:
        """Apply a batch of CloudKit modify operations in the given zone.

        ``CloudKitContainerClient`` reports a rejected record as a
        ``CloudKitApiError``. Translate it to the Photos hierarchy so a failed
        photos write surfaces as ``PhotosApiError`` (or a more specific Photos
        error) rather than a raw CloudKit error.
        """
        try:
            return self._client.modify(
                operations=operations, zone_id=zone_id, atomic=atomic
            )
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def lookup(
        self,
        *,
        record_names: list[str],
        zone_id: CKZoneIDReq,
        desired_keys: list[str] | None = None,
    ) -> CKLookupResponse:
        """Look up CloudKit records by name in the given zone."""
        try:
            return self._client.lookup(
                record_names=record_names,
                zone_id=zone_id,
                desired_keys=desired_keys,
            )
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def zones_list(self) -> CKZoneListResponse:
        """List the CloudKit zones for the Photos container."""
        try:
            return self._client.zones_list()
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def database_changes(
        self, *, sync_token: str | None = None
    ) -> CKDatabaseChangesResponse:
        """Yield CloudKit database changes, optionally from a sync token."""
        try:
            return self._client.database_changes(sync_token=sync_token)
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def download_asset_bytes(self, url: str) -> bytes:
        """Download the raw bytes of a CloudKit asset at ``url``."""
        try:
            return self._client.download_asset_bytes(url)
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def batch_count(self, *, container_id: str, zone_id: dict[str, str]) -> int:
        """
        Query the Hyperion index count used by Photos albums.

        This remains a Photos-specific raw endpoint because the shared CloudKit
        request models do not yet represent the batched internal count API.
        """

        payload = PhotosBatchCountRequest(
            batch=[
                PhotosBatchCountRequestBatch(
                    resultsLimit=1,
                    query=PhotosBatchCountQuery(
                        recordType="HyperionIndexCountLookup",
                        filterBy=PhotosBatchCountFilter(
                            fieldName="indexCountID",
                            comparator="IN",
                            fieldValue=PhotosBatchCountStringListValue(
                                type="STRING_LIST",
                                value=[container_id],
                            ),
                        ),
                    ),
                    zoneWide=True,
                    zoneID=CKZoneIDReq(**zone_id),
                )
            ]
        ).model_dump(mode="json", exclude_none=True)
        try:
            raw_data = self._client.raw_post(
                "/internal/records/query/batch",
                payload,
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)
        try:
            data = PhotosBatchCountResponse.model_validate(raw_data)
        except ValidationError as exc:
            raise PhotosApiError("Photos count query failed", payload=raw_data) from exc
        try:
            return data.batch[0].records[0].fields.itemCount.value
        except Exception as exc:
            raise PhotosApiError(
                "Photos count query failed", payload=data.model_dump(mode="json")
            ) from exc

    def upload_status(self, job_ids: list[str]) -> dict[str, PhotosUploadStatus]:
        """Report ingest progress for upload jobs returned by :meth:`upload_file`.

        Progress is informational: an asset becomes usable when CloudKit indexes
        its records, which :meth:`upload_file`'s caller waits for separately.
        """

        if self._uploader is None:
            raise PhotosApiError("Photos uploads are not configured")
        try:
            return self._uploader.upload_status(job_ids)
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)

    def upload_file(
        self,
        path: str,
        *,
        zone_name: str,
        import_group: str | None = None,
    ) -> PhotosPutAssetResult:
        """Upload a file into ``zone_name`` and return its registration.

        The returned result carries ``cplMaster`` and ``cplAsset`` record
        names, which callers hydrate with a follow-up lookup once CloudKit
        has indexed them. A file iCloud already holds comes back as a
        duplicate result rather than an error.
        """

        if self._uploader is None:
            raise PhotosApiError("Photos uploads are not configured")
        try:
            return self._uploader.upload(
                path, zone_name=zone_name, import_group=import_group
            )
        except (
            CloudKitApiError,
            CloudKitAuthError,
            CloudKitRateLimited,
            PyiCloudAPIResponseException,
        ) as exc:
            self._raise_photos_error(exc)
