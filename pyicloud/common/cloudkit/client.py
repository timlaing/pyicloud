"""Reusable typed CloudKit container client."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Iterator, List, Optional, TypeVar
from urllib.parse import urlencode

from pydantic import ValidationError

from .base import CloudKitExtraMode, resolve_cloudkit_validation_extra
from .models import (
    CKDatabaseChangesResponse,
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
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CKZoneListResponse,
)

LOGGER = logging.getLogger(__name__)

_ResponseModelT = TypeVar(
    "_ResponseModelT",
    CKQueryResponse,
    CKLookupResponse,
    CKZoneChangesResponse,
    CKModifyResponse,
    CKZoneListResponse,
    CKDatabaseChangesResponse,
)


class CloudKitAuthError(Exception):
    """Raised when Apple rejects a CloudKit request due to auth/session state."""


class CloudKitRateLimited(Exception):
    """Raised when Apple rate-limits a CloudKit request."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CloudKitApiError(Exception):
    """Raised for transport, validation, or server-side CloudKit failures."""

    def __init__(self, message: str, *, payload=None) -> None:
        super().__init__(message)
        self.payload = payload


class _CloudKitHTTP:
    """Minimal HTTP transport shared by typed CloudKit container clients."""

    _REQUEST_TIMEOUT = (10.0, 60.0)

    def __init__(self, base_url: str, session, base_params: Dict[str, object]):
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._params = self._normalize_params(base_params or {})

    @staticmethod
    def _normalize_params(params: Dict[str, object]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, value in params.items():
            out[key] = str(value)
        return out

    def build_url(self, path: str) -> str:
        q = urlencode(self._params)
        return f"{self._base_url}{path}" + (f"?{q}" if q else "")

    def post(self, path: str, payload: Dict, *, headers: Dict | None = None) -> Dict:
        url = self.build_url(path)
        LOGGER.debug("CloudKit POST %s", url)
        kwargs = {"json": payload, "timeout": self._REQUEST_TIMEOUT}
        if headers is not None:
            kwargs["headers"] = headers
        resp = self._session.post(
            url,
            **kwargs,
        )
        code = getattr(resp, "status_code", 0)
        if not isinstance(code, int):
            code = 200

        if code in (401, 403):
            raise CloudKitAuthError(f"HTTP {code}: unauthorized")
        if code == 429:
            retry_after = None
            try:
                hdr = resp.headers.get("Retry-After")
                if hdr:
                    retry_after = float(hdr)
            except Exception:
                retry_after = None
            raise CloudKitRateLimited("HTTP 429: rate limited", retry_after=retry_after)
        if code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise CloudKitApiError(f"HTTP {code}", payload=body)

        try:
            return resp.json()
        except Exception as exc:
            raise CloudKitApiError(
                "Invalid JSON response",
                payload=getattr(resp, "text", None),
            ) from exc

    def get_bytes(self, url: str) -> bytes:
        LOGGER.debug("CloudKit asset GET %s", url)
        resp = self._session.get(url, timeout=self._REQUEST_TIMEOUT)
        code = getattr(resp, "status_code", 0)
        if not isinstance(code, int):
            code = 200
        if code in (401, 403):
            raise CloudKitAuthError(f"HTTP {code}: unauthorized")
        if code == 429:
            retry_after = None
            try:
                hdr = resp.headers.get("Retry-After")
                if hdr:
                    retry_after = float(hdr)
            except Exception:
                retry_after = None
            raise CloudKitRateLimited("HTTP 429: rate limited", retry_after=retry_after)
        if code >= 400:
            raise CloudKitApiError(
                f"HTTP {code} on asset GET",
                payload=getattr(resp, "text", None),
            )
        content = getattr(resp, "content", None)
        if isinstance(content, bytes):
            return content
        text = getattr(resp, "text", None)
        if isinstance(text, str):
            return text.encode("utf-8")
        raise CloudKitApiError("Invalid asset response", payload=text)


class CloudKitContainerClient:
    """Typed CloudKit client for a single container/environment/scope."""

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
    ):
        self._http = _CloudKitHTTP(base_url, session, base_params)
        self._validation_extra = validation_extra

    def _validate_response(
        self,
        model_cls: type[_ResponseModelT],
        data: Dict,
    ) -> _ResponseModelT:
        return model_cls.model_validate(
            data,
            extra=resolve_cloudkit_validation_extra(self._validation_extra),
        )

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
        except ValidationError as exc:
            raise CloudKitApiError(
                "Query response validation failed",
                payload=data,
            ) from exc

    def lookup(
        self,
        record_names: Iterable[str],
        *,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
    ) -> CKLookupResponse:
        payload = CKLookupRequest(
            records=[CKLookupDescriptor(recordName=str(name)) for name in record_names],
            zoneID=zone_id,
            desiredKeys=desired_keys,
        ).model_dump(mode="json", exclude_none=True)
        data = self._http.post("/records/lookup", payload)
        try:
            return self._validate_response(CKLookupResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Lookup response validation failed",
                payload=data,
            ) from exc

    def iter_changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
        results_limit: Optional[int] = None,
    ) -> Iterator[CKZoneChangesZone]:
        req = CKZoneChangesRequest(
            zones=[zone_req],
            resultsLimit=results_limit,
        )
        while True:
            payload = req.model_dump(mode="json", exclude_none=True)
            data = self._http.post("/changes/zone", payload)
            try:
                envelope = self._validate_response(CKZoneChangesResponse, data)
            except ValidationError as exc:
                raise CloudKitApiError(
                    "Changes response validation failed",
                    payload=data,
                ) from exc
            zone = envelope.zones[0] if envelope.zones else None
            if zone is None:
                return
            yield zone
            if not zone.moreComing:
                return
            req.zones[0].syncToken = zone.syncToken

    def modify(
        self,
        *,
        operations: List[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: Optional[bool] = None,
    ) -> CKModifyResponse:
        payload = CKModifyRequest(
            operations=operations,
            zoneID=zone_id,
            atomic=atomic,
        ).model_dump(mode="json", exclude_none=True)
        data = self._http.post("/records/modify", payload)
        try:
            return self._validate_response(CKModifyResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Modify response validation failed",
                payload=data,
            ) from exc

    def zones_list(self) -> CKZoneListResponse:
        data = self._http.post("/zones/list", {})
        try:
            return self._validate_response(CKZoneListResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Zones list response validation failed",
                payload=data,
            ) from exc

    def database_changes(
        self,
        *,
        sync_token: Optional[str] = None,
    ) -> CKDatabaseChangesResponse:
        payload = {}
        if sync_token:
            payload["syncToken"] = sync_token
        data = self._http.post("/changes/database", payload)
        try:
            return self._validate_response(CKDatabaseChangesResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Database changes response validation failed",
                payload=data,
            ) from exc

    def download_asset_bytes(self, url: str) -> bytes:
        return self._http.get_bytes(url)


__all__ = [
    "CloudKitApiError",
    "CloudKitAuthError",
    "CloudKitContainerClient",
    "CloudKitRateLimited",
]
