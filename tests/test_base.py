"""
Test the PyiCloudService and PyiCloudSession classes."""

import unittest
from unittest.mock import MagicMock, mock_open, patch

import pytest
from requests import Response

from pyicloud.base import PyiCloudService, PyiCloudSession
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)


def test_authenticate_with_force_refresh(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with force_refresh=True."""
    with (
        patch("pyicloud.base.PyiCloudSession.post") as mock_post_response,
        patch("pyicloud.base.PyiCloudService._validate_token") as validate_token,
    ):
        pyicloud_service.session_data = {"session_token": "valid_token"}
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
        pyicloud_service._validate_token = validate_token  # pylint: disable=protected-access
        pyicloud_service.authenticate(force_refresh=True, service="test_service")
        mock_post_response.assert_called_once()
        validate_token.assert_called_once()


def test_authenticate_with_missing_token(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with missing session_token."""
    with patch("pyicloud.base.PyiCloudSession.post") as mock_post_response:
        mock_post_response.json.return_value = {
            "salt": "U29tZVNhbHQ=",
            "b": "U29tZUJ5dGVz",
            "c": "TestC",
            "iteration": 1000,
            "dsInfo": {"hsaVersion": 1},
            "hsaChallengeRequired": False,
            "webservices": "TestWebservices",
        }
        pyicloud_service.session.post = mock_post_response
        pyicloud_service.session_data = {}
        pyicloud_service.params = {}
        pyicloud_service.authenticate()
        mock_post_response.assert_called_once()


def test_validate_2fa_code(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with a valid code."""
    pyicloud_service.session_data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service.session = mock_session

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


def test_validate_2fa_code_failure_2(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with a different error code."""
    exception = PyiCloudAPIResponseException("Invalid code")
    exception.code = -1
    with (
        patch("pyicloud.base.PyiCloudSession") as mock_session,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        mock_session.post.side_effect = exception
        pyicloud_service.session = mock_session
        pyicloud_service.validate_2fa_code("000000")


def test_get_webservice_url_success(pyicloud_service: PyiCloudService) -> None:
    """Test the _get_webservice_url method with a valid key."""
    pyicloud_service._webservices = {"test_key": {"url": "https://example.com"}}  # pylint: disable=protected-access
    url: str = pyicloud_service._get_webservice_url("test_key")  # pylint: disable=protected-access
    assert url == "https://example.com"


def test_get_webservice_url_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the _get_webservice_url method with an invalid key."""
    pyicloud_service._webservices = {}  # pylint: disable=protected-access
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_service._get_webservice_url("invalid_key")  # pylint: disable=protected-access


def test_trust_session_success(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a successful response."""
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service.session = mock_session
        assert pyicloud_service.trust_session()


def test_trust_session_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a failed response."""
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service.session = mock_session
        mock_session.get.side_effect = PyiCloudAPIResponseException("Trust failed")
        assert not pyicloud_service.trust_session()


def test_cookiejar_path_property(pyicloud_service: PyiCloudService) -> None:
    """Test the cookiejar_path property."""
    path: str = pyicloud_service.cookiejar_path
    assert isinstance(path, str)


def test_session_path_property(pyicloud_service: PyiCloudService) -> None:
    """Test the session_path property."""
    path: str = pyicloud_service.session_path
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


class TestPyiCloudSession(unittest.TestCase):
    """
    Test the PyiCloudSession class.
    """

    @patch("os.path.exists", return_value=False)
    def setUp(self, mock_exists) -> None:
        self.mock_service = MagicMock()
        self.mock_service.session_data = {
            "session_token": "valid_token"
        }  # JSON serializable
        self.session = PyiCloudSession(self.mock_service)
        self.cookies = MagicMock()
        self.session._lwp_cookies = self.cookies  # Mock cookies

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_success(self, mock_request, mock_open) -> None:
        # Test the request method with a successful response.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        response: Response = self.session.request(
            "POST", "https://example.com", data={"key": "value"}
        )
        self.assertEqual(response.json(), {"success": True})
        self.assertEqual(response.headers.get("Content-Type"), "application/json")
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
        self.cookies.save.assert_called_once()

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_failure(self, mock_request, mock_open) -> None:
        # Test the request method with a failure response.
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        with self.assertRaises(PyiCloudAPIResponseException):
            self.session.request("POST", "https://example.com", data={"key": "value"})

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
        self.cookies.save.assert_called_once()

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_with_custom_headers(self, mock_request, mock_open) -> None:
        # Test the request method with custom headers.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "header test"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        response: Response = self.session.request(
            "GET",
            "https://example.com",
            headers={"Custom-Header": "Value"},
        )
        self.assertEqual(response.json(), {"data": "header test"})
        self.assertEqual(response.headers.get("Content-Type"), "application/json")
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
        self.cookies.save.assert_called_once()

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_error_handling_for_response_conditions(
        self, mock_request, mock_open
    ) -> None:
        # Mock the _get_webservice_url to return a valid fmip_url.
        self.mock_service._get_webservice_url.return_value = "https://fmip.example.com"

        # Mock the response with conditions that cause an error.
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.json.return_value = {"error": "Server Error"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        # Use the mocked fmip_url in the request.
        with self.assertRaises(PyiCloudAPIResponseException):
            self.session.request("GET", "https://fmip.example.com/path")

        mock_request.assert_called_with(
            method="GET",
            url="https://fmip.example.com/path",
            data=None,
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
        mock_open.assert_called()
        assert self.cookies.save.call_count == 3  # number of retries

    def test_raise_error_2sa_required(self) -> None:
        with self.assertRaises(PyiCloud2SARequiredException):
            self.session._raise_error(
                401, reason="Missing X-APPLE-WEBAUTH-TOKEN cookie"
            )

    def test_raise_error_service_not_activated(self) -> None:
        with self.assertRaises(PyiCloudServiceNotActivatedException):
            self.session._raise_error("ZONE_NOT_FOUND", reason="ServiceNotActivated")

    def test_raise_error_access_denied(self) -> None:
        with self.assertRaises(PyiCloudAPIResponseException):
            self.session._raise_error("ACCESS_DENIED", reason="ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()
