"""Library base file."""

import base64
import getpass
import hashlib
import http.cookiejar as cookielib
import inspect
import json
import logging
from os import environ, mkdir, path
from re import match
from tempfile import gettempdir
from typing import cast
from uuid import uuid1

import srp
from requests import Session
from requests.cookies import RequestsCookieJar
from requests.models import Response

from pyicloud.const import (
    ACCOUNT_NAME,
    CONTENT_TYPE,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT_JSON,
    HEADER_DATA,
)
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services import (
    AccountService,
    CalendarService,
    ContactsService,
    DriveService,
    FindMyiPhoneServiceManager,
    PhotosService,
    RemindersService,
    UbiquityService,
)
from pyicloud.services.hidemyemail import HideMyEmailService
from pyicloud.utils import get_password_from_keyring

LOGGER = logging.getLogger(__name__)

KEY_RETRIED = "retried"


class SrpPassword:
    """SRP password."""

    def __init__(self, password: str) -> None:
        self.password: str = password

    def set_encrypt_info(self, salt: bytes, iterations: int, key_length: int) -> None:
        """Set encrypt info."""
        self.salt: bytes = salt
        self.iterations: int = iterations
        self.key_length: int = key_length

    def encode(self) -> bytes:
        """Encode password."""
        password_hash: bytes = hashlib.sha256(self.password.encode("utf-8")).digest()
        return hashlib.pbkdf2_hmac(
            "sha256",
            password_hash,
            self.salt,
            self.iterations,
            self.key_length,
        )


class PyiCloudPasswordFilter(logging.Filter):
    """Password log hider."""

    def __init__(self, password):
        super().__init__(password)

    def filter(self, record):
        message = record.getMessage()
        if self.name in message:
            record.msg = message.replace(self.name, "*" * 8)
            record.args = ()

        return True


class PyiCloudSession(Session):
    """iCloud session."""

    def __init__(self, service):
        self.service = service
        super().__init__()

    def _get_request_logger(self, stack):
        """Charge logging to the right service endpoint."""
        callee = stack[2]
        module = inspect.getmodule(callee[0])
        if module:
            request_logger = logging.getLogger(module.__name__).getChild("http")
            if self.service.password_filter not in request_logger.filters:
                request_logger.addFilter(self.service.password_filter)
            return request_logger
        return logging.getLogger()

    def _save_session_data(self):
        """Save session_data to file."""
        with open(self.service.session_path, "w", encoding="utf-8") as outfile:
            json.dump(self.service.session_data, outfile)
            LOGGER.debug("Saved session data to file")

    def _update_session_data(self, response) -> None:
        """Update session_data with new data."""
        for header, value in HEADER_DATA.items():
            if response.headers.get(header):
                session_arg = value
                self.service.session_data.update(
                    {session_arg: response.headers.get(header)}
                )

    def _is_json_response(self, response) -> bool:
        content_type = response.headers.get(CONTENT_TYPE, "")
        json_mimetypes = [
            CONTENT_TYPE_JSON,
            CONTENT_TYPE_TEXT_JSON,
        ]
        return content_type in json_mimetypes

    def _reauthenticate_find_my_iphone(self, response) -> None:
        LOGGER.debug("Re-authenticating Find My iPhone service")
        try:
            service = None if response.status_code == 450 else "find"
            self.service.authenticate(True, service)
        except PyiCloudAPIResponseException:
            LOGGER.debug("Re-authentication failed")

    def request(self, method, url, **kwargs):  # type: ignore
        """Request method."""
        request_logger: logging.Logger = self._get_request_logger(inspect.stack())
        request_logger.debug("%s %s %s", method, url, kwargs.get("data", ""))

        has_retried: bool = kwargs.pop(KEY_RETRIED, False)
        response: Response = super().request(method, url, **kwargs)

        self._update_session_data(response)
        self._save_session_data()

        # Save cookies to file
        if isinstance(self.cookies, cookielib.LWPCookieJar):
            self.cookies.save(ignore_discard=True, ignore_expires=True)
        LOGGER.debug("Cookies saved to %s", self.service.cookiejar_path)

        if not response.ok and (
            self._is_json_response(response) or response.status_code in [421, 450, 500]
        ):
            try:
                # pylint: disable=protected-access
                fmip_url = self.service._get_webservice_url("findme")
                if (
                    not has_retried
                    and response.status_code in [421, 450, 500]
                    and fmip_url in url
                ):
                    self._reauthenticate_find_my_iphone(response)
                    kwargs[KEY_RETRIED] = True
                    return self.request(method, url, **kwargs)
            except Exception:
                pass

            if not has_retried and response.status_code in [421, 450, 500]:
                api_error = PyiCloudAPIResponseException(
                    response.reason, response.status_code, retry=True
                )
                request_logger.debug(api_error)
                kwargs[KEY_RETRIED] = True
                return self.request(method, url, **kwargs)

            self._raise_error(response.status_code, response.reason)

        if not self._is_json_response(response):
            return response

        self._decode_json_response(response, request_logger)

        return response

    def _decode_json_response(self, response, logger):
        try:
            data = response.json()
            logger.debug(data)

            if isinstance(data, dict):
                reason = data.get("errorMessage")
                reason = reason or data.get("reason")
                reason = reason or data.get("errorReason")
                if not reason and isinstance(data.get("error"), str):
                    reason = data.get("error")
                if not reason and data.get("error"):
                    reason = "Unknown reason"

                code = data.get("errorCode")
                if not code and data.get("serverErrorCode"):
                    code = data.get("serverErrorCode")

                if reason:
                    self._raise_error(code, reason)
        except json.JSONDecodeError:
            logger.warning("Failed to parse response with JSON mimetype")

    def _raise_error(self, code, reason):
        if (
            self.service.requires_2sa
            and reason == "Missing X-APPLE-WEBAUTH-TOKEN cookie"
        ):
            raise PyiCloud2SARequiredException(self.service.user["apple_id"])
        if code in ("ZONE_NOT_FOUND", "AUTHENTICATION_FAILED"):
            reason = (
                "Please log into https://icloud.com/ to manually "
                "finish setting up your iCloud service"
            )
            api_error = PyiCloudServiceNotActivatedException(reason, code)
            LOGGER.error(api_error)

            raise (api_error)
        if code == "ACCESS_DENIED":
            reason = (
                reason + ".  Please wait a few minutes then try again."
                "The remote servers might be trying to throttle requests."
            )
        if code in [421, 450, 500]:
            reason = "Authentication required for Account."

        api_error = PyiCloudAPIResponseException(reason, code)
        LOGGER.error(api_error)
        raise api_error


