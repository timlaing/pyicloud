"""Shared context and authentication helpers for the Typer CLI."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import typer
from click import confirm
from rich.console import Console

from pyicloud import PyiCloudService, utils
from pyicloud.base import resolve_cookie_directory
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudServiceUnavailable
from pyicloud.ssl_context import configurable_ssl_verification

from .account_index import AccountIndexEntry, prune_accounts, remember_account
from .output import OutputFormat, write_json

EXECUTION_CONTEXT_OVERRIDES_META_KEY = "execution_context_overrides"


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


@dataclass(frozen=True)
class CLIInvocationDefaults:
    """Root-level execution context defaults captured before leaf parsing."""

    username: str
    password: Optional[str]
    china_mainland: bool
    interactive: bool
    delete_from_keyring: bool
    accept_terms: bool
    with_family: bool
    session_dir: Optional[str]
    http_proxy: Optional[str]
    https_proxy: Optional[str]
    no_verify_ssl: bool
    log_level: LogLevel
    output_format: OutputFormat


@dataclass(frozen=True)
class CommandOverrides:
    """Leaf command execution-context overrides."""

    username: Optional[str] = None
    password: Optional[str] = None
    china_mainland: Optional[bool] = None
    interactive: Optional[bool] = None
    accept_terms: Optional[bool] = None
    with_family: Optional[bool] = None
    session_dir: Optional[str] = None
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    no_verify_ssl: Optional[bool] = None
    log_level: Optional[LogLevel] = None
    output_format: Optional[OutputFormat] = None


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
        self._probe_api: Optional[PyiCloudService] = None
        self._resolved_username: Optional[str] = self.username or None

    @classmethod
    def from_invocation(
        cls,
        defaults: CLIInvocationDefaults,
        overrides: Optional[CommandOverrides] = None,
    ) -> "CLIState":
        """Build CLI state from root defaults plus leaf overrides."""

        overrides = overrides or CommandOverrides()
        return cls(
            username=(
                defaults.username if overrides.username is None else overrides.username
            ),
            password=(
                defaults.password if overrides.password is None else overrides.password
            ),
            china_mainland=(
                defaults.china_mainland
                if overrides.china_mainland is None
                else overrides.china_mainland
            ),
            interactive=(
                defaults.interactive
                if overrides.interactive is None
                else overrides.interactive
            ),
            delete_from_keyring=defaults.delete_from_keyring,
            accept_terms=(
                defaults.accept_terms
                if overrides.accept_terms is None
                else overrides.accept_terms
            ),
            with_family=(
                defaults.with_family
                if overrides.with_family is None
                else overrides.with_family
            ),
            session_dir=(
                defaults.session_dir
                if overrides.session_dir is None
                else overrides.session_dir
            ),
            http_proxy=(
                defaults.http_proxy
                if overrides.http_proxy is None
                else overrides.http_proxy
            ),
            https_proxy=(
                defaults.https_proxy
                if overrides.https_proxy is None
                else overrides.https_proxy
            ),
            no_verify_ssl=(
                defaults.no_verify_ssl
                if overrides.no_verify_ssl is None
                else overrides.no_verify_ssl
            ),
            log_level=(
                defaults.log_level
                if overrides.log_level is None
                else overrides.log_level
            ),
            output_format=(
                defaults.output_format
                if overrides.output_format is None
                else overrides.output_format
            ),
        )

    @property
    def has_explicit_username(self) -> bool:
        """Return whether the user explicitly passed --username."""

        return bool(self.username)

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
        return self.delete_keyring_password(self.username)

    def delete_keyring_password(self, username: str) -> bool:
        """Delete a stored keyring password for a username."""

        if utils.password_exists_in_keyring(username):
            utils.delete_password_in_keyring(username)
            self.prune_local_accounts()
            return True
        self.prune_local_accounts()
        return False

    def has_keyring_password(self, username: Optional[str] = None) -> bool:
        """Return whether a keyring password exists for a username."""

        candidate = (username or self._resolved_username or self.username).strip()
        if not candidate:
            return False
        return utils.password_exists_in_keyring(candidate)

    @property
    def session_root(self) -> Path:
        """Return the resolved session root for this CLI invocation."""

        return Path(resolve_cookie_directory(self.session_dir))

    def local_accounts(self) -> list[AccountIndexEntry]:
        """Return discoverable local accounts after opportunistic pruning."""

        return prune_accounts(self.session_root, self.has_keyring_password)

    def prune_local_accounts(self) -> list[AccountIndexEntry]:
        """Prune stale indexed accounts and return the discoverable set."""

        return self.local_accounts()

    def remember_account(self, api: PyiCloudService, *, select: bool = True) -> None:
        """Persist an account entry for later local discovery."""

        remember_account(
            self.session_root,
            username=api.account_name,
            session_path=api.session.session_path,
            cookiejar_path=api.session.cookiejar_path,
            keyring_has=self.has_keyring_password,
        )
        if select:
            self._resolved_username = api.account_name

    def _resolve_username(self) -> str:
        if self._resolved_username:
            return self._resolved_username

        accounts = self.local_accounts()
        if not accounts:
            raise CLIAbort(
                "No local accounts were found; pass --username to bootstrap one."
            )
        if len(accounts) > 1:
            options = "\n".join(f"  - {entry['username']}" for entry in accounts)
            raise CLIAbort(
                "Multiple local accounts were found; pass --username to choose one.\n"
                f"{options}"
            )

        self._resolved_username = accounts[0]["username"]
        return self._resolved_username

    def not_logged_in_message(self) -> str:
        """Return the default message for commands that require an active session."""

        return (
            "You are not logged into any iCloud accounts. To log in, run: "
            "icloud auth login --username <apple-id>"
        )

    def not_logged_in_for_account_message(self, username: str) -> str:
        """Return the message for account-targeted commands without an active session."""

        return (
            f"You are not logged into iCloud for {username}. Run: "
            f"icloud auth login --username {username}"
        )

    @staticmethod
    def multiple_logged_in_accounts_message(usernames: list[str]) -> str:
        """Return the message for ambiguous active-session account selection."""

        options = "\n".join(f"  - {username}" for username in usernames)
        return (
            "Multiple logged-in iCloud accounts were found; pass --username to choose one.\n"
            f"{options}"
        )

    def _password_for_login(self, username: str) -> Optional[str]:
        if self.password:
            return self.password
        return utils.get_password(username, interactive=self.interactive)

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

    def get_login_api(self) -> PyiCloudService:
        """Return a PyiCloudService, bootstrapping login if needed."""

        if self._api is not None:
            return self._api
        username = self._resolve_username()

        password = self._password_for_login(username)
        if not password:
            raise CLIAbort("No password supplied and no stored password was found.")

        logging.basicConfig(level=self.log_level.logging_level())

        try:
            api = PyiCloudService(
                apple_id=username,
                password=password,
                china_mainland=self.china_mainland,
                cookie_directory=self.session_dir,
                accept_terms=self.accept_terms,
                with_family=self.with_family,
            )
        except PyiCloudFailedLoginException as err:
            if utils.password_exists_in_keyring(username):
                utils.delete_password_in_keyring(username)
                self.prune_local_accounts()
            raise CLIAbort(f"Bad username or password for {username}") from err

        if (
            not utils.password_exists_in_keyring(username)
            and self.interactive
            and confirm("Save password in keyring?")
        ):
            utils.store_password_in_keyring(username, password)

        if api.requires_2fa:
            self._handle_2fa(api)
        elif api.requires_2sa:
            self._handle_2sa(api)

        self._api = api
        self.remember_account(api)
        return api

    def get_api(self) -> PyiCloudService:
        """Return an authenticated PyiCloudService backed by an active session."""

        if self._api is not None:
            return self._api

        if self.has_explicit_username:
            username = self._resolve_username()
            api = self.build_probe_api(username)
            status = api.get_auth_status()
            if not status["authenticated"]:
                raise CLIAbort(self.not_logged_in_for_account_message(username))
            self._api = api
            self.remember_account(api)
            return api

        active_probes = self.active_session_probes()
        if not active_probes:
            raise CLIAbort(self.not_logged_in_message())
        if len(active_probes) > 1:
            raise CLIAbort(
                self.multiple_logged_in_accounts_message(
                    [api.account_name for api, _status in active_probes]
                )
            )

        api, _status = active_probes[0]
        self._api = api
        self.remember_account(api)
        return api

    def build_probe_api(self, username: str) -> PyiCloudService:
        """Build a non-authenticating PyiCloudService for session probes."""

        logging.basicConfig(level=self.log_level.logging_level())
        return PyiCloudService(
            apple_id=username,
            password=self.password,
            china_mainland=self.china_mainland,
            cookie_directory=self.session_dir,
            accept_terms=self.accept_terms,
            with_family=self.with_family,
            authenticate=False,
        )

    def get_probe_api(self) -> PyiCloudService:
        """Return a cached non-authenticating PyiCloudService for session probes."""

        if self._probe_api is not None:
            return self._probe_api
        username = self._resolve_username()
        self._probe_api = self.build_probe_api(username)
        return self._probe_api

    def auth_storage_info(
        self, api: Optional[PyiCloudService] = None
    ) -> dict[str, Any]:
        """Return session storage paths and presence flags."""

        probe_api = api or self.get_probe_api()
        session_path = Path(probe_api.session.session_path)
        cookiejar_path = Path(probe_api.session.cookiejar_path)
        return {
            "session_path": str(session_path),
            "cookiejar_path": str(cookiejar_path),
            "has_session_file": session_path.exists(),
            "has_cookiejar_file": cookiejar_path.exists(),
        }

    def active_session_probes(self) -> list[tuple[PyiCloudService, dict[str, Any]]]:
        """Return authenticated sessions discoverable from local session files."""

        probes: list[tuple[PyiCloudService, dict[str, Any]]] = []
        for entry in self.local_accounts():
            session_path = Path(entry["session_path"])
            cookiejar_path = Path(entry["cookiejar_path"])
            if not (session_path.exists() or cookiejar_path.exists()):
                continue
            api = self.build_probe_api(entry["username"])
            status = api.get_auth_status()
            if status["authenticated"]:
                self.remember_account(api, select=False)
                probes.append((api, status))
        return probes


def get_state(ctx: typer.Context) -> CLIState:
    """Return the resolved CLI state for a leaf command."""

    root_ctx = ctx.find_root()
    state = root_ctx.obj
    if isinstance(state, CLIState):
        return state
    if not isinstance(state, CLIInvocationDefaults):
        raise RuntimeError("CLI state was not initialized.")

    overrides = CommandOverrides(
        **ctx.meta.get(EXECUTION_CONTEXT_OVERRIDES_META_KEY, {})
    )
    resolved = CLIState.from_invocation(state, overrides)
    resolved.open()
    root_ctx.call_on_close(resolved.close)
    root_ctx.obj = resolved
    ctx.obj = resolved
    return resolved


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
