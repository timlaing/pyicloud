"""Low-level CloudKit client for the Invites container.

Holds three :class:`CloudKitContainerClient` sub-clients (one per CK database
scope: ``private``, ``shared``, ``public``) since Invites operates across all
three:

* ``private`` — owner's own events
* ``shared`` — events accepted as a guest
* ``public`` — share resolution (``records/resolve``, ``records/accept``)

The ``private`` and ``shared`` sub-clients use the standard CK query/lookup/
modify abstraction. The ``public`` sub-client is wrapped only for URL/HTTP
plumbing reuse; its operations (``records/resolve`` / ``records/accept``)
don't fit the records-in-zones shape and are issued via a small dedicated
helper that mirrors the common HTTP wrapper's auth/rate-limit/error behavior.

A follow-up PR may promote a ``scope=`` parameter to
:class:`CloudKitContainerClient` and collapse this composition into a single
client; see the Invites design doc.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Literal, NoReturn, Optional
from urllib.parse import urlencode

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
DEFAULT_TIMEOUT = (10.0, 60.0)

ScopeLiteral = Literal["private", "shared"]


# ------------------------------- Errors --------------------------------------


class InvitesError(Exception):
    """Base Invites transport error."""


class InvitesAuthError(InvitesError):
    """Auth/PCS/cookie issues (401/403)."""


class InvitesRateLimited(InvitesError):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class InvitesApiError(InvitesError):
    """Catch-all API error."""

    def __init__(self, message: str, payload: Optional[object] = None) -> None:
        super().__init__(message)
        self.payload = payload


# ------------------------------ Raw client -----------------------------------


class CloudKitInvitesClient:
    """Raw CloudKit service for the Invites container.

    Three sub-clients are constructed at init time (private, shared, public).
    Public methods take a ``scope`` argument to select between private and
    shared; ``resolve`` / ``accept`` are dedicated public-scope helpers.
    """

    def __init__(
        self,
        env_base_url: str,
        session: Any,
        base_params: Dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._base_params = dict(base_params or {})
        self._timeout = timeout
        self._public_base = f"{env_base_url}/public"

        common_kwargs: Dict[str, Any] = {
            "session": session,
            "base_params": base_params,
            "validation_extra": validation_extra,
            "timeout": timeout,
            "bool_param_style": "lower",
            "redact_urls": True,
        }
        self._private = CloudKitContainerClient(
            f"{env_base_url}/private", **common_kwargs
        )
        self._shared = CloudKitContainerClient(
            f"{env_base_url}/shared", **common_kwargs
        )
        LOGGER.info("CloudKitInvitesClient initialized.")

    def _client_for(self, scope: ScopeLiteral) -> CloudKitContainerClient:
        if scope == "private":
            return self._private
        if scope == "shared":
            return self._shared
        raise ValueError(f"Unsupported scope: {scope!r}")

    @staticmethod
    def _raise_invites_error(exc: Exception) -> NoReturn:
        cause = exc.__cause__ or exc
        if isinstance(exc, CloudKitAuthError):
            raise InvitesAuthError(str(exc)) from cause
        if isinstance(exc, CloudKitRateLimited):
            raise InvitesRateLimited(str(exc), retry_after=exc.retry_after) from cause
        if isinstance(exc, CloudKitApiError):
            raise InvitesApiError(str(exc), payload=exc.payload) from cause
        raise

    # ----- Records-in-zones scoped wrappers ----------------------------------

    def query(
        self,
        scope: ScopeLiteral,
        *,
        query: CKQueryObject,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
        results_limit: Optional[int] = None,
        continuation: Optional[str] = None,
    ) -> CKQueryResponse:
        try:
            return self._client_for(scope).query(
                query=query,
                zone_id=zone_id,
                desired_keys=desired_keys,
                results_limit=results_limit,
                continuation=continuation,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_invites_error(exc)

    def lookup(
        self,
        scope: ScopeLiteral,
        record_names: Iterable[str],
        *,
        zone_id: CKZoneIDReq,
        desired_keys: Optional[List[str]] = None,
    ) -> CKLookupResponse:
        try:
            return self._client_for(scope).lookup(
                record_names,
                zone_id=zone_id,
                desired_keys=desired_keys,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_invites_error(exc)

    def modify(
        self,
        scope: ScopeLiteral,
        *,
        operations: List[CKModifyOperation],
        zone_id: CKZoneIDReq,
        atomic: Optional[bool] = None,
    ) -> CKModifyResponse:
        try:
            return self._client_for(scope).modify(
                operations=operations,
                zone_id=zone_id,
                atomic=atomic,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_invites_error(exc)

    def changes(
        self,
        scope: ScopeLiteral,
        *,
        zone_req: CKZoneChangesZoneReq,
        results_limit: Optional[int] = None,
    ) -> CKZoneChangesResponse:
        try:
            return self._client_for(scope).changes(
                zone_req=zone_req,
                results_limit=results_limit,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_invites_error(exc)

    # ----- Public-scope resolve / accept -------------------------------------

    def resolve(self, short_guids: List[str]) -> Dict[str, Any]:
        """POST ``public/records/resolve`` — preview a share without joining."""
        return self._post_public(
            "/records/resolve",
            {"shortGUIDs": [{"value": g} for g in short_guids]},
        )

    def accept(self, short_guids: List[str]) -> Dict[str, Any]:
        """POST ``public/records/accept`` — accept a share and gain access."""
        return self._post_public(
            "/records/accept",
            {"shortGUIDs": [{"value": g} for g in short_guids]},
        )

    def _post_public(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to the public-scope endpoint.

        Mirrors the auth / rate-limit / error-response handling of the common
        client's HTTP wrapper. Inlined here (rather than promoted to common)
        to keep this PR scoped to the Invites service.
        """
        url = self._public_base + path
        params = self._normalized_params()
        if params:
            url = f"{url}?{urlencode(params)}"
        LOGGER.debug("CloudKit Invites POST %s", path)

        resp = self._session.post(url, json=payload, timeout=self._timeout)
        code = getattr(resp, "status_code", 0)
        if not isinstance(code, int):
            code = 200

        if code in (401, 403):
            raise InvitesAuthError(f"HTTP {code}: unauthorized")
        if code == 429:
            retry_after: Optional[float] = None
            try:
                hdr = resp.headers.get("Retry-After")
                if hdr:
                    retry_after = float(hdr)
            except Exception:
                retry_after = None
            raise InvitesRateLimited("HTTP 429: rate limited", retry_after=retry_after)
        if code >= 400:
            try:
                body: Any = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise InvitesApiError(f"HTTP {code}", payload=body)

        try:
            return resp.json()
        except Exception as exc:
            raise InvitesApiError(
                "Invalid JSON response",
                payload=getattr(resp, "text", None),
            ) from exc

    def _normalized_params(self) -> Dict[str, str]:
        """Stringify base params the same way the common HTTP wrapper does."""
        out: Dict[str, str] = {}
        for key, value in self._base_params.items():
            if isinstance(value, bool):
                out[key] = "true" if value else "false"
            else:
                out[key] = str(value)
        return out

    # ----- Asset helpers -----------------------------------------------------

    def download_asset_bytes(self, url: str) -> bytes:
        try:
            return self._private.download_asset_bytes(url)
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_invites_error(exc)
