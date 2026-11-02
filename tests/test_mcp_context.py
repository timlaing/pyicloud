"""Offline lifecycle and cancellation ownership regressions for the MCP lane."""

# pylint: disable=protected-access

import asyncio
from collections.abc import Iterator
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import PreparedRequest, Response
from requests.exceptions import ChunkedEncodingError, Timeout
from requests.exceptions import ConnectionError as RequestConnectionError
from urllib3.exceptions import ReadTimeoutError

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.context import BoundedAdapter, ServerConfig, SessionOwner, ToolFailure


@pytest.fixture(autouse=True)
def real_worker_threads() -> Iterator[None]:
    """Only these ownership tests need real threads; I/O guards remain enabled."""
    with patch("threading.Thread", Thread):
        yield


def configuration(**kwargs: Any) -> ServerConfig:
    """Synthetic configuration never reads credentials or account directories."""
    return ServerConfig(username="synthetic", session_dir="synthetic", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"username": "x"},
        {"session_dir": "x"},
        {"services": ("calendar",)},
        {"username": "x", "session_dir": "x", "services": ("unsupported",)},
        {"timeout": 0},
        {"timeout": 121},
        {"queue_limit": 0},
        {"queue_limit": 33},
    ],
)
def test_invalid_operator_configuration(kwargs: dict[str, Any]) -> None:
    """Invalid limits and unapproved services fail before any account access."""
    with pytest.raises(ValueError):
        ServerConfig(**kwargs)


def test_transport_timeout_and_deadline() -> None:
    """Requests inherit bounds even when their caller specifies no timeout."""
    adapter = BoundedAdapter(10)
    request, response = PreparedRequest(), Response()
    request.url = "https://example.invalid"
    request.prepare_headers({})
    response._content = b"{}"
    vars(response)["_content_consumed"] = True
    response.raw = MagicMock()
    response.status_code = 200
    with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
        assert adapter.send(request, timeout=None) is response
        assert response.json() == {}
        assert send.call_args.kwargs["timeout"] == (5, 1)
        assert send.call_args.kwargs["stream"] is True
        request.url = "http" + "://example.invalid"
        with pytest.raises(ToolFailure, match="insecure_transport"):
            adapter.send(request)
        request.url = "https://example.invalid"
        adapter.requests_left = 0
        with pytest.raises(ToolFailure, match="request_budget_exceeded"):
            adapter.send(request)
        adapter.requests_left = 1
        adapter.deadline = 9
        with (
            patch("pyicloud.mcp.context.time.monotonic", return_value=10),
            pytest.raises(ToolFailure, match="timeout"),
        ):
            adapter.send(request)


@pytest.mark.parametrize("endpoint", ["enableDeviceConsentForPCS", "requestPCS"])
def test_transport_never_sends_pcs_consent_or_device_requests(endpoint: str) -> None:
    """Lazy service access cannot initiate approval/device-cookie notifications."""
    adapter = BoundedAdapter(5)
    request = PreparedRequest()
    request.url = f"https://example.invalid/{endpoint}?clientId=synthetic"
    request.prepare_headers({})
    with (
        patch("requests.adapters.HTTPAdapter.send") as send,
        pytest.raises(ToolFailure, match="verification_required"),
    ):
        adapter.send(request)
    send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        ({"authenticated": False}, "authentication_required"),
        ({"requires_2fa": True}, "verification_required"),
        ({"requires_2sa": True}, "verification_required"),
    ],
)
async def test_probe_does_not_fall_back_to_login(
    status: dict[str, bool], code: str
) -> None:
    """Saved-session probes never become credential login or challenge tools."""
    client = MagicMock()
    client.get_auth_status.return_value = status
    owner = SessionOwner(configuration(), lambda: client)
    owner._adapter = BoundedAdapter(1)
    try:
        with pytest.raises(ToolFailure, match=code):
            await owner.run(lambda _client: pytest.fail("Unauthenticated operation"))
        assert owner._adapter.deadline is None
        assert not owner.authenticated
        client.authenticate.assert_not_called()
    finally:
        await owner.close()
    client.session.close.assert_called_once()
    await owner.close()
    with pytest.raises(ToolFailure, match="server_closed"):
        await owner.run(lambda _: None)


