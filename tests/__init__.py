"""Library tests."""

# pylint: disable=protected-access

import json
from io import BytesIO
from typing import Any, Optional

from requests import Response

from pyicloud import base
from tests.const import (
    AUTHENTICATED_USER,
    REQUIRES_2FA_TOKEN,
    REQUIRES_2FA_USER,
    VALID_2FA_CODE,
    VALID_COOKIE,
    VALID_TOKEN,
    VALID_TOKENS,
    VALID_USERS,
)
from tests.const_account import ACCOUNT_DEVICES_WORKING, ACCOUNT_STORAGE_WORKING
from tests.const_account_family import ACCOUNT_FAMILY_WORKING
from tests.const_drive import (
    DRIVE_FILE_DOWNLOAD_WORKING,
    DRIVE_FOLDER_WORKING,
    DRIVE_ROOT_INVALID,
    DRIVE_ROOT_WORKING,
    DRIVE_SUBFOLDER_WORKING,
    DRIVE_TRASH_DELETE_FOREVER_WORKING,
    DRIVE_TRASH_RECOVER_WORKING,
    DRIVE_TRASH_WORKING,
)
from tests.const_findmyiphone import FMI_FAMILY_WORKING
from tests.const_login import (
    AUTH_OK,
    LOGIN_2FA,
    LOGIN_WORKING,
    TRUSTED_DEVICE_1,
    TRUSTED_DEVICES,
    VERIFICATION_CODE_KO,
    VERIFICATION_CODE_OK,
)


class ResponseMock(Response):
    """Mocked Response."""

    def __init__(self, result, status_code=200, **kwargs) -> None:
        """Set up response mock."""
        Response.__init__(self)
        self.result = result
        self.status_code = status_code
        self.raw = kwargs.get("raw")
        self.headers = kwargs.get("headers", {})

    @property
    def text(self) -> str:
        """Return text."""
        return json.dumps(self.result)


