"""
Test the PyiCloudService and PyiCloudSession classes."""

# pylint: disable=protected-access

from unittest.mock import MagicMock, mock_open, patch

import pytest
from requests import Response

from pyicloud.base import PyiCloudService, PyiCloudSession, b64_encode
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
)


def test_authenticate_with_force_refresh(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with force_refresh=True."""
    with (
        patch("pyicloud.base.PyiCloudSession.post") as mock_post_response,
        patch("pyicloud.base.PyiCloudService._validate_token") as validate_token,
    ):
        pyicloud_service.session._data = {"session_token": "valid_token"}
        mock_post_response.json.return_value = {
            "apps": {"test_service": {"canLaunchWithOneFactor": True}},
            "status": "success",
        }
        pyicloud_service.data = {
            "apps": {"test_service": {"canLaunchWithOneFactor": True}}
        }
        validate_token = MagicMock(
            return_value={
                "status": "success",
                "dsInfo": {"hsaVersion": 1},
                "webservices": "TestWebservices",
            }
        )
        pyicloud_service._validate_token = validate_token
        pyicloud_service.authenticate(force_refresh=True, service="test_service")
        mock_post_response.assert_called_once()
        validate_token.assert_called_once()


def test_authenticate_with_missing_token(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with missing session_token."""
    with (
        patch("pyicloud.base.PyiCloudSession.post") as mock_post_response,
        patch.object(
            pyicloud_service,
            "_authenticate_with_token",
            side_effect=[PyiCloudFailedLoginException, None],
        ) as mock_authenticate_with_token,
    ):
        mock_post_response.return_value.json.side_effect = [
            {
                "salt": "U29tZVNhbHQ=",
                "b": "U29tZUJ5dGVz",
                "c": "TestC",
                "iteration": 1000,
                "dsInfo": {"hsaVersion": 1},
                "hsaChallengeRequired": False,
                "webservices": "TestWebservices",
            },
            None,
        ]
        pyicloud_service.session.post = mock_post_response
        pyicloud_service.session._data = {}
        pyicloud_service.params = {}
        pyicloud_service.authenticate()
        assert mock_post_response.call_count == 2
        assert mock_authenticate_with_token.call_count == 2


def test_validate_2fa_code(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with a valid code."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service.session = mock_session
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }

        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"success": True}
        mock_session.post.return_value = mock_post_response

        assert pyicloud_service.validate_2fa_code("123456")


def test_validate_2fa_code_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with an invalid code."""
    exception = PyiCloudAPIResponseException("Invalid code")
    exception.code = -21669
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        mock_session.post.side_effect = exception
        pyicloud_service.session = mock_session
        assert not pyicloud_service.validate_2fa_code("000000")


@patch("pyicloud.base.CtapHidDevice.list_devices", return_value=[MagicMock()])
@patch("pyicloud.base.Fido2Client")
def test_confirm_security_key_success(
    mock_fido2_client_cls, mock_list_devices, pyicloud_service: PyiCloudService
) -> None:
    """Test that the FIDO2 WebAuthn flow works"""
    rp_id = "example.com"
    challenge = "ZmFrZV9jaGFsbGVuZ2U"

    # Arrange
    pyicloud_service._submit_webauthn_assertion_response = MagicMock()
    pyicloud_service.trust_session = MagicMock()

    # Simulated WebAuthn options returned from backend
    pyicloud_service._get_webauthn_options = MagicMock(
        return_value={
            "fsaChallenge": {
                "challenge": challenge,  # base64url(fake_challenge)
                "keyHandles": ["a2V5MQ", "a2V5Mg"],  # base64url(fake_key_ids)
                "rpId": rp_id,
            }
        }
    )

    # Simulated FIDO2 response
    mock_response = MagicMock()
    mock_response.response = MagicMock()
    mock_response.response.client_data = b"client_data"
    mock_response.response.signature = b"signature"
    mock_response.response.authenticator_data = b"auth_data"
    mock_response.response.user_handle = b"user_handle"
    mock_response.raw_id = b"cred_id"

    mock_fido2_client = MagicMock()
    mock_fido2_client.get_assertion.return_value.get_response.return_value = (
        mock_response
    )
    mock_fido2_client_cls.return_value = mock_fido2_client

    # Act
    pyicloud_service.confirm_security_key()

    # Assert
    mock_list_devices.assert_called_once()
    mock_fido2_client.get_assertion.assert_called_once()

    # Check if data was submitted correctly
    pyicloud_service._submit_webauthn_assertion_response.assert_called_once_with(
        {
            "challenge": challenge,
            "rpId": rp_id,
            "clientData": b64_encode(mock_response.response.client_data),
            "signatureData": b64_encode(mock_response.response.signature),
            "authenticatorData": b64_encode(mock_response.response.authenticator_data),
            "userHandle": b64_encode(mock_response.response.user_handle),
            "credentialID": b64_encode(mock_response.raw_id),
        }
    )

    pyicloud_service.trust_session.assert_called_once()


def test_get_webservice_url_success(pyicloud_service: PyiCloudService) -> None:
    """Test the get_webservice_url method with a valid key."""
    pyicloud_service._webservices = {"test_key": {"url": "https://example.com"}}
    url: str = pyicloud_service.get_webservice_url("test_key")
    assert url == "https://example.com"


def test_get_webservice_url_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the get_webservice_url method with an invalid key."""
    pyicloud_service._webservices = {}
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_service.get_webservice_url("invalid_key")