@pytest.mark.asyncio
async def test_unconfigured_status_never_constructs_client() -> None:
    """Core-only status requires no saved directory or account factory."""
    factory = MagicMock()
    owner = SessionOwner(ServerConfig(), factory)
    with pytest.raises(ToolFailure, match="authentication_required"):
        await owner.run(lambda _: None)
    await owner.close()
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_cancelled_or_timed_out_call_retains_lane_ownership(cancel: bool) -> None:
    """A late result cannot allow overlapping session use or early closure."""
    started, release = Event(), Event()
    client = MagicMock()
    client.get_auth_status.return_value = {"authenticated": True}
    owner = SessionOwner(configuration(timeout=0.05, queue_limit=1), lambda: client)

    def blocked(_client: object) -> str:
        started.set()
        release.wait(1)
        return "late"

    task = asyncio.create_task(owner.run(blocked))
    await asyncio.to_thread(started.wait, 1)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ToolFailure, match="timeout"):
            await task
    with pytest.raises(ToolFailure, match="busy"):
        await owner.run(lambda _: None)
    closing = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    client.session.close.assert_not_called()
    release.set()
    await closing
    assert owner.authenticated
    client.session.close.assert_called_once()


@pytest.mark.asyncio
async def test_client_construction_is_lazy_and_private() -> None:
    """Factory opens only an existing private operator directory with login off."""
    fake_path = MagicMock()
    fake_path.resolve.return_value = fake_path
    fake_path.is_dir.return_value = True
    fake_path.stat.return_value = SimpleNamespace(st_mode=0o700)
    client = MagicMock()
    client.get_auth_status.return_value = {"authenticated": True}
    with (
        patch("pyicloud.mcp.context.Path", return_value=fake_path),
        patch("pyicloud.mcp.context.PyiCloudService", return_value=client) as factory,
    ):
        owner = SessionOwner(configuration())
        factory.assert_not_called()
        try:
            assert await owner.run(lambda _: "safe") == "safe"
            assert factory.call_args.kwargs["authenticate"] is False
            assert client.session.mount.call_count == 2
        finally:
            await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exists,mode,code",
    [
        (False, 0o700, "authentication_required"),
        (True, 0o777, "insecure_session_directory"),
    ],
)
async def test_missing_or_public_storage_rejected(
    exists: bool, mode: int, code: str
) -> None:
    """Never create storage silently or use another user's readable cookies."""
    directory = MagicMock()
    directory.resolve.return_value = directory
    directory.is_dir.return_value = exists
    directory.stat.return_value = SimpleNamespace(st_mode=mode)
    with (
        patch("pyicloud.mcp.context.Path", return_value=directory),
        patch("pyicloud.mcp.context.os.name", "posix"),
    ):
        owner = SessionOwner(configuration())
        try:
            with pytest.raises(ToolFailure, match=code):
                await owner.run(lambda _: None)
        finally:
            await owner.close()


@pytest.mark.asyncio
async def test_cancelled_queued_operation_never_contacts_account() -> None:
    """A cancelled request waiting behind active work must not execute later."""
    started, release = Event(), Event()
    client = MagicMock()
    client.get_auth_status.return_value = {"authenticated": True}
    owner = SessionOwner(configuration(), lambda: client)

    def blocked(_client: object) -> None:
        started.set()
        release.wait(1)

    first = asyncio.create_task(owner.run(blocked))
    await asyncio.to_thread(started.wait, 1)
    operation = MagicMock()
    queued = asyncio.create_task(owner.run(operation))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await first
    await owner.close()
    operation.assert_not_called()


