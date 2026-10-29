"""Exceptions raised by the Invites service."""

from __future__ import annotations

from pyicloud.exceptions import PyiCloudException

from ._constants import ONE_TIME_LINK_RECORD_NAME_SUFFIX


class InvitesError(PyiCloudException):
    """Base Invites transport error."""


class InvitesAuthError(InvitesError):
    """Auth/PCS/cookie issues (401/403)."""


class InvitesRateLimited(InvitesError):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class InvitesEntitlementError(InvitesError):
    """The account may not perform this write.

    Apple gates event edits behind a subscription feature and answers an
    unproven write with ``502 INTERNAL_ERROR``, which says nothing useful. This
    is raised before the request instead.
    """


class InvitesApiError(InvitesError):
    """Catch-all API error."""

    def __init__(self, message: str, payload: object | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class InvitesZoneNotFoundError(InvitesApiError):
    """The requested event zone does not exist for this account.

    Apple answers ``ZONE_NOT_FOUND`` for an event zone that was never created
    or has been deleted. Callers can catch it to report "event not found"
    instead of a generic error. Subclasses ``InvitesApiError``, so existing
    handlers keep catching it.
    """

    def __init__(
        self,
        message: str,
        payload: object | None = None,
        *,
        zone_name: str | None = None,
    ) -> None:
        super().__init__(message, payload)
        self.zone_name = zone_name


class InvitesCreationError(InvitesApiError):
    """Creation failed; identifies the new zone and whether cleanup succeeded.

    ``cleanup_succeeded`` is None if zone creation was not confirmed. Such an
    ambiguous outcome is not automatically deleted. The original failure is
    chained as the cause, and a failed cleanup is retained separately.
    """

    def __init__(
        self,
        event_id: str,
        *,
        cleanup_succeeded: bool | None,
        cleanup_error: Exception | None = None,
    ) -> None:
        super().__init__(f"Event creation failed for zone {event_id!r}")
        self.event_id = event_id
        self.cleanup_succeeded = cleanup_succeeded
        self.cleanup_error = cleanup_error


class InvitesInvitationError(InvitesApiError):
    """A submitted invitation failed or its result could not be verified.

    Inspect the original cause and the retained participant/record IDs before
    retrying. A timeout or malformed response does not imply rollback. No
    automatic retry or compensating participant removal has been attempted.
    """

    def __init__(self, event_id: str, participant_id: str) -> None:
        super().__init__(
            f"Invitation for participant {participant_id!r} in event {event_id!r} "
            "failed or could not be verified; inspect it before retrying."
        )
        self.event_id = event_id
        self.participant_id = participant_id
        self.record_name = f"{participant_id}{ONE_TIME_LINK_RECORD_NAME_SUFFIX}"


class EventNotFound(InvitesError):
    """Raised when a requested event ID is not present in any scope."""
