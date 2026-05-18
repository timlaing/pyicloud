"""Reusable typed CloudKit container client."""

from __future__ import annotations

import logging
from typing import Callable, Dict, Iterable, Iterator, List, Literal, Optional, TypeVar
from urllib.parse import urlencode, urlsplit, urlunsplit

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
CloudKitBoolParamStyle = Literal["python", "lower"]
CloudKitDebugHook = Callable[[str, str, Dict, object], None]


def redact_cloudkit_url(url: str) -> str:
    """Return a CloudKit URL without query parameters or fragments."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        timeout: tuple[float, float] | None = None,
        bool_param_style: CloudKitBoolParamStyle = "python",
        redact_urls: bool = False,
        debug_hook: CloudKitDebugHook | None = None,
        handle_rate_limits: bool = True,
    ):
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._params = self._normalize_params(
            base_params or {}, bool_param_style=bool_param_style
        )
        self._timeout = timeout or self._REQUEST_TIMEOUT
        self._redact_urls = redact_urls
        self._debug_hook = debug_hook
        self._handle_rate_limits = handle_rate_limits

    @staticmethod
    def _normalize_params(
        params: Dict[str, object],
        *,
        bool_param_style: CloudKitBoolParamStyle = "python",
    ) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, value in params.items():
            if isinstance(value, bool) and bool_param_style == "lower":
                out[key] = "true" if value else "false"
            else:
                out[key] = str(value)
        return out

    def build_url(self, path: str) -> str:
        q = urlencode(self._params)
        return f"{self._base_url}{path}" + (f"?{q}" if q else "")

    def _display_url(self, url: str) -> str:
        if self._redact_urls:
            return redact_cloudkit_url(url)
        return url

    def _run_debug_hook(self, op: str, url: str, payload: Dict, response) -> None:
        if self._debug_hook is None:
            return
        try:
            self._debug_hook(op, url, payload, response)
        except Exception:
            LOGGER.debug("CloudKit debug hook failed for %s", op, exc_info=True)

    def post(self, path: str, payload: Dict, *, headers: Dict | None = None) -> Dict:
        url = self.build_url(path)
        op = path.strip("/")
        display_url = self._display_url(url) if self._redact_urls else path
        LOGGER.debug("CloudKit POST %s", display_url)
        kwargs = {"json": payload, "timeout": self._timeout}
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
            self._run_debug_hook(op, url, payload, resp)
            raise CloudKitAuthError(f"HTTP {code}: unauthorized")
        if code == 429 and self._handle_rate_limits:
            self._run_debug_hook(op, url, payload, resp)
            retry_after = None
            try:
                hdr = resp.headers.get("Retry-After")
                if hdr:
                    retry_after = float(hdr)
            except Exception:
                retry_after = None
            raise CloudKitRateLimited("HTTP 429: rate limited", retry_after=retry_after)
        if code >= 400:
            self._run_debug_hook(op, url, payload, resp)
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise CloudKitApiError(f"HTTP {code}", payload=body)

        try:
            return resp.json()
        except Exception as exc:
            self._run_debug_hook(op, url, payload, resp)
            raise CloudKitApiError(
                "Invalid JSON response",
                payload=getattr(resp, "text", None),
            ) from exc

    def get_bytes(self, url: str) -> bytes:
        LOGGER.debug("CloudKit asset GET <redacted>")
        resp = self._session.get(url, timeout=self._timeout)
        code = getattr(resp, "status_code", 0)
        if not isinstance(code, int):
            code = 200
        if code in (401, 403):
            self._run_debug_hook("asset_get", url, {}, resp)
            raise CloudKitAuthError(f"HTTP {code}: unauthorized")
        if code == 429 and self._handle_rate_limits:
            self._run_debug_hook("asset_get", url, {}, resp)
            retry_after = None
            try:
                hdr = resp.headers.get("Retry-After")
                if hdr:
                    retry_after = float(hdr)
            except Exception:
                retry_after = None
            raise CloudKitRateLimited("HTTP 429: rate limited", retry_after=retry_after)
        if code >= 400:
            self._run_debug_hook("asset_get", url, {}, resp)
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

    def get_stream(self, url: str, *, chunk_size: int = 65536) -> Iterator[bytes]:
        LOGGER.debug("CloudKit asset stream GET %s", self._display_url(url))
        resp = self._session.get(url, stream=True, timeout=self._timeout)
        try:
            code = getattr(resp, "status_code", 0)
            if not isinstance(code, int):
                code = 200
            if code in (401, 403):
                self._run_debug_hook("asset_get", url, {}, resp)
                raise CloudKitAuthError(f"HTTP {code}: unauthorized")
            if code == 429 and self._handle_rate_limits:
                self._run_debug_hook("asset_get", url, {}, resp)
                retry_after = None
                try:
                    hdr = resp.headers.get("Retry-After")
                    if hdr:
                        retry_after = float(hdr)
                except Exception:
                    retry_after = None
                raise CloudKitRateLimited(
                    "HTTP 429: rate limited", retry_after=retry_after
                )
            if code >= 400:
                self._run_debug_hook("asset_get", url, {}, resp)
                raise CloudKitApiError(
                    f"HTTP {code} on asset GET",
                    payload=getattr(resp, "text", None),
                )
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class CloudKitContainerClient:
    """Typed CloudKit client for a single container/environment/scope."""

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
        timeout: tuple[float, float] | None = None,
        bool_param_style: CloudKitBoolParamStyle = "python",
        redact_urls: bool = False,
        debug_hook: CloudKitDebugHook | None = None,
        handle_rate_limits: bool = True,
    ):
        self._http = _CloudKitHTTP(
            base_url,
            session,
            base_params,
            timeout=timeout,
            bool_param_style=bool_param_style,
            redact_urls=redact_urls,
            debug_hook=debug_hook,
            handle_rate_limits=handle_rate_limits,
        )
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
        zone_id: Optional[CKZoneIDReq] = None,
        desired_keys: Optional[List[str]] = None,
        results_limit: Optional[int] = None,
        continuation: Optional[str] = None,
        zone_wide: bool = False,
    ) -> CKQueryResponse:
        if zone_wide and zone_id is not None:
            raise ValueError("zone_id must be omitted when zone_wide=True")
        if not zone_wide and zone_id is None:
            raise ValueError("zone_id is required unless zone_wide=True")
        payload = CKQueryRequest(
            query=query,
            zoneID=zone_id,
            desiredKeys=desired_keys,
            resultsLimit=results_limit,
            continuationMarker=continuation,
            zoneWide=zone_wide if zone_wide else None,
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

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
        results_limit: Optional[int] = None,
    ) -> CKZoneChangesResponse:
        payload = CKZoneChangesRequest(
            zones=[zone_req],
            resultsLimit=results_limit,
        ).model_dump(mode="json", exclude_none=True)
        data = self._http.post("/changes/zone", payload)
        try:
            return self._validate_response(CKZoneChangesResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Changes response validation failed",
                payload=data,
            ) from exc

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

    def download_asset_stream(
        self,
        url: str,
        *,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        yield from self._http.get_stream(url, chunk_size=chunk_size)

    def query_sync_token(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        results_limit: int = 1,
    ) -> str | None:
        payload = CKQueryRequest(
            query=query,
            zoneID=zone_id,
            resultsLimit=results_limit,
        ).model_dump(mode="json", exclude_none=True)
        data = self._http.post("/records/query", payload)
        try:
            response = self._validate_response(CKQueryResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Sync token query response validation failed",
                payload=data,
            ) from exc
        if getattr(response, "syncToken", None):
            return str(response.syncToken)
        return None


__all__ = [
    "CloudKitApiError",
    "CloudKitAuthError",
    "CloudKitContainerClient",
    "CloudKitRateLimited",
    "redact_cloudkit_url",
]
