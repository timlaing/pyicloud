"""Tests for the SSL context configuration in pyicloud.ssl_context."""

import warnings
from typing import Any

import requests
from pytest import MonkeyPatch
from urllib3.exceptions import InsecureRequestWarning

from pyicloud.ssl_context import configurable_ssl_verification


def test_ssl_verification_true(monkeypatch: MonkeyPatch) -> None:
    """Test that SSL verification is enabled by default."""
    called: dict[str, Any] = {}

    def fake_merge(self, url, proxies, stream, verify, cert) -> dict[str, Any]:  # pylint: disable=unused-argument
        called["verify"] = verify
        called["proxies"] = proxies
        return {"verify": verify, "proxies": proxies}

    monkeypatch.setattr(requests.Session, "merge_environment_settings", fake_merge)
    with configurable_ssl_verification():
        session = requests.Session()
        session.merge_environment_settings("https://example.com", {}, False, True, None)
    assert called["verify"] is True
    assert called["proxies"] == {}


def test_ssl_verification_false(monkeypatch: MonkeyPatch) -> None:
    """Test that SSL verification is disabled when verify_ssl=False."""
    called: dict[str, Any] = {}

    def fake_merge(self, url, proxies, stream, verify, cert) -> dict[str, Any]:  # pylint: disable=unused-argument
        called["verify"] = verify
        called["proxies"] = proxies
        return {"verify": verify, "proxies": proxies}

    monkeypatch.setattr(requests.Session, "merge_environment_settings", fake_merge)
    with configurable_ssl_verification(verify_ssl=False):
        session = requests.Session()
        result = session.merge_environment_settings(
            "https://example.com", {}, False, True, None
        )
    assert result["verify"] is False
    assert result["proxies"] == {}


def test_proxy_settings(monkeypatch: MonkeyPatch) -> None:
    """Test that proxy settings are applied correctly."""
    called: dict[str, Any] = {}

    def fake_merge(self, url, proxies, stream, verify, cert) -> dict[str, Any]:  # pylint: disable=unused-argument
        called["verify"] = verify
        called["proxies"] = proxies
        return {"verify": verify, "proxies": proxies}

    monkeypatch.setattr(requests.Session, "merge_environment_settings", fake_merge)
    with configurable_ssl_verification(
        http_proxy="http://proxy", https_proxy="https://proxy"
    ):
        session = requests.Session()
        result = session.merge_environment_settings(
            "https://example.com", {}, False, True, None
        )
    assert result["proxies"] == {"http": "http://proxy", "https": "https://proxy"}


def test_insecure_request_warning(monkeypatch: MonkeyPatch) -> None:
    """Test that InsecureRequestWarning is suppressed when verify_ssl=False."""
    warnings.simplefilter("always")
    monkeypatch.setattr(
        requests.Session, "merge_environment_settings", lambda *a, **kw: {}
    )
    with configurable_ssl_verification(verify_ssl=False):
        with warnings.catch_warnings(record=True) as w:
            warnings.warn("test", InsecureRequestWarning)
            # InsecureRequestWarning should be suppressed
            insecure_warnings: list[warnings.WarningMessage] = [
                warning
                for warning in w
                if issubclass(warning.category, InsecureRequestWarning)
            ]
            assert len(insecure_warnings) == 0, (
                "InsecureRequestWarning should be suppressed"
            )
