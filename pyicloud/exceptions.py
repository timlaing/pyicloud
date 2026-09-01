"""Library exceptions."""

from typing import Any

from requests import Response

# Apple answers a withdrawn endpoint with 410 rather than 404, which makes it an
# unambiguous signal: the resource is permanently gone, not merely absent.
HTTP_GONE: int = 410


class PyiCloudException(Exception):
    """Generic iCloud exception."""


class PyiCloudPasswordException(PyiCloudException):
    """Password exception."""


class PyiCloudServiceUnavailable(PyiCloudException):
    """Service unavailable exception."""


class TokenException(PyiCloudException):
    """Token exception."""


# API
class PyiCloudAPIResponseException(PyiCloudException):
    """iCloud response exception."""

    def __init__(
        self,
        reason: str,
        code: int | str | None = None,
        response: Response | None = None,
    ) -> None:
        """Capture a normalized API error and the optional HTTP context."""
        self.reason: str = reason
        self.code: int | str | None = code
        self.response: Response | None = response
        message: str = reason or ""
        if code:
            message += f" ({code})"

        if response is not None and response.text:
            message += f": {response.text}"

        super().__init__(message)


class PyiCloudServiceNotActivatedException(PyiCloudAPIResponseException):
    """iCloud service not activated exception."""


class PyiCloudEndpointGoneException(PyiCloudAPIResponseException):
    """Raised when Apple reports an endpoint as permanently gone (HTTP 410).

    Apple withdraws endpoints without notice, and has done so more than once.
    A 410 means the endpoint this library calls no longer exists, so it signals
    that pyicloud needs updating rather than that the caller's request,
    credentials, or session were wrong.

    Distinguishing it matters because the alternative is expensive: when the
    Photos upload endpoint was withdrawn it surfaced as a generic response
    error, and the reporter ruled out a stale session, an outdated client, a
    missing PCS handshake, the wrong dsid, and the wrong partition before
    concluding the endpoint itself had gone.

    ``endpoint`` is a redacted host-and-path label safe to quote in a bug
    report; the full URL is available on ``response`` when one was captured.
    """

    def __init__(self, endpoint: str, response: Response | None = None) -> None:
        """Describe a withdrawn endpoint and point at the issue tracker."""
        self.endpoint: str = endpoint
        super().__init__(
            f"Apple no longer serves {endpoint}. This endpoint appears to have "
            "been withdrawn, which means pyicloud needs updating rather than "
            "that your request was wrong. Please report it at "
            "https://github.com/timlaing/pyicloud/issues and quote this endpoint",
            HTTP_GONE,
            response,
        )


# Login
class PyiCloudFailedLoginException(PyiCloudException):
    """iCloud failed login exception."""

    def __init__(
        self,
        msg: str,
        *args: Any,
        response: Response | None = None,
    ) -> None:
        """Initialize a login failure with optional HTTP response details."""
        self.response: Response | None = response
        message: str = msg or "Failed login to iCloud"
        if response is not None and response.text:
            message = f"{message} ({response.status_code}): {response.text}"
        super().__init__(message, *args)


class PyiCloudAcceptTermsException(PyiCloudException):
    """iCloud accept terms exception."""


class PyiCloud2FARequiredException(PyiCloudException):
    """iCloud 2FA required exception."""

    def __init__(self, apple_id: str, response: Response) -> None:
        """Initialize a 2FA-required error for an HSA2 login challenge."""
        message: str = f"2FA authentication required for account: {apple_id} (HSA2)"
        super().__init__(message)
        self.response: Response = response


class PyiCloud2SARequiredException(PyiCloudException):
    """iCloud 2SA required exception."""

    def __init__(self, apple_id: str) -> None:
        """Initialize a 2SA-required error for a legacy login challenge."""
        message: str = f"Two-step authentication required for account: {apple_id}"
        super().__init__(message)


class PyiCloudAuthRequiredException(PyiCloudException):
    """iCloud re-authentication required exception."""

    def __init__(self, apple_id: str, response: Response) -> None:
        """Initialize a reauthentication-required error with the triggering response."""
        message: str = f"Re-authentication required for account: {apple_id}"
        super().__init__(message)
        self.response: Response = response


class PyiCloudNoTrustedNumberAvailable(PyiCloudException):
    """iCloud no trusted number exception."""


class PyiCloudTrustedDevicePromptException(PyiCloudAPIResponseException):
    """Trusted-device prompt bootstrap exception."""


class PyiCloudTrustedDeviceVerificationException(PyiCloudAPIResponseException):
    """Trusted-device bridge verification exception."""


class PyiCloudNoStoredPasswordAvailableException(PyiCloudException):
    """iCloud no stored password exception."""


# Webservice specific
class PyiCloudNoDevicesException(PyiCloudException):
    """iCloud no device exception."""
