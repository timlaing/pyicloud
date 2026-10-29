"""Exceptions raised by the Notes service."""

from __future__ import annotations

from pyicloud.exceptions import PyiCloudException


class NotesError(PyiCloudException):
    """Base Notes transport error."""


class NotesAuthError(NotesError):
    """Auth/PCS/cookie issues (401/403)."""


class NotesRateLimited(NotesError):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotesApiError(NotesError):
    """Catch-all API error."""

    def __init__(self, message: str, payload: object | None = None):
        super().__init__(message)
        self.payload = payload


class NotesZoneNotFoundError(NotesApiError):
    """The Notes zone does not exist for this account.

    Apple answers ``ZONE_NOT_FOUND`` for accounts whose Notes data is not
    stored in CloudKit (or whose zone has not been created). Callers can catch
    it to report "Notes not set up" instead of a generic error. Subclasses
    ``NotesApiError``, so existing handlers keep catching it.
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


class NoteNotFound(NotesError):
    """Raised when a note cannot be found."""


class NoteLockedError(NotesError):
    """Raised when a note is password protected and cannot be accessed."""
