"""Exceptions raised by the Reminders service."""

from __future__ import annotations

from pyicloud.exceptions import PyiCloudException


class RemindersAuthError(PyiCloudException):
    """Auth/PCS/cookie issues (401/403)."""


class RemindersApiError(PyiCloudException):
    """Catch-all API error."""

    def __init__(self, message: str, payload: object | None = None):
        super().__init__(message)
        self.payload = payload


class RemindersZoneNotFoundError(RemindersApiError):
    """The Reminders zone does not exist for this account.

    Apple answers ``ZONE_NOT_FOUND`` for accounts that have not enabled
    Reminders (or whose Reminders zone has not been created yet). This is a
    valid account state rather than a transport or server failure, so callers
    can catch it to report "Reminders not set up" instead of a generic error.

    Subclasses ``RemindersApiError``, so existing handlers keep catching it.
    """

    def __init__(
        self,
        message: str,
        payload: object | None = None,
        *,
        zone_name: str | None = None,
    ):
        super().__init__(message, payload)
        self.zone_name = zone_name


class CRDTDecodeError(ValueError):
    """Raised when a Reminders CRDT payload cannot be decoded."""


class CRDTStructureError(CRDTDecodeError):
    """Raised when a decodable payload has no CRDT structure to edit.

    The document carries no substrings, so there is no replica state to
    preserve and re-encoding from scratch is equivalent. Callers that fall
    back to a fresh document should catch this rather than
    :class:`CRDTDecodeError`, whose other cases (a missing vector clock or
    start sentinel) mean replica state does exist and must not be discarded.
    """
