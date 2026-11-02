"""One serialized owner for optional, explicitly configured saved-session reads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Literal, TypeVar

from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, Timeout
from urllib3.exceptions import ReadTimeoutError

from pyicloud import PyiCloudService

T = TypeVar("T")


class ToolFailure(Exception):
    """A fixed safe error code, not an upstream exception or account payload."""


def transport_failure(error: RequestException) -> ToolFailure:
    """Requests wraps urllib3 body-read timeouts in ConnectionError."""
    wrapped = (error.__context__, error.__cause__, *error.args)
    if isinstance(error, Timeout) or any(
        isinstance(item, ReadTimeoutError) for item in wrapped
    ):
        return ToolFailure("timeout")
    return ToolFailure("upstream_failure")


@dataclass(frozen=True)
class ServerConfig:
    """Operator-selected account, storage and services; no password input."""

    username: str | None = None
    session_dir: str | None = None
    services: tuple[Literal["calendar", "reminders"], ...] = ()
    timeout: float = 30.0
    queue_limit: int = 8
    china_mainland: bool = False

    def __post_init__(self) -> None:
        if bool(self.username) != bool(self.session_dir):
            raise ValueError("Username and session directory must be supplied together")
        if self.services and not self.username:
            raise ValueError("Enabling services requires an explicit account")
        if set(self.services) - {"calendar", "reminders"}:
            raise ValueError("Unsupported service")
        if not 0 < self.timeout <= 120 or not 1 <= self.queue_limit <= 32:
            raise ValueError("Invalid execution limits")


class BoundedAdapter(HTTPAdapter):
    """Use the existing Requests transport with bounded timeouts and no retries."""

    def __init__(self, timeout: float) -> None:
        super().__init__(max_retries=0)
        self.timeout = timeout
        self.deadline: float | None = None
        self.requests_left = 32

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> Response:
        if not (request.url or "").startswith("https://"):
            raise ToolFailure("insecure_transport")
        remaining = self.timeout
        if self.requests_left <= 0:
            raise ToolFailure("request_budget_exceeded")
        self.requests_left -= 1
        if self.deadline is not None:
            remaining = min(remaining, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ToolFailure("timeout")
        # Caller-controlled upstream defaults cannot remove this extension's bound.
        bounded_timeout = (min(5.0, remaining), min(1.0, remaining))
        request.headers["Accept-Encoding"] = "identity"
        try:
            response = super().send(
                request,
                stream=True,
                timeout=bounded_timeout,
                verify=verify,
                cert=cert,
                proxies=proxies,
            )
        except RequestException as error:
            raise transport_failure(error) from error
        return self._read_response(response)

    def _read_response(self, response: Response) -> Response:
        """Apply response policy and consume a bounded body before releasing it."""
        try:
            # Keep transport failures distinct before get_auth_status normalizes them.
            if response.status_code == 429:
                raise ToolFailure("rate_limited")
            if response.status_code is not None and response.status_code >= 500:
                raise ToolFailure("upstream_failure")
            if response.headers.get("Content-Encoding", "identity").lower() not in {
                "",
                "identity",
            }:
                raise ToolFailure("unsupported_content_encoding")
            body = bytearray()
            deadline = self.deadline or (time.monotonic() + self.timeout)
            # One-byte reads prevent a slow trickle from filling a buffered chunk
            # indefinitely. Identity encoding also avoids decompressor buffering.
            for chunk in response.iter_content(chunk_size=1):
                if time.monotonic() >= deadline:
                    raise ToolFailure("timeout")
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    raise ToolFailure("response_too_large")
            # Requests has no public content setter. Cache the bounded body so its
            # normal Session/JSON/cookie pipeline remains intact after closure.
            response._content = bytes(body)  # pylint: disable=protected-access
            vars(response)["_content_consumed"] = True
            return response
        except RequestException as error:
            raise transport_failure(error) from error
        finally:
            response.close()


class SessionOwner:
    """Single-worker lane: timed-out/cancelled awaits never unlock running work."""

    def __init__(
        self, config: ServerConfig, factory: Callable[[], Any] | None = None
    ) -> None:
        self.config = config
        self._factory = factory or self._create_client
        self._client: Any = None
        self._adapter: BoundedAdapter | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="icloud-mcp"
        )
        self._pending = 0
        self._closed = False
        self.authenticated = False

    def _create_client(self) -> PyiCloudService:
        """Open only the operator's existing private storage, with login disabled."""
        directory = Path(self.config.session_dir or "").resolve()
        if not directory.is_dir():
            raise ToolFailure("authentication_required")
        if os.name == "posix" and directory.stat().st_mode & 0o077:
            raise ToolFailure("insecure_session_directory")
        client = PyiCloudService(
            self.config.username or "",
            cookie_directory=str(directory),
            authenticate=False,
            refresh_interval=None,
            china_mainland=self.config.china_mainland,
        )
        self._adapter = BoundedAdapter(self.config.timeout)
        client.session.mount("https://", self._adapter)
        # The broad prefix also intercepts plaintext requests, which send rejects.
        client.session.mount("http", self._adapter)
        return client

    def _invoke(self, operation: Callable[[Any], T]) -> T:
        try:
            if self._client is None:
                self._client = self._factory()
            if self._adapter is not None:
                self._adapter.deadline = time.monotonic() + self.config.timeout
                self._adapter.requests_left = 32
            self.authenticated = False
            status = self._client.get_auth_status()
            if status.get("requires_2fa") or status.get("requires_2sa"):
                raise ToolFailure("verification_required")
            if not status.get("authenticated"):
                raise ToolFailure("authentication_required")
            self.authenticated = True
            return operation(self._client)
        finally:
            if self._adapter is not None:
                self._adapter.deadline = None

    async def run(self, operation: Callable[[Any], T]) -> T:
        """Reject overload; shield worker ownership until actual completion."""
        if self._closed:
            raise ToolFailure("server_closed")
        if not self.config.username:
            raise ToolFailure("authentication_required")
        if self._pending >= self.config.queue_limit:
            raise ToolFailure("busy")
        self._pending += 1
        work = self._executor.submit(self._invoke, operation)
        future = asyncio.wrap_future(work)

        def completed(done: asyncio.Future[T]) -> None:
            self._pending -= 1
            if not done.cancelled():
                done.exception()  # Retrieve late failures without logging private data.

        future.add_done_callback(completed)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.config.timeout)
        except asyncio.TimeoutError as error:
            # Only queued work is cancellable; running work retains ownership.
            work.cancel()
            raise ToolFailure("timeout") from error
        except asyncio.CancelledError:
            work.cancel()
            raise

    async def close(self) -> None:
        """Drain on the same lane before closing; never close an active session."""
        if self._closed:
            return
        self._closed = True

        def release() -> None:
            if self._client is not None:
                try:
                    self._client.session.close()
                except Exception:
                    raise ToolFailure("shutdown_failure") from None

        try:
            future = asyncio.wrap_future(self._executor.submit(release))

            def released(done: asyncio.Future[None]) -> None:
                if not done.cancelled():
                    done.exception()

            future.add_done_callback(released)
            try:
                await asyncio.wait_for(asyncio.shield(future), self.config.timeout + 2)
            except asyncio.TimeoutError as error:
                # The queued release still closes only AFTER running work ends.
                raise ToolFailure("shutdown_timeout") from error
        finally:
            self._executor.shutdown(wait=False)
