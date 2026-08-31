"""Pyicloud Session handling"""

from json import JSONDecodeError, dump, load
import logging
import os
from os import path
from re import match
from typing import TYPE_CHECKING, Any, NoReturn, cast

import requests
from requests.models import Response

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
    from pyicloud.base import PyiCloudService


NON_PERSISTED_SESSION_KEYS = frozenset({
    "akdata",
    "connection_path",
    "data",
    "encryptedCode",
    "encrypted_code",
    "idmsdata",
    "mid",
    "nextStep",
    "next_step",
    "ptkn",
    "push_token",
    "salt",
    "sessionUUID",
    "session_uuid",
    "source_app_id",
    "topic",
    "topics_by_hash",
    "txnid",
})


class PyiCloudSession(requests.Session):
    """iCloud session."""

    def __init__(
        self,
        service: "PyiCloudService",
        client_id: str,
        cookie_directory: str,
        verify: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the persisted requests session used by the service."""
        super().__init__()

        self._service: PyiCloudService = service
        self.verify = verify
        self._cookie_directory: str = cookie_directory
        self.cookies = PyiCloudCookieJar(filename=self.cookiejar_path)
        self._data: dict[str, Any] = {}

        self._logger: logging.Logger = logging.getLogger(__name__)

        if headers:
            self.headers.update(headers)

        self._load_session_data()

        if not self._data.get("client_id"):
            self._data.update({"client_id": client_id})

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
                cast(PyiCloudCookieJar, self.cookies).load()
            except (OSError, ValueError) as exc:
                self._logger.warning(
                    "Failed to load cookie jar %s: %s; starting without persisted "
                    "cookies",
                    self.cookiejar_path,
                    exc,
                )
                cast(PyiCloudCookieJar, self.cookies).clear()

        self._logger.debug("Using session file %s", self.session_path)
        self._data = {}
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
            # Copy to avoid dict mutation during concurrent access
            dump(
                {
                    key: value
                    for key, value in dict(self._data).items()
                    if key not in NON_PERSISTED_SESSION_KEYS
                },
                outfile,
            )
            self.logger.debug("Saved session data to file: %s", self.session_path)

        try:
            cast(PyiCloudCookieJar, self.cookies).save()
            self.logger.debug("Saved cookies data to file: %s", self.cookiejar_path)
        except (OSError, ValueError) as exc:
            self.logger.warning("Failed to save cookies data: %s", exc)

    def clear_persistence(self, remove_files: bool = True) -> None:
        """Clear persisted session and cookie state."""

        try:
            cast(PyiCloudCookieJar, self.cookies).clear()
        except (KeyError, RuntimeError) as exc:
            self._logger.warning(
                "Failed to clear cookie jar %s: %s; resetting in-memory cookie jar",
                self.cookiejar_path,
                exc,
            )
            self.cookies = PyiCloudCookieJar(filename=self.cookiejar_path)

        self._data = {}

        if remove_files:
            for persisted_path in (self.cookiejar_path, self.session_path):
                try:
                    os.remove(persisted_path)
                except FileNotFoundError:
                    continue
        else:
            self._save_session_data()

    def _update_session_data(self, response: Response) -> None:
        """Update session_data with new data."""
        for header, value in HEADER_DATA.items():
            if response.headers.get(header):
                session_arg: str = value
                self._data.update({session_arg: response.headers.get(header)})

    def _is_json_response(self, response: Response) -> bool:
        """Return whether a response advertises one of the accepted JSON mimetypes."""
        content_type: str = response.headers.get(CONTENT_TYPE, "")
        json_mimetypes: list[str] = [
            CONTENT_TYPE_JSON,
            CONTENT_TYPE_TEXT_JSON,
        ]
        return content_type.split(";")[0] in json_mimetypes

    def request(
        self,
        method: str | bytes,
        url: str | bytes,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        cookies: Any = None,
        files: Any = None,
        auth: Any = None,
        timeout: Any = None,
        allow_redirects: bool = True,
        proxies: Any = None,
        hooks: Any = None,
        stream: Any = None,
        verify: Any = None,
        cert: Any = None,
        json: Any = None,
    ) -> Response:
        """Dispatch a request through the normalized session request pipeline."""
        return self._request(
            cast(str, method),
            cast(str, url),
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
        )

    def request_raw(
        self,  # noqa: S107
        method: str,
        url: str,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        cookies: Any = None,
        files: Any = None,
        auth: Any = None,
        timeout: Any = None,
        allow_redirects: bool = True,
        proxies: Any = None,
        hooks: Any = None,
        stream: Any = None,
        verify: Any = None,
        cert: Any = None,
        json: Any = None,
    ) -> Response:
        """Dispatch a request without response-status normalization."""

        return self._request_raw(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
        )

    def _request_raw(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Response:
        """Perform a request and persist cookies/session data without raising."""

        self.logger.debug(
            "%s %s",
            method,
            url,
        )
        try:
            response: Response = super().request(
                method=method,
                url=url,
                **kwargs,
            )
        except requests.exceptions.RequestException as err:
            self._raise_request_exception(err)
        self._update_session_data(response)
        self._save_session_data()
        return response

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Response:
        """Request method."""
        self.logger.debug(
            "%s %s",
            method,
            url,
        )

        try:
            response: Response = super().request(
                method=method,
                url=url,
                **kwargs,
            )

            self._update_session_data(response)
            self._save_session_data()

            status_code: int = int(response.status_code)

            if not response.ok and (
                self._is_json_response(response)
                or status_code
                in [
                    AppleAuthError.TWO_FACTOR_REQUIRED,
                    AppleAuthError.FIND_MY_REAUTH_REQUIRED,
                    AppleAuthError.LOGIN_TOKEN_EXPIRED,
                    AppleAuthError.GENERAL_AUTH_ERROR,
                ]
            ):
                return self._handle_request_error(
                    status_code=status_code,
                    response=response,
                )

            response.raise_for_status()

            if not self._is_json_response(response):
                return response

            self._decode_json_response(response)

            return response
        except requests.exceptions.RequestException as err:
            self._raise_request_exception(err)

    @staticmethod
    def _raise_request_exception(err: requests.exceptions.RequestException) -> NoReturn:
        """Normalize low-level requests failures into the session's public error
        type.
        """

        if isinstance(err, requests.HTTPError) and err.response is not None:
            raise PyiCloudAPIResponseException(
                reason=err.response.text,
                code=err.response.status_code,
            ) from err
        raise PyiCloudAPIResponseException("Request failed to iCloud") from err

    def _handle_request_error(
        self,
        status_code: int,
        response: Response,
    ) -> Response:
        """Handle request error."""
        if status_code == AppleAuthError.TWO_FACTOR_REQUIRED and self._is_json_response(
            response
        ):
            auth_type: str | None = self._auth_type_from_hsa2_body(response)
            if auth_type == "hsa2":
                raise PyiCloud2FARequiredException(
                    apple_id=self.service.account_name,
                    response=response,
                )

        if status_code == AppleAuthError.FIND_MY_REAUTH_REQUIRED:
            raise PyiCloudAuthRequiredException(
                apple_id=self.service.account_name,
                response=response,
            )

        self._raise_error(response, status_code, response.reason)

    def _auth_type_from_hsa2_body(self, response: Response) -> str | None:
        """Return the HSA2 authentication type from a challenge body, if any.

        Apple uses ``authType`` on some endpoints and ``authenticationType`` on
        the SMS securitycode challenge responses. JSON parsing is best-effort so
        an empty, invalid, or non-object body falls through to the generic error
        path rather than masking it.
        """
        try:
            data = response.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("authType") == "hsa2":
            return "hsa2"
        if data.get("authenticationType") == "hsa2":
            return "hsa2"
        return None

    def _decode_json_response(self, response: Response) -> None:
        """Decode JSON response."""
        if len(response.content) == 0:
            return

        try:
            data: list[dict[str, Any]] | dict[str, Any] = response.json()
            if isinstance(data, dict):
                reason: str | None = data.get("errorMessage")
                reason = reason or data.get("reason")
                reason = reason or data.get("errorReason")
                reason = reason or data.get("error")
                if reason and not isinstance(reason, str):
                    reason = "Unknown reason"

                if reason:
                    code: int | str | None = data.get("errorCode")
                    code = code or data.get("serverErrorCode")
                    self._raise_error(response, code, reason)

        except JSONDecodeError:
            self.logger.debug(
                "Failed to parse response body as JSON despite JSON mimetype; "
                "status=%s",
                getattr(response, "status_code", "unknown"),
            )

    def _raise_error(
        self, response: Response, code: int | str | None, reason: str
    ) -> NoReturn:
        """Raise the session's public exception for a parsed iCloud error payload."""
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
            raise PyiCloudServiceNotActivatedException(reason, code, response)
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

        raise PyiCloudAPIResponseException(reason, code, response)

    @property
    def service(self) -> "PyiCloudService":
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
