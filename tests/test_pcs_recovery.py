"""PCS grants are reused and rejected reads recover without replaying writes."""

# pylint: disable=protected-access

from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
)
from pyicloud.session import PyiCloudSession

CK_ROOT = "https://ck.example.test"
DRIVE_ROOT = "https://drive.example.test"
DOC_ROOT = "https://doc.example.test"
REMINDERS_QUERY = (
    f"{CK_ROOT}/database/1/com.apple.reminders/production/private/records/query"
)
APPS = ("photos", "reminders", "notes3", "iclouddrive")


@pytest.fixture(name="pcs_service")
def pcs_service_fixture(pyicloud_service: PyiCloudService) -> Iterator[PyiCloudService]:
    """Use a real session with only the network and persistence mocked."""
    pyicloud_service._webservices = {
        "ckdatabasews": {"url": CK_ROOT},
        "drivews": {"url": DRIVE_ROOT},
        "docws": {"url": DOC_ROOT},
    }
    with (
        patch.object(
            pyicloud_service,
            "_check_pcs_consent",
            return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
        ),
        patch.object(
            pyicloud_service, "_send_pcs_request", return_value={"status": "success"}
        ),
        patch.object(pyicloud_service.session, "_save_session_data"),
    ):
        yield pyicloud_service


def _response(status: int, url: str = REMINDERS_QUERY) -> Response:
    response = Response()
    response.status_code = status
    response.reason = "Unauthorized" if status == 401 else "Locked"
    response.url = url
    response.headers["Content-Type"] = "application/json"
    response._content = b"{}"
    cast(Any, response)._content_consumed = True
    return response


def test_successful_grants_are_cached_per_app(pcs_service: PyiCloudService) -> None:
    """Polling one app must not prompt again or suppress grants for other apps."""
    for app in APPS:
        pcs_service._request_pcs_for_service(app)
        pcs_service._request_pcs_for_service(app)
    assert pcs_service._pcs_granted == set(APPS)
    assert isinstance(pcs_service._check_pcs_consent, MagicMock)
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._check_pcs_consent.call_count == len(APPS)
    assert pcs_service._send_pcs_request.call_count == len(APPS)


@pytest.mark.parametrize("property_name", ["photos", "reminders", "notes", "drive"])
def test_cached_properties_do_not_request_pcs_again(
    pcs_service: PyiCloudService, property_name: str
) -> None:
    """Previously created service objects keep the same identity across polls."""
    cached = object()
    setattr(pcs_service, f"_{property_name}", cached)
    assert getattr(pcs_service, property_name) is cached
    assert getattr(pcs_service, property_name) is cached
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._send_pcs_request.call_count == 1


@pytest.mark.parametrize("failure", ["consent", "grant"])
def test_failed_grants_can_be_requested_again(
    pcs_service: PyiCloudService, failure: str
) -> None:
    """Neither a failed consent check nor a failed grant poisons the cache."""
    method = "_check_pcs_consent" if failure == "consent" else "_send_pcs_request"
    with (
        patch.object(
            pcs_service, method, side_effect=PyiCloudAPIResponseException("PCS failed")
        ),
        pytest.raises(PyiCloudAPIResponseException, match="PCS failed"),
    ):
        pcs_service._request_pcs_for_service("reminders")
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("reminders")
    assert pcs_service._pcs_granted == {"reminders"}


def test_non_adp_skip_is_not_cached(pcs_service: PyiCloudService) -> None:
    """A skipped grant must not hide later changes to the account's ADP state."""
    with patch.object(pcs_service, "_check_pcs_consent", return_value={}):
        pcs_service._request_pcs_for_service("photos")
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("photos")
    assert pcs_service._pcs_granted == {"photos"}


