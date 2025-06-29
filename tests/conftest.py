"""Pytest configuration file for the pyicloud package."""
# pylint: disable=redefined-outer-name,protected-access

import os
import secrets
from unittest.mock import MagicMock, mock_open, patch

import pytest
from requests.cookies import RequestsCookieJar

from pyicloud.base import PyiCloudService
from pyicloud.services.contacts import ContactsService
from pyicloud.services.drive import COOKIE_APPLE_WEBAUTH_VALIDATE
from pyicloud.services.hidemyemail import HideMyEmailService
from pyicloud.session import PyiCloudSession
from tests import PyiCloudSessionMock
from tests.const_login import LOGIN_WORKING

BUILTINS_OPEN: str = "builtins.open"
EXAMPLE_DOMAIN: str = "https://example.com"


# pylint: disable=protected-access
# pylint: disable=redefined-outer-name


class FileSystemAccessError(Exception):
    """Raised when a test tries to access the file system."""


@pytest.fixture(autouse=True, scope="function")
def mock_mkdir():
    """Mock the mkdir function to prevent file system access."""
    mkdir = os.mkdir

    def my_mkdir(path, *args, **kwargs):
        if "python-test-results" not in path:
            raise FileSystemAccessError(
                f"You should not be creating directories in tests. {path}"
            )
        return mkdir(path, *args, **kwargs)

    with patch("os.mkdir", my_mkdir) as mkdir_mock:
        yield mkdir_mock


@pytest.fixture(autouse=True, scope="session")
def mock_open_fixture():
    """Mock the open function to prevent file system access."""
    builtins_open = open

    def my_open(path, *args, **kwargs):
        if "python-test-results" not in path:
            raise FileSystemAccessError(
                f"You should not be opening files in tests. {path}"
            )
        return builtins_open(path, *args, **kwargs)

    with patch(BUILTINS_OPEN, my_open) as open_mock:
        yield open_mock


@pytest.fixture
def pyicloud_service() -> PyiCloudService:
    """Create a PyiCloudService instance with mocked authenticate method."""
    with (
        patch("pyicloud.base.PyiCloudService.authenticate") as mock_authenticate,
        patch(BUILTINS_OPEN, new_callable=mock_open),
    ):
        # Mock the authenticate method during initialization
        mock_authenticate.return_value = None
        service = PyiCloudService("test@example.com", secrets.token_hex(32))
        return service


@pytest.fixture
def pyicloud_service_working(pyicloud_service: PyiCloudService) -> PyiCloudService:
    """Set the service to a working state."""
    pyicloud_service.data = LOGIN_WORKING
    pyicloud_service._webservices = LOGIN_WORKING["webservices"]
    with patch(BUILTINS_OPEN, new_callable=mock_open):
        pyicloud_service.session = PyiCloudSessionMock(
            pyicloud_service,
            "",
            cookie_directory="",
        )
        pyicloud_service.session._data = {"session_token": "valid_token"}
        check_pcs_consent = MagicMock(
            return_value={
                "isICDRSDisabled": False,
                "isDeviceConsentedForPCS": True,
            }
        )
        pyicloud_service._check_pcs_consent = check_pcs_consent

    return pyicloud_service


@pytest.fixture
def pyicloud_session(pyicloud_service_working: PyiCloudService) -> PyiCloudSession:
    """Mock the PyiCloudSession class."""
    pyicloud_service_working.session.cookies = MagicMock()
    return pyicloud_service_working.session


@pytest.fixture
def mock_session() -> MagicMock:
    """Fixture to create a mock PyiCloudSession."""
    return MagicMock(spec=PyiCloudSession)


@pytest.fixture
def contacts_service(mock_session: MagicMock) -> ContactsService:
    """Fixture to create a ContactsService instance."""
    return ContactsService(
        service_root=EXAMPLE_DOMAIN,
        session=mock_session,
        params={"test_param": "value"},
    )


@pytest.fixture
def mock_photos_service() -> MagicMock:
    """Fixture for mocking PhotosService."""
    service = MagicMock()
    service.service_endpoint = EXAMPLE_DOMAIN
    service.params = {"dsid": "12345"}
    service.session = MagicMock()
    return service


@pytest.fixture
def mock_photo_library(mock_photos_service: MagicMock) -> MagicMock:
    """Fixture for mocking PhotoLibrary."""
    library = MagicMock()
    library.service = mock_photos_service
    return library


@pytest.fixture
def hidemyemail_service(mock_session: MagicMock) -> HideMyEmailService:
    """Fixture for initializing HideMyEmailService."""
    return HideMyEmailService(EXAMPLE_DOMAIN, mock_session, {"dsid": "12345"})


@pytest.fixture
def mock_service_with_cookies(
    pyicloud_service_working: PyiCloudService,
) -> PyiCloudService:
    """Fixture to create a mock PyiCloudService with cookies."""
    jar = RequestsCookieJar()
    jar.set(COOKIE_APPLE_WEBAUTH_VALIDATE, "t=768y9u", domain="icloud.com", path="/")

    # Attach a real CookieJar so code that calls `.cookies.get()` keeps working.
    pyicloud_service_working.session.cookies = jar

    return pyicloud_service_working
