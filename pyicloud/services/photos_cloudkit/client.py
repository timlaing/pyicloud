"""Photos-specific CloudKit client helpers."""

from __future__ import annotations

from collections.abc import Iterator

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
    CloudKitContainerClient,
)
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.session import PyiCloudSession

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
        return self._client.query(
            query=query,
            zone_id=zone_id,
            results_limit=results_limit,
            continuation=continuation,
            desired_keys=desired_keys,
        )

    def iter_changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
    ) -> Iterator[CKZoneChangesZone]:
        """Yield CloudKit record changes in the given zone."""
        yield from self._client.iter_changes(zone_req=zone_req)

    def modify(
        self,
        *,
        operations: list[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: bool | None = None,
    ) -> CKModifyResponse:
        """Apply a batch of CloudKit modify operations in the given zone."""
        return self._client.modify(
            operations=operations, zone_id=zone_id, atomic=atomic
        )

    def lookup(
        self,
        *,
        record_names: list[str],
        zone_id: CKZoneIDReq,
        desired_keys: list[str] | None = None,
    ) -> CKLookupResponse:
        """Look up CloudKit records by name in the given zone."""
        return self._client.lookup(
            record_names=record_names,
            zone_id=zone_id,
            desired_keys=desired_keys,
        )

    def zones_list(self) -> CKZoneListResponse:
        """List the CloudKit zones for the Photos container."""
        return self._client.zones_list()

    def database_changes(
        self, *, sync_token: str | None = None
    ) -> CKDatabaseChangesResponse:
        """Yield CloudKit database changes, optionally from a sync token."""
        return self._client.database_changes(sync_token=sync_token)

    def download_asset_bytes(self, url: str) -> bytes:
        """Download the raw bytes of a CloudKit asset at ``url``."""
        return self._client.download_asset_bytes(url)

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
        raw_data = self._client.raw_post(
            "/internal/records/query/batch",
            payload,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        try:
            data = PhotosBatchCountResponse.model_validate(raw_data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Photos count query failed", payload=raw_data
            ) from exc
        try:
            return data.batch[0].records[0].fields.itemCount.value
        except Exception as exc:
            raise CloudKitApiError(
                "Photos count query failed", payload=data.model_dump(mode="json")
            ) from exc

    def upload_status(self, job_ids: list[str]) -> dict[str, PhotosUploadStatus]:
        """Report ingest progress for upload jobs returned by :meth:`upload_file`.

        Progress is informational: an asset becomes usable when CloudKit indexes
        its records, which :meth:`upload_file`'s caller waits for separately.
        """

        if self._uploader is None:
            raise CloudKitApiError("Photos uploads are not configured")
        return self._uploader.upload_status(job_ids)

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
            raise CloudKitApiError("Photos uploads are not configured")
        return self._uploader.upload(
            path, zone_name=zone_name, import_group=import_group
        )