class PyiCloudService(object):
    """
    A base authentication class for the iCloud service. Handles the
    authentication required to access iCloud services.

    Usage:
        from pyicloud import PyiCloudService
        pyicloud = PyiCloudService('username@apple.com', 'password')
        pyicloud.iphone.location()
    """

    def _setup_endpoints(self, china_mainland) -> None:
        """Set up the endpoints for the service."""
        # If the country or region setting of your Apple ID is China mainland.
        # See https://support.apple.com/en-us/HT208351
        icloud_china: str = (
            ".cn" if china_mainland or environ.get("icloud_china", "0") == "1" else ""
        )
        self.AUTH_ENDPOINT: str = (
            f"https://idmsa.apple.com{icloud_china}/appleauth/auth"
        )
        self.HOME_ENDPOINT: str = f"https://www.icloud.com{icloud_china}"
        self.SETUP_ENDPOINT: str = f"https://setup.icloud.com{icloud_china}/setup/ws/1"

    def _setup_cookie_directory(self, cookie_directory) -> None:
        """Set up the cookie directory for the service."""
        if cookie_directory:
            self._cookie_directory = path.expanduser(path.normpath(cookie_directory))
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)
        else:
            topdir = path.join(gettempdir(), "pyicloud")
            self._cookie_directory = path.join(topdir, getpass.getuser())
            if not path.exists(topdir):
                mkdir(topdir, 0o777)
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)

    def __init__(
        self,
        apple_id,
        password=None,
        cookie_directory=None,
        verify=True,
        client_id=None,
        with_family=True,
        china_mainland=False,
    ) -> None:
        self._setup_endpoints(china_mainland)

        if password is None:
            password = get_password_from_keyring(apple_id)

        self.user = {ACCOUNT_NAME: apple_id, "password": password}
        self.data = {}

        self.params = {}
        self.client_id = client_id or ("auth-%s" % str(uuid1()).lower())
        self.with_family = with_family

        self.password_filter = PyiCloudPasswordFilter(password)
        LOGGER.addFilter(self.password_filter)

        self._setup_cookie_directory(cookie_directory)

        LOGGER.debug("Using session file %s", self.session_path)

        self.session_data = {}
        try:
            with open(self.session_path, encoding="utf-8") as session_f:
                self.session_data = json.load(session_f)
        except (
            json.JSONDecodeError,
            OSError,
        ):  # pylint: disable=bare-except
            LOGGER.info("Session file does not exist")
        if self.session_data.get("client_id"):
            self.client_id = self.session_data.get("client_id")
        else:
            self.session_data.update({"client_id": self.client_id})

        self.session = PyiCloudSession(self)
        self.session.verify = verify
        self.session.headers.update(
            {"Origin": self.HOME_ENDPOINT, "Referer": "%s/" % self.HOME_ENDPOINT}
        )

        cookiejar_path = self.cookiejar_path
        if path.exists(cookiejar_path):
            try:
                cookies = cookielib.LWPCookieJar(filename=cookiejar_path)
                cookies.load(ignore_discard=True, ignore_expires=True)
                self.session.cookies = cast(RequestsCookieJar, cookies)
                LOGGER.debug("Read cookies from %s", cookiejar_path)
            except (ValueError, OSError):
                # Most likely a pickled cookiejar from earlier versions.
                # The cookiejar will get replaced with a valid one after
                # successful authentication.
                LOGGER.warning("Failed to read cookiejar %s", cookiejar_path)

        self.authenticate()

        self._drive = None
        self._files = None
        self._photos = None

    def authenticate(self, force_refresh=False, service=None):
        """
        Handles authentication, and persists cookies so that
        subsequent logins will not cause additional e-mails from Apple.
        """

        login_successful = False
        if self.session_data.get("session_token") and not force_refresh:
            LOGGER.debug("Checking session token validity")
            try:
                self.data = self._validate_token()
                login_successful = True
            except PyiCloudAPIResponseException:
                LOGGER.debug("Invalid authentication token, will log in from scratch.")

        if not login_successful and service is not None:
            app = self.data["apps"][service]
            if "canLaunchWithOneFactor" in app and app["canLaunchWithOneFactor"]:
                LOGGER.debug(
                    "Authenticating as %s for %s", self.user[ACCOUNT_NAME], service
                )
                try:
                    self._authenticate_with_credentials_service(service)
                    login_successful = True
                except Exception:
                    LOGGER.debug(
                        "Could not log into service. Attempting brand new login."
                    )

        if not login_successful:
            self._authenticate()

        if (
            "dsInfo" in self.data
            and isinstance(self.data["dsInfo"], dict)
            and "dsid" in self.data["dsInfo"]
        ):
            self.params.update({"dsid": self.data["dsInfo"]["dsid"]})

        self._webservices = self.data["webservices"]

        LOGGER.debug("Authentication completed successfully")

    def _authenticate(self):
        LOGGER.debug("Authenticating as %s", self.user[ACCOUNT_NAME])

        headers = self._get_auth_headers()
        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data.get("scnt")

        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

        self._srp_authentication(headers)
        self._authenticate_with_token()

    def _srp_authentication(self, headers):
        """SRP authentication."""
        srp_password = SrpPassword(self.user["password"])
        srp.rfc5054_enable()
        srp.no_username_in_x()
        usr = srp.User(
            self.user[ACCOUNT_NAME],
            srp_password,
            hash_alg=srp.SHA256,
            ng_type=srp.NG_2048,
        )
        uname, A = usr.start_authentication()
        data = {
            "a": base64.b64encode(A).decode(),
            ACCOUNT_NAME: uname,
            "protocols": ["s2k", "s2k_fo"],
        }

        try:
            response = self.session.post(
                "%s/signin/init" % self.AUTH_ENDPOINT,
                data=json.dumps(data),
                headers=headers,
            )
            response.raise_for_status()
        except PyiCloudAPIResponseException as error:
            msg = "Failed to initiate srp authentication."
            raise PyiCloudFailedLoginException(msg, error) from error

        body = response.json()
        salt = base64.b64decode(body["salt"])
        b = base64.b64decode(body["b"])
        c = body["c"]
        iterations = body["iteration"]
        key_length = 32
        srp_password.set_encrypt_info(salt, iterations, key_length)
        m1 = usr.process_challenge(salt, b)
        m2 = usr.H_AMK
        if m1 and m2:
            data = {
                ACCOUNT_NAME: uname,
                "c": c,
                "m1": base64.b64encode(m1).decode(),
                "m2": base64.b64encode(m2).decode(),
                "rememberMe": True,
                "trustTokens": [],
            }
        if self.session_data.get("trust_token"):
            data["trustTokens"] = [self.session_data.get("trust_token")]

        try:
            req = self.session.post(
                "%s/signin/complete" % self.AUTH_ENDPOINT,
                params={"isRememberMeEnabled": "true"},
                data=json.dumps(data),
                headers=headers,
            )
            self.data = req.json()

            if req.status_code == 403 and self.data["serviceErrors"]:
                error = self.data["serviceErrors"][0]
                LOGGER.debug("srp_authentication signin/complete has Service Error %s", error)
                raise PyiCloudFailedLoginException(error["message"], error["code"])

        except PyiCloudAPIResponseException as error:
            msg = "Invalid email/password combination."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _authenticate_with_token(self):
        """Authenticate using session token."""
        data = {
            "accountCountryCode": self.session_data.get("account_country"),
            "dsWebAuthToken": self.session_data.get("session_token"),
            "extended_login": True,
            "trustToken": self.session_data.get("trust_token", ""),
        }

        try:
            req = self.session.post(
                "%s/accountLogin" % self.SETUP_ENDPOINT, data=json.dumps(data)
            )
            self.data = req.json()
        except PyiCloudAPIResponseException as error:
            msg = "Invalid authentication token."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _authenticate_with_credentials_service(self, service):
        """Authenticate to a specific service using credentials."""
        data = {
            "appName": service,
            "apple_id": self.user[ACCOUNT_NAME],
            "password": self.user["password"],
        }

        try:
            self.session.post(
                "%s/accountLogin" % self.SETUP_ENDPOINT, data=json.dumps(data)
            )

            self.data = self._validate_token()
        except PyiCloudAPIResponseException as error:
            msg = "Invalid email/password combination."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _validate_token(self):
        """Checks if the current access token is still valid."""
        LOGGER.debug("Checking session token validity")
        try:
            req = self.session.post("%s/validate" % self.SETUP_ENDPOINT, data="null")
            LOGGER.debug("Session token is still valid")
            return req.json()
        except PyiCloudAPIResponseException as err:
            LOGGER.debug("Invalid authentication token")
            raise err

    def _get_auth_headers(self, overrides=None):
        headers = {
            "Accept": f"{CONTENT_TYPE_JSON}, text/javascript",
            "Content-Type": CONTENT_TYPE_JSON,
            "X-Apple-OAuth-Client-Id": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": "https://www.icloud.com",
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": self.client_id,
            "X-Apple-Widget-Key": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
        }
        if overrides:
            headers.update(overrides)
        return headers

    @property
    def cookiejar_path(self):
        """Get path for cookiejar file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.user.get(ACCOUNT_NAME, "") if match(r"\w", c)]),
        )

    @property
    def session_path(self):
        """Get path for session data file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.user.get(ACCOUNT_NAME, "") if match(r"\w", c)])
            + ".session",
        )

    @property
    def requires_2sa(self):
        """Returns True if two-step authentication is required."""
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) >= 1 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def requires_2fa(self):
        """Returns True if two-factor authentication is required."""
        return self.data["dsInfo"].get("hsaVersion", 0) == 2 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def is_trusted_session(self):
        """Returns True if the session is trusted."""
        return self.data.get("hsaTrustedBrowser", False)

    @property
    def trusted_devices(self):
        """Returns devices trusted for two-step authentication."""
        request = self.session.get(
            "%s/listDevices" % self.SETUP_ENDPOINT, params=self.params
        )
        return request.json().get("devices")

    def send_verification_code(self, device):
        """Requests that a verification code is sent to the given device."""
        data = json.dumps(device)
        request = self.session.post(
            "%s/sendVerificationCode" % self.SETUP_ENDPOINT,
            params=self.params,
            data=data,
        )
        return request.json().get("success", False)

    def validate_verification_code(self, device, code):
        """Verifies a verification code received on a trusted device."""
        device.update({"verificationCode": code, "trustBrowser": True})
        data = json.dumps(device)

        try:
            self.session.post(
                "%s/validateVerificationCode" % self.SETUP_ENDPOINT,
                params=self.params,
                data=data,
            )
        except PyiCloudAPIResponseException as error:
            if error.code == -21669:
                # Wrong verification code
                return False
            raise

        self.trust_session()

        return not self.requires_2sa

    def validate_2fa_code(self, code):
        """Verifies a verification code received via Apple's 2FA system (HSA2)."""
        data = {"securityCode": {"code": code}}

        headers = self._get_auth_headers({"Accept": CONTENT_TYPE_JSON})

        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data.get("scnt")

        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

        try:
            self.session.post(
                "%s/verify/trusteddevice/securitycode" % self.AUTH_ENDPOINT,
                data=json.dumps(data),
                headers=headers,
            )
        except PyiCloudAPIResponseException as error:
            if error.code == -21669:
                # Wrong verification code
                LOGGER.error("Code verification failed.")
                return False
            raise

        LOGGER.debug("Code verification successful.")

        self.trust_session()
        return not self.requires_2sa

    def trust_session(self):
        """Request session trust to avoid user log in going forward."""
        headers = self._get_auth_headers()

        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data.get("scnt")

        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

        try:
            self.session.get(
                f"{self.AUTH_ENDPOINT}/2sv/trust",
                headers=headers,
            )
            self._authenticate_with_token()
            return True
        except PyiCloudAPIResponseException:
            LOGGER.error("Session trust failed.")
            return False

    def _get_webservice_url(self, ws_key):
        """Get webservice URL, raise an exception if not exists."""
        if self._webservices.get(ws_key) is None:
            raise PyiCloudServiceNotActivatedException(
                "Webservice not available", ws_key
            )
        return self._webservices[ws_key]["url"]

    @property
    def devices(self):
        """Returns all devices."""
        service_root = self._get_webservice_url("findme")
        return FindMyiPhoneServiceManager(
            service_root, self.session, self.params, self.with_family
        )

    @property
    def hidemyemail(self):
        """Gets the 'HME' service."""
        service_root = self._get_webservice_url("premiummailsettings")
        return HideMyEmailService(service_root, self.session, self.params)

    @property
    def iphone(self):
        """Returns the iPhone."""
        return self.devices[0]

    @property
    def account(self):
        """Gets the 'Account' service."""
        service_root = self._get_webservice_url("account")
        return AccountService(service_root, self.session, self.params)

    @property
    def files(self):
        """Gets the 'File' service."""
        if not self._files:
            service_root = self._get_webservice_url("ubiquity")
            self._files = UbiquityService(service_root, self.session, self.params)
        return self._files

    @property
    def photos(self):
        """Gets the 'Photo' service."""
        if not self._photos:
            service_root = self._get_webservice_url("ckdatabasews")
            upload_url = self._get_webservice_url("uploadimagews")
            shared_streams_url = self._get_webservice_url("sharedstreams")
            self.params["dsid"] = self.data["dsInfo"]["dsid"]

            self._photos = PhotosService(
                service_root, self.session, self.params, upload_url, shared_streams_url
            )
        return self._photos

    @property
    def calendar(self):
        """Gets the 'Calendar' service."""
        service_root = self._get_webservice_url("calendar")
        return CalendarService(service_root, self.session, self.params)

    @property
    def contacts(self):
        """Gets the 'Contacts' service."""
        service_root = self._get_webservice_url("contacts")
        return ContactsService(service_root, self.session, self.params)

    @property
    def reminders(self):
        """Gets the 'Reminders' service."""
        service_root = self._get_webservice_url("reminders")
        return RemindersService(service_root, self.session, self.params)

    @property
    def drive(self):
        """Gets the 'Drive' service."""
        if not self._drive:
            self._drive = DriveService(
                service_root=self._get_webservice_url("drivews"),
                document_root=self._get_webservice_url("docws"),
                session=self.session,
                params=self.params,
            )
        return self._drive

    def __str__(self):
        return f"iCloud API: {self.user.get('accountName')}"

    def __repr__(self):
        return f"<{self}>"
