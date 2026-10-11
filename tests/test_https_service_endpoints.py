"""Advertised service roots must use HTTPS before credentials can be sent."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.base import PyiCloudService
from pyicloud.exceptions import PyiCloudServiceNotActivatedException


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "ftp://example.invalid",
        "file:///fixture",
        "//example.invalid",
        "/relative",
        "https://",
        "https://[",
    ],
)
def test_insecure_or_malformed_endpoints_are_refused_without_io(url: Any) -> None:
    """Insecure or malformed endpoints are refused without io."""
    api = object.__new__(PyiCloudService)
    api._webservices = {"calendar": {"url": url}}
    session = MagicMock()
    api._session = session
    with pytest.raises(PyiCloudServiceNotActivatedException, match="HTTPS"):
        api.get_webservice_url("calendar")
    session.request.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/path",
        "https://example.invalid:8443",
        "https://p01-calendarws.icloud.com",
        "https://p01-calendarws.icloud.com.cn",
    ],
)
def test_valid_https_roots_preserve_their_exact_value(url: Any) -> None:
    """Valid https roots preserve their exact value."""
    api = object.__new__(PyiCloudService)
    api._webservices = {"calendar": {"url": url}}
    assert api.get_webservice_url("calendar") == url
