"""
Low-level CloudKit client for the Reminders container.
"""

from __future__ import annotations

import logging
from typing import Dict, List, NoReturn, Optional

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKModifyOperation,
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKZoneChangesResponse,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CloudKitExtraMode,
)
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
)

LOGGER = logging.getLogger(__name__)


class RemindersAuthError(Exception):
    """Auth/PCS/cookie issues (401/403)."""


class RemindersApiError(Exception):
    """Catch-all API error."""

    def __init__(self, message: str, payload: Optional[object] = None):
        super().__init__(message)
        self.payload = payload


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
        self._client = CloudKitContainerClient(
            base_url,
            session,
            base_params,
            validation_extra=validation_extra,
            bool_param_style="lower",
            handle_rate_limits=False,
        )
        self._validation_extra = validation_extra

    @staticmethod
    def _raise_reminders_error(exc: Exception) -> NoReturn:
        cause = exc.__cause__ or exc
        if isinstance(exc, CloudKitAuthError):
            raise RemindersAuthError(str(exc)) from cause
        if isinstance(exc, CloudKitRateLimited):
            raise RemindersApiError(
                str(exc), payload={"retry_after": exc.retry_after}
            ) from cause
        if isinstance(exc, CloudKitApiError):
            raise RemindersApiError(str(exc), payload=exc.payload) from cause
        raise

    def lookup(
        self,
        record_names: List[str],
        zone_id: CKZoneIDReq,
    ) -> CKLookupResponse:
        """Fetch records by ID."""
        try:
            return self._client.lookup(record_names, zone_id=zone_id)
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_reminders_error(exc)

    def query(
        self,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
        results_limit: Optional[int] = None,
        continuation: Optional[str] = None,
    ) -> CKQueryResponse:
        try:
            return self._client.query(
                query=query,
                zone_id=zone_id,
                desired_keys=desired_keys,
                results_limit=results_limit,
                continuation=continuation,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_reminders_error(exc)

    def current_sync_token(
        self,
        *,
        zone_id: CKZoneIDReq,
        record_type: str = "reminderList",
    ) -> str | None:
        """Fetch the current zone sync token using a lightweight query first."""
        try:
            return self._client.query_sync_token(
                query=CKQueryObject(recordType=record_type),
                zone_id=zone_id,
            )
        except CloudKitAuthError as exc:
            self._raise_reminders_error(exc)
        except (CloudKitApiError, CloudKitRateLimited) as exc:
            LOGGER.debug("current_sync_token suppressed CloudKit error", exc_info=exc)
            return None

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
        results_limit: Optional[int] = None,
    ) -> CKZoneChangesResponse:
        """Fetch changes (sync) for a zone."""
        try:
            return self._client.changes(
                zone_req=zone_req,
                results_limit=results_limit,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_reminders_error(exc)

    def modify(
        self,
        *,
        operations: List[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: Optional[bool] = None,
    ) -> CKModifyResponse:
        """Modify (create/update/delete) records."""
        try:
            return self._client.modify(
                operations=operations,
                zone_id=zone_id,
                atomic=atomic,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_reminders_error(exc)

    def download_asset_bytes(self, url: str) -> bytes:
        """Download raw bytes from a CloudKit asset URL."""
        try:
            return self._client.download_asset_bytes(url)
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_reminders_error(exc)
