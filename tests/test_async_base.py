"""Tests for async PyiCloudService."""

import pytest
from unittest.mock import MagicMock, Mock, patch

from pyicloud.async_base import AsyncPyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException


@pytest.mark.asyncio
async def test_async_pyicloud_service_create():
    """Test creating an AsyncPyiCloudService instance."""
    with patch("pyicloud.async_base.AsyncPyiCloudSession") as mock_session_class:
        # Use regular Mock for session since we're patching its methods
        mock_session = MagicMock()
        mock_session.data = {"session_token": "test_token"}
        mock_session.cookies = MagicMock()
        mock_session.cookies.get = MagicMock(return_value="test_cookie")
        
        # Make close async
        async def mock_close():
            pass
        mock_session.close = mock_close
        
        mock_session_class.return_value = mock_session
        
        # Mock the post method to return a successful response
        # Use Mock instead of AsyncMock for response since httpx.Response.json() is sync
        mock_response = Mock()
        mock_response.json.return_value = {
            "dsInfo": {"dsid": "test_dsid"},
            "webservices": {},
            "hsaTrustedBrowser": True,
        }
        mock_response.raise_for_status = MagicMock()
        
        # Make post async
        async def mock_post(*args, **kwargs):
            return mock_response
        
        mock_session.post = mock_post
        
        api = await AsyncPyiCloudService.create(
            "test@example.com", "password123"
        )
        
        assert api.account_name == "test@example.com"
        assert isinstance(api, AsyncPyiCloudService)
        
        await api.close()


@pytest.mark.asyncio
async def test_async_pyicloud_service_context_manager():
    """Test using AsyncPyiCloudService as a context manager."""
    with patch("pyicloud.async_base.AsyncPyiCloudSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.data = {"session_token": "test_token"}
        mock_session.cookies = MagicMock()
        mock_session.cookies.get = MagicMock(return_value="test_cookie")
        
        async def mock_close():
            pass
        mock_session.close = mock_close
        
        mock_session_class.return_value = mock_session
        
        # Mock the post method
        mock_response = Mock()
        mock_response.json.return_value = {
            "dsInfo": {"dsid": "test_dsid"},
            "webservices": {},
            "hsaTrustedBrowser": True,
        }
        mock_response.raise_for_status = MagicMock()
        
        async def mock_post(*args, **kwargs):
            return mock_response
        
        mock_session.post = mock_post
        
        async with await AsyncPyiCloudService.create(
            "test@example.com", "password123"
        ) as api:
            assert api.account_name == "test@example.com"


@pytest.mark.asyncio
async def test_async_pyicloud_service_authenticate_with_token():
    """Test authentication with existing token."""
    with patch("pyicloud.async_base.AsyncPyiCloudSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.data = {
            "session_token": "existing_token",
            "account_country": "US",
            "trust_token": "trust123",
        }
        mock_session.cookies = MagicMock()
        mock_session.cookies.get = MagicMock(return_value="test_cookie")
        
        async def mock_close():
            pass
        mock_session.close = mock_close
        
        mock_session_class.return_value = mock_session
        
        # Mock successful responses
        mock_response = Mock()
        mock_response.json.return_value = {
            "dsInfo": {"dsid": "test_dsid"},
            "webservices": {"test": {"url": "http://test.com"}},
            "hsaTrustedBrowser": True,
        }
        mock_response.raise_for_status = MagicMock()
        
        async def mock_post(*args, **kwargs):
            return mock_response
        
        mock_session.post = mock_post
        
        api = await AsyncPyiCloudService.create(
            "test@example.com", "password123"
        )
        
        assert api.is_trusted_session is True
        assert api.get_webservice_url("test") == "http://test.com"
        
        await api.close()


@pytest.mark.asyncio
async def test_async_pyicloud_requires_2fa():
    """Test that requires_2fa property works."""
    with patch("pyicloud.async_base.AsyncPyiCloudSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.data = {"session_token": "test_token"}
        mock_session.cookies = MagicMock()
        mock_session.cookies.get = MagicMock(return_value="test_cookie")
        
        async def mock_close():
            pass
        mock_session.close = mock_close
        
        mock_session_class.return_value = mock_session
        
        # Mock response for 2FA required
        mock_response = Mock()
        mock_response.json.return_value = {
            "dsInfo": {"dsid": "test_dsid", "hsaVersion": 2},
            "webservices": {},
            "hsaTrustedBrowser": False,
        }
        mock_response.raise_for_status = MagicMock()
        
        async def mock_post(*args, **kwargs):
            return mock_response
        
        mock_session.post = mock_post
        
        api = await AsyncPyiCloudService.create(
            "test@example.com", "password123"
        )
        
        assert api.requires_2fa is True
        
        await api.close()


@pytest.mark.asyncio
async def test_async_str_and_repr():
    """Test string representations."""
    with patch("pyicloud.async_base.AsyncPyiCloudSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.data = {"session_token": "test_token"}
        mock_session.cookies = MagicMock()
        mock_session.cookies.get = MagicMock(return_value="test_cookie")
        
        async def mock_close():
            pass
        mock_session.close = mock_close
        
        mock_session_class.return_value = mock_session
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "dsInfo": {"dsid": "test_dsid"},
            "webservices": {},
            "hsaTrustedBrowser": True,
        }
        mock_response.raise_for_status = MagicMock()
        
        async def mock_post(*args, **kwargs):
            return mock_response
        
        mock_session.post = mock_post
        
        api = await AsyncPyiCloudService.create(
            "test@example.com", "password123"
        )
        
        assert str(api) == "Async iCloud API: test@example.com"
        assert repr(api) == "<Async iCloud API: test@example.com>"
        
        await api.close()
