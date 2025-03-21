"""Library exceptions."""

from typing import Optional, Union

from requests import Response


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
        self, reason: str, code: Optional[Union[int, str]] = None, retry: bool = False
    ) -> None:
        self.reason: str = reason
        self.code: Optional[Union[int, str]] = code
        message: str = reason or ""
        if code:
            message += f" ({code})"
        if retry:
            message += ". Retrying ..."

        super().__init__(message)


class PyiCloudServiceNotActivatedException(PyiCloudAPIResponseException):
    """iCloud service not activated exception."""


# Login
class PyiCloudFailedLoginException(PyiCloudException):
    """iCloud failed login exception."""


class PyiCloud2FARequiredException(PyiCloudException):
    """iCloud 2FA required exception."""

    def __init__(self, apple_id: str, response: Response) -> None:
        message: str = f"2FA authentication required for account: {apple_id} (HSA2)"
        super().__init__(message)
        self.response: Response = response


class PyiCloud2SARequiredException(PyiCloudException):
    """iCloud 2SA required exception."""

    def __init__(self, apple_id: str) -> None:
        message: str = f"Two-step authentication required for account: {apple_id}"
        super().__init__(message)


class PyiCloudNoStoredPasswordAvailableException(PyiCloudException):
    """iCloud no stored password exception."""


# Webservice specific
class PyiCloudNoDevicesException(PyiCloudException):
    """iCloud no device exception."""