def test_trust_session_success(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a successful response."""

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }
        pyicloud_service.session = mock_session
        assert pyicloud_service.trust_session()


def test_trust_session_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a failed response."""
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service.session = mock_session
        mock_session.get.side_effect = PyiCloudAPIResponseException("Trust failed")
        assert not pyicloud_service.trust_session()


def test_cookiejar_path_property(pyicloud_session: PyiCloudSession) -> None:
    """Test the cookiejar_path property."""
    path: str = pyicloud_session.cookiejar_path
    assert isinstance(path, str)


def test_session_path_property(pyicloud_session: PyiCloudSession) -> None:
    """Test the session_path property."""
    path: str = pyicloud_session.session_path
    assert isinstance(path, str)


def test_requires_2sa_property(pyicloud_service: PyiCloudService) -> None:
    """Test the requires_2sa property."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}}
    assert pyicloud_service.requires_2sa


def test_requires_2fa_property(pyicloud_service: PyiCloudService) -> None:
    """Test the requires_2fa property."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}, "hsaChallengeRequired": False}
    assert pyicloud_service.requires_2fa


def test_is_trusted_session_property(pyicloud_service: PyiCloudService) -> None:
    """Test the is_trusted_session property."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}}
    assert not pyicloud_service.is_trusted_session


def test_request_success(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with a successful response."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=""
        )

        response: Response = pyicloud_session.request(
            "POST", "https://example.com", data={"key": "value"}
        )
        assert response.json() == {"success": True}
        assert response.headers.get("Content-Type") == "application/json"
        mock_request.assert_called_once_with(
            method="POST",
            url="https://example.com",
            data={"key": "value"},
            params=None,
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
        )
        mock_save.assert_called_once()


def test_request_failure(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with a failure response."""

    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open) as open_mock,
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.ok = False
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=""
        )
        with pytest.raises(PyiCloudAPIResponseException):
            pyicloud_session.request(
                "POST", "https://example.com", data={"key": "value"}
            )

        mock_request.assert_called_once_with(
            method="POST",
            url="https://example.com",
            data={"key": "value"},
            params=None,
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
        )
        mock_save.assert_called_once()
        assert open_mock.call_count == 2


def test_request_with_custom_headers(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with custom headers."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "header test"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=""
        )

        response: Response = pyicloud_session.request(
            "GET",
            "https://example.com",
            headers={"Custom-Header": "Value"},
        )
        assert response.json() == {"data": "header test"}
        assert response.headers.get("Content-Type") == "application/json"
        mock_request.assert_called_once_with(
            method="GET",
            url="https://example.com",
            data=None,
            headers={"Custom-Header": "Value"},
            params=None,
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
        )
        mock_save.assert_called_once()


