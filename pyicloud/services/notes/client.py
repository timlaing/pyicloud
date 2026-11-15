"""
Low-level CloudKit client for the Notes container.

This \"escape hatch\" is also used internally by NotesService to implement
developer-friendly methods. It returns typed Pydantic models from
pyicloud.services.notes_models.cloudkit and hides HTTP details.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
import logging
import os
from typing import Any, NoReturn
import uuid

from pydantic import ValidationError
from requests import Response

from pyicloud.common.cloudkit import (
    CKFVString,
    CKLookupResponse,
    CKQueryFilterBy,
    CKQueryObject,
    CKQueryResponse,
    CKRecord,
    CKZoneChangesZone,
    CKZoneChangesZoneReq,
    CKZoneID,
    CKZoneIDReq,
    CloudKitExtraMode,
)
from pyicloud.common.cloudkit.base import resolve_cloudkit_validation_extra
from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitContainerClient,
    CloudKitRateLimited,
    CloudKitZoneNotFoundError,
    redact_cloudkit_url,
)
from pyicloud.exceptions import PyiCloudException
from pyicloud.session import PyiCloudSession

from ._constants import NOTES_ZONE_NAME, NOTES_ZONE_REQ
from .exceptions import (
    NotesApiError,
    NotesAuthError,
    NotesError,
    NotesRateLimited,
    NotesWriteRejected,
    NotesWriteUncertain,
    NotesZoneNotFoundError,
)
from .models.write import (
    NotesFolderReference,
    NotesModifyOperation,
    NotesModifyRequest,
    NotesPreviewLocator,
    NotesPreviewReceipt,
    NoteWriteResult,
)

__all__ = [
    "NotesApiError",
    "NotesAuthError",
    "NotesError",
    "NotesRateLimited",
    "NotesZoneNotFoundError",
]

LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT = (10.0, 60.0)


# ------------------------------ Raw client -----------------------------------


class CloudKitNotesClient:
    """
    Raw CloudKit service for the Notes container.

    Methods map 1:1 to CloudKit endpoints:
      - /records/query
      - /records/lookup
      - /records/modify
      - /changes/zone
    """

    def __init__(
        self,
        base_url: str,
        session: PyiCloudSession,
        base_params: dict[str, object],
        *,
        validation_extra: CloudKitExtraMode | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
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
        """Translate a shared CloudKit failure into the Notes error type.

        The dedicated zone-not-found error keeps its payload and zone name so
        callers can report "Notes not set up"; other failures propagate
        unchanged.
        """
        cause = exc.__cause__ or exc
        if isinstance(exc, CloudKitAuthError):
            raise NotesAuthError(str(exc)) from cause
        if isinstance(exc, CloudKitRateLimited):
            raise NotesRateLimited(str(exc), retry_after=exc.retry_after) from cause
        if isinstance(exc, CloudKitZoneNotFoundError):
            raise NotesZoneNotFoundError(
                str(exc), exc.payload, zone_name=exc.zone_name
            ) from cause
        if isinstance(exc, CloudKitApiError):
            raise NotesApiError(str(exc), payload=exc.payload) from cause
        raise exc

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
        desired_keys: list[str] | None = None,
        results_limit: int | None = None,
        continuation: str | None = None,
    ) -> CKQueryResponse:
        """Query records in the Notes zone, returning the typed response."""
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
        desired_keys: list[str] | None = None,
    ) -> CKLookupResponse:
        """Look up records by name in the Notes zone."""
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

    def modify_note(
        self, operation: NotesModifyOperation, *, public_id: str | None = None
    ) -> NoteWriteResult:
        """Submit one note or folder mutation without redirects or retries."""
        record_id = operation.record.recordName
        note_id = public_id or record_id
        payload = self._build_modify_payload(operation)
        try:
            data = self._client.raw_post(
                "/records/modify", payload, allow_redirects=False, require_no_retry=True
            )
        except (OSError, PyiCloudException):
            raise NotesWriteUncertain(note_id) from None
        item = self._write_ack_item(data, record_id, note_id)
        revision = self._verify_write_ack(item, operation, note_id)
        return NoteWriteResult(id=record_id, record_change_tag=revision)

    @staticmethod
    def _build_modify_payload(operation: NotesModifyOperation) -> dict[str, Any]:
        """Serialize a mutation and restore explicit nulls that clear fields."""
        payload = NotesModifyRequest(
            operations=[operation], zoneID=NOTES_ZONE_REQ
        ).model_dump(mode="json", exclude_none=True)
        if operation.record.recordType == "Folder":
            sent = payload["operations"][0]["record"]
            parent_field = operation.record.fields.get("ParentFolder")
            if parent_field is not None and parent_field.value is None:
                sent["fields"]["ParentFolder"] = {"value": None}
            if (
                operation.record.parent is not None
                and operation.record.parent.recordName is None
            ):
                sent["parent"] = {"recordName": None}
        if operation.record.recordType == "Attachment":
            sent = payload["operations"][0]["record"]["fields"]
            for field in (
                "MergeableDataAsset",
                "EncryptedValues",
                "EncryptedValuesAsset",
            ):
                if field in sent and not sent[field]:
                    sent[field] = {"value": None}
        return payload

    @staticmethod
    def _write_ack_item(data: object, record_id: str, note_id: str) -> dict[str, Any]:
        """Select the exact acknowledged record or report rejection or uncertainty."""
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list) or len(records) != 1:
            raise NotesWriteUncertain(note_id)
        item = records[0]
        if not isinstance(item, dict) or item.get("recordName") != record_id:
            raise NotesWriteUncertain(note_id)
        if "serverErrorCode" in item:
            code = item["serverErrorCode"]
            if not isinstance(code, str) or not code.strip():
                raise NotesWriteUncertain(note_id)
            raise NotesWriteRejected(note_id, code)
        return item

    def _verify_write_ack(
        self, item: dict[str, Any], operation: NotesModifyOperation, note_id: str
    ) -> str:
        """Return the new revision only if the response matches the mutation."""
        try:
            record = CKRecord.model_validate(
                item,
                extra=resolve_cloudkit_validation_extra(self._validation_extra),
            )
        except ValidationError:
            raise NotesWriteUncertain(note_id) from None
        if (
            record.recordType != operation.record.recordType
            or record.deleted
            or record.recordChangeTag is None
            or not record.recordChangeTag.strip()
            or record.recordChangeTag == operation.record.recordChangeTag
            or (record.zoneID is not None and record.zoneID.zoneName != NOTES_ZONE_NAME)
        ):
            raise NotesWriteUncertain(note_id)
        parent = operation.record.parent
        if not self._matches_write_parent(
            record, parent.recordName if parent is not None else None
        ):
            raise NotesWriteUncertain(note_id)
        fields = item.get("fields", {})
        if not self._matches_write_values(fields, operation):
            raise NotesWriteUncertain(note_id)
        return record.recordChangeTag

    @staticmethod
    def _matches_write_parent(record: CKRecord, parent: str | None) -> bool:
        """Verify the parent references relevant to this record type."""
        if record.recordType == "Folder":
            return (
                getattr(record.fields.get_value("ParentFolder"), "recordName", None)
                == parent
                and getattr(record.parent, "recordName", None) == parent
            )
        if record.recordType in {"InlineAttachment", "Attachment"}:
            return (
                parent is not None
                and getattr(record.parent, "recordName", None) == parent
                and getattr(record.fields.get_value("Note"), "recordName", None)
                == parent
            )
        if parent is None:
            return True
        folder = record.fields.get_value("Folder")
        folders = record.fields.get_value("Folders")
        return (
            getattr(folder, "recordName", None) == parent
            and isinstance(folders, list)
            and [getattr(ref, "recordName", None) for ref in folders] == [parent]
            and (record.parent is None or record.parent.recordName == parent)
        )

    def delete_empty_folder(self, folder_id: str) -> bool:
        """Submit the observed irreversible deletion; caller verifies emptiness."""
        payload = {
            "operations": [
                {"operationType": "forceDelete", "record": {"recordName": folder_id}}
            ],
            "zoneID": NOTES_ZONE_REQ.model_dump(mode="json", exclude_none=True),
        }
        try:
            data = self._client.raw_post(
                "/records/modify", payload, allow_redirects=False, require_no_retry=True
            )
        except (OSError, PyiCloudException):
            raise NotesWriteUncertain(folder_id) from None
        records = data.get("records") if isinstance(data, dict) else None
        if (
            not isinstance(records, list)
            or len(records) != 1
            or not isinstance(records[0], dict)
            or records[0].get("recordName") != folder_id
        ):
            raise NotesWriteUncertain(folder_id)
        record = records[0]
        code = record.get("serverErrorCode")
        if isinstance(code, str) and code.strip():
            raise NotesWriteRejected(folder_id, code)
        if "serverErrorCode" in record or record.get("deleted") is not True:
            raise NotesWriteUncertain(folder_id)
        return True

    @staticmethod
    def _matches_write_values(
        fields: dict[str, Any], operation: NotesModifyOperation
    ) -> bool:
        """Verify the scalar values echoed by the Notes modify endpoint."""
        for name, expected in operation.record.fields.items():
            actual = fields.get(name)
            value = actual.get("value") if isinstance(actual, dict) else None
            target = expected.value
            if operation.record.recordType == "Note_UserSpecific" and isinstance(
                target, NotesFolderReference
            ):
                if not CloudKitNotesClient._matches_user_reference(value, target):
                    return False
            elif isinstance(target, NotesPreviewReceipt):
                if not CloudKitNotesClient._matches_preview_receipt(value, target):
                    return False
            elif isinstance(target, (int, str)) and value != target:
                return False
        return True

    @staticmethod
    def _matches_preview_receipt(value: object, target: NotesPreviewReceipt) -> bool:
        """Check that the response acknowledges the exact preview asset."""
        return isinstance(value, dict) and all(
            value.get(key) == getattr(target, key)
            for key in ("fileChecksum", "referenceChecksum", "size")
        )

    @staticmethod
    def _matches_user_reference(value: object, target: NotesFolderReference) -> bool:
        """Match the note or folder reference on a per-user record."""
        if not isinstance(value, dict) or value.get("recordName") != target.recordName:
            return False
        zone = value.get("zoneID")
        owner = target.zoneID.ownerRecordName
        return (
            isinstance(zone, dict)
            and zone.get("zoneName") == NOTES_ZONE_NAME
            and (not owner or zone.get("ownerRecordName") == owner)
        )

    def rereference_preview(self, locator: NotesPreviewLocator) -> NotesPreviewReceipt:
        """Request a write receipt for a specific existing Notes preview."""
        error = "Could not verify the existing Notes preview; no note write submitted"
        payload = {
            "assets": [locator.model_dump(mode="json")],
            "zoneID": NOTES_ZONE_REQ.model_dump(mode="json", exclude_none=True),
        }
        try:
            data = self._client.raw_post(
                "/assets/rereference", payload, allow_redirects=False
            )
            assets = data.get("assets") if isinstance(data, dict) else None
            if not isinstance(assets, list) or len(assets) != 1:
                raise NotesError(error)
            receipt = NotesPreviewReceipt.model_validate(assets[0])
        except (OSError, PyiCloudException, ValidationError):
            raise NotesError(error) from None
        if any(
            getattr(receipt, key) != value
            for key, value in locator.model_dump().items()
        ):
            raise NotesError(error)
        return receipt

    # ----- Changes (paged generator) -----

    def changes(
        self,
        *,
        zone_req: CKZoneChangesZoneReq,
    ) -> Iterator[CKZoneChangesZone]:
        """Yield paged zone-change results for the Notes zone."""
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
        """Stream raw bytes from a CloudKit asset URL."""
        try:
            yield from self._client.download_asset_stream(
                url,
                chunk_size=chunk_size,
            )
        except (CloudKitApiError, CloudKitAuthError, CloudKitRateLimited) as exc:
            self._raise_notes_error(exc)

    def download_asset_to(self, url: str, directory: str) -> str:
        """Download a CloudKit asset URL to a file in the given directory."""
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
        requests getCurrentSyncToken=true (already in params) and reading the
        top-level token.

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
    def _dump_http_debug(
        op: str, url: str, payload: dict[str, Any], resp: Response
    ) -> None:
        if op in {"records/modify", "assets/rereference"} or not os.getenv(
            "PYICLOUD_NOTES_DEBUG"
        ):
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
    def _log_validation(op: str, data: dict[str, Any], err: ValidationError) -> None:
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
