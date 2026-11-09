"""Existing Drive reservation/multipart/commit contract using staged bytes only."""

# Reuse the existing operation-local cookie-token accessor, never expose credentials.
# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Callable
import hashlib
import mimetypes
import time
from typing import Any
from urllib.parse import urlsplit

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.services.drive import CLOUD_DOCS_ZONE

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


def validate_upload_session(
    drive: Any,
    credentials: dict[str, str],
    *,
    bind_adapter: Callable[[BoundedAdapter], None] | None = None,
) -> float | None:
    """Check both exact registration endpoints before any approval is consumed."""
    root = drive.document_root
    if not isinstance(root, str) or not root:
        raise WriteDenied("unsupported_upload_route")
    try:
        parsed = urlsplit(root)
    except ValueError:
        raise WriteDenied("unsupported_upload_route") from None
    hosts = frozenset({parsed.hostname}) if parsed.hostname else frozenset()
    checked_upload_url(root, hosts)
    if parsed.query or "?" in root or "#" in root:
        raise WriteDenied("unsupported_upload_route")
    params = {**drive.params, **credentials}
    deadlines: list[float] = []
    for path in ("/upload/web", "/update/documents"):
        url = root.rstrip("/") + "/ws/" + CLOUD_DOCS_ZONE + path
        adapter = checked_adapter(
            drive.session, prepared_url(drive.session, url, params), bounded=True
        )
        assert isinstance(adapter, BoundedAdapter)
        if bind_adapter is not None:
            bind_adapter(adapter)
        deadline = getattr(adapter, "deadline", None)
        if deadline is not None:
            if deadline <= time.monotonic():
                raise WriteDenied("upload_deadline_exceeded")
            deadlines.append(deadline)
    return min(deadlines) if deadlines else None


def snapshot_upload_credentials(drive: Any) -> dict[str, str]:
    """Validate a private operation-local token without exposing cookie errors."""
    try:
        credentials = drive._get_token_from_cookie()
    except Exception:
        raise WriteDenied("upload_credentials_unavailable") from None
    return validated_upload_credentials(credentials)


def validated_upload_credentials(credentials: Any) -> dict[str, str]:
    """Copy only the observed upload-token contract, never arbitrary query fields."""
    if (
        not isinstance(credentials, dict)
        or set(credentials) != {"token"}
        or not isinstance(credentials["token"], str)
        or not credentials["token"]
    ):
        raise WriteDenied("upload_credentials_unavailable")
    return {"token": credentials["token"]}


def _success_status(status: Any) -> bool:
    """Recognize explicit commit success without interpreting boolean codes."""
    if status == "OK":
        return True
    return (
        isinstance(status, dict)
        and isinstance(status.get("status_code"), int)
        and not isinstance(status.get("status_code"), bool)
        and status["status_code"] == 0
    )


def _commit_matches(response: Any, document_id: str) -> bool:
    """Require one successful result correlated with the reserved document ID."""
    if not isinstance(response, dict) or not _success_status(response.get("status")):
        return False
    results = response.get("results")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        return False
    result = results[0]
    document = result.get("document")
    return (
        _success_status(result.get("status"))
        and isinstance(document, dict)
        and document.get("document_id") == document_id
    )


def _validate_stage(
    stage: StagedUpload, modified_ms: int, *, acquired: bool = False
) -> None:
    """Require unchanged, unexpired bounded bytes and a strict timestamp."""
    if (
        not isinstance(stage, StagedUpload)
        or not isinstance(stage.data, bytes)
        or not 0 <= stage.size <= 4 * 1024 * 1024
        or len(stage.data) != stage.size
        or hashlib.sha256(stage.data).hexdigest() != stage.sha256
        or (not acquired and stage.expires_at <= time.monotonic())
        or isinstance(modified_ms, bool)
        or not isinstance(modified_ms, int)
        or modified_ms < 0
    ):
        raise WriteDenied("invalid_upload_bytes")


def _receipt_data(response: Any, size: int) -> dict[str, Any]:
    """Validate the exact transfer receipt before constructing commit data."""
    receipt = response.get("singleFile") if isinstance(response, dict) else None
    if (
        not isinstance(receipt, dict)
        or isinstance(receipt.get("size"), bool)
        or receipt.get("size") != size
        or not all(
            isinstance(receipt.get(key), str) and receipt[key]
            for key in ("fileChecksum", "wrappingKey", "referenceChecksum")
        )
    ):
        raise WriteDenied("upload_outcome_uncertain")
    if size and (not isinstance(receipt.get("receipt"), str) or not receipt["receipt"]):
        raise WriteDenied("upload_outcome_uncertain")
    data = {
        "signature": receipt["fileChecksum"],
        "wrapping_key": receipt["wrappingKey"],
        "reference_signature": receipt["referenceChecksum"],
        "size": size,
    }
    if receipt.get("receipt"):
        data["receipt"] = receipt["receipt"]
    return data


