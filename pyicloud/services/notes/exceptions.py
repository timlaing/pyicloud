"""Exceptions raised by the Notes service."""

from __future__ import annotations

from typing import Literal

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


class NotesWriteRejected(NotesError):
    """CloudKit explicitly rejected a particular note mutation."""

    def __init__(self, note_id: str, error_code: str) -> None:
        super().__init__(f"iCloud rejected the write for note {note_id}")
        self.note_id = note_id
        self.error_code = error_code


class NoteRevisionConflict(NotesError):
    """A note no longer has the revision the caller intended to change."""


class NotesWriteUncertain(NotesError):
    """A write may have taken effect without a verifiable acknowledgment."""

    def __init__(self, note_id: str) -> None:
        super().__init__(
            f"Write outcome is unknown for note {note_id}. "
            "Check the note before retrying."
        )
        self.note_id = note_id


class NotesLinkedWriteIncomplete(NotesError):
    """A linked record or its parent update could not be fully verified."""

    def __init__(
        self,
        note_id: str,
        object_id: str,
        *,
        note_state: Literal["confirmed", "uncertain", "not_submitted", "rejected"],
        object_state: Literal["confirmed", "uncertain", "not_submitted", "rejected"],
        error_code: str | None = None,
    ) -> None:
        super().__init__(
            f"Linked write incomplete for note {note_id} (record {object_id}). "
            f"Note: {note_state}; linked record: {object_state}. Check before retrying."
        )
        self.note_id = note_id
        self.object_id = object_id
        self.note_state = note_state
        self.object_state = object_state
        self.error_code = error_code
