"""Path-free existing Photos upload protocol; internal, not an approval surface."""

from __future__ import annotations

import hashlib
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pyicloud.services.photos.models import (
    PhotosCreateUploadUrlRequest,
    PhotosCreateUploadUrlResponse,
    PhotosPutAssetFile,
    PhotosPutAssetRequest,
    PhotosPutAssetResult,
    PhotosSingleFileUploadResponse,
)
from pyicloud.services.photos.upload import CREATE_UPLOAD_URL_PATH, PUT_ASSET_PATH

from .authorization import WriteDenied
from .context import BoundedAdapter
from .one_shot import checked_adapter, prepared_url
from .upload_staging import StagedUpload
from .upload_transport import (
    UploadDispatchState,
    UploadNotDispatched,
    UploadTransport,
    checked_upload_url,
)


def validate_photo_upload_session(
    session: Any, root: str, params: dict[str, Any]
) -> list[BoundedAdapter]:
    """Validate both exact registration adapters before spending Photos resources."""
    try:
        parsed = urlsplit(root)
    except ValueError:
        raise WriteDenied("unsupported_upload_route") from None
    hosts = frozenset({parsed.hostname}) if parsed.hostname else frozenset()
    checked_upload_url(root, hosts)
    if "?" in root or "#" in root:
        raise WriteDenied("unsupported_upload_route")
    adapters = []
    for path in (CREATE_UPLOAD_URL_PATH, PUT_ASSET_PATH):
        adapter = checked_adapter(
            session,
            prepared_url(session, root.rstrip("/") + path, params),
            bounded=True,
        )
        assert isinstance(adapter, BoundedAdapter)
        adapters.append(adapter)
    return adapters


def _validate_photo_stage(
    stage: StagedUpload, modified_ms: int, stage_acquired: bool
) -> None:
    """Require the existing immutable byte/leaf/time contract before dispatch."""
    if (
        not isinstance(stage, StagedUpload)
        or not isinstance(stage.data, bytes)
        or not 0 < stage.size <= 4 * 1024 * 1024
        or len(stage.data) != stage.size
        or hashlib.sha256(stage.data).hexdigest() != stage.sha256
        or (not stage_acquired and stage.expires_at <= time.monotonic())
        or not stage.name
        or stage.name in {".", ".."}
        or any(char in stage.name for char in ("/", "\\", "\x00"))
        or isinstance(modified_ms, bool)
        or not isinstance(modified_ms, int)
        or modified_ms < 0
    ):
        raise WriteDenied("invalid_upload_bytes")


def _photo_parameter_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    """Copy the existing parameters only after validating the account identifier."""
    account = params.get("dsid")
    if not isinstance(account, str) or not account.isascii() or not account.isdecimal():
        raise WriteDenied("account_unavailable")
    return dict(params)


class PhotoUploadBytes:
    """One reservation/transfer/registration, never a mutation retry or success claim.

    Route, account parameters and transfer hosts must be trusted internal selections.
    Returned IDs require independent private-library indexing verification before a
    future receipt-bound adapter can return success. No filesystem materialization.
    """

    def __init__(self, transport: UploadTransport, timeout: float = 30) -> None:
        self.transport = transport
        self.timeout = timeout

    def upload(
        self,
        session: Any,
        root: str,
        params: dict[str, Any],
        zone: str,
        stage: StagedUpload,
        *,
        modified_ms: int,
        stage_acquired: bool = False,
        owner_deadline: float | None = None,
    ) -> PhotosPutAssetResult:
        """Use the existing typed wire contract with exact immutable staged bytes."""
        parsed = urlsplit(root)
        hosts = frozenset({parsed.hostname}) if parsed.hostname else frozenset()
        checked_upload_url(root, hosts)
        if parsed.query or parsed.fragment or not zone or zone != "PrimarySync":
            raise WriteDenied("unsupported_upload_route")
        _validate_photo_stage(stage, modified_ms, stage_acquired)
        snapshot = _photo_parameter_snapshot(params)
        identifier = str(uuid4())
        deadline = time.monotonic() + self.timeout
        if owner_deadline is not None:
            deadline = min(deadline, owner_deadline)

        def url(path: str) -> str:
            return root.rstrip("/") + path

        dispatch_state = UploadDispatchState()
        try:
            reservation = PhotosCreateUploadUrlResponse.model_validate(
                self.transport.register(
                    session,
                    url(CREATE_UPLOAD_URL_PATH),
                    hosts,
                    PhotosCreateUploadUrlRequest(
                        zoneName=zone, assets={identifier: stage.size}
                    ).model_dump(mode="json"),
                    deadline=deadline,
                    params=snapshot,
                    dispatch_state=dispatch_state,
                )
            )
            if set(reservation.uploadUrls) != {identifier}:
                raise WriteDenied("upload_outcome_uncertain")
            receipt = PhotosSingleFileUploadResponse.model_validate(
                self.transport.transfer(
                    reservation.uploadUrls[identifier], stage.data, deadline=deadline
                )
            ).singleFile
            if receipt.size != stage.size or not all((
                receipt.fileChecksum,
                receipt.referenceChecksum,
                receipt.wrappingKey,
                receipt.receipt,
            )):
                raise WriteDenied("upload_outcome_uncertain")
            result = self.transport.register(
                session,
                url(PUT_ASSET_PATH),
                hosts,
                PhotosPutAssetRequest(
                    zoneName=zone,
                    importGroup=identifier,
                    localTimeZoneId="UTC",
                    files=[
                        PhotosPutAssetFile(
                            fileName=stage.name,
                            lastModDate=modified_ms,
                            timeZoneOffset=0,
                            singleFileUploadRequest=receipt,
                        )
                    ],
                ).model_dump(mode="json"),
                deadline=deadline,
                params=snapshot,
            )
            if not isinstance(result, list) or len(result) != 1:
                raise WriteDenied("upload_outcome_uncertain")
            registered = PhotosPutAssetResult.model_validate(result[0])
            if (
                not registered.cplMaster
                or not registered.cplAsset
                or registered.response is None
                or registered.response.status not in (200, 409)
            ):
                raise WriteDenied("upload_outcome_uncertain")
            return registered
        except UploadNotDispatched:
            if not dispatch_state.attempted:
                raise
            raise WriteDenied("upload_outcome_uncertain") from None
        except Exception:
            # Even a reservation/transfer failure may have allocated/stored data.
            # No implicit cleanup, re-reservation or registration replay is safe.
            raise WriteDenied("upload_outcome_uncertain") from None