def _reservation_target(reservation: Any) -> tuple[str, str]:
    """Require one bounded allocation identity and a typed transfer target."""
    if (
        not isinstance(reservation, list)
        or len(reservation) != 1
        or not isinstance(reservation[0], dict)
    ):
        raise WriteDenied("upload_outcome_uncertain")
    allocation, target = reservation[0].get("document_id"), reservation[0].get("url")
    if (
        not isinstance(allocation, str)
        or not allocation
        or len(allocation) > 512
        or not isinstance(target, str)
    ):
        raise WriteDenied("upload_outcome_uncertain")
    return allocation, target


def _validate_upload_name(name: Any) -> None:
    """Require the existing bounded UTF-8 leaf name before any upload dispatch."""
    try:
        name_size = len(name.encode("utf-8")) if isinstance(name, str) else 0
    except UnicodeEncodeError:
        raise WriteDenied("invalid_upload_bytes") from None
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or name_size > 255
        or any(ord(char) < 32 or char in {"/", "\\", "\x7f"} for char in name)
    ):
        raise WriteDenied("invalid_upload_bytes")


class DriveUploadBytes:
    """One existing upload flow; acknowledged commit is not final file identity."""

    def __init__(self, transport: UploadTransport, timeout: float = 30) -> None:
        self.transport = transport
        self.timeout = timeout

    def upload(
        self,
        drive: Any,
        folder_id: str,
        stage: StagedUpload,
        *,
        modified_ms: int,
        credential_snapshot: dict[str, str] | None = None,
        approved_hosts: frozenset[str] | None = None,
        stage_acquired: bool = False,
        owner_deadline: float | None = None,
    ) -> None:
        """Snapshot upload credentials and send exact bytes with no filesystem paths."""
        root = drive.document_root
        if not isinstance(root, str) or not root:
            raise WriteDenied("unsupported_upload_route")
        try:
            parsed = urlsplit(root)
        except ValueError:
            raise WriteDenied("unsupported_upload_route") from None
        hosts = frozenset({parsed.hostname}) if parsed.hostname else frozenset()
        checked_upload_url(root, hosts)
        if (
            parsed.query
            or "?" in root
            or "#" in root
            or not isinstance(folder_id, str)
            or not folder_id
        ):
            raise WriteDenied("unsupported_upload_route")
        # Expiry is an admission rule: take() validated it atomically before
        # consuming approval. Acquired immutable bytes remain valid in flight.
        _validate_stage(stage, modified_ms, acquired=stage_acquired)
        params = dict(drive.params)
        _validate_upload_name(stage.name)
        credentials = (
            snapshot_upload_credentials(drive)
            if credential_snapshot is None
            else validated_upload_credentials(credential_snapshot)
        )
        params.update(credentials)
        deadline = time.monotonic() + self.timeout
        if owner_deadline is not None:
            deadline = min(deadline, owner_deadline)

        def url(path: str) -> str:
            return root.rstrip("/") + "/ws/" + CLOUD_DOCS_ZONE + path

        dispatch_state = UploadDispatchState()
        try:
            reservation = self.transport.register(
                drive.session,
                url("/upload/web"),
                hosts,
                {
                    "filename": stage.name,
                    "type": "FILE",
                    "content_type": mimetypes.guess_type(stage.name)[0] or "",
                    "size": stage.size,
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
                params=params,
                dispatch_state=dispatch_state,
                deadline=deadline,
            )
            allocation, target = _reservation_target(reservation)
            if approved_hosts is not None:
                checked_upload_url(target, approved_hosts)
            response = self.transport.transfer(
                target, stage.data, deadline=deadline, multipart_name=stage.name
            )
            data = _receipt_data(response, stage.size)
            committed = self.transport.register(
                drive.session,
                url("/update/documents"),
                hosts,
                {
                    "data": data,
                    "command": "add_file",
                    "create_short_guid": True,
                    "document_id": allocation,
                    "path": {"starting_document_id": folder_id, "path": stage.name},
                    "allow_conflict": True,
                    "file_flags": {
                        "is_writable": True,
                        "is_executable": False,
                        "is_hidden": False,
                    },
                    "mtime": modified_ms,
                    "btime": modified_ms,
                },
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
                params=params,
                deadline=deadline,
            )
            if not _commit_matches(committed, allocation):
                raise WriteDenied("upload_outcome_uncertain")
        except UploadNotDispatched:
            if not dispatch_state.attempted:
                raise
            raise WriteDenied("upload_outcome_uncertain") from None
        except Exception:
            raise WriteDenied("upload_outcome_uncertain") from None
