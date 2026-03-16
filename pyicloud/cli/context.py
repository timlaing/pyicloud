"""Shared context and authentication helpers for the Typer CLI."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import typer
from click import confirm
from rich.console import Console

from pyicloud import PyiCloudService, utils
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudServiceUnavailable
from pyicloud.ssl_context import configurable_ssl_verification

from .output import OutputFormat, write_json


class CLIAbort(RuntimeError):
    """Abort execution with a user-facing message."""


class LogLevel(str, Enum):
    """Supported log levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"

    def logging_level(self) -> int:
        """Return the stdlib logging level."""

        if self is LogLevel.ERROR:
            return logging.ERROR
        if self is LogLevel.INFO:
            return logging.INFO
        if self is LogLevel.DEBUG:
            return logging.DEBUG
        return logging.WARNING


class CLIState:
    """Shared CLI state and authenticated API access."""

    def __init__(
        self,
        *,
        username: str,
        password: Optional[str],
        china_mainland: bool,
        interactive: bool,
        delete_from_keyring: bool,
        accept_terms: bool,
        with_family: bool,
        session_dir: Optional[str],
        http_proxy: Optional[str],
        https_proxy: Optional[str],
        no_verify_ssl: bool,
        log_level: LogLevel,
        output_format: OutputFormat,
    ) -> None:
        self.username = username.strip()
        self.password = password
        self.china_mainland = china_mainland
        self.interactive = interactive
        self.delete_from_keyring = delete_from_keyring
        self.accept_terms = accept_terms
        self.with_family = with_family
        self.session_dir = session_dir
        self.http_proxy = http_proxy
        self.https_proxy = https_proxy
        self.no_verify_ssl = no_verify_ssl
        self.log_level = log_level
        self.output_format = output_format
        self.console = Console()
        self.err_console = Console(stderr=True)
        self._stack = ExitStack()
        self._api: Optional[PyiCloudService] = None

    @property
    def json_output(self) -> bool:
        """Return whether the current command expects JSON."""

        return self.output_format is OutputFormat.JSON

    def open(self) -> None:
        """Open CLI-scoped resources."""

        self._stack.enter_context(
            configurable_ssl_verification(
                verify_ssl=not self.no_verify_ssl,
                http_proxy=self.http_proxy or "",
                https_proxy=self.https_proxy or "",
            )
        )

    def close(self) -> None:
        """Close CLI-scoped resources."""

        self._stack.close()

    def write_json(self, payload: Any) -> None:
        """Write a JSON payload to stdout."""

        write_json(self.console, payload)

    def delete_stored_password(self) -> bool:
        """Delete a stored keyring password."""

        if not self.username:
            raise CLIAbort("A username is required with --delete-from-keyring.")
        if utils.password_exists_in_keyring(self.username):
            utils.delete_password_in_keyring(self.username)
            return True
        return False

    def _password_for_login(self) -> Optional[str]:
        if self.password:
            return self.password
        return utils.get_password(self.username, interactive=self.interactive)

    def _prompt_index(self, prompt: str, count: int) -> int:
        if count <= 1 or not self.interactive:
            return 0
        raw = typer.prompt(prompt, default="0")
        try:
            idx = int(raw)
        except ValueError as exc:
            raise CLIAbort("Invalid device selection.") from exc
        if idx < 0 or idx >= count:
            raise CLIAbort("Invalid device selection.")
        return idx

    def _handle_2fa(self, api: PyiCloudService) -> None:
        fido2_devices = list(getattr(api, "fido2_devices", []) or [])
        if fido2_devices:
            self.console.print("Security key verification required.")
            for index, _device in enumerate(fido2_devices):
                self.console.print(f"  {index}: Security key {index}")
            selected_index = self._prompt_index(
                "Select security key index", len(fido2_devices)
            )
            self.console.print("Touch the selected security key to continue.")
            try:
                api.confirm_security_key(fido2_devices[selected_index])
            except Exception as exc:  # pragma: no cover - live auth path
                raise CLIAbort("Security key verification failed.") from exc
        else:
            if not self.interactive:
                raise CLIAbort(
                    "Two-factor authentication is required, but interactive prompts are disabled."
                )
            code = typer.prompt("Enter 2FA code")
            if not api.validate_2fa_code(code):
                raise CLIAbort("Failed to verify the 2FA code.")
        if not api.is_trusted_session:
            api.trust_session()

    def _handle_2sa(self, api: PyiCloudService) -> None:
        devices = list(api.trusted_devices or [])
        if not devices:
            raise CLIAbort(
                "Two-step authentication is required but no trusted devices are available."
            )
        self.console.print("Trusted devices:")
        for index, device in enumerate(devices):
            label = (
                "SMS trusted device" if device.get("phoneNumber") else "Trusted device"
            )
            self.console.print(f"  {index}: {label}")
        selected_index = self._prompt_index("Select trusted device index", len(devices))
        device = devices[selected_index]
        if not api.send_verification_code(device):
            raise CLIAbort("Failed to send the 2SA verification code.")
        if not self.interactive:
            raise CLIAbort(
                "Two-step authentication is required, but interactive prompts are disabled."
            )
        code = typer.prompt("Enter 2SA verification code")
        if not api.validate_verification_code(device, code):
            raise CLIAbort("Failed to verify the 2SA code.")

    def get_api(self) -> PyiCloudService:
        """Return an authenticated PyiCloudService instance."""

        if self._api is not None:
            return self._api
        if not self.username:
            raise CLIAbort(
                "The --username option is required for authenticated commands."
            )

        password = self._password_for_login()
        if not password:
            raise CLIAbort("No password supplied and no stored password was found.")

        logging.basicConfig(level=self.log_level.logging_level())

        try:
            api = PyiCloudService(
                apple_id=self.username,
                password=password,
                china_mainland=self.china_mainland,
                cookie_directory=self.session_dir,
                accept_terms=self.accept_terms,
                with_family=self.with_family,
            )
        except PyiCloudFailedLoginException as err:
            if utils.password_exists_in_keyring(self.username):
                utils.delete_password_in_keyring(self.username)
            raise CLIAbort(f"Bad username or password for {self.username}") from err

        if (
            not utils.password_exists_in_keyring(self.username)
            and self.interactive
            and confirm("Save password in keyring?")
        ):
            utils.store_password_in_keyring(self.username, password)

        if api.requires_2fa:
            self._handle_2fa(api)
        elif api.requires_2sa:
            self._handle_2sa(api)

        self._api = api
        return api


