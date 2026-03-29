"""
Low-level CloudKit client for the Reminders container.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TypeVar

from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKLookupDescriptor,
    CKLookupRequest,
    CKLookupResponse,
    CKModifyOperation,
    CKModifyRequest,
    CKModifyResponse,
    CKQueryObject,
    CKQueryRequest,
    CKQueryResponse,
    CKZoneChangesRequest,
    CKZoneChangesResponse,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CloudKitExtraMode,
    resolve_cloudkit_validation_extra,
)

LOGGER = logging.getLogger(__name__)
_ResponseModelT = TypeVar("_ResponseModelT")


# ... (Error classes remain the same) ...


class RemindersAuthError(Exception):
    """Auth/PCS/cookie issues (401/403)."""


class RemindersApiError(Exception):
    """Catch-all API error."""

    def __init__(self, message: str, payload: Optional[object] = None):
        super().__init__(message)
        self.payload = payload


class _CloudKitClient:
    """
    Minimal HTTP transport for CloudKit.
    """

    _REQUEST_TIMEOUT = (10.0, 60.0)

    def __init__(self, base_url: str, session, base_params: Dict[str, object]):
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._params = self._normalize_params(base_params or {})

    @staticmethod
    def _normalize_params(params: Dict[str, object]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, v in params.items():
            if isinstance(v, bool):
                out[k] = "true" if v else "false"
            else:
                out[k] = str(v)
        return out

    def _build_url(self, path: str) -> str:
        from urllib.parse import urlencode

        q = urlencode(self._params)
        return f"{self._base_url}{path}" + (f"?{q}" if q else "")

    def post(self, path: str, payload: Dict) -> Dict:
        url = self._build_url(path)
        LOGGER.debug("POST to %s", url)
        resp = self._session.post(url, json=payload, timeout=self._REQUEST_TIMEOUT)
        code = getattr(resp, "status_code", 0)

        if code in (401, 403):
            raise RemindersAuthError(f"HTTP {code}: unauthorized")
        if code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise RemindersApiError(f"HTTP {code}", payload=body)

        try:
            return resp.json()
        except Exception:
            raise RemindersApiError(
                "Invalid JSON response", payload=getattr(resp, "text", None)
            )

    def get_bytes(self, url: str) -> bytes:
        LOGGER.debug("GET asset from %s", url)
        resp = self._session.get(url, timeout=self._REQUEST_TIMEOUT)
        code = getattr(resp, "status_code", 0)

        if code in (401, 403):
            raise RemindersAuthError(f"HTTP {code}: unauthorized")
        if code >= 400:
            raise RemindersApiError(
                f"HTTP {code} on asset GET", payload=getattr(resp, "text", None)
            )

        content = getattr(resp, "content", None)
        if isinstance(content, bytes):
            return content

        text = getattr(resp, "text", None)
        if isinstance(text, str):
            return text.encode("utf-8")

        raise RemindersApiError("Invalid asset response", payload=text)


class CloudKitRemindersClient:
    """
    Raw CloudKit service for the Reminders container.
    """

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
    ):
        self._http = _CloudKitClient(base_url, session, base_params)
        self._validation_extra = validation_extra

    def _validate_response(
        self, model_cls: type[_ResponseModelT], data: Dict
    ) -> _ResponseModelT:
        return model_cls.model_validate(
            data,
            extra=resolve_cloudkit_validation_extra(self._validation_extra),
        )

    def lookup(
        self,
        record_names: List[str],
        zone_id: CKZoneIDReq,
    ) -> CKLookupResponse:
        """Fetch records by ID."""
        payload = CKLookupRequest(
            records=[CKLookupDescriptor(recordName=n) for n in record_names],
            zoneID=zone_id,
        ).model_dump(mode="json", exclude_none=True)

        data = self._http.post("/records/lookup", payload)
        try:
            return self._validate_response(CKLookupResponse, data)
        except ValidationError as e:
            raise RemindersApiError(
                "Lookup response validation failed", payload=data
            ) from e

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
        results_limit: Optional[int] = None,
        continuation: Optional[str] = None,
    ) -> CKQueryResponse:
        payload = CKQueryRequest(
            query=query,
            zoneID=zone_id,
            desiredKeys=desired_keys,
            resultsLimit=results_limit,
            continuationMarker=continuation,
        ).model_dump(mode="json", exclude_none=True)

        data = self._http.post("/records/query", payload)
        try:
            return self._validate_response(CKQueryResponse, data)
        except ValidationError as e:
            raise RemindersApiError(
                "Query response validation failed", payload=data
            ) from e

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
        results_limit: Optional[int] = None,
    ) -> CKZoneChangesResponse:
        """Fetch changes (sync) for a zone."""

        payload = CKZoneChangesRequest(
            zones=[zone_req],
            resultsLimit=results_limit,
        ).model_dump(mode="json", exclude_none=True)

        data = self._http.post("/changes/zone", payload)
        try:
            return self._validate_response(CKZoneChangesResponse, data)
        except ValidationError as e:
            raise RemindersApiError(
                "Changes response validation failed", payload=data
            ) from e

    def modify(
        self,
        *,
        operations: List[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: Optional[bool] = None,
    ) -> CKModifyResponse:
        """Modify (create/update/delete) records."""
        payload = CKModifyRequest(
            operations=operations,
            zoneID=zone_id,
            atomic=atomic,
        ).model_dump(mode="json", exclude_none=True)

        data = self._http.post("/records/modify", payload)
        try:
            return self._validate_response(CKModifyResponse, data)
        except ValidationError as e:
            raise RemindersApiError(
                "Modify response validation failed", payload=data
            ) from e

    def download_asset_bytes(self, url: str) -> bytes:
        """Download raw bytes from a CloudKit asset URL."""
        return self._http.get_bytes(url)
