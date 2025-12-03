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
        self,
        reason: str,
        code: Optional[Union[int, str]] = None,
        response: Optional[Response] = None,
    ) -> None:
        self.reason: str = reason
        self.code: Optional[Union[int, str]] = code
        self.response: Optional[Response] = response
        message: str = reason or ""
        if code:
            message += f" ({code})"

        if response is not None and response.text:
            message += f": {response.text}"

        super().__init__(message)


class PyiCloudServiceNotActivatedException(PyiCloudAPIResponseException):
    """iCloud service not activated exception."""


# Login
class PyiCloudFailedLoginException(PyiCloudException):
    """iCloud failed login exception."""

    def __init__(
        self,
        msg: str,
        *args,
        response: Optional[Response] = None,
    ) -> None:
        self.response: Optional[Response] = response
        message: str = msg or "Failed login to iCloud"
        if response is not None and response.text:
            message = f"{message} ({response.status_code}): {response.text}"
        super().__init__(message, *args)


class PyiCloudAcceptTermsException(PyiCloudException):
    """iCloud accept terms exception."""


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


class PyiCloudAuthRequiredException(PyiCloudException):
    """iCloud re-authentication required exception."""

    def __init__(self, apple_id: str, response: Response) -> None:
        message: str = f"Re-authentication required for account: {apple_id}"
        super().__init__(message)
        self.response: Response = response


class PyiCloudNoTrustedNumberAvailable(PyiCloudException):
    """iCloud no trusted number exception."""


class PyiCloudNoStoredPasswordAvailableException(PyiCloudException):
    """iCloud no stored password exception."""


# Webservice specific
class PyiCloudNoDevicesException(PyiCloudException):
    """iCloud no device exception."""