def get_state(ctx: typer.Context) -> CLIState:
    """Return the shared CLI state for a command."""

    state = ctx.obj
    if not isinstance(state, CLIState):
        raise RuntimeError("CLI state was not initialized.")
    return state


def service_call(label: str, fn):
    """Wrap a service call with user-facing service-unavailable handling."""

    try:
        return fn()
    except PyiCloudServiceUnavailable as err:
        raise CLIAbort(f"{label} service unavailable: {err}") from err


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 datetime string."""

    if value is None:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise typer.BadParameter("Expected an ISO-8601 datetime value.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_device(api: PyiCloudService, query: str):
    """Return a device matched by id or common display names."""

    lowered = query.strip().lower()
    for device in api.devices:
        candidates = [
            getattr(device, "id", ""),
            getattr(device, "name", ""),
            getattr(device, "deviceDisplayName", ""),
        ]
        if any(str(candidate).strip().lower() == lowered for candidate in candidates):
            return device
    raise CLIAbort(f"No device matched '{query}'.")


def resolve_drive_node(drive, path: str, *, trash: bool = False):
    """Resolve an iCloud Drive node."""

    node = drive.trash if trash else drive.root
    normalized = PurePosixPath(path or "/")
    if str(normalized) in {".", "/"}:
        return node
    for part in normalized.parts:
        if part in {"", "/"}:
            continue
        node = node[part]
    return node


def write_response_to_path(response: Any, output: Path) -> None:
    """Stream a download response to disk."""

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as file_out:
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_out.write(chunk)
            return
        if hasattr(response, "raw") and hasattr(response.raw, "read"):
            while True:
                chunk = response.raw.read(8192)
                if not chunk:
                    break
                file_out.write(chunk)
            return
    raise CLIAbort("The download response could not be streamed.")
