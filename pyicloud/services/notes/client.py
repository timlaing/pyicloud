"""
Low-level CloudKit client for the Notes container.

This \"escape hatch\" is also used internally by NotesService to implement
developer-friendly methods. It returns typed Pydantic models from
pyicloud.services.notes_models.cloudkit and hides HTTP details.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Iterable, Iterator, List, Optional, TypeVar
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKFVString,
    CKLookupDescriptor,
    CKLookupRequest,
    CKLookupResponse,
    CKQueryFilterBy,
    CKQueryObject,
    CKQueryRequest,
    CKQueryResponse,
    CKZoneChangesRequest,
    CKZoneChangesResponse,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CloudKitExtraMode,
    resolve_cloudkit_validation_extra,
)

LOGGER = logging.getLogger(__name__)
_ResponseModelT = TypeVar("_ResponseModelT")
DEFAULT_TIMEOUT = (10.0, 60.0)


# ------------------------------- Errors --------------------------------------


class NotesError(Exception):
    """Base Notes transport error."""


class NotesAuthError(NotesError):
    """Auth/PCS/cookie issues (401/403)."""


class NotesRateLimited(NotesError):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotesApiError(NotesError):
    """Catch-all API error."""

    def __init__(self, message: str, payload: Optional[object] = None):
        super().__init__(message)
        self.payload = payload


# ------------------------------- Transport -----------------------------------


class _CloudKitClient:
    """
    Minimal HTTP transport:
      - JSON requests via `json=payload`
      - Lowercase boolean query params
      - Bounded debug dumps (PYICLOUD_DEBUG_MAX_BYTES)
    """

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ):
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._params = self._normalize_params(base_params or {})
        self._timeout = timeout
        LOGGER.debug("Initialized _CloudKitClient with base_url: %s", self._base_url)

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

    @staticmethod
    def _redact_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def post(self, path: str, payload: Dict) -> Dict:
        url = self._build_url(path)
        redacted_url = self._redact_url(url)
        LOGGER.info("POST to %s", redacted_url)
        resp = self._session.post(url, json=payload, timeout=self._timeout)
        code = getattr(resp, "status_code", 0)
        LOGGER.debug("POST to %s returned status %d", redacted_url, code)
        if code >= 400:
            self._dump_http_debug(path.strip("/"), url, payload, resp)
            if code in (401, 403):
                LOGGER.error(
                    "POST to %s failed with auth error: %d", redacted_url, code
                )
                raise NotesAuthError(f"HTTP {code}: unauthorized")
            if code == 429:
                retry_after = None
                try:
                    hdr = resp.headers.get("Retry-After")
                    if hdr:
                        retry_after = float(hdr)
                except Exception:
                    retry_after = None
                LOGGER.warning(
                    "POST to %s was rate-limited. Retry after: %s",
                    redacted_url,
                    retry_after,
                )
                raise NotesRateLimited(
                    "HTTP 429: rate limited", retry_after=retry_after
                )
            # Try to include server json error if possible
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            LOGGER.error("POST to %s failed with code %d", redacted_url, code)
            raise NotesApiError(f"HTTP {code}", payload=body)
        try:
            json_response = resp.json()
            LOGGER.debug("Successfully parsed JSON response from %s", redacted_url)
            return json_response
        except Exception:
            self._dump_http_debug(path.strip("/"), url, payload, resp)
            LOGGER.error("Failed to parse JSON response from %s", redacted_url)
            raise NotesApiError(
                "Invalid JSON response", payload=getattr(resp, "text", None)
            )

    @staticmethod
    def _dump_http_debug(op: str, url: str, payload: Dict, resp) -> None:
        if not os.getenv("PYICLOUD_NOTES_DEBUG"):
            return
        ts = __import__("time").strftime("%Y%m%d-%H%M%S")
        out_dir = os.path.join("workspace", "notes_debug")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return
        # Request
        req_path = os.path.join(out_dir, f"{ts}_{op}_http_request.json")
        try:
            with open(req_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"url": url, "payload": payload}, f, ensure_ascii=False, indent=2
                )
        except Exception:
            pass
        # Response
        res_path = os.path.join(out_dir, f"{ts}_{op}_http_response.txt")
        try:
            status = getattr(resp, "status_code", None)
            headers = getattr(resp, "headers", {})
            body_text = None
            try:
                body_text = resp.text
            except Exception:
                body_text = None
            with open(res_path, "w", encoding="utf-8") as f:
                f.write(f"status={status}\nurl={url}\nheaders={dict(headers)}\n\n")
                if body_text:
                    max_bytes = int(os.getenv("PYICLOUD_DEBUG_MAX_BYTES", "524288"))
                    if len(body_text) > max_bytes:
                        f.write(body_text[:max_bytes] + "\n[truncated]\n")
                    else:
                        f.write(body_text)
        except Exception:
            pass

    # Simple helpers for assets (streaming GET)
    def get_stream(self, url: str, chunk_size: int = 65536) -> Iterator[bytes]:
        redacted_url = self._redact_url(url)
        LOGGER.info("GET stream from %s", redacted_url)
        resp = self._session.get(url, stream=True, timeout=self._timeout)
        code = getattr(resp, "status_code", 0)
        if code >= 400:
            self._dump_http_debug("asset_get", url, {}, resp)
            LOGGER.error("GET stream from %s failed with code %d", redacted_url, code)
            raise NotesApiError(
                f"HTTP {code} on asset GET", payload=getattr(resp, "text", None)
            )
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk


# ------------------------------ Raw client -----------------------------------


class CloudKitNotesClient:
    """
    Raw CloudKit service for the Notes container.

    Methods map 1:1 to CloudKit endpoints:
      - /records/query
      - /records/lookup
      - /changes/zone
    """

    def __init__(
        self,
        base_url: str,
        session,
        base_params: Dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ):
        self._http = _CloudKitClient(
            base_url,
            session,
            base_params,
            timeout=timeout,
        )
        self._validation_extra = validation_extra
        LOGGER.info("CloudKitNotesClient initialized.")

    def _validate_response(
        self, model_cls: type[_ResponseModelT], data: Dict
    ) -> _ResponseModelT:
        return model_cls.model_validate(
            data,
            extra=resolve_cloudkit_validation_extra(self._validation_extra),
        )

    # ----- Query -----

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
        results_limit: Optional[int] = None,
        continuation: Optional[str] = None,
    ) -> CKQueryResponse:
        LOGGER.info("Executing query for recordType: %s", query.recordType)
        payload = CKQueryRequest(
            query=query,
            zoneID=zone_id,
            desiredKeys=desired_keys,
            resultsLimit=results_limit,
            continuationMarker=continuation,
        ).model_dump(exclude_none=True)
        data = self._http.post("/records/query", payload)
        try:
            resp = self._validate_response(CKQueryResponse, data)
            LOGGER.info("Query returned %d records.", len(resp.records))
            return resp
        except ValidationError as e:
            self._log_validation("records.query", data, e)
            LOGGER.error("Query response validation failed.")
            raise NotesApiError("Query response validation failed", payload=data) from e

    # ----- Lookup -----

    def lookup(
        self,
        record_names: Iterable[str],
        *,
        desired_keys: Optional[List[str]] = None,
    ) -> CKLookupResponse:
        record_names_list = list(record_names)
        LOGGER.info("Executing lookup for %d records.", len(record_names_list))
        req = CKLookupRequest(
            records=[
                CKLookupDescriptor(recordName=str(rn)) for rn in record_names_list
            ],
            zoneID=CKZoneIDReq(zoneName="Notes"),
            desiredKeys=desired_keys,
        )
        payload = req.model_dump(exclude_none=True)
        data = self._http.post("/records/lookup", payload)
        try:
            resp = self._validate_response(CKLookupResponse, data)
            LOGGER.info("Lookup returned %d records.", len(resp.records))
            return resp
        except ValidationError as e:
            self._log_validation("records.lookup", data, e)
            LOGGER.error("Lookup response validation failed.")
            raise NotesApiError(
                "Lookup response validation failed", payload=data
            ) from e

    # ----- Changes (paged generator) -----

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
    ) -> Iterator[CKZoneChangesZone]:
        req = CKZoneChangesRequest(zones=[zone_req])
        LOGGER.info("Start fetching changes for zone: %s", zone_req.zoneID.zoneName)
        page_num = 1
        while True:
            payload = req.model_dump(exclude_none=True)
            LOGGER.debug("Fetching changes page %d", page_num)
            data = self._http.post("/changes/zone", payload)
            try:
                envelope = self._validate_response(CKZoneChangesResponse, data)
            except ValidationError as e:
                self._log_validation("changes.zone", data, e)
                LOGGER.error("Changes response validation failed.")
                raise NotesApiError(
                    "Changes response validation failed", payload=data
                ) from e
            zone = envelope.zones[0] if envelope.zones else None
            if not zone:
                LOGGER.info("No more changes available.")
                return

            LOGGER.info(
                "Changes page %d returned %d records.", page_num, len(zone.records)
            )
            yield zone

            if not zone.moreComing:
                LOGGER.info("All changes fetched.")
                return
            # advance sync token
            LOGGER.debug("More changes to come, advancing sync token.")
            req.zones[0].syncToken = zone.syncToken
            page_num += 1

    # ----- Asset helpers -----

    def download_asset_stream(
        self,
        url: str,
        *,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        yield from self._http.get_stream(url, chunk_size=chunk_size)

    def download_asset_to(self, url: str, directory: str) -> str:
        import os
        import uuid

        LOGGER.info(
            "Downloading asset from %s to directory %s",
            self._http._redact_url(url),
            directory,
        )
        os.makedirs(directory, exist_ok=True)
        fname = f"icloud-asset-{uuid.uuid4().hex}"
        path = os.path.join(directory, fname)
        with open(path, "wb") as f:
            for chunk in self.download_asset_stream(url):
                f.write(chunk)
        LOGGER.info("Finished downloading asset to %s", path)
        return path

    # ----- Sync token convenience -----

    def current_sync_token(self, *, zone_name: str) -> str:
        """
        Fetch a current sync token cheaply by issuing a zero-limit query that
        requests getCurrentSyncToken=true (already in params) and reading the top-level token.

        Some deployments place the token in CKQueryResponse.syncToken; if absent,
        we fall back to a one-shot changes call (no records) to harvest a token.
        """
        LOGGER.info("Fetching current sync token for zone: %s", zone_name)
        # Approach 1: /records/query on SearchIndexes with limit 1
        LOGGER.debug("Attempting to get sync token via cheap query.")
        q = CKQueryObject(
            recordType="SearchIndexes",
            filterBy=[
                CKQueryFilterBy(
                    comparator="EQUALS",
                    fieldName="indexName",
                    fieldValue=CKFVString(type="STRING", value="recents"),
                )
            ],
        )
        payload = CKQueryRequest(
            query=q,
            zoneID=CKZoneIDReq(zoneName=zone_name),
            resultsLimit=1,
        ).model_dump(exclude_none=True)
        try:
            data = self._http.post("/records/query", payload)
            resp = self._validate_response(CKQueryResponse, data)
            if getattr(resp, "syncToken", None):
                LOGGER.info("Successfully obtained sync token via query.")
                return str(resp.syncToken)
        except Exception as e:
            LOGGER.warning(
                "Failed to get sync token via query, falling back. Error: %s", e
            )
            # ignore and fall back
            pass

        # Approach 2: one empty /changes/zone call to get initial token
        LOGGER.debug("Falling back to get sync token via changes call.")
        req = CKZoneChangesRequest(
            zones=[
                CKZoneChangesZoneReq(
                    zoneID={"zoneName": zone_name, "zoneType": "REGULAR_CUSTOM_ZONE"},  # type: ignore[dict-item]
                    desiredRecordTypes=[],
                    desiredKeys=[],
                    reverse=False,
                )
            ]
        )
        data = self._http.post("/changes/zone", req.model_dump(exclude_none=True))
        env = self._validate_response(CKZoneChangesResponse, data)
        z = env.zones[0] if env.zones else None
        if z and getattr(z, "syncToken", None):
            LOGGER.info("Successfully obtained sync token via changes call.")
            return str(z.syncToken)

        LOGGER.error("Failed to obtain sync token for zone: %s", zone_name)
        raise NotesApiError("Unable to obtain sync token")

    # ----- Debug -----

    @staticmethod
    def _log_validation(op: str, data: Dict, err: ValidationError) -> None:
        if not os.getenv("PYICLOUD_NOTES_DEBUG"):
            return
        ts = __import__("time").strftime("%Y%m%d-%H%M%S")
        out_dir = os.path.join("workspace", "notes_debug")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return
        path = os.path.join(out_dir, f"{ts}_{op}_validation.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"op": op, "errors": err.errors(), "data": data},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            pass
