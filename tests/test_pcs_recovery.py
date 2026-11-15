"""PCS grants are reused and rejected reads recover without replaying writes."""

# pylint: disable=protected-access

from collections.abc import Iterator
import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Request, Response

from pyicloud import PyiCloudService
from pyicloud.base import PCS_GRANT_TTL
from pyicloud.common.cloudkit import CKQueryObject, CKZoneIDReq
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
)
from pyicloud.services.drive import DriveService
from pyicloud.services.notes.client import CloudKitNotesClient
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.reminders.client import CloudKitRemindersClient
from pyicloud.session import PyiCloudSession, pcs_request_options

CK_ROOT = "https://ck.example.test"
DRIVE_ROOT = "https://drive.example.test"
DOC_ROOT = "https://doc.example.test"
REMINDERS_QUERY = (
    f"{CK_ROOT}/database/1/com.apple.reminders/production/private/records/query"
)
APPS = ("photos", "reminders", "notes3", "iclouddrive")


def _grant(service: PyiCloudService, *apps: str) -> None:
    service._pcs_granted.update(dict.fromkeys(apps, time.monotonic()))


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
    assert set(pcs_service._pcs_granted) == set(APPS)
    assert isinstance(pcs_service._check_pcs_consent, MagicMock)
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._check_pcs_consent.call_count == len(APPS)
    assert pcs_service._send_pcs_request.call_count == len(APPS)


@pytest.mark.parametrize("app_name", [None, "reminders", "unknown"])
def test_owner_invalidation_does_not_prompt_or_revoke_other_grants(
    pcs_service: PyiCloudService, app_name: str | None
) -> None:
    """Shared callers can invalidate one grant or reset all without accessing state."""
    for app in APPS:
        pcs_service._request_pcs_for_service(app)
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.reset_mock()
    pcs_service.invalidate_pcs_grants(app_name)
    expected = set() if app_name is None else set(APPS) - {app_name}
    assert set(pcs_service._pcs_granted) == expected
    pcs_service._send_pcs_request.assert_not_called()


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
    assert set(pcs_service._pcs_granted) == {"reminders"}


def test_non_adp_skip_is_not_cached(pcs_service: PyiCloudService) -> None:
    """A skipped grant must not hide later changes to the account's ADP state."""
    with patch.object(pcs_service, "_check_pcs_consent", return_value={}):
        pcs_service._request_pcs_for_service("photos")
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("photos")
    assert set(pcs_service._pcs_granted) == {"photos"}


@pytest.mark.parametrize("reset", ["state", "authenticate", "token", "persistence"])
def test_session_resets_clear_grants(pcs_service: PyiCloudService, reset: str) -> None:
    """Auth and persistence resets cannot leave stale in-memory PCS grants."""
    _grant(pcs_service, *APPS)
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
    _grant(pcs_service, *APPS)
    rejected = _response(status, url)
    if not json_response:
        rejected.headers.clear()
    success = _response(200, url)
    options.update({"params": {"dsid": "test"}, "timeout": 10, "stream": True})
    with (
        patch("requests.Session.request", side_effect=[rejected, success]) as send,
        patch.object(rejected, "close") as close,
    ):
        assert (
            pcs_service.session.request(method, url, pcs_app_name=app, **options)
            is success
        )
    assert send.call_count == 2
    assert send.call_args_list[0] == send.call_args_list[1]
    close.assert_called_once_with()
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_called_once_with(
        app, derived_from_user_action=True
    )
    assert set(pcs_service._pcs_granted) == set(APPS)


def test_second_rejection_propagates_and_invalidates_grant(
    pcs_service: PyiCloudService,
) -> None:
    """No retry loop; a later property access can request a new grant."""
    _grant(pcs_service, "reminders")
    with (
        patch(
            "requests.Session.request", side_effect=[_response(401), _response(423)]
        ) as send,
        pytest.raises(PyiCloudAPIResponseException) as error,
    ):
        pcs_service.session.post(REMINDERS_QUERY, json={}, pcs_app_name="reminders")
    assert error.value.code == 423
    assert send.call_count == 2
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("reminders")
    assert set(pcs_service._pcs_granted) == {"reminders"}