@pytest.mark.parametrize("reset", ["state", "authenticate", "token", "persistence"])
def test_session_resets_clear_grants(pcs_service: PyiCloudService, reset: str) -> None:
    """Auth and persistence resets cannot leave stale in-memory PCS grants."""
    pcs_service._pcs_granted.update(APPS)
    if reset == "state":
        pcs_service._clear_authenticated_state()
    elif reset == "authenticate":
        with patch.object(pcs_service, "_try_reuse_cached_session", return_value=True):
            pcs_service.authenticate()
    elif reset == "token":
        # Failed login also invalidates grants before attempting network I/O.
        pcs_service.session.data["session_token"] = "test-token"
        with (
            patch.object(
                pcs_service.session,
                "post",
                side_effect=PyiCloudAPIResponseException("login failed"),
            ),
            pytest.raises(PyiCloudFailedLoginException),
        ):
            pcs_service._authenticate_with_token()
    else:
        pcs_service.session.clear_persistence(remove_files=False)
    assert not pcs_service._pcs_granted


@pytest.mark.parametrize("status", [401, 423])
@pytest.mark.parametrize("json_response", [True, False])
@pytest.mark.parametrize(
    ("app", "url", "method", "options"),
    [
        ("reminders", REMINDERS_QUERY, "POST", {"json": {"query": {}}}),
        (
            "notes3",
            f"{CK_ROOT}/database/1/com.apple.notes/production/shared/records/lookup",
            "POST",
            {"json": {"records": []}},
        ),
        (
            "photos",
            f"{CK_ROOT}/database/1/com.apple.photos.cloud/"
            "production/private/zones/list",
            "POST",
            {"data": "{}"},
        ),
        (
            "iclouddrive",
            f"{DRIVE_ROOT}/retrieveItemDetailsInFolders",
            "POST",
            {"data": "[]"},
        ),
        ("iclouddrive", f"{DOC_ROOT}/ws/com.apple.CloudDocs/download/by_id", "GET", {}),
    ],
)
def test_rejected_reads_refresh_only_the_matching_app_once(
    pcs_service: PyiCloudService,
    status: int,
    json_response: bool,
    app: str,
    url: str,
    method: str,
    options: dict[str, Any],
) -> None:
    """Refresh expired cookies and resend the identical read with all options."""
    pcs_service._pcs_granted.update(APPS)
    rejected = _response(status, url)
    if not json_response:
        rejected.headers.clear()
    success = _response(200, url)
    options.update({"params": {"dsid": "test"}, "timeout": 10, "stream": True})
    with (
        patch("requests.Session.request", side_effect=[rejected, success]) as send,
        patch.object(rejected, "close") as close,
    ):
        assert pcs_service.session.request(method, url, **options) is success
    assert send.call_count == 2
    assert send.call_args_list[0] == send.call_args_list[1]
    close.assert_called_once_with()
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_called_once_with(
        app, derived_from_user_action=True
    )
    assert pcs_service._pcs_granted == set(APPS)


def test_second_rejection_propagates_and_invalidates_grant(
    pcs_service: PyiCloudService,
) -> None:
    """No retry loop; a later property access can request a new grant."""
    pcs_service._pcs_granted.add("reminders")
    with (
        patch(
            "requests.Session.request", side_effect=[_response(401), _response(423)]
        ) as send,
        pytest.raises(PyiCloudAPIResponseException) as error,
    ):
        pcs_service.session.post(REMINDERS_QUERY, json={})
    assert error.value.code == 423
    assert send.call_count == 2
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("reminders")
    assert pcs_service._pcs_granted == {"reminders"}


def test_refresh_failure_propagates_without_resending_read(
    pcs_service: PyiCloudService,
) -> None:
    """Failed renewal does not mark the service granted or replay the request."""
    pcs_service._pcs_granted.add("reminders")
    with (
        patch("requests.Session.request", return_value=_response(401)) as send,
        patch.object(
            pcs_service,
            "_send_pcs_request",
            side_effect=PyiCloudAPIResponseException("PCS refresh failed"),
        ),
        pytest.raises(PyiCloudAPIResponseException, match="PCS refresh failed"),
    ):
        pcs_service.session.post(REMINDERS_QUERY, json={})
    send.assert_called_once()
    assert not pcs_service._pcs_granted


