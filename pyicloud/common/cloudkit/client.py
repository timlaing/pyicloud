"""Reusable typed CloudKit container client."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
import logging
from typing import Any, Literal, TypeVar, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import ValidationError
from requests import Response

from pyicloud.session import PyiCloudSession

from .base import CloudKitExtraMode, resolve_cloudkit_validation_extra
from .models import (
    CKDatabaseChangesResponse,
    CKErrorItem,
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
CloudKitDebugHook = Callable[[str, str, dict[str, Any], Response], None]

_RATE_LIMITED = "HTTP 429: rate limited"


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

    def __init__(self, message: str, *, payload: Any | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class _CloudKitHTTP:
    """Minimal HTTP transport shared by typed CloudKit container clients."""

    _REQUEST_TIMEOUT = (10.0, 60.0)

    def __init__(
        self,
        base_url: str,
        session: PyiCloudSession,
        base_params: dict[str, object],
        *,
        timeout: tuple[float, float] | None = None,
        bool_param_style: CloudKitBoolParamStyle = "python",
        redact_urls: bool = False,
        debug_hook: CloudKitDebugHook | None = None,
        handle_rate_limits: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._params = self._normalize_params(
            base_params or {}, bool_param_style=bool_param_style
        )
        self._timeout = timeout or self._REQUEST_TIMEOUT
        self._redact_urls = redact_urls
        self._debug_hook = debug_hook
        self._handle_rate_limits = handle_rate_limits

    @property
    def timeout(self) -> tuple[float, float]:
        """Return the configured request timeout."""
        return self._timeout

    @staticmethod
    def _normalize_params(
        params: dict[str, object],
        *,
        bool_param_style: CloudKitBoolParamStyle = "python",
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in params.items():
            if isinstance(value, bool) and bool_param_style == "lower":
                out[key] = "true" if value else "false"
            else:
                out[key] = str(value)
        return out

    def build_url(self, path: str) -> str:
        """Build a full request URL from a base path and query parameters."""
        q = urlencode(self._params)
        return f"{self._base_url}{path}" + (f"?{q}" if q else "")

    def _display_url(self, url: str) -> str:
        if self._redact_urls:
            return redact_cloudkit_url(url)
        return url

    def _run_debug_hook(
        self, op: str, url: str, payload: dict[str, Any], response: Response
    ) -> None:
        if self._debug_hook is None:
            return
        try:
            self._debug_hook(op, url, payload, response)
        except Exception:
            LOGGER.debug("CloudKit debug hook failed for %s", op, exc_info=True)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a CloudKit POST request and return the parsed JSON response."""
        url = self.build_url(path)
        op = path.strip("/")
        display_url = self._display_url(url) if self._redact_urls else path
        LOGGER.debug("CloudKit POST %s", display_url)
        resp = self._session.post(
            url,
            json=payload,
            timeout=self._timeout,
            headers=headers,
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
            raise CloudKitRateLimited(_RATE_LIMITED, retry_after=retry_after)
        if code >= 400:
            self._run_debug_hook(op, url, payload, resp)
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise CloudKitApiError(f"HTTP {code}", payload=body)

        try:
            return cast(dict[str, Any], resp.json())
        except Exception as exc:
            self._run_debug_hook(op, url, payload, resp)
            raise CloudKitApiError(
                "Invalid JSON response",
                payload=getattr(resp, "text", None),
            ) from exc

    def get_bytes(self, url: str) -> bytes:
        """Fetch an asset URL and return its contents as bytes."""
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
            raise CloudKitRateLimited(_RATE_LIMITED, retry_after=retry_after)
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
        """Stream an asset URL in chunks of the given size."""
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
                raise CloudKitRateLimited(_RATE_LIMITED, retry_after=retry_after)
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
                with suppress(Exception):
                    close()


def _raise_for_record_errors(response: CKModifyResponse) -> None:
    """Raise when CloudKit reported a per-record failure inside a 200 response.

    CloudKit answers ``200`` for a modify whose records were rejected, and puts
    the reason in the record entry. Callers looking for their record by name
    find a ``CKErrorItem`` instead, and every one of them silently skipped it:
    a rejected write surfaced as a missing record, or as nothing at all.

    Raising here means the reason Apple gave -- "Attempt to save encrypted data
    in non encrypted field type", say -- reaches the caller instead of being
    replaced by a guess about the response shape.
    """

    errors = [record for record in response.records if isinstance(record, CKErrorItem)]
    if not errors:
        return

    detail = "; ".join(
        f"{error.recordName or '<unnamed>'}: {error.serverErrorCode}"
        + (f" ({error.reason})" if error.reason else "")
        for error in errors
    )
    raise CloudKitApiError(
        f"CloudKit rejected {len(errors)} record(s) -- {detail}",
        payload=response.model_dump(mode="json", exclude_none=True),
    )


class CloudKitContainerClient:
    """Typed CloudKit client for a single container/environment/scope."""

    def __init__(
        self,
        base_url: str,
        session: PyiCloudSession,
        base_params: dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
        timeout: tuple[float, float] | None = None,
        bool_param_style: CloudKitBoolParamStyle = "python",
        redact_urls: bool = False,
        debug_hook: CloudKitDebugHook | None = None,
        handle_rate_limits: bool = True,
    ) -> None:
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
        data: dict[str, Any],
    ) -> _ResponseModelT:
        return model_cls.model_validate(
            data,
            extra=resolve_cloudkit_validation_extra(self._validation_extra),
        )

    @property
    def timeout(self) -> tuple[float, float]:
        """Return the configured request timeout."""
        return self._http.timeout

    def raw_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST a raw request body to an arbitrary CloudKit endpoint path."""
        return self._http.post(path, payload, headers=headers)

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq | None = None,
        desired_keys: list[str] | None = None,
        results_limit: int | None = None,
        continuation: str | None = None,
        zone_wide: bool = False,
    ) -> CKQueryResponse:
        """Run a CloudKit query and return the parsed response."""
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
        desired_keys: list[str] | None = None,
    ) -> CKLookupResponse:
        """Look up records by name and return the parsed response."""
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
        results_limit: int | None = None,
    ) -> Iterator[CKZoneChangesZone]:
        """Yield zone changes, following continuation markers until done."""
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
        results_limit: int | None = None,
    ) -> CKZoneChangesResponse:
        """Fetch zone changes and return the parsed response."""
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
        operations: list[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: bool | None = None,
    ) -> CKModifyResponse:
        """Send record modify operations and return the parsed response."""
        payload = CKModifyRequest(
            operations=operations,
            zoneID=zone_id,
            atomic=atomic,
        ).model_dump(mode="json", exclude_none=True)
        data = self._http.post("/records/modify", payload)
        try:
            response = self._validate_response(CKModifyResponse, data)
        except ValidationError as exc:
            raise CloudKitApiError(
                "Modify response validation failed",
                payload=data,
            ) from exc
        _raise_for_record_errors(response)
        return response

    def zones_list(self) -> CKZoneListResponse:
        """List the container's zones and return the parsed response."""
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
        sync_token: str | None = None,
    ) -> CKDatabaseChangesResponse:
        """Fetch database-level changes and return the parsed response."""
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
        """Download an asset from the given URL as bytes."""
        return self._http.get_bytes(url)

    def download_asset_stream(
        self,
        url: str,
        *,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        """Stream an asset from the given URL in chunks."""
        yield from self._http.get_stream(url, chunk_size=chunk_size)

    def query_sync_token(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        results_limit: int = 1,
    ) -> str | None:
        """Run a query and return its sync token if present."""
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