def test_refresh_failure_propagates_without_resending_read(
    pcs_service: PyiCloudService,
) -> None:
    """Failed renewal does not mark the service granted or replay the request."""
    _grant(pcs_service, "reminders")
    with (
        patch("requests.Session.request", return_value=_response(401)) as send,
        patch.object(
            pcs_service,
            "_send_pcs_request",
            side_effect=PyiCloudAPIResponseException("PCS refresh failed"),
        ),
        pytest.raises(PyiCloudAPIResponseException, match="PCS refresh failed"),
    ):
        pcs_service.session.post(REMINDERS_QUERY, json={}, pcs_app_name="reminders")
    send.assert_called_once()
    assert not pcs_service._pcs_granted
    # A saved service client can recover on its next read without property access.
    success = _response(200)
    with patch("requests.Session.request", side_effect=[_response(401), success]):
        assert (
            pcs_service.session.post(REMINDERS_QUERY, json={}, pcs_app_name="reminders")
            is success
        )
    assert set(pcs_service._pcs_granted) == {"reminders"}


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
    _grant(pcs_service, "reminders")
    url = REMINDERS_QUERY.removesuffix("/records/query") + route
    with (
        patch("requests.Session.request", return_value=_response(401, url)) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.request(method, url, pcs_app_name="reminders", **options)
    send.assert_called_once()
    assert not pcs_service._pcs_granted
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()


def test_raw_requests_remain_one_shot(pcs_service: PyiCloudService) -> None:
    """Raw callers receive the original rejection without a refresh or retry."""
    _grant(pcs_service, "reminders")
    rejected = _response(401)
    with patch("requests.Session.request", return_value=rejected) as send:
        assert pcs_service.session.request_raw("POST", REMINDERS_QUERY) is rejected
    send.assert_called_once()
    assert set(pcs_service._pcs_granted) == {"reminders"}
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
    _grant(pcs_service, *APPS)
    with (
        patch("requests.Session.request", return_value=_response(401, url)) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.post(url)
    send.assert_called_once()
    assert set(pcs_service._pcs_granted) == set(APPS)


@pytest.mark.parametrize("status", [403, 409, 429, 450, 500])
def test_other_failures_do_not_refresh(
    pcs_service: PyiCloudService, status: int
) -> None:
    """Throttling, account locks and 2FA challenges retain existing handling."""
    _grant(pcs_service, "reminders")
    with patch("requests.Session.request", return_value=_response(status)) as send:
        exception = (
            PyiCloudAuthRequiredException
            if status == 450
            else PyiCloudAPIResponseException
        )
        with pytest.raises(exception):
            pcs_service.session.post(REMINDERS_QUERY, pcs_app_name="reminders")
    send.assert_called_once()
    assert set(pcs_service._pcs_granted) == {"reminders"}


def test_missing_advertised_root_does_not_refresh(pcs_service: PyiCloudService) -> None:
    """Untagged requests do not guess a service from its URL or metadata."""
    _grant(pcs_service, *APPS)
    pcs_service._webservices = None
    with (
        patch("requests.Session.request", return_value=_response(401)) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.post(REMINDERS_QUERY)
    send.assert_called_once()


def test_url_without_grant_does_not_refresh(pcs_service: PyiCloudService) -> None:
    """An untagged auth failure does not cause automatic PCS prompts."""
    with (
        patch("requests.Session.request", return_value=_response(401)),
        pytest.raises(PyiCloudAPIResponseException),
    ):
        pcs_service.session.post(REMINDERS_QUERY)
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()


def test_head_is_a_reusable_read() -> None:
    """HEAD requests can recover without replaying an operation or stream body."""
    assert PyiCloudSession._can_retry_pcs_read("head", REMINDERS_QUERY, {})


@pytest.mark.parametrize(
    "entry",
    [None, [], "invalid", {}, {"url": 42}, {"url": ""}, {"url": "https://[invalid"}],
)
def test_malformed_unrelated_metadata_does_not_block_drive_recovery(
    pcs_service: PyiCloudService, entry: Any
) -> None:
    """Invalid CloudKit advertisements cannot mask a rejected Drive request."""
    _grant(pcs_service, *APPS)
    assert pcs_service._webservices is not None
    pcs_service._webservices["ckdatabasews"] = entry
    url = f"{DRIVE_ROOT}/retrieveAppLibraries"
    with patch(
        "requests.Session.request",
        side_effect=[_response(401, url), _response(200, url)],
    ) as send:
        assert (
            pcs_service.session.get(url, pcs_app_name="iclouddrive").status_code == 200
        )
    assert send.call_count == 2
    assert set(pcs_service._pcs_granted) == set(APPS)


@pytest.mark.parametrize("advertised", [None, [], "invalid"])
def test_malformed_service_map_preserves_original_error(
    pcs_service: PyiCloudService, advertised: Any
) -> None:
    """Unexpected metadata must not replace the original auth exception."""
    _grant(pcs_service, *APPS)
    pcs_service._webservices = advertised
    with (
        patch("requests.Session.request", return_value=_response(401)),
        pytest.raises(PyiCloudAPIResponseException) as error,
    ):
        pcs_service.session.post(REMINDERS_QUERY)
    assert error.value.code == 401
    assert set(pcs_service._pcs_granted) == set(APPS)


@pytest.mark.parametrize("shape", ["mapping", "pairs", "iterator"])
def test_one_shot_query_parameters_are_not_replayed(
    pcs_service: PyiCloudService, shape: str
) -> None:
    """Preparation consumes iterators, so retrying them would change the URL."""
    _grant(pcs_service, "iclouddrive")
    url = f"{DOC_ROOT}/ws/com.apple.CloudDocs/download/by_id"
    params: Any
    if shape == "mapping":
        params = {"document_id": iter(["file-a"])}
    elif shape == "pairs":
        params = [("document_id", iter(["file-a"]))]
    else:
        params = iter([("document_id", "file-a")])
    prepared_urls: list[str | None] = []

    def prepare_response(*_args: Any, **kwargs: Any) -> Response:
        prepared = Request(
            kwargs["method"], kwargs["url"], params=kwargs["params"]
        ).prepare()
        prepared_urls.append(prepared.url)
        return _response(401, prepared.url or url)

    with (
        patch("requests.Session.request", side_effect=prepare_response) as send,
        pytest.raises(PyiCloudAPIResponseException) as error,
    ):
        pcs_service.session.get(url, params=params, pcs_app_name="iclouddrive")
    assert error.value.code == 401
    send.assert_called_once()
    assert prepared_urls == [f"{url}?document_id=file-a"]
    assert not pcs_service._pcs_granted


@pytest.mark.parametrize(
    "params",
    [
        None,
        "document_id=file-a",
        b"document_id=file-a",
        {"document_id": ["file-a"]},
        [("document_id", ("file-a",))],
    ],
)
def test_reusable_query_parameters_remain_retryable(params: Any) -> None:
    """Scalar, encoded, and reusable multi-value params retain read recovery."""
    assert PyiCloudSession._can_retry_pcs_read(
        "GET", REMINDERS_QUERY, {"params": params}
    )


@pytest.mark.parametrize(
    "elapsed", [0, PCS_GRANT_TTL - 1, PCS_GRANT_TTL, PCS_GRANT_TTL + 1]
)
def test_grants_expire_after_six_hours(
    pcs_service: PyiCloudService, elapsed: int
) -> None:
    """The monotonic TTL includes its exact boundary and accepts a zero timestamp."""
    pcs_service._pcs_granted["reminders"] = 0.0
    with patch("pyicloud.base.time.monotonic", return_value=float(elapsed)):
        pcs_service._request_pcs_for_service("reminders")
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    assert pcs_service._send_pcs_request.call_count == int(elapsed >= PCS_GRANT_TTL)
    assert pcs_service._pcs_granted["reminders"] == (
        float(elapsed) if elapsed >= PCS_GRANT_TTL else 0.0
    )


def test_expired_grant_is_removed_when_renewal_fails(
    pcs_service: PyiCloudService,
) -> None:
    """A failed TTL renewal cannot leave a stale timestamp suppressing recovery."""
    pcs_service._pcs_granted["photos"] = 0.0
    with (
        patch("pyicloud.base.time.monotonic", return_value=float(PCS_GRANT_TTL)),
        patch.object(
            pcs_service,
            "_send_pcs_request",
            side_effect=PyiCloudAPIResponseException("PCS failed"),
        ),
        pytest.raises(PyiCloudAPIResponseException, match="PCS failed"),
    ):
        pcs_service._request_pcs_for_service("photos")
    assert not pcs_service._pcs_granted
    pcs_service._request_pcs_for_service("photos")
    assert set(pcs_service._pcs_granted) == {"photos"}


@pytest.mark.parametrize("app", APPS)
def test_service_clients_supply_their_own_pcs_context(
    pcs_service: PyiCloudService, app: str
) -> None:
    """Clients own their URLs; PCS renewal never reconstructs or matches them."""
    _grant(pcs_service, *APPS)
    pcs_service._webservices = None
    success = _response(200)
    success._content = b'{"zones": [], "items": [], "records": []}'
    with patch(
        "requests.Session.request", side_effect=[_response(401), success]
    ) as send:
        if app == "photos":
            client = PhotosCloudKitClient(
                base_url=CK_ROOT, session=pcs_service.session, base_params={}
            )
            assert client.zones_list().zones == []
        elif app == "reminders":
            assert (
                CloudKitRemindersClient(CK_ROOT, pcs_service.session, {})
                .zones_list()
                .zones
                == []
            )
        elif app == "notes3":
            notes = CloudKitNotesClient(CK_ROOT, pcs_service.session, {})
            assert (
                notes.query(
                    query=CKQueryObject(recordType="Note"),
                    zone_id=CKZoneIDReq(zoneName="Notes"),
                ).records
                == []
            )
        else:
            drive = DriveService(DRIVE_ROOT, DOC_ROOT, pcs_service.session, {})
            assert drive.get_app_data() == []
    assert send.call_count == 2
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_called_once_with(
        app, derived_from_user_action=True
    )
    assert all("pcs_app_name" not in call.kwargs for call in send.call_args_list)


def test_injected_sessions_do_not_receive_pcs_extension_kwargs() -> None:
    """Keep compatibility with plain requests-compatible test/application transports."""
    assert not pcs_request_options(object(), "reminders")


@pytest.mark.parametrize(
    "route", ["/changes/zone", "/changes/database", "/internal/records/query/batch"]
)
def test_existing_cloudkit_read_routes_are_retryable(route: str) -> None:
    """Use the existing read operation routes, including sync and batch counts."""
    assert PyiCloudSession._can_retry_pcs_read("POST", CK_ROOT + route, {"json": {}})


@pytest.mark.parametrize("status", [401, 423])
@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("create_folders", ("root", "name")),
        ("delete_items", ("id", "etag")),
        ("rename_items", ("id", "etag", "name")),
        ("move_items_to_trash", ("id", "etag")),
        ("recover_items_from_trash", ("id", "etag")),
        ("delete_forever_from_trash", ("id", "etag")),
    ],
)
def test_drive_write_rejection_invalidates_grant_without_replaying(
    pcs_service: PyiCloudService,
    status: int,
    operation: str,
    args: tuple[str, ...],
) -> None:
    """Write failures allow later renewal, but never initiate it or replay writes."""
    _grant(pcs_service, "iclouddrive", "photos")
    drive = DriveService(
        DRIVE_ROOT, DOC_ROOT, pcs_service.session, {"clientId": "test"}
    )
    invoke = getattr(drive, operation)
    with (
        patch(
            "requests.Session.request", return_value=_response(status, DRIVE_ROOT)
        ) as send,
        pytest.raises(PyiCloudAPIResponseException),
    ):
        invoke(*args)
    send.assert_called_once()
    assert set(pcs_service._pcs_granted) == {"photos"}
    assert isinstance(pcs_service._send_pcs_request, MagicMock)
    pcs_service._send_pcs_request.assert_not_called()
