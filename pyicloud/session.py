"""Pyicloud Session handling"""

import http.cookiejar
import logging
import os
from json import JSONDecodeError, dump
from typing import Any, NoReturn, Optional, Union, cast

import requests
import requests.cookies
from requests.models import Response

from pyicloud.const import (
    CONTENT_TYPE,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT_JSON,
    ERROR_ACCESS_DENIED,
    ERROR_AUTHENTICATION_FAILED,
    ERROR_ZONE_NOT_FOUND,
    HEADER_DATA,
)
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)

KEY_RETRIED = "retried"


class PyiCloudCookieJar(
    requests.cookies.RequestsCookieJar, http.cookiejar.LWPCookieJar
):
    """Mix the Requests CookieJar with the LWPCookieJar to allow persistance"""


class PyiCloudSession(requests.Session):
    """iCloud session."""

    def __init__(self, service) -> None:
        super().__init__()
        self._service = service
        self._logger: logging.Logger = logging.getLogger(__name__)
        self.cookies = PyiCloudCookieJar(self.service.cookiejar_path)
        if os.path.exists(self.service.cookiejar_path):
            self.cookies.load(ignore_discard=True, ignore_expires=True)

    @property
    def logger(self) -> logging.Logger:
        """Gets the request logger"""
        if (
            self.service.password_filter is not None
            and self.service.password_filter not in self._logger.filters
        ):
            self._logger.addFilter(self.service.password_filter)
        return self._logger

    def _save_session_data(self) -> None:
        """Save session_data to file."""
        with open(self.service.session_path, "w", encoding="utf-8") as outfile:
            dump(self.service.session_data, outfile)
            self.logger.debug(
                "Saved session data to file: %s", self.service.session_path
            )

        cast(PyiCloudCookieJar, self.cookies).save(
            ignore_discard=True, ignore_expires=True
        )
        self.logger.debug("Saved cookies data to file: %s", self.service.cookiejar_path)

    def _update_session_data(self, response: Response) -> None:
        """Update session_data with new data."""
        for header, value in HEADER_DATA.items():
            if response.headers.get(header):
                session_arg: str = value
                self.service.session_data.update(
                    {session_arg: response.headers.get(header)}
                )

    def _is_json_response(self, response: Response) -> bool:
        content_type: str = response.headers.get(CONTENT_TYPE, "")
        json_mimetypes: list[str] = [
            CONTENT_TYPE_JSON,
            CONTENT_TYPE_TEXT_JSON,
        ]
        return content_type.split(";")[0] in json_mimetypes

    def _reauthenticate_find_my_iphone(self, response: Response) -> None:
        self.logger.debug("Re-authenticating Find My iPhone service")
        try:
            service: Optional[str] = None if response.status_code == 450 else "find"
            self.service.authenticate(True, service)
        except PyiCloudAPIResponseException:
            self.logger.debug("Re-authentication failed")

    def request(
        self,
        method,
        url,
        params=None,
        data=None,
        headers=None,
        cookies=None,
        files=None,
        auth=None,
        timeout=None,
        allow_redirects=True,
        proxies=None,
        hooks=None,
        stream=None,
        verify=None,
        cert=None,
        json=None,
    ) -> Response:
        return self._request(
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

    def _request(
        self,
        method,
        url,
        *,
        data=None,
        has_retried: bool = False,
        **kwargs,
    ) -> Response:
        """Request method."""
        self.logger.debug(
            "%s %s %s",
            method,
            url,
            data or "",
        )

        response: Response = super().request(
            method=method,
            url=url,
            data=data,
            **kwargs,
        )

        self._update_session_data(response)
        self._save_session_data()

        if not response.ok and (
            self._is_json_response(response) or response.status_code in [421, 450, 500]
        ):
            return self._handle_request_error(
                response=response,
                method=method,
                url=url,
                data=data,
                has_retried=has_retried,
                **kwargs,
            )

        response.raise_for_status()

        if not self._is_json_response(response):
            return response

        self._decode_json_response(response)

        return response

    def _handle_request_error(
        self,
        response: Response,
        method,
        url,
        *,
        data=None,
        has_retried: bool = False,
        **kwargs,
    ) -> Response:
        """Handle request error."""

        if (
            response.status_code == 409
            and self._is_json_response(response)
            and (response.json().get("authType") == "hsa2")
        ):
            raise PyiCloud2FARequiredException(
                apple_id=self.service.account_name,
                response=response,
            )

        try:
            fmip_url: str = self.service.get_webservice_url("findme")
            if (
                not has_retried
                and response.status_code in [421, 450, 500]
                and fmip_url in url
            ):
                self._reauthenticate_find_my_iphone(response)
                return self._request(
                    method=method,
                    url=url,
                    data=data,
                    has_retried=True,
                    **kwargs,
                )
        except PyiCloudServiceNotActivatedException:
            pass

        if not has_retried and response.status_code in [421, 450, 500]:
            return self._request(
                method=method,
                url=url,
                data=data,
                has_retried=True,
                **kwargs,
            )

        self._raise_error(response.status_code, response.reason)

    def _decode_json_response(self, response: Response) -> None:
        """Decode JSON response."""
        try:
            if len(response.content) == 0:
                return

            data: Union[list[dict[str, Any]], dict[str, Any]] = response.json()
            if isinstance(data, dict):
                reason: Optional[str] = data.get("errorMessage")
                reason = reason or data.get("reason")
                reason = reason or data.get("errorReason")
                if not reason and isinstance(data.get("error"), str):
                    reason = data.get("error")
                if not reason and data.get("error"):
                    reason = "Unknown reason"

                code: Optional[Union[int, str]] = data.get("errorCode")
                if not code and data.get("serverErrorCode"):
                    code = data.get("serverErrorCode")

                if reason:
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
        if code in [421, 450, 500]:
            reason = "Authentication required for Account."

        raise PyiCloudAPIResponseException(reason, code)

    @property
    def service(self):
        """Gets the service."""
        return self._service
