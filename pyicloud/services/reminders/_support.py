"""Shared CloudKit support helpers for the Reminders service."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from pyicloud.common.cloudkit import CKErrorItem, CKModifyResponse, CKRecord

from .client import RemindersApiError


def _raise_record_errors(records: Iterable[Any], operation_name: str) -> None:
    """Raise when CloudKit returned one or more per-record errors."""
    errors = [item for item in records if isinstance(item, CKErrorItem)]
    if not errors:
        return

    details = []
    for err in errors:
        record_name = err.recordName or "<unknown record>"
        reason = err.reason or "no reason provided"
        details.append(f"{record_name}: {err.serverErrorCode} ({reason})")

    raise RemindersApiError(
        f"{operation_name} failed for {len(errors)} record(s): " + "; ".join(details),
        payload=[
            {
                "recordName": e.recordName,
                "serverErrorCode": e.serverErrorCode,
                "reason": e.reason,
            }
            for e in errors
        ],
    )


def _assert_modify_success(response: CKModifyResponse, operation_name: str) -> None:
    """Raise when CloudKit accepted the request but rejected one or more records."""
    _raise_record_errors(response.records, operation_name)


def _assert_read_success(records: Iterable[Any], operation_name: str) -> None:
    """Raise when a read endpoint returned one or more per-record errors."""
    _raise_record_errors(records, operation_name)


def _response_record_change_tag(
    response: CKModifyResponse,
    record_name: str,
) -> Optional[str]:
    """Return the latest recordChangeTag for a record from a modify response."""
    change_tag: Optional[str] = None
    for item in response.records:
        if not isinstance(item, CKRecord):
            continue
        if item.recordName != record_name:
            continue
        if item.recordChangeTag:
            change_tag = item.recordChangeTag
    return change_tag


def _refresh_record_change_tag(
    response: CKModifyResponse,
    model_obj: Any,
    record_name: str,
) -> None:
    """Hydrate an in-memory model object's record_change_tag from modify ack."""
    change_tag = _response_record_change_tag(response, record_name)
    if change_tag:
        setattr(model_obj, "record_change_tag", change_tag)
