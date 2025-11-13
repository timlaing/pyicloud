"""Async Pyicloud Session handling"""

import logging
import os
from json import JSONDecodeError, dump, load
from os import path
from re import match
from typing import TYPE_CHECKING, Any, NoReturn, Optional, Union

import httpx

from pyicloud.const import (
    CONTENT_TYPE,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT_JSON,
    ERROR_ACCESS_DENIED,
    ERROR_AUTHENTICATION_FAILED,
    ERROR_ZONE_NOT_FOUND,
    HEADER_DATA,
    AppleAuthError,
)
from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudServiceNotActivatedException,
)

if TYPE_CHECKING:
    from pyicloud.async_base import AsyncPyiCloudService


class AsyncPyiCloudSession:
    """Async iCloud session using httpx."""

    def __init__(
        self,
        service: "AsyncPyiCloudService",
        client_id: str,
        cookie_directory: str,
        verify: bool = False,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._service: AsyncPyiCloudService = service
        self._verify = verify
        self._cookie_directory: str = cookie_directory
        self._cookies = PyiCloudCookieJar(filename=self.cookiejar_path)
        self._data: dict[str, Any] = {}
        self._headers: dict[str, str] = headers or {}

        self._logger: logging.Logger = logging.getLogger(__name__)
        self._client: Optional[httpx.AsyncClient] = None

        self._load_session_data()

        if not self._data.get("client_id"):
            self._data.update({"client_id": client_id})

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Ensure the httpx client is created."""
        if self._client is None:
            # Convert cookies to httpx format
            cookie_dict = {}
            for cookie in self._cookies:
                if cookie.name and cookie.value:
                    cookie_dict[cookie.name] = cookie.value

            self._client = httpx.AsyncClient(
                verify=self._verify,
                headers=self._headers,
                cookies=cookie_dict,
                follow_redirects=True,
            )

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def cookies(self) -> PyiCloudCookieJar:
        """Gets the cookies"""
        return self._cookies

    @property
    def data(self) -> dict[str, Any]:
        """Gets the session data"""
        return self._data

    @property
    def logger(self) -> logging.Logger:
        """Gets the request logger"""
        return self._logger

    def _load_session_data(self) -> None:
        """Load session_data from file."""
        if os.path.exists(self.cookiejar_path):
            try:
                self._cookies.load()
            except (OSError, ValueError) as exc:
                self._logger.warning(
                    "Failed to load cookie jar %s: %s; starting without persisted cookies",
                    self.cookiejar_path,
                    exc,
                )
                self._cookies.clear()

        self._logger.debug("Using session file %s", self.session_path)
        self._data: dict[str, Any] = {}
        try:
            with open(self.session_path, encoding="utf-8") as session_f:
                self._data = load(session_f)
        except (
            JSONDecodeError,
            OSError,
        ):
            self._logger.info("Session file does not exist")

    def _save_session_data(self) -> None:
        """Save session_data to file."""
        if self._cookie_directory and not os.path.isdir(self._cookie_directory):
            os.makedirs(self._cookie_directory, exist_ok=True)
        with open(self.session_path, "w", encoding="utf-8") as outfile:
            dump(self._data, outfile)
            self.logger.debug("Saved session data to file: %s", self.session_path)

        try:
            self._cookies.save()
            self.logger.debug("Saved cookies data to file: %s", self.cookiejar_path)
        except (OSError, ValueError) as exc:
            self.logger.warning("Failed to save cookies data: %s", exc)

    def _update_session_data(self, response: httpx.Response) -> None:
        """Update session_data with new data."""
        for header, value in HEADER_DATA.items():
            if response.headers.get(header):
                session_arg: str = value
                self._data.update({session_arg: response.headers.get(header)})

    def _is_json_response(self, response: httpx.Response) -> bool:
        content_type: str = response.headers.get(CONTENT_TYPE, "")
        json_mimetypes: list[str] = [
            CONTENT_TYPE_JSON,
            CONTENT_TYPE_TEXT_JSON,
        ]
        return content_type.split(";")[0] in json_mimetypes

    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an async HTTP request."""
        await self._ensure_client()
        return await self._request(method, url, **kwargs)

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Async GET request."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Async POST request."""
        return await self.request("POST", url, **kwargs)

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Request method."""
        self.logger.debug("%s %s", method, url)

        try:
            if self._client is None:
                await self._ensure_client()

            response: httpx.Response = await self._client.request(
                method=method,
                url=url,
                **kwargs,
            )

            # Update cookies from response
            for cookie_name, cookie_value in response.cookies.items():
                self._cookies.set(cookie_name, cookie_value)

            self._update_session_data(response)
            self._save_session_data()

            status_code: int = int(response.status_code)

            if not response.is_success and (
                self._is_json_response(response)
                or status_code
                in [
                    AppleAuthError.TWO_FACTOR_REQUIRED,
                    AppleAuthError.FIND_MY_REAUTH_REQUIRED,
                    AppleAuthError.LOGIN_TOKEN_EXPIRED,
                    AppleAuthError.GENERAL_AUTH_ERROR,
                ],
            ):
                return await self._handle_request_error(
                    status_code=status_code,
                    response=response,
                )

            response.raise_for_status()

            if not self._is_json_response(response):
                return response

            self._decode_json_response(response)

            return response
        except httpx.HTTPStatusError as err:
            raise PyiCloudAPIResponseException(
                reason=err.response.text,
                code=err.response.status_code,
            ) from err
        except httpx.RequestError as err:
            raise PyiCloudAPIResponseException("Request failed to iCloud") from err

    async def _handle_request_error(
        self,
        status_code: int,
        response: httpx.Response,
    ) -> httpx.Response:
        """Handle request error."""
        if (
            status_code == AppleAuthError.TWO_FACTOR_REQUIRED
            and self._is_json_response(response)
            and (response.json().get("authType") == "hsa2")
        ):
            raise PyiCloud2FARequiredException(
                apple_id=self.service.account_name,
                response=response,
            )

        if status_code == AppleAuthError.FIND_MY_REAUTH_REQUIRED:
            raise PyiCloudAuthRequiredException(
                apple_id=self.service.account_name,
                response=response,
            )

        self._raise_error(status_code, response.reason_phrase)

    def _decode_json_response(self, response: httpx.Response) -> None:
        """Decode JSON response."""
        if len(response.content) == 0:
            return

        try:
            data: Union[list[dict[str, Any]], dict[str, Any]] = response.json()
            if isinstance(data, dict):
                reason: Optional[str] = data.get("errorMessage")
                reason = reason or data.get("reason")
                reason = reason or data.get("errorReason")
                reason = reason or data.get("error")
                if reason and not isinstance(reason, str):
                    reason = "Unknown reason"

                if reason:
                    code: Optional[Union[int, str]] = data.get("errorCode")
                    code = code or data.get("serverErrorCode")
                    self._raise_error(code, reason)

        except JSONDecodeError:
            self.logger.warning(
                "Failed to parse response with JSON mimetype: %s", response.text
            )

    def _raise_error(self, code: Optional[Union[int, str]], reason: str) -> NoReturn:
        if (
            self.service.requires_2sa
            and reason == "Missing X-APPLE-WEBAUTH-TOKEN cookie"
        ):
            raise PyiCloud2SARequiredException(self.service.account_name)
        if code in (ERROR_ZONE_NOT_FOUND, ERROR_AUTHENTICATION_FAILED):
            reason = (
                "Please log into https://icloud.com/ to manually "
                "finish setting up your iCloud service"
            )
            raise PyiCloudServiceNotActivatedException(reason, code)
        if code == ERROR_ACCESS_DENIED:
            reason = (
                reason + ".  Please wait a few minutes then try again."
                "The remote servers might be trying to throttle requests."
            )
        if isinstance(code, int) and code in [
            AppleAuthError.TWO_FACTOR_REQUIRED,
            AppleAuthError.FIND_MY_REAUTH_REQUIRED,
            AppleAuthError.LOGIN_TOKEN_EXPIRED,
            AppleAuthError.GENERAL_AUTH_ERROR,
        ]:
            reason = "Authentication required for Account."

        raise PyiCloudAPIResponseException(reason, code)

    @property
    def service(self) -> "AsyncPyiCloudService":
        """Gets the service."""
        return self._service

    @property
    def cookiejar_path(self) -> str:
        """Get path for cookiejar file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.service.account_name if match(r"\w", c)])
            + ".cookiejar",
        )

    @property
    def session_path(self) -> str:
        """Get path for session data file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.service.account_name if match(r"\w", c)])
            + ".session",
        )
