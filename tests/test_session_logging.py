"""Session request logs must not disclose signed URL credentials."""

import logging
from unittest.mock import patch

import pytest
from requests import Response
from requests.exceptions import InvalidURL

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.session import PyiCloudSession

SIGNED_URL = (
    "https://log-user:log-password@content.example.test:443/123456789/download"
    "?token=synthetic-signature&dsid=account-secret#fragment-secret"
)


@pytest.fixture(name="real_session")
def real_session_fixture(pyicloud_service_working: PyiCloudService) -> PyiCloudSession:
    """Use the production request implementation without reading persisted files."""
    with patch.object(PyiCloudSession, "_load_session_data"):
        return PyiCloudSession(pyicloud_service_working, "client", "")


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("request", SIGNED_URL),
        ("request", SIGNED_URL.encode()),
        ("request_raw", SIGNED_URL),
    ],
)
def test_request_logs_redact_url_without_changing_transport(
    real_session: PyiCloudSession,
    caplog: pytest.LogCaptureFixture,
    method: str,
    url: str | bytes,
) -> None:
    """Both public request paths log routing context but send the original URL."""
    session = real_session
    response = Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/plain"
    params = {"clientId": "client-secret"}
    with (
        patch("requests.Session.request", return_value=response) as transport,
        patch.object(session, "_save_session_data"),
        caplog.at_level(logging.DEBUG, logger="pyicloud.session"),
    ):
        assert getattr(session, method)("GET", url, params=params) is response

    messages = [r.getMessage() for r in caplog.records if r.name == "pyicloud.session"]
    assert "GET content.example.test:443/<id>/download" in messages
    for secret in (
        "log-user",
        "log-password",
        "123456789",
        "synthetic-signature",
        "account-secret",
        "fragment-secret",
        "client-secret",
    ):
        assert secret not in "\n".join(messages)
    assert transport.call_args.kwargs["url"] == url
    assert transport.call_args.kwargs["params"] is params


@pytest.mark.parametrize("method", ["request", "request_raw"])
def test_malformed_url_logging_preserves_transport_error(
    real_session: PyiCloudSession,
    caplog: pytest.LogCaptureFixture,
    method: str,
) -> None:
    """Invalid URLs reach the transport and retain its public exception mapping."""
    session = real_session
    url = "https://[invalid-host?token=synthetic-signature"
    request = getattr(session, method)
    with (
        patch(
            "requests.Session.request", side_effect=InvalidURL("Invalid URL")
        ) as transport,
        caplog.at_level(logging.DEBUG, logger="pyicloud.session"),
        pytest.raises(PyiCloudAPIResponseException, match="Request failed to iCloud"),
    ):
        request("GET", url)
    assert "GET <invalid-url>" in caplog.messages
    assert "synthetic-signature" not in caplog.text
    assert transport.call_args.kwargs["url"] == url