class PyiCloudSessionMock(base.PyiCloudSession):
    """Mocked PyiCloudSession."""

    def _request(self, method, url, **kwargs) -> ResponseMock:
        """Make the request."""
        params = kwargs.get("params")
        headers = kwargs.get("headers")
        data = kwargs.get("json")
        if not data:
            data = json.loads(kwargs.get("data", "{}")) if kwargs.get("data") else {}

        if self._service._setup_endpoint in url:
            if resp := self._handle_setup_endpoint(url, method, data, headers):
                return resp

        if self._service.auth_endpoint in url:
            if resp := self._handle_auth_endpoint(url, method, data):
                return resp

        if resp := self._handle_other_endpoints(url, method, data, params):
            return resp

        raise ValueError("No valid response")

    def _handle_other_endpoints(
        self, url, method, data, params
    ) -> Optional[ResponseMock]:
        """Handle other endpoints."""
        if "device/getDevices" in url and method == "GET":
            return ResponseMock(ACCOUNT_DEVICES_WORKING)
        if "family/getFamilyDetails" in url and method == "GET":
            return ResponseMock(ACCOUNT_FAMILY_WORKING)
        if "setup/ws/1/storageUsageInfo" in url and method == "POST":
            return ResponseMock(ACCOUNT_STORAGE_WORKING)

        resp: Optional[ResponseMock] = None

        resp = self._handle_drive_endpoints_post(url, method, data)
        if resp:
            return resp

        resp = self._handle_drive_endpoints_get(url, method, params)
        if resp:
            return resp

        if "fmi" in url and method == "POST":
            return ResponseMock(FMI_FAMILY_WORKING)

    def _handle_drive_endpoints_post(self, url, method, data) -> Optional[ResponseMock]:
        """Handle drive endpoints post requests."""
        if "retrieveItemDetailsInFolders" in url and method == "POST":
            if resp := self._handle_drive_retrieve(data):
                return resp

        if "putBackItemsFromTrash" in url and method == "POST":
            if resp := self._handle_drive_trash_recover(data):
                return resp

        if "deleteItems" in url and method == "POST":
            if resp := self._handle_drive_trash_delete(data):
                return resp

    def _handle_drive_endpoints_get(
        self, url, method, params
    ) -> Optional[ResponseMock]:
        """Handle drive endpoints get requests."""
        if "com.apple.CloudDocs/download/by_id" in url and method == "GET" and params:
            if resp := self._handle_drive_download(params):
                return resp

        if "icloud-content.com" in url and method == "GET":
            if resp := self._handle_icloud_content(url):
                return resp

    def _handle_setup_endpoint(
        self, url, method, data, headers
    ) -> Optional[ResponseMock]:
        """Handle setup endpoint requests."""
        if "accountLogin" in url and method == "POST":
            return self._handle_account_login(data)

        if "listDevices" in url and method == "GET":
            return ResponseMock(TRUSTED_DEVICES)

        if "sendVerificationCode" in url and method == "POST":
            return self._handle_send_verification_code(data)

        if "validateVerificationCode" in url and method == "POST":
            return self._handle_validate_verification_code(data)

        if "validate" in url and method == "POST" and headers:
            return self._handle_validate(headers)

    def _handle_auth_endpoint(self, url, method, data) -> Optional[ResponseMock]:
        """Handle auth endpoint requests."""
        if "signin" in url and method == "POST":
            return self._handle_signin(data)

        if "securitycode" in url and method == "POST":
            return self._handle_security_code(data)

        if "trust" in url and method == "GET":
            return ResponseMock("", status_code=204)

    def _handle_account_login(self, data: dict[str, Any]) -> ResponseMock:
        """Handle account login."""
        if data.get("dsWebAuthToken") not in VALID_TOKENS:
            self._raise_error(None, "Unknown reason")
        if data.get("dsWebAuthToken") == REQUIRES_2FA_TOKEN:
            return ResponseMock(LOGIN_2FA)
        return ResponseMock(LOGIN_WORKING)

    def _handle_send_verification_code(self, data: dict[str, Any]) -> ResponseMock:
        """Handle send verification code."""
        if data == TRUSTED_DEVICE_1:
            return ResponseMock(VERIFICATION_CODE_OK)
        return ResponseMock(VERIFICATION_CODE_KO)

    def _handle_validate_verification_code(self, data: dict[str, Any]) -> ResponseMock:
        """Handle validate verification code."""
        TRUSTED_DEVICE_1.update(
            {
                "verificationCode": "0",
                "trustBrowser": True,
            }
        )
        if data == TRUSTED_DEVICE_1:
            self._service._apple_id = AUTHENTICATED_USER
            return ResponseMock(VERIFICATION_CODE_OK)
        self._raise_error(None, "FOUND_CODE")

    def _handle_validate(self, headers: dict[str, Any]) -> ResponseMock:
        """Handle validate."""
        if headers.get("X-APPLE-WEBAUTH-TOKEN") == VALID_COOKIE:
            return ResponseMock(LOGIN_WORKING)
        self._raise_error(None, "Session expired")

    def _handle_signin(self, data: dict[str, Any]) -> ResponseMock:
        """Handle signin."""
        if data.get("accountName") not in VALID_USERS:
            self._raise_error(None, "Unknown reason")
        if data.get("accountName") == REQUIRES_2FA_USER:
            self._service.session._data["session_token"] = REQUIRES_2FA_TOKEN
            return ResponseMock(AUTH_OK)

        self._service.session._data["session_token"] = VALID_TOKEN
        return ResponseMock(AUTH_OK)

    def _handle_security_code(self, data: dict[str, Any]) -> ResponseMock:
        """Handle security code."""
        if data.get("securityCode", {}).get("code") != VALID_2FA_CODE:
            self._raise_error(None, "Incorrect code")

        self._service.session._data["session_token"] = VALID_TOKEN
        return ResponseMock("", status_code=204)

    def _handle_drive_retrieve(self, data: dict[Any, Any]) -> Optional[ResponseMock]:
        """Handle drive retrieve item details."""
        drivewsid = data[0].get("drivewsid")
        if drivewsid == "FOLDER::com.apple.CloudDocs::root":
            return ResponseMock(DRIVE_ROOT_WORKING)
        if drivewsid == "FOLDER::com.apple.Preview::documents":
            return ResponseMock(DRIVE_ROOT_INVALID)
        if drivewsid == "FOLDER::com.apple.CloudDocs::TRASH_ROOT":
            return ResponseMock(DRIVE_TRASH_WORKING)
        if (
            drivewsid
            == "FOLDER::com.apple.CloudDocs::1C7F1760-D940-480F-8C4F-005824A4E05B"
        ):
            return ResponseMock(DRIVE_FOLDER_WORKING)
        if (
            drivewsid
            == "FOLDER::com.apple.CloudDocs::D5AA0425-E84F-4501-AF5D-60F1D92648CF"
        ):
            return ResponseMock(DRIVE_SUBFOLDER_WORKING)

    def _handle_drive_trash_recover(
        self, data: dict[str, Any]
    ) -> Optional[ResponseMock]:
        """Handle drive trash recover."""
        items_data = data.get("items")
        if (
            items_data
            and items_data[0].get("drivewsid")
            == "FOLDER::com.apple.CloudDocs::2BF8600B-5DCC-4421-805A-1C28D07197D5"
        ):
            return ResponseMock(DRIVE_TRASH_RECOVER_WORKING)

    def _handle_drive_trash_delete(
        self, data: dict[str, Any]
    ) -> Optional[ResponseMock]:
        """Handle drive trash delete forever."""
        items_data = data.get("items")
        if (
            items_data
            and items_data[0].get("drivewsid")
            == "FOLDER::com.apple.CloudDocs::478AEA23-42A2-468A-ABC1-1A04BC07F738"
        ):
            return ResponseMock(DRIVE_TRASH_DELETE_FOREVER_WORKING)

    def _handle_drive_download(self, params: dict[str, Any]) -> Optional[ResponseMock]:
        """Handle drive download."""
        if params.get("document_id") == "516C896C-6AA5-4A30-B30E-5502C2333DAE":
            return ResponseMock(DRIVE_FILE_DOWNLOAD_WORKING)

    def _handle_icloud_content(self, url: str) -> Optional[ResponseMock]:
        """Handle iCloud content."""
        if "Scanned+document+1.pdf" in url:
            return ResponseMock({}, raw=BytesIO(b"PDF_CONTENT"))
