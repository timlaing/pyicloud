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
from typing import Dict, Iterable, Iterator, List, NoReturn, Optional

from pydantic import ValidationError

from pyicloud.common.cloudkit import (
    CKFVString,
    CKLookupResponse,
    CKQueryFilterBy,
    CKQueryObject,
    CKQueryResponse,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneID,
    CKZoneIDReq,
    CloudKitExtraMode,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
    redact_cloudkit_url,
)

from ._constants import NOTES_ZONE_REQ

LOGGER = logging.getLogger(__name__)
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
        self._client = CloudKitContainerClient(
            base_url,
            session,
            base_params,
            validation_extra=validation_extra,
            timeout=timeout,
            bool_param_style="lower",
            redact_urls=True,
            debug_hook=self._dump_http_debug,
        )
        self._validation_extra = validation_extra
        LOGGER.info("CloudKitNotesClient initialized.")

    @staticmethod
    def _raise_notes_error(exc: Exception) -> NoReturn:
        cause = exc.__cause__ or exc
        if isinstance(exc, CloudKitAuthError):
            raise NotesAuthError(str(exc)) from cause
        if isinstance(exc, CloudKitRateLimited):
            raise NotesRateLimited(str(exc), retry_after=exc.retry_after) from cause
        if isinstance(exc, CloudKitApiError):
            raise NotesApiError(str(exc), payload=exc.payload) from cause
        raise

    def _log_cloudkit_validation(self, op: str, exc: Exception) -> bool:
        if isinstance(exc, CloudKitApiError) and isinstance(
            exc.__cause__, ValidationError
        ):
            self._log_validation(op, exc.payload or {}, exc.__cause__)
            return True
        return False

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
        try:
            resp = self._client.query(
                query=query,
                zone_id=zone_id,
                desired_keys=desired_keys,
                results_limit=results_limit,
                continuation=continuation,
            )
            LOGGER.info("Query returned %d records.", len(resp.records))
            return resp
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            if self._log_cloudkit_validation("records.query", exc):
                LOGGER.error("Query response validation failed.")
            self._raise_notes_error(exc)

    # ----- Lookup -----

    def lookup(
        self,
        record_names: Iterable[str],
        *,
        desired_keys: Optional[List[str]] = None,
    ) -> CKLookupResponse:
        record_names_list = list(record_names)
        LOGGER.info("Executing lookup for %d records.", len(record_names_list))
        try:
            resp = self._client.lookup(
                record_names_list,
                zone_id=NOTES_ZONE_REQ,
                desired_keys=desired_keys,
            )
            LOGGER.info("Lookup returned %d records.", len(resp.records))
            return resp
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            if self._log_cloudkit_validation("records.lookup", exc):
                LOGGER.error("Lookup response validation failed.")
            self._raise_notes_error(exc)

    # ----- Changes (paged generator) -----

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
    ) -> Iterator[CKZoneChangesZone]:
        LOGGER.info("Start fetching changes for zone: %s", zone_req.zoneID.zoneName)
        page_num = 1
        try:
            for zone in self._client.iter_changes(zone_req=zone_req):
                LOGGER.info(
                    "Changes page %d returned %d records.",
                    page_num,
                    len(zone.records),
                )
                yield zone
                if zone.moreComing:
                    LOGGER.debug("More changes to come, advancing sync token.")
                else:
                    LOGGER.info("All changes fetched.")
                page_num += 1
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            if self._log_cloudkit_validation("changes.zone", exc):
                LOGGER.error("Changes response validation failed.")
            self._raise_notes_error(exc)

    # ----- Asset helpers -----

    def download_asset_stream(
        self,
        url: str,
        *,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        try:
            yield from self._client.download_asset_stream(
                url,
                chunk_size=chunk_size,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_notes_error(exc)

    def download_asset_to(self, url: str, directory: str) -> str:
        import os
        import uuid

        LOGGER.info(
            "Downloading asset from %s to directory %s",
            redact_cloudkit_url(url),
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
        try:
            token = self._client.query_sync_token(
                query=q,
                zone_id=CKZoneIDReq(zoneName=zone_name),
            )
            if token:
                LOGGER.info("Successfully obtained sync token via query.")
                return token
        except Exception as e:
            LOGGER.warning(
                "Failed to get sync token via query, falling back. Error: %s", e
            )
            # ignore and fall back
            pass

        # Approach 2: one empty /changes/zone call to get initial token
        LOGGER.debug("Falling back to get sync token via changes call.")
        zone_req = CKZoneChangesZoneReq(
            zoneID=CKZoneID(zoneName=zone_name, zoneType="REGULAR_CUSTOM_ZONE"),
            desiredRecordTypes=[],
            desiredKeys=[],
            reverse=False,
        )
        try:
            env = self._client.changes(zone_req=zone_req)
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_notes_error(exc)
        z = env.zones[0] if env.zones else None
        if z and getattr(z, "syncToken", None):
            LOGGER.info("Successfully obtained sync token via changes call.")
            return str(z.syncToken)

        LOGGER.error("Failed to obtain sync token for zone: %s", zone_name)
        raise NotesApiError("Unable to obtain sync token")

    # ----- Debug -----

    @staticmethod
    def _dump_http_debug(op: str, url: str, payload: Dict, resp) -> None:
        if not os.getenv("PYICLOUD_NOTES_DEBUG"):
            return
        ts = __import__("time").strftime("%Y%m%d-%H%M%S")
        safe_op = op.replace("/", ".")
        out_dir = os.path.join("workspace", "notes_debug")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return
        req_path = os.path.join(out_dir, f"{ts}_{safe_op}_http_request.json")
        try:
            with open(req_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"url": url, "payload": payload}, f, ensure_ascii=False, indent=2
                )
        except Exception:
            pass
        res_path = os.path.join(out_dir, f"{ts}_{safe_op}_http_response.txt")
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
