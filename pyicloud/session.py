"""Pyicloud Session handling"""

import logging
import os
from http.cookiejar import LWPCookieJar
from json import JSONDecodeError, dump
from typing import Any, NoReturn, Optional, Union

import requests
from requests.models import Response

from pyicloud.const import (
    CONTENT_TYPE,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT_JSON,
    HEADER_DATA,
)
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)

KEY_RETRIED = "retried"


class PyiCloudSession(requests.Session):
    """iCloud session."""

    def __init__(self, service) -> None:
        super().__init__()
        self._service = service
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._lwp_cookies = LWPCookieJar(self.service.cookiejar_path)
        if os.path.exists(self.service.cookiejar_path):
            self._lwp_cookies.load()
            self.cookies._cookies = self._lwp_cookies._cookies  # type: ignore

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

        self._lwp_cookies.save()
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

    def _request(self, method, url, *, data=None, **kwargs) -> Response:
        """Request method."""
        self.logger.debug(
            "%s %s %s",
            method,
            url,
            data or "",
        )

        has_retried: bool = kwargs.pop(KEY_RETRIED, False)

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
            try:
                # pylint: disable=protected-access
                fmip_url: str = self.service._get_webservice_url("findme")
                if (
                    not has_retried
                    and response.status_code in [421, 450, 500]
                    and fmip_url in url
                ):
                    self._reauthenticate_find_my_iphone(response)
                    kwargs[KEY_RETRIED] = True
                    return self._request(
                        method=method,
                        url=url,
                        data=data,
                        **kwargs,
                    )
            except Exception:
                pass

            if not has_retried and response.status_code in [421, 450, 500]:
                api_error = PyiCloudAPIResponseException(
                    response.reason, response.status_code, retry=True
                )
                self.logger.debug(api_error)
                kwargs[KEY_RETRIED] = True
                return self._request(
                    method=method,
                    url=url,
                    data=data,
                    **kwargs,
                )

            if self._is_json_response(response):
                self._decode_json_response(response)
            self._raise_error(response.status_code, response.reason)

        if not self._is_json_response(response):
            return response

        self._decode_json_response(response)

        return response

    def _decode_json_response(self, response: Response) -> None:
        try:
            data: dict[str, Any] = response.json()

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
            self.logger.warning("Failed to parse response with JSON mimetype")

    def _raise_error(self, code: Optional[Union[int, str]], reason: str) -> NoReturn:
        if (
            self.service.requires_2sa
            and reason == "Missing X-APPLE-WEBAUTH-TOKEN cookie"
        ):
            raise PyiCloud2SARequiredException(self.service.account_name)
        if code in ("ZONE_NOT_FOUND", "AUTHENTICATION_FAILED"):
            reason = (
                "Please log into https://icloud.com/ to manually "
                "finish setting up your iCloud service"
            )
            api_error = PyiCloudServiceNotActivatedException(reason, code)
            self.logger.error(api_error)

            raise (api_error)
        if code == "ACCESS_DENIED":
            reason = (
                reason + ".  Please wait a few minutes then try again."
                "The remote servers might be trying to throttle requests."
            )
        if code in [421, 450, 500]:
            reason = "Authentication required for Account."

        api_error = PyiCloudAPIResponseException(reason, code)
        self.logger.error(api_error, stacklevel=5)
        raise api_error

    @property
    def service(self):
        """Gets the service."""
        return self._service
