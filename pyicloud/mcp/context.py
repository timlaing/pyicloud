"""One serialized owner for optional, explicitly configured saved-session reads."""

# cspell:words poolmanager

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, Timeout
from urllib3.exceptions import HTTPError as UrllibHTTPError
from urllib3.exceptions import ReadTimeoutError

from pyicloud import PyiCloudService

from .action_policy import (
    STANDARD_WRITE_ACTIONS,
    SUPPORTED_HIGH_RISK_ACTIONS,
    is_high_risk,
)
from .header_deadline import deadline_pool

T = TypeVar("T")
ServiceName = Literal[
    "calendar", "reminders", "contacts", "drive", "notes", "photos", "mail"
]
SUPPORTED_SERVICES: tuple[ServiceName, ...] = (
    "calendar",
    "reminders",
    "contacts",
    "drive",
    "notes",
    "photos",
    "mail",
)
SUPPORTED_WRITE_ACTIONS = STANDARD_WRITE_ACTIONS + SUPPORTED_HIGH_RISK_ACTIONS


class ToolFailure(Exception):
    """A fixed safe error code, not an upstream exception or account payload."""


def transport_failure(error: RequestException | UrllibHTTPError) -> ToolFailure:
    """Requests wraps urllib3 body-read timeouts in ConnectionError."""
    wrapped = (error.__context__, error.__cause__, *error.args)
    if isinstance(error, (Timeout, ReadTimeoutError)) or any(
        isinstance(item, ReadTimeoutError) for item in wrapped
    ):
        return ToolFailure("timeout")
    return ToolFailure("upstream_failure")


@dataclass(frozen=True)
class ServerConfig:
    """Operator-selected account, storage and services; no password input."""

    username: str | None = None
    session_dir: str | None = None
    services: tuple[ServiceName, ...] = ()
    content_services: tuple[ServiceName, ...] = ()
    write_actions: tuple[str, ...] = ()
    high_risk_actions: tuple[str, ...] = ()
    upload_transfer_hosts: tuple[str, ...] = ()
    approval_dir: str | None = None
    timeout: float = 30.0
    queue_limit: int = 8
    china_mainland: bool = False

    def __post_init__(self) -> None:
        if bool(self.username) != bool(self.session_dir):
            raise ValueError("Username and session directory must be supplied together")
        if self.services and not self.username:
            raise ValueError("Enabling services requires an explicit account")
        if set(self.services) - set(SUPPORTED_SERVICES):
            raise ValueError("Unsupported service")
        if set(self.content_services) - {"drive", "notes", "photos", "mail"}:
            raise ValueError("Unsupported content service")
        if not set(self.content_services) <= set(self.services):
            raise ValueError("Content access also requires the service opt-in")
        if set(self.write_actions) - set(SUPPORTED_WRITE_ACTIONS):
            raise ValueError("Unsupported write action")
        self._validate_upload_policy()
        self._validate_write_policy()
        if not 0 < self.timeout <= 120 or not 1 <= self.queue_limit <= 32:
            raise ValueError("Invalid execution limits")

    def _validate_upload_policy(self) -> None:
        """Require exactly the selected bounded upload host policy."""
        uploading = bool(
            {"photos.upload_file", "drive.upload_file"} & set(self.write_actions)
        )
        if uploading != bool(self.upload_transfer_hosts):
            raise ValueError("Uploads require explicit transfer-host policy")
        if len(self.upload_transfer_hosts) > 8 or any(
            self._invalid_upload_host(host) for host in self.upload_transfer_hosts
        ):
            raise ValueError("Invalid upload transfer host")

    @staticmethod
    def _invalid_upload_host(host: str) -> bool:
        """Retain strict DNS label validation and reject numeric host selectors."""
        return (
            not isinstance(host, str)
            or not 1 < len(host) <= 253
            or host != host.lower()
            or "." not in host
            or any(
                not part
                or part.startswith("-")
                or part.endswith("-")
                or len(part) > 63
                or not all(
                    char.isascii() and (char.isalnum() or char == "-") for char in part
                )
                for part in host.split(".")
            )
            or all(part.isdecimal() for part in host.split("."))
        )

    def _validate_write_policy(self) -> None:
        """Preserve separate elevated selection, content consent and mailbox guards."""
        selected_high = set(self.high_risk_actions)
        required_high = {
            action for action in self.write_actions if is_high_risk(action)
        }
        if selected_high != required_high:
            raise ValueError(
                "High-risk actions require matching separate elevated selection"
            )
        if {"mail.edit_draft", "mail.send_draft"} & set(
            self.write_actions
        ) and "mail" not in self.content_services:
            raise ValueError("Draft edits and sends require the Mail content opt-in")
        if self.write_actions and (
            not self.approval_dir
            or any(
                action.split(".", 1)[0] not in self.services
                for action in self.write_actions
            )
        ):
            raise ValueError(
                "Writes require the service opt-in and private approval directory"
            )
        if self.approval_dir and not self.write_actions:
            raise ValueError("Approval storage requires explicit write actions")


