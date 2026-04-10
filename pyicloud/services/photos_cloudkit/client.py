"""Photos-specific CloudKit client helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Dict
from urllib.parse import urlencode

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKModifyOperation,
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitContainerClient,
)
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT

from .models import (
    PhotosBatchCountFilter,
    PhotosBatchCountQuery,
    PhotosBatchCountRequest,
    PhotosBatchCountRequestBatch,
    PhotosBatchCountResponse,
    PhotosBatchCountStringListValue,
    PhotosUploadResponse,
)


class PhotosCloudKitClient:
    """Photos container adapter on top of the generic CloudKit client."""

    def __init__(
        self,
        *,
        base_url: str,
        session,
        base_params: Dict[str, object],
        upload_url: str | None = None,
    ) -> None:
        self._session = session
        self._upload_url = upload_url
        self._base_params = base_params
        self._client = CloudKitContainerClient(base_url, session, base_params)

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        results_limit: int | None = None,
        continuation: str | None = None,
        desired_keys: list[str] | None = None,
    ) -> CKQueryResponse:
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
    ):
        yield from self._client.iter_changes(zone_req=zone_req)

    def modify(
        self,
        *,
        operations: list[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: bool | None = None,
    ) -> CKModifyResponse:
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
        return self._client.lookup(
            record_names=record_names,
            zone_id=zone_id,
            desired_keys=desired_keys,
        )

    def zones_list(self):
        return self._client.zones_list()

    def database_changes(self, *, sync_token: str | None = None):
        return self._client.database_changes(sync_token=sync_token)

    def download_asset_bytes(self, url: str) -> bytes:
        return self._client.download_asset_bytes(url)

    def batch_count(self, *, container_id: str, zone_id: dict[str, str]) -> int:
        """
        Query the Hyperion index count used by Photos albums.

        This remains a Photos-specific raw endpoint because the shared CloudKit
        request models do not yet represent the batched internal count API.
        """

        url = self._client._http.build_url("/internal/records/query/batch")
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
        response = self._session.post(
            url,
            json=payload,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        data = PhotosBatchCountResponse.model_validate(response.json())
        try:
            return data.batch[0].records[0].fields.itemCount.value
        except Exception as exc:
            raise CloudKitApiError(
                "Photos count query failed", payload=data.model_dump(mode="json")
            ) from exc

    def upload_file(self, path: str, *, dsid: str) -> PhotosUploadResponse:
        """Upload a file through Apple’s uploadimagews endpoint."""

        if not self._upload_url:
            raise CloudKitApiError("Photos uploads are not configured")
        upload_path = Path(path)
        params = {"dsid": dsid, "filename": upload_path.name}
        url = f"{self._upload_url}/upload?{urlencode(params)}"
        with upload_path.open("rb") as handle:
            response = self._session.post(url=url, data=handle)
        data = PhotosUploadResponse.model_validate(response.json())
        if data.errors:
            first = data.errors[0]
            raise CloudKitApiError(
                f"{first.code or 'UPLOAD_ERROR'}: {first.message or ''}".strip()
            )
        return data
