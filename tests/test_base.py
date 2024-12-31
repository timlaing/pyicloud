import unittest
from unittest.mock import MagicMock, mock_open, patch

from pyicloud.base import PyiCloudService, PyiCloudSession
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)


class TestPyiCloudService(unittest.TestCase):
    def setUp(self):
        self.apple_id = "test@example.com"
        self.password = "password"

    @patch("builtins.open", new_callable=mock_open)
    def create_service_with_mock_authenticate(self, mock_open):
        with patch("pyicloud.base.PyiCloudService.authenticate") as mock_authenticate:
            # Mock the authenticate method during initialization
            mock_authenticate.return_value = None
            service = PyiCloudService(self.apple_id, self.password)
        return service

    @patch("pyicloud.base.PyiCloudSession")
    def test_authenticate_with_force_refresh(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.session_data = {"session_token": "valid_token"}
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {
            "apps": {"test_service": {"canLaunchWithOneFactor": True}},
            "status": "success",
        }
        service.session.post.return_value = mock_post_response  # type: ignore
        service.data = {"apps": {"test_service": {"canLaunchWithOneFactor": True}}}
        service._validate_token = MagicMock(
            return_value={
                "status": "success",
                "dsInfo": {"hsaVersion": 1},
                "webservices": "TestWebservices",
            }
        )
        service.authenticate(force_refresh=True, service="test_service")
        self.assertTrue(service._validate_token.called)

    @patch("pyicloud.base.PyiCloudSession")
    def test_authenticate_with_missing_token(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.session_data = {}
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {
            "salt": "U29tZVNhbHQ=",
            "b": "U29tZUJ5dGVz",
            "c": "TestC",
            "iteration": 1000,
            "dsInfo": {"hsaVersion": 1},
            "hsaChallengeRequired": False,
            "webservices": "TestWebservices",
        }
        service.session.post.return_value = mock_post_response  # type: ignore
        service.params = {}
        service.authenticate()

    @patch("pyicloud.base.PyiCloudSession")
    def test_validate_2fa_code(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.session_data = {"scnt": "test_scnt", "session_id": "test_session_id"}
        service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}
        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"success": True}
        mock_session.post.return_value = mock_post_response
        service.session.post = mock_session.post
        self.assertTrue(service.validate_2fa_code("123456"))

    @patch("pyicloud.base.PyiCloudSession")
    def test_validate_2fa_code_failure(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        exception = PyiCloudAPIResponseException("Invalid code")
        exception.code = -21669
        mock_session.post.side_effect = exception
        service.session.post = mock_session.post
        self.assertFalse(service.validate_2fa_code("000000"))

    @patch("pyicloud.base.PyiCloudSession")
    def test_validate_2fa_code_failure_2(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        exception = PyiCloudAPIResponseException("Invalid code")
        exception.code = -1
        mock_session.post.side_effect = exception
        service.session.post = mock_session.post
        with self.assertRaises(PyiCloudAPIResponseException):
            service.validate_2fa_code("000000")

    @patch("pyicloud.base.PyiCloudSession")
    def test_get_webservice_url_success(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service._webservices = {"test_key": {"url": "https://example.com"}}
        url = service._get_webservice_url("test_key")
        self.assertEqual(url, "https://example.com")

    @patch("pyicloud.base.PyiCloudSession")
    def test_get_webservice_url_failure(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service._webservices = {}
        with self.assertRaises(PyiCloudServiceNotActivatedException):
            service._get_webservice_url("invalid_key")

    @patch("pyicloud.base.PyiCloudSession")
    def test_trust_session_success(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        mock_session.get.return_value.status_code = 200
        service.session.get = mock_session.get
        self.assertTrue(service.trust_session())

    @patch("pyicloud.base.PyiCloudSession")
    def test_trust_session_failure(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        mock_session.get.side_effect = PyiCloudAPIResponseException("Trust failed")
        service.session.get = mock_session.get
        self.assertFalse(service.trust_session())

    @patch("pyicloud.base.PyiCloudSession")
    def test_cookiejar_path_property(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        path = service.cookiejar_path
        self.assertIsInstance(path, str)

    @patch("pyicloud.base.PyiCloudSession")
    def test_session_path_property(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        path = service.session_path
        self.assertIsInstance(path, str)

    @patch("pyicloud.base.PyiCloudSession")
    def test_requires_2sa_property(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.data = {"dsInfo": {"hsaVersion": 2}}
        self.assertTrue(service.requires_2sa)

    @patch("pyicloud.base.PyiCloudSession")
    def test_requires_2fa_property(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.data = {"dsInfo": {"hsaVersion": 2}, "hsaChallengeRequired": False}
        self.assertTrue(service.requires_2fa)

    @patch("pyicloud.base.PyiCloudSession")
    def test_is_trusted_session_property(self, mock_session):
        service = self.create_service_with_mock_authenticate()
        service.data = {"dsInfo": {"hsaVersion": 2}}
        self.assertFalse(service.is_trusted_session)


class TestPyiCloudSession(unittest.TestCase):
    def setUp(self):
        self.mock_service = MagicMock()
        self.mock_service.session_data = {
            "session_token": "valid_token"
        }  # JSON serializable
        self.session = PyiCloudSession(self.mock_service)
        self.session.cookies = MagicMock()  # Mock cookies

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_success(self, mock_request, mock_open):
        # Test the request method with a successful response.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        response = self.session.request(
            "POST", "https://example.com", data={"key": "value"}
        )
        self.assertEqual(response.json(), {"success": True})
        self.assertEqual(response.headers.get("Content-Type"), "application/json")
        mock_request.assert_called_once_with(
            "POST",
            "https://example.com",
            data={"key": "value"},
        )

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_failure(self, mock_request, mock_open):
        # Test the request method with a failure response.
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        with self.assertRaises(PyiCloudAPIResponseException):
            self.session.request("POST", "https://example.com", data={"key": "value"})

        mock_request.assert_called_once_with(
            "POST",
            "https://example.com",
            data={"key": "value"},
        )

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_with_custom_headers(self, mock_request, mock_open):
        # Test the request method with custom headers.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "header test"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        response = self.session.request(
            "GET",
            "https://example.com",
            headers={"Custom-Header": "Value"},
        )
        self.assertEqual(response.json(), {"data": "header test"})
        self.assertEqual(response.headers.get("Content-Type"), "application/json")
        mock_request.assert_called_once_with(
            "GET",
            "https://example.com",
            headers={"Custom-Header": "Value"},
        )

    @patch("builtins.open", new_callable=mock_open)
    @patch("requests.Session.request")
    def test_request_error_handling_for_response_conditions(
        self, mock_request, mock_open
    ):
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
            "GET",
            "https://fmip.example.com/path",
        )
        mock_open.assert_called()

    def test_raise_error_2sa_required(self):
        with self.assertRaises(PyiCloud2SARequiredException):
            self.session._raise_error(
                401, reason="Missing X-APPLE-WEBAUTH-TOKEN cookie"
            )

    def test_raise_error_service_not_activated(self):
        with self.assertRaises(PyiCloudServiceNotActivatedException):
            self.session._raise_error("ZONE_NOT_FOUND", reason="ServiceNotActivated")

    def test_raise_error_access_denied(self):
        with self.assertRaises(PyiCloudAPIResponseException):
            self.session._raise_error("ACCESS_DENIED", reason="ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()