class BoundedAdapter(HTTPAdapter):
    """Use the existing Requests transport with bounded timeouts and no retries."""

    def __init__(self, timeout: float) -> None:
        super().__init__(max_retries=0)
        self.timeout = timeout
        self.deadline: float | None = None
        self.requests_left = 32
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": deadline_pool(self._response_deadline),
        }

    def _response_deadline(self) -> float:
        """Use the owner deadline for headers as well as response bodies."""
        return (
            self.deadline
            if self.deadline is not None
            else time.monotonic() + self.timeout
        )

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
        endpoint = urlsplit(request.url or "").path.rsplit("/", 1)[-1]
        if endpoint in {"enableDeviceConsentForPCS", "requestPCS"}:
            raise ToolFailure("verification_required")
        remaining = self.timeout
        if self.requests_left <= 0:
            raise ToolFailure("request_budget_exceeded")
        self.requests_left -= 1
        if self.deadline is not None:
            remaining = min(remaining, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ToolFailure("timeout")
        # Caller-controlled upstream defaults cannot remove this extension's bound.
        bounded_timeout = (min(5.0, remaining), remaining)
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
            deadline = (
                self.deadline
                if self.deadline is not None
                else time.monotonic() + self.timeout
            )
            for chunk in self._body_chunks(response, deadline):
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
        except (RequestException, UrllibHTTPError) as error:
            raise transport_failure(error) from error
        finally:
            response.close()

    def _body_chunks(self, response: Response, deadline: float) -> Iterator[bytes]:
        """Read bounded buffers with one underlying read per remaining deadline."""
        if getattr(response, "_content_consumed", False) is True:
            yield from response.iter_content(chunk_size=8192)
            return
        raw = response.raw
        read = getattr(raw, "read1", None)
        if not callable(read):
            raise ToolFailure("unsupported_transport")
        while True:
            remaining = min(self.timeout, deadline - time.monotonic())
            if remaining <= 0:
                raise ToolFailure("timeout")
            connection = getattr(raw, "connection", None)
            sock = getattr(connection, "sock", None)
            if sock is None:
                stream = getattr(getattr(raw, "_fp", None), "fp", None)
                sock = getattr(getattr(stream, "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = read(8192, decode_content=False)
            if not isinstance(chunk, bytes):
                raise ToolFailure("upstream_failure")
            if not chunk:
                return
            yield chunk


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
        self._cleanup: list[Callable[[], None]] = []

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        """Trusted resource cleanup is drained on the same ownership lane."""
        if self._closed:
            raise ToolFailure("server_closed")
        self._cleanup.append(callback)

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
        # Replace both default scheme adapters: plaintext send is always denied.
        for scheme in ("https", "http"):
            client.session.mount(f"{scheme}://", self._adapter)
        # Retain the broad fallback for other http-prefixed schemes.
        client.session.mount("http", self._adapter)
        return client

    @staticmethod
    def _prevent_authentication(client: Any) -> None:
        """Refuse session recovery and direct service token-login entry points."""

        def denied(*_args: Any, **_kwargs: Any) -> None:
            raise ToolFailure("authentication_required")

        for name in ("authenticate", "_authenticate", "_authenticate_with_token"):
            if hasattr(client, name):
                setattr(client, name, denied)

    def _invoke(self, operation: Callable[[Any], T]) -> T:
        try:
            if self._client is None:
                self._client = self._factory()
            self._prevent_authentication(self._client)
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
            failed = False
            try:
                if self._client is not None:
                    self._client.session.close()
            except Exception:
                failed = True
            for callback in self._cleanup:
                try:
                    callback()
                except Exception:
                    failed = True
            if failed:
                raise ToolFailure("shutdown_failure")

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
