"""Library exceptions."""

from typing import Optional


class PyiCloudException(Exception):
    """Generic iCloud exception."""


class PasswordException(PyiCloudException):
    """Password exception."""


class TokenException(PyiCloudException):
    """Token exception."""


# API
class PyiCloudAPIResponseException(PyiCloudException):
    """iCloud response exception."""

    def __init__(self, reason, code=None, retry=False) -> None:
        self.reason: str = reason
        self.code: Optional[int] = code
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


class PyiCloud2SARequiredException(PyiCloudException):
    """iCloud 2SA required exception."""

    def __init__(self, apple_id) -> None:
        message: str = f"Two-step authentication required for account: {apple_id}"
        super().__init__(message)


class PyiCloudNoStoredPasswordAvailableException(PyiCloudException):
    """iCloud no stored password exception."""


# Webservice specific
class PyiCloudNoDevicesException(PyiCloudException):
    """iCloud no device exception."""
