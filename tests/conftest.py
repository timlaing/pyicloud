"""Pytest configuration file for the pyicloud package."""

import os
import secrets
from unittest.mock import mock_open, patch

import pytest

from pyicloud.base import PyiCloudService
from tests import PyiCloudSessionMock
from tests.const_login import LOGIN_WORKING


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

    with patch("builtins.open", my_open) as open_mock:
        yield open_mock


@pytest.fixture
def pyicloud_service():
    """Create a PyiCloudService instance with mocked authenticate method."""
    with (
        patch("pyicloud.base.PyiCloudService.authenticate") as mock_authenticate,
        patch("builtins.open", new_callable=mock_open),
    ):
        # Mock the authenticate method during initialization
        mock_authenticate.return_value = None
        service = PyiCloudService("test@example.com", secrets.token_hex(32))
        return service


@pytest.fixture
def pyicloud_service_working(pyicloud_service: PyiCloudService):
    """Set the service to a working state."""
    pyicloud_service.data = LOGIN_WORKING
    pyicloud_service._webservices = LOGIN_WORKING["webservices"]
    pyicloud_service.session = PyiCloudSessionMock(pyicloud_service)
    yield pyicloud_service


@pytest.fixture
def mock_session(pyicloud_service_working: PyiCloudService):
    """Mock the PyiCloudSession class."""
    yield pyicloud_service_working.session
