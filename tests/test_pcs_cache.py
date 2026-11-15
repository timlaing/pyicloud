"""PCS query caching stays local to the account's base service."""

# pylint: disable=protected-access

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from pyicloud import PyiCloudService
from pyicloud.base import PCS_GRANT_TTL
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
)

APPS = ("photos", "reminders", "notes3", "iclouddrive")


@pytest.fixture(name="pcs_service")
def pcs_service_fixture(pyicloud_service: PyiCloudService) -> Iterator[PyiCloudService]:
    """Mock just the PCS query endpoints, never the caching method itself."""
    with (
        patch.object(
            pyicloud_service,
            "_check_pcs_consent",
            return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
        ),
        patch.object(
            pyicloud_service, "_send_pcs_request", return_value={"status": "success"}
        ),
    ):
        yield pyicloud_service


def test_successful_queries_are_cached_separately(pcs_service: PyiCloudService) -> None:
    """Repeated access skips queries without suppressing other services' grants."""
    with patch("pyicloud.base.time.monotonic", return_value=100.0):
        for app in APPS:
            pcs_service._request_pcs_for_service(app)
            pcs_service._request_pcs_for_service(app)
    assert pcs_service._pcs_granted == dict.fromkeys(APPS, 100.0)
    assert isinstance(pcs_service._check_pcs_consent, MagicMock)
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._check_pcs_consent.call_count == len(APPS)
    assert pcs_service._send_pcs_request.call_count == len(APPS)


@pytest.mark.parametrize("property_name", ["photos", "reminders", "notes", "drive"])
def test_cached_service_property_skips_queries(
    pcs_service: PyiCloudService, property_name: str
) -> None:
    """Normal polling reuses both the service object and its successful PCS query."""
    cached = object()
    setattr(pcs_service, f"_{property_name}", cached)
    with patch("pyicloud.base.time.monotonic", return_value=100.0):
        assert getattr(pcs_service, property_name) is cached
        assert getattr(pcs_service, property_name) is cached
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._send_pcs_request.call_count == 1


@pytest.mark.parametrize(
    "elapsed", [0, PCS_GRANT_TTL - 1, PCS_GRANT_TTL, PCS_GRANT_TTL + 1]
)
def test_six_hour_expiry(pcs_service: PyiCloudService, elapsed: int) -> None:
    """Refresh at the exact TTL boundary, including when the timestamp is zero."""
    pcs_service._pcs_granted["reminders"] = 0.0
    with patch("pyicloud.base.time.monotonic", return_value=float(elapsed)):
        pcs_service._request_pcs_for_service("reminders")
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._send_pcs_request.call_count == int(elapsed >= PCS_GRANT_TTL)
    assert pcs_service._pcs_granted["reminders"] == (
        float(elapsed) if elapsed >= PCS_GRANT_TTL else 0.0
    )


@pytest.mark.parametrize("query", ["_check_pcs_consent", "_send_pcs_request"])
@pytest.mark.parametrize("expired", [False, True])
def test_query_failures_are_not_cached(
    pcs_service: PyiCloudService, query: str, expired: bool
) -> None:
    """Failed initial and expired queries can be retried on the next access."""
    if expired:
        pcs_service._pcs_granted["photos"] = 0.0
    with (
        patch("pyicloud.base.time.monotonic", return_value=float(PCS_GRANT_TTL)),
        patch.object(
            pcs_service, query, side_effect=PyiCloudAPIResponseException("PCS failed")
        ),
        pytest.raises(PyiCloudAPIResponseException, match="PCS failed"),
    ):
        pcs_service._request_pcs_for_service("photos")
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("photos")
    assert "photos" in pcs_service._pcs_granted


def test_no_pcs_needed_result_is_cached_until_expiry(
    pcs_service: PyiCloudService,
) -> None:
    """Skip repeated web-access queries too; recheck account state after the TTL."""
    with (
        patch("pyicloud.base.time.monotonic", return_value=0.0),
        patch.object(
            pcs_service, "_check_pcs_consent", return_value={"isICDRSDisabled": False}
        ) as check,
    ):
        pcs_service._request_pcs_for_service("photos")
        pcs_service._request_pcs_for_service("photos")
    check.assert_called_once_with()
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()
    with patch("pyicloud.base.time.monotonic", return_value=float(PCS_GRANT_TTL)):
        pcs_service._request_pcs_for_service("photos")
    pcs_service._send_pcs_request.assert_called_once_with(
        "photos", derived_from_user_action=True
    )


@pytest.mark.parametrize("reset", ["state", "authenticate", "token"])
def test_authentication_reset_clears_queries(
    pcs_service: PyiCloudService, reset: str
) -> None:
    """Authentication lifecycle changes discard all locally cached PCS results."""
    pcs_service._pcs_granted.update(dict.fromkeys(APPS, 100.0))
    if reset == "state":
        pcs_service._clear_authenticated_state()
    elif reset == "authenticate":
        with patch.object(pcs_service, "_try_reuse_cached_session", return_value=True):
            pcs_service.authenticate()
    else:
        with pytest.raises(PyiCloudFailedLoginException, match="No session token"):
            pcs_service._authenticate_with_token()
    assert not pcs_service._pcs_granted