def test_request_error_handling_for_response_conditions() -> None:
    """Mock the get_webservice_url to return a valid fmip_url."""
    pyicloud_service = MagicMock(spec=PyiCloudService)
    with (
        pytest.raises(PyiCloudAPIResponseException),
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("os.path.exists", return_value=False),
        patch("http.cookiejar.LWPCookieJar.save"),
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://fmip.example.com",
        ),
    ):
        # Mock the response with conditions that cause an error.
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.json.return_value = {"error": "Server Error"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        pyicloud_session = PyiCloudSession(pyicloud_service, "", cookie_directory="")
        pyicloud_service.data = {"session_token": "valid_token"}

        # Use the mocked fmip_url in the request.
        pyicloud_session.request("GET", "https://fmip.example.com/path")


def test_raise_error_2sa_required(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with a 2SA required exception."""
    with (
        pytest.raises(PyiCloud2SARequiredException),
        patch("pyicloud.base.PyiCloudService.requires_2sa", return_value=True),
    ):
        pyicloud_session._raise_error(
            401, reason="Missing X-APPLE-WEBAUTH-TOKEN cookie"
        )


def test_raise_error_service_not_activated(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with a service not activated exception."""
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_session._raise_error("ZONE_NOT_FOUND", reason="ServiceNotActivated")


def test_raise_error_access_denied(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with an access denied exception."""
    with pytest.raises(PyiCloudAPIResponseException):
        pyicloud_session._raise_error("ACCESS_DENIED", reason="ACCESS_DENIED")


def test_request_pcs_for_service_icdrs_not_disabled(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when ICDRS is not disabled (should early return)."""
    mock_logger = MagicMock()
    pyicloud_service.session = MagicMock()
    pyicloud_service.session.post = MagicMock(
        return_value=MagicMock(json=MagicMock(return_value={"isICDRSDisabled": False}))
    )
    pyicloud_service.params = {}
    with patch("pyicloud.base.LOGGER", mock_logger):
        pyicloud_service._send_pcs_request = MagicMock()
        pyicloud_service._request_pcs_for_service("photos")
        mock_logger.warning.assert_called_once_with("ICDRS is not disabled")
        pyicloud_service._send_pcs_request.assert_not_called()


def test_request_pcs_for_service_consent_needed_and_notification_sent(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when device consent is needed and notification is sent."""
    # First call: ICDRS disabled, device not consented
    # Second call: device consented (simulate after waiting)
    consent_states = [
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
    ]

    pyicloud_service._check_pcs_consent = MagicMock(side_effect=consent_states)
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service.session.post.return_value.json.side_effect = [
        {"isDeviceConsentNotificationSent": True},
        {"status": "success", "message": "ok"},
    ]
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    pyicloud_service.session.post.assert_any_call(
        f"{pyicloud_service._setup_endpoint}/enableDeviceConsentForPCS",
        params=pyicloud_service.params,
    )
    # Should not raise


def test_request_pcs_for_service_consent_needed_and_notification_not_sent(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when device consent notification is not sent (should raise)."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": False}
    )
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service.session.post.return_value.json.return_value = {
        "isDeviceConsentNotificationSent": False
    }
    with pytest.raises(
        PyiCloudAPIResponseException, match="Unable to request PCS access!"
    ):
        pyicloud_service._request_pcs_for_service("photos")


def test_request_pcs_for_service_pcs_consent_waits(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service waits for PCS consent and then proceeds."""
    # Simulate PCS consent not granted for first 2 tries, then granted
    consent_states = [
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
    ]
    pyicloud_service._check_pcs_consent = MagicMock(side_effect=consent_states)
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service.session.post.return_value.json.return_value = {
        "isDeviceConsentNotificationSent": True
    }
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "success", "message": "ok"}
    )
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    assert pyicloud_service._send_pcs_request.called


def test_request_pcs_for_service_success_on_first_attempt(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service grants PCS access on first attempt."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "success", "message": "ok"}
    )
    pyicloud_service._request_pcs_for_service("photos")
    pyicloud_service._send_pcs_request.assert_called_once_with(
        "photos", derived_from_user_action=True
    )


def test_request_pcs_for_service_retries_on_cookie_messages(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service retries on known cookie messages and succeeds."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    responses = [
        {"status": "error", "message": "Requested the device to upload cookies."},
        {"status": "error", "message": "Cookies not available yet on server."},
        {"status": "success", "message": "ok"},
    ]
    pyicloud_service._send_pcs_request = MagicMock(side_effect=responses)
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    assert pyicloud_service._send_pcs_request.call_count == 3


def test_request_pcs_for_service_raises_on_unknown_message(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service raises on unknown PCS state message."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service.session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "error", "message": "Some unknown error"}
    )
    mock_logger = MagicMock()

    with (
        pytest.raises(
            PyiCloudAPIResponseException, match="Unable to request PCS access!"
        ),
        patch("pyicloud.base.LOGGER", mock_logger),
    ):
        pyicloud_service._request_pcs_for_service("photos")
        mock_logger.error.assert_called()