@pytest.mark.parametrize(
    ("method", "route", "options"),
    [
        ("POST", "/records/modify", {"json": {}}),
        ("POST", "/zones/modify", {"json": {}}),
        ("DELETE", "/records/query", {}),
        ("POST", "/records/query", {"data": iter([b"{}"])}),
        ("POST", "/records/query", {"files": {"file": b"data"}}),
    ],
)
def test_writes_and_non_reusable_bodies_are_never_replayed(
    pcs_service: PyiCloudService, method: str, route: str, options: dict[str, Any]
) -> None:
    """Invalidate rejected access without silently replaying unsafe requests."""
    pcs_service._pcs_granted.add("reminders")
    url = REMINDERS_QUERY.removesuffix("/records/query") + route
    with (
        patch("requests.Session.request", return_value=_response(401, url)) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.request(method, url, **options)
    send.assert_called_once()
    assert not pcs_service._pcs_granted
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()


def test_raw_requests_remain_one_shot(pcs_service: PyiCloudService) -> None:
    """Raw callers receive the original rejection without a refresh or retry."""
    pcs_service._pcs_granted.add("reminders")
    rejected = _response(401)
    with patch("requests.Session.request", return_value=rejected) as send:
        assert pcs_service.session.request_raw("POST", REMINDERS_QUERY) is rejected
    send.assert_called_once()
    assert not pcs_service._pcs_granted
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://setup.icloud.com/setup/ws/1/requestPCS",
        f"{CK_ROOT}/database/1/com.apple.invites/production/private/records/query",
        f"{CK_ROOT}/database/1/com.apple.reminders.other/"
        "production/private/records/query",
        REMINDERS_QUERY.replace("https:", "http:"),
        REMINDERS_QUERY.replace("ck.example.test", "evil.example.test"),
        REMINDERS_QUERY.replace("ck.example.test", "ck.example.test.evil.test"),
    ],
)
def test_unrelated_endpoints_do_not_refresh(
    pcs_service: PyiCloudService, url: str
) -> None:
    """Auth failures on other services and origins cannot trigger PCS prompts."""
    pcs_service._pcs_granted.update(APPS)
    with (
        patch("requests.Session.request", return_value=_response(401, url)) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.post(url)
    send.assert_called_once()
    assert pcs_service._pcs_granted == set(APPS)


@pytest.mark.parametrize("status", [403, 409, 429, 450, 500])
def test_other_failures_do_not_refresh(
    pcs_service: PyiCloudService, status: int
) -> None:
    """Throttling, account locks and 2FA challenges retain existing handling."""
    pcs_service._pcs_granted.add("reminders")
    with patch("requests.Session.request", return_value=_response(status)) as send:
        exception = (
            PyiCloudAuthRequiredException
            if status == 450
            else PyiCloudAPIResponseException
        )
        with pytest.raises(exception):
            pcs_service.session.post(REMINDERS_QUERY)
    send.assert_called_once()
    assert pcs_service._pcs_granted == {"reminders"}


def test_missing_advertised_root_does_not_refresh(pcs_service: PyiCloudService) -> None:
    """A cached grant does not authorize arbitrary hosts if its endpoint is absent."""
    pcs_service._pcs_granted.update(APPS)
    pcs_service._webservices = None
    assert pcs_service._pcs_service_for_url(REMINDERS_QUERY) is None


def test_url_without_grant_does_not_refresh(pcs_service: PyiCloudService) -> None:
    """Ordinary unauthenticated failures do not cause automatic PCS prompts."""
    assert pcs_service._pcs_service_for_url(REMINDERS_QUERY) is None


def test_head_is_a_reusable_read() -> None:
    """HEAD requests can recover without replaying an operation or stream body."""
    assert PyiCloudSession._can_retry_pcs_read("head", REMINDERS_QUERY, {})
