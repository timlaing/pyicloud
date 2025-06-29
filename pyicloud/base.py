"""Library base file."""

import base64
import getpass
import hashlib
import logging
import time
from os import environ, mkdir, path
from tempfile import gettempdir
from typing import Any, Dict, List, Optional
from uuid import uuid1

import srp
from fido2.client import DefaultClientDataCollector, Fido2Client
from fido2.hid import CtapHidDevice
from fido2.webauthn import (
    AuthenticationResponse,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
    PublicKeyCredentialType,
    UserVerificationRequirement,
)
from requests import HTTPError
from requests.models import Response

from pyicloud.const import ACCOUNT_NAME, CONTENT_TYPE_JSON
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudPasswordException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailable,
)
from pyicloud.services import (
    AccountService,
    AppleDevice,
    CalendarService,
    ContactsService,
    DriveService,
    FindMyiPhoneServiceManager,
    HideMyEmailService,
    PhotosService,
    RemindersService,
    UbiquityService,
)
from pyicloud.session import PyiCloudSession
from pyicloud.utils import get_password_from_keyring

LOGGER: logging.Logger = logging.getLogger(__name__)
PCS_SLEEP_TIME: int = 5
PCS_MAX_RETRIES: int = 10


def b64url_decode(s: str) -> bytes:
    """Decode a base64url encoded string."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64_encode(b: bytes) -> str:
    """Encode bytes to a base64url encoded string."""
    return base64.b64encode(b).decode()


class SrpPassword:
    """SRP password."""

    def __init__(self, password: str) -> None:
        self.password: str = password
        self.salt: bytes
        self.iterations: int
        self.key_length: int

    def set_encrypt_info(self, salt: bytes, iterations: int, key_length: int) -> None:
        """Set encrypt info."""
        self.salt = salt
        self.iterations = iterations
        self.key_length = key_length

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

    def __init__(self, password: str) -> None:
        super().__init__(password)

    def filter(self, record) -> bool:
        message: str = record.getMessage()
        if self.name in message:
            record.msg = message.replace(self.name, "*" * 8)
            record.args = ()

        return True


class PyiCloudService(object):
    """
    A base authentication class for the iCloud service. Handles the
    authentication required to access iCloud services.

    Usage:
        from pyicloud import PyiCloudService
        pyicloud = PyiCloudService('username@apple.com', 'password')
        pyicloud.iphone.location()
    """

    def _setup_endpoints(self) -> None:
        """Set up the endpoints for the service."""
        # If the country or region setting of your Apple ID is China mainland.
        # See https://support.apple.com/en-us/HT208351
        icloud_china: str = ".cn" if self._is_china_mainland else ""
        self.auth_endpoint: str = (
            f"https://idmsa.apple.com{icloud_china}/appleauth/auth"
        )
        self._home_endpoint: str = f"https://www.icloud.com{icloud_china}"
        self._setup_endpoint: str = f"https://setup.icloud.com{icloud_china}/setup/ws/1"

    def _setup_cookie_directory(self, cookie_directory: Optional[str]) -> str:
        """Set up the cookie directory for the service."""
        _cookie_directory: str = ""
        if cookie_directory:
            _cookie_directory = path.expanduser(path.normpath(cookie_directory))
            if not path.exists(_cookie_directory):
                mkdir(_cookie_directory, 0o700)
        else:
            topdir: str = path.join(gettempdir(), "pyicloud")
            _cookie_directory = path.join(topdir, getpass.getuser())
            if not path.exists(topdir):
                mkdir(topdir, 0o777)
            if not path.exists(_cookie_directory):
                mkdir(_cookie_directory, 0o700)

        return _cookie_directory

    def __init__(
        self,
        apple_id: str,
        password: Optional[str] = None,
        cookie_directory: Optional[str] = None,
        verify: bool = True,
        client_id: Optional[str] = None,
        with_family: bool = True,
        china_mainland: bool = False,
    ) -> None:
        self._is_china_mainland: bool = (
            china_mainland or environ.get("icloud_china", "0") == "1"
        )
        self._setup_endpoints()
        self._password: Optional[str] = password
        self._apple_id: str = apple_id

        if self._password is None:
            self._password = get_password_from_keyring(apple_id)

        self.data: dict[str, Any] = {}

        self.params: dict[str, Any] = {}
        self._client_id: str = client_id or (f"auth-{str(uuid1()).lower()}")
        self._with_family: bool = with_family
        self._password_filter: Optional[PyiCloudPasswordFilter] = None

        _cookie_directory = self._setup_cookie_directory(cookie_directory)

        self.session: PyiCloudSession = PyiCloudSession(
            self,
            verify=verify,
            headers={
                "Origin": self._home_endpoint,
                "Referer": f"{self._home_endpoint}/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15",
            },
            client_id=self._client_id,
            cookie_directory=_cookie_directory,
        )

        self._client_id = self.session.data.get("client_id", self._client_id)

        self.params = {
            "clientBuildNumber": "2512Hotfix21",
            "clientMasteringNumber": "2512Hotfix21",
            "ckjsBuildVersion": "2310ProjectDev27",
            "ckjsVersion": "2.6.4",
            "clientId": self._client_id,
        }

        self._webservices: Optional[dict[str, dict[str, Any]]] = None

        self._account: Optional[AccountService] = None
        self._calendar: Optional[CalendarService] = None
        self._contacts: Optional[ContactsService] = None
        self._devices: Optional[FindMyiPhoneServiceManager] = None
        self._drive: Optional[DriveService] = None
        self._files: Optional[UbiquityService] = None
        self._hidemyemail: Optional[HideMyEmailService] = None
        self._photos: Optional[PhotosService] = None
        self._reminders: Optional[RemindersService] = None

        self.authenticate()

    def authenticate(
        self, force_refresh: bool = False, service: Optional[str] = None
    ) -> None:
        """
        Handles authentication, and persists cookies so that
        subsequent logins will not cause additional e-mails from Apple.
        """

        login_successful = False
        if self.session.data.get("session_token") and not force_refresh:
            try:
                self.data = self._validate_token()
                login_successful = True
            except PyiCloudAPIResponseException:
                LOGGER.debug("Invalid authentication token, will log in from scratch.")

        if (
            not login_successful
            and service is not None
            and service in self.data["apps"]
        ):
            app: dict[str, Any] = self.data["apps"][service]
            if "canLaunchWithOneFactor" in app and app["canLaunchWithOneFactor"]:
                LOGGER.debug("Authenticating as %s for %s", self.account_name, service)
                try:
                    self._authenticate_with_credentials_service(service)
                    login_successful = True
                except Exception:
                    LOGGER.debug(
                        "Could not log into service. Attempting brand new login."
                    )

        if not login_successful:
            try:
                self._authenticate()
            except PyiCloud2FARequiredException:
                LOGGER.debug("2FA is required")

        self._update_state()

    def _update_state(self) -> None:
        """Update the state of the service."""
        if (
            "dsInfo" in self.data
            and isinstance(self.data["dsInfo"], dict)
            and "dsid" in self.data["dsInfo"]
        ):
            self.params.update({"dsid": self.data["dsInfo"]["dsid"]})

        if "webservices" in self.data:
            self._webservices = self.data["webservices"]

            LOGGER.debug("Authentication completed successfully")

    def _authenticate(self) -> None:
        LOGGER.debug("Authenticating as %s", self.account_name)

        try:
            self._authenticate_with_token()
        except (PyiCloudFailedLoginException, PyiCloud2FARequiredException):
            headers: dict[str, str] = self._get_auth_headers()

            self._srp_authentication(headers)
            self._authenticate_with_token()

    def _srp_authentication(self, headers: dict[str, Any]) -> None:
        """SRP authentication."""
        try:
            srp_password = SrpPassword(self.password)
        except PyiCloudPasswordException as error:
            raise PyiCloudFailedLoginException("Password not provided") from error
        srp.rfc5054_enable()
        srp.no_username_in_x()
        usr = srp.User(
            self.account_name,
            srp_password,
            hash_alg=srp.SHA256,
            ng_type=srp.NG_2048,
        )
        uname, A = usr.start_authentication()  # pylint: disable=invalid-name
        data: dict[str, Any] = {
            "a": b64_encode(A),
            ACCOUNT_NAME: uname,
            "protocols": ["s2k", "s2k_fo"],
        }

        try:
            response: Response = self.session.post(
                f"{self.auth_endpoint}/signin/init",
                json=data,
                headers=headers,
            )
            response.raise_for_status()
        except (PyiCloudAPIResponseException, HTTPError) as error:
            msg = "Failed to initiate srp authentication."
            raise PyiCloudFailedLoginException(msg, error) from error

        body = response.json()
        salt: bytes = base64.b64decode(body["salt"])
        b: bytes = base64.b64decode(body["b"])
        c = body["c"]
        iterations: int = body["iteration"]
        key_length: int = 32
        srp_password.set_encrypt_info(salt, iterations, key_length)
        m1 = usr.process_challenge(salt, b)
        m2 = usr.H_AMK
        if m1 and m2:
            data = {
                ACCOUNT_NAME: uname,
                "c": c,
                "m1": b64_encode(m1),
                "m2": b64_encode(m2),
                "rememberMe": True,
                "trustTokens": [],
            }
        if self.session.data.get("trust_token"):
            data["trustTokens"] = [self.session.data.get("trust_token")]

        try:
            self.session.post(
                f"{self.auth_endpoint}/signin/complete",
                params={
                    "isRememberMeEnabled": "true",
                },
                json=data,
                headers=headers,
            )
        except PyiCloud2FARequiredException:
            LOGGER.debug("2FA required to complete authentication.")
        except PyiCloudAPIResponseException as error:
            msg = "Invalid email/password combination."
            raise PyiCloudFailedLoginException(msg) from error

    def _authenticate_with_token(self) -> None:
        """Authenticate using session token."""
        if not self.session.data.get("session_token"):
            raise PyiCloudFailedLoginException("No session token available")

        try:
            data: dict[str, Any] = {
                "accountCountryCode": self.session.data.get("account_country"),
                "dsWebAuthToken": self.session.data.get("session_token"),
                "extended_login": True,
                "trustToken": self.session.data.get("trust_token", ""),
            }

            resp: Response = self.session.post(
                f"{self._setup_endpoint}/accountLogin", json=data
            )
            resp.raise_for_status()

            self.data = resp.json()
            if not self.is_trusted_session:
                raise PyiCloud2FARequiredException(self.account_name, resp)
        except (PyiCloudAPIResponseException, HTTPError) as error:
            msg = "Invalid authentication token."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _authenticate_with_credentials_service(self, service: Optional[str]) -> None:
        """Authenticate to a specific service using credentials."""
        data: dict[str, Any] = {
            "appName": service,
            "apple_id": self.account_name,
            "password": self.password,
        }

        try:
            self.session.post(f"{self._setup_endpoint}/accountLogin", json=data)

            self.data = self._validate_token()
        except PyiCloudAPIResponseException as error:
            msg = "Invalid email/password combination."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _validate_token(self) -> Any:
        """Checks if the current access token is still valid."""
        LOGGER.debug("Checking session token validity")
        if not self.session.cookies.get("X-APPLE-WEBAUTH-TOKEN"):
            raise PyiCloudAPIResponseException(
                "Missing X-APPLE-WEBAUTH-TOKEN cookie", None
            )
        try:
            req: Response = self.session.post(
                f"{self._setup_endpoint}/validate", data="null"
            )
            LOGGER.debug("Session token is still valid")
            return req.json()
        except PyiCloudAPIResponseException:
            LOGGER.debug("Invalid authentication token")
            raise

    def _get_auth_headers(
        self, overrides: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        headers: dict[str, Any] = {
            "Accept": f"{CONTENT_TYPE_JSON}, text/javascript",
            "Content-Type": CONTENT_TYPE_JSON,
            "X-Apple-OAuth-Client-Id": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": "https://www.icloud.com",
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": self._client_id,
            "X-Apple-Widget-Key": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
        }

        if self.session.data.get("scnt"):
            headers["scnt"] = self.session.data["scnt"]

        if self.session.data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session.data["session_id"]

        if overrides:
            headers.update(overrides)

        return headers

    @property
    def requires_2sa(self) -> bool:
        """Returns True if two-step authentication is required."""
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) >= 1 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def requires_2fa(self) -> bool:
        """Returns True if two-factor authentication is required."""
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) == 2 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def is_trusted_session(self) -> bool:
        """Returns True if the session is trusted."""
        return self.data.get("hsaTrustedBrowser", False)

    @property
    def trusted_devices(self) -> list[dict[str, Any]]:
        """Returns devices trusted for two-step authentication."""
        request: Response = self.session.get(
            f"{self._setup_endpoint}/listDevices", params=self.params
        )
        return request.json().get("devices")

    def send_verification_code(self, device: dict[str, Any]) -> bool:
        """Requests that a verification code is sent to the given device."""
        request: Response = self.session.post(
            f"{self._setup_endpoint}/sendVerificationCode",
            params=self.params,
            json=device,
        )
        return request.json().get("success", False)

    def validate_verification_code(self, device: dict[str, Any], code: str) -> bool:
        """Verifies a verification code received on a trusted device."""
        device.update({"verificationCode": code, "trustBrowser": True})

        try:
            self.session.post(
                f"{self._setup_endpoint}/validateVerificationCode",
                params=self.params,
                json=device,
            )
        except PyiCloudAPIResponseException as error:
            if error.code == -21669:
                # Wrong verification code
                return False
            raise

        self.trust_session()

        return not self.requires_2sa

    def _get_webauthn_options(self) -> Dict:
        """Retrieve WebAuthn request options (PublicKeyCredentialRequestOptions) for assertion."""
        headers = self._get_auth_headers({"Accept": CONTENT_TYPE_JSON})

        return self.session.get(self.auth_endpoint, headers=headers).json()

    @property
    def security_key_names(self) -> Optional[List[str]]:
        """Security key names which can be used for the WebAuthn assertion."""
        return self._get_webauthn_options().get("keyNames")

    def _submit_webauthn_assertion_response(self, data: Dict) -> None:
        """Submit the WebAuthn assertion response for authentication."""
        headers = self._get_auth_headers({"Accept": CONTENT_TYPE_JSON})

        self.session.post(
            f"{self.auth_endpoint}/verify/security/key", json=data, headers=headers
        )

    @property
    def fido2_devices(self) -> List[CtapHidDevice]:
        """List the available FIDO2 devices."""
        return list(CtapHidDevice.list_devices())

    def confirm_security_key(self, device: Optional[CtapHidDevice] = None) -> None:
        """Conduct the WebAuthn assertion ceremony with user's FIDO2 device."""
        options = self._get_webauthn_options()
        challenge = options["fsaChallenge"]["challenge"]
        allowed_credentials = options["fsaChallenge"]["keyHandles"]
        rp_id = options["fsaChallenge"]["rpId"]

        if not device:
            devices: List[CtapHidDevice] = list(CtapHidDevice.list_devices())

            if not devices:
                raise RuntimeError("No FIDO2 devices found")

            device = devices[0]

        client = Fido2Client(
            device,
            client_data_collector=DefaultClientDataCollector("https://apple.com"),
        )
        credentials: List[PublicKeyCredentialDescriptor] = [
            PublicKeyCredentialDescriptor(
                id=b64url_decode(cred_id), type=PublicKeyCredentialType("public-key")
            )
            for cred_id in allowed_credentials
        ]
        assertion_options = PublicKeyCredentialRequestOptions(
            challenge=b64url_decode(challenge),
            rp_id=rp_id,
            allow_credentials=credentials,
            user_verification=UserVerificationRequirement("discouraged"),
        )
        result: AuthenticationResponse = client.get_assertion(
            assertion_options
        ).get_response(0)

        self._submit_webauthn_assertion_response(
            {
                "challenge": challenge,
                "clientData": b64_encode(result.response.client_data),
                "signatureData": b64_encode(result.response.signature),
                "authenticatorData": b64_encode(result.response.authenticator_data),
                "userHandle": b64_encode(result.response.user_handle)
                if result.response.user_handle
                else None,
                "credentialID": b64_encode(result.raw_id),
                "rpId": rp_id,
            }
        )

        self.trust_session()

    def _check_pcs_consent(self) -> dict[str, Any]:
        """Check if the user has consented to PCS access."""
        LOGGER.debug("Querying web access state")
        resp = self.session.post(
            f"{self._setup_endpoint}/requestWebAccessState", params=self.params
        ).json()

        return resp

    def _send_pcs_request(
        self, app_name: str, derived_from_user_action: bool
    ) -> dict[str, Any]:
        """Send a request to the PCS endpoint to check the status of PCS access."""
        LOGGER.debug("Querying PCS status")

        return self.session.post(
            f"{self._setup_endpoint}/requestPCS",
            json={
                "appName": app_name,
                "derivedFromUserAction": derived_from_user_action,
            },
            params=self.params,
        ).json()

    def _request_pcs_for_service(self, app_name: str) -> None:
        """Request PCS access for a specific service."""
        _check_pcs_resp: dict[str, Any] = self._check_pcs_consent()

        if not _check_pcs_resp.get("isICDRSDisabled", False):
            LOGGER.warning("ICDRS is not disabled")
            return

        if not _check_pcs_resp.get("isDeviceConsentedForPCS", True):
            LOGGER.debug("Requesting PCS consent")

            resp = self.session.post(
                f"{self._setup_endpoint}/enableDeviceConsentForPCS", params=self.params
            ).json()

            if not resp.get("isDeviceConsentNotificationSent"):
                raise PyiCloudAPIResponseException("Unable to request PCS access!")

        LOGGER.debug("Waiting for PCS consent")
        for _ in range(PCS_MAX_RETRIES):
            if _check_pcs_resp.get("isDeviceConsentedForPCS", True):
                LOGGER.debug("PCS consent granted")
                break

            LOGGER.debug("PCS consent not granted yet, waiting...")
            time.sleep(PCS_SLEEP_TIME)
            _check_pcs_resp = self._check_pcs_consent()

        for attempt in range(PCS_MAX_RETRIES):
            resp: dict[str, Any] = self._send_pcs_request(
                app_name,
                derived_from_user_action=attempt == 0,
            )

            if resp["status"] == "success":
                LOGGER.debug("PCS access was granted")
                return

            if resp["message"] in (
                "Requested the device to upload cookies.",
                "Cookies not available yet on server.",
            ):
                LOGGER.debug("PCS access couldn't be obtained: %s", resp["message"])
                time.sleep(PCS_SLEEP_TIME)
            else:
                LOGGER.error("Unknown PCS state: %s", resp["message"])
                raise PyiCloudAPIResponseException("Unable to request PCS access!")

    def validate_2fa_code(self, code: str) -> bool:
        """Verifies a verification code received via Apple's 2FA system (HSA2)."""
        data: dict[str, Any] = {"securityCode": {"code": code}}
        headers: dict[str, Any] = self._get_auth_headers({"Accept": CONTENT_TYPE_JSON})

        try:
            self.session.post(
                f"{self.auth_endpoint}/verify/trusteddevice/securitycode",
                json=data,
                headers=headers,
            )
        except PyiCloudAPIResponseException:
            # Wrong verification code
            LOGGER.error("Code verification failed.")
            return False

        LOGGER.debug("Code verification successful.")

        self.trust_session()
        return not self.requires_2sa

    def trust_session(self) -> bool:
        """Request session trust to avoid user log in going forward."""
        headers: dict[str, Any] = self._get_auth_headers()

        try:
            self.session.get(
                f"{self.auth_endpoint}/2sv/trust",
                headers=headers,
            )
            self._authenticate_with_token()
            return True
        except (PyiCloudAPIResponseException, PyiCloud2FARequiredException):
            LOGGER.error("Session trust failed.")
            return False

    def get_webservice_url(self, ws_key: str) -> str:
        """Get webservice URL, raise an exception if not exists."""
        if self._webservices is None or self._webservices.get(ws_key) is None:
            raise PyiCloudServiceNotActivatedException(
                f"Webservice not available: {ws_key}"
            )

        return self._webservices[ws_key]["url"]

    @property
    def devices(self) -> FindMyiPhoneServiceManager:
        """Returns all devices."""
        if not self._devices:
            try:
                service_root: str = self.get_webservice_url("findme")
                self._devices = FindMyiPhoneServiceManager(
                    service_root=service_root,
                    token_endpoint=self._setup_endpoint,
                    session=self.session,
                    params=self.params,
                    with_family=self._with_family,
                )
            except PyiCloudServiceNotActivatedException as error:
                raise PyiCloudServiceUnavailable(
                    "Find My iPhone service not available"
                ) from error
        return self._devices

    @property
    def hidemyemail(self) -> HideMyEmailService:
        """Gets the 'HME' service."""
        if not self._hidemyemail:
            service_root: str = self.get_webservice_url("premiummailsettings")
            try:
                self._hidemyemail = HideMyEmailService(
                    service_root=service_root,
                    session=self.session,
                    params=self.params,
                )
            except PyiCloudAPIResponseException as error:
                raise PyiCloudServiceUnavailable(
                    "Hide My Email service not available"
                ) from error
        return self._hidemyemail

    @property
    def iphone(self) -> AppleDevice:
        """Returns the iPhone."""
        return self.devices[0]

    @property
    def account(self) -> AccountService:
        """Gets the 'Account' service."""
        if not self._account:
            service_root: str = self.get_webservice_url("account")
            try:
                self._account = AccountService(
                    service_root=service_root,
                    session=self.session,
                    china_mainland=self._is_china_mainland,
                    params=self.params,
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Account service not available"
                ) from error
        return self._account

    @property
    def files(self) -> UbiquityService:
        """Gets the 'File' service."""
        if not self._files:
            service_root: str = self.get_webservice_url("ubiquity")
            try:
                self._files = UbiquityService(
                    service_root=service_root,
                    session=self.session,
                    params=self.params,
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Files service not available"
                ) from error
        return self._files

    @property
    def photos(self) -> PhotosService:
        """Gets the 'Photo' service."""
        self._request_pcs_for_service("photos")

        if not self._photos:
            service_root: str = self.get_webservice_url("ckdatabasews")
            upload_url: str = self.get_webservice_url("uploadimagews")
            shared_streams_url: str = self.get_webservice_url("sharedstreams")
            self.params["dsid"] = self.data["dsInfo"]["dsid"]

            try:
                self._photos = PhotosService(
                    service_root=service_root,
                    session=self.session,
                    params=self.params,
                    upload_url=upload_url,
                    shared_streams_url=shared_streams_url,
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Photos service not available"
                ) from error
        return self._photos

    @property
    def calendar(self) -> CalendarService:
        """Gets the 'Calendar' service."""
        if not self._calendar:
            service_root: str = self.get_webservice_url("calendar")
            try:
                self._calendar = CalendarService(
                    service_root=service_root, session=self.session, params=self.params
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Calendar service not available"
                ) from error
        return self._calendar

    @property
    def contacts(self) -> ContactsService:
        """Gets the 'Contacts' service."""
        if not self._contacts:
            service_root: str = self.get_webservice_url("contacts")
            try:
                self._contacts = ContactsService(
                    service_root=service_root, session=self.session, params=self.params
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Contacts service not available"
                ) from error
        return self._contacts

    @property
    def reminders(self) -> RemindersService:
        """Gets the 'Reminders' service."""
        if not self._reminders:
            service_root: str = self.get_webservice_url("reminders")
            try:
                self._reminders = RemindersService(
                    service_root=service_root, session=self.session, params=self.params
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Reminders service not available"
                ) from error
        return self._reminders

    @property
    def drive(self) -> DriveService:
        """Gets the 'Drive' service."""
        self._request_pcs_for_service("iclouddrive")

        if not self._drive:
            try:
                self._drive = DriveService(
                    service_root=self.get_webservice_url("drivews"),
                    document_root=self.get_webservice_url("docws"),
                    session=self.session,
                    params=self.params,
                )
            except (PyiCloudAPIResponseException,) as error:
                raise PyiCloudServiceUnavailable(
                    "Drive service not available"
                ) from error
        return self._drive

    @property
    def account_name(self) -> str:
        """Retrieves the account name associated with the Apple ID."""

        return self._apple_id

    @property
    def password(self) -> str:
        """Retrieves the password associated with the Apple ID."""
        if self._password is None:
            raise PyiCloudPasswordException()
        self._password_filter = PyiCloudPasswordFilter(self._password)
        LOGGER.addFilter(self._password_filter)
        return self._password

    @property
    def password_filter(self) -> Optional[PyiCloudPasswordFilter]:
        """Retrieves the password filter"""
        return self._password_filter if self._password_filter else None

    def __str__(self) -> str:
        return f"iCloud API: {self.account_name}"

    def __repr__(self) -> str:
        return f"<{self}>"