@pytest.mark.parametrize(
    "kind,code",
    [
        ("rate", "rate_limited"),
        ("upstream", "upstream_failure"),
        ("encoding", "unsupported_content_encoding"),
        ("deadline", "timeout"),
        ("size", "response_too_large"),
    ],
)
def test_response_reads_are_bounded_and_always_closed(kind: str, code: str) -> None:
    """A trickling or oversized body cannot occupy the lane indefinitely."""
    adapter = BoundedAdapter(5)
    adapter.deadline = 5
    request = PreparedRequest()
    request.url = "https://example.invalid"
    request.prepare_headers({})
    response = MagicMock(spec=Response)
    response.status_code = {"rate": 429, "upstream": 503}.get(kind, 200)
    response.headers = {"Content-Encoding": "gzip"} if kind == "encoding" else {}
    response.iter_content.return_value = iter([
        b"x" * (4 * 1024 * 1024 + 1) if kind == "size" else b"x"
    ])
    times = [1, 6] if kind == "deadline" else [1, 1]
    with (
        patch("requests.adapters.HTTPAdapter.send", return_value=response),
        patch("pyicloud.mcp.context.time.monotonic", side_effect=times),
        pytest.raises(ToolFailure, match=code),
    ):
        adapter.send(request)
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "error,code",
    [
        (Timeout("private"), "timeout"),
        (RequestConnectionError("private"), "upstream_failure"),
    ],
)
def test_transport_failure_cannot_be_swallowed_by_auth_probe(
    error: Exception, code: str
) -> None:
    """Connection and timeout failures stay distinct from credential rejection."""
    adapter = BoundedAdapter(5)
    request = PreparedRequest()
    request.url = "https://example.invalid"
    request.prepare_headers({})
    with (
        patch("requests.adapters.HTTPAdapter.send", side_effect=error),
        pytest.raises(ToolFailure, match=code),
    ):
        adapter.send(request)


@pytest.mark.parametrize(
    "error,code",
    [
        (Timeout("private"), "timeout"),
        (RequestConnectionError("private"), "upstream_failure"),
        (
            RequestConnectionError(ReadTimeoutError(MagicMock(), None, "private")),
            "timeout",
        ),
        (ChunkedEncodingError("private"), "upstream_failure"),
    ],
)
def test_body_transport_errors_preserve_probe_failure_category(
    error: Exception, code: str
) -> None:
    """Errors raised after headers still bypass generic auth-probe conversion."""
    adapter = BoundedAdapter(5)
    request = PreparedRequest()
    request.url = "https://example.invalid"
    request.prepare_headers({})
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.headers = {}
    response.iter_content.side_effect = error
    with (
        patch("requests.adapters.HTTPAdapter.send", return_value=response),
        pytest.raises(ToolFailure, match=code),
    ):
        adapter.send(request)
    response.close.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_drain_timeout_does_not_close_an_active_session() -> None:
    """Bound close's await, leaving the queued release behind the running owner."""
    started, release = Event(), Event()
    client = MagicMock()
    client.get_auth_status.return_value = {"authenticated": True}
    owner = SessionOwner(configuration(), lambda: client)

    def blocked(_client: object) -> None:
        started.set()
        release.wait(1)

    task = asyncio.create_task(owner.run(blocked))
    await asyncio.to_thread(started.wait, 1)
    with (
        patch(
            "pyicloud.mcp.context.asyncio.wait_for", side_effect=asyncio.TimeoutError
        ),
        pytest.raises(ToolFailure, match="shutdown_timeout"),
    ):
        await owner.close()
    client.session.close.assert_not_called()
    release.set()
    await task


@pytest.mark.asyncio
async def test_rate_limited_probe_is_not_converted_to_login_failure() -> None:
    """Intercept 429 before the public probe catches generic API response errors."""
    adapter = BoundedAdapter(5)
    response = MagicMock(spec=Response)
    response.status_code = 429
    request = PreparedRequest()
    request.url = "https://example.invalid"
    request.prepare_headers({})
    client = MagicMock()
    client.session.data = {"session_token": "synthetic"}

    def probe() -> dict[str, bool]:
        try:
            adapter.send(request)
        except PyiCloudAPIResponseException:
            return {"authenticated": False}
        return {"authenticated": True}

    client.get_auth_status.side_effect = probe
    owner = SessionOwner(configuration(), lambda: client)
    owner._adapter = adapter
    with patch("requests.adapters.HTTPAdapter.send", return_value=response):
        try:
            with pytest.raises(ToolFailure, match="rate_limited"):
                await owner.run(lambda _: None)
            assert client.session.data == {"session_token": "synthetic"}
        finally:
            await owner.close()


@pytest.mark.asyncio
async def test_shutdown_failure_is_sanitized() -> None:
    """Even custom session cleanup failures must not expose upstream messages."""
    client = MagicMock()
    client.get_auth_status.return_value = {"authenticated": True}
    client.session.close.side_effect = RuntimeError("private account payload")
    owner = SessionOwner(configuration(), lambda: client)
    await owner.run(lambda _: None)
    with pytest.raises(ToolFailure, match="shutdown_failure") as caught:
        await owner.close()
    assert "private" not in str(caught.value)
