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
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
    PyiCloudNoTrustedNumberAvailable,
    PyiCloudServiceUnavailable,
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)
from pyicloud.ssl_context import configurable_ssl_verification

from .account_index import (
    AccountIndexEntry,
    load_accounts,
    prune_accounts,
    remember_account,
)
from .output import OutputFormat, write_json

COMMAND_OPTIONS_META_KEY = "command_options"


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
class CLICommandOptions:
    """Command-local options captured from the leaf command."""

    username: Optional[str] = None
    password: Optional[str] = None
    china_mainland: Optional[bool] = None
    interactive: bool = True
    accept_terms: bool = False
    with_family: bool = False
    session_dir: Optional[str] = None
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    no_verify_ssl: bool = False
    log_level: LogLevel = LogLevel.WARNING
    output_format: OutputFormat = OutputFormat.TEXT


class CLIState:
    """Shared CLI state and authenticated API access."""

    def __init__(
        self,
        *,
        username: Optional[str],
        password: Optional[str],
        china_mainland: Optional[bool],
        interactive: bool,
        accept_terms: bool,
        with_family: bool,
        session_dir: Optional[str],
        http_proxy: Optional[str],
        https_proxy: Optional[str],
        no_verify_ssl: bool,
        log_level: LogLevel,
        output_format: OutputFormat,
    ) -> None:
        """Capture the CLI options and shared runtime state for one invocation."""
        self.username = (username or "").strip()
        self.password = password
        self.china_mainland = china_mainland
        self.interactive = interactive
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
        self._logging_configured = False

    @classmethod
    def from_options(cls, options: CLICommandOptions) -> "CLIState":
        """Build CLI state from one leaf command's options."""

        return cls(
            username=options.username,
            password=options.password,
            china_mainland=options.china_mainland,
            interactive=options.interactive,
            accept_terms=options.accept_terms,
            with_family=options.with_family,
            session_dir=options.session_dir,
            http_proxy=options.http_proxy,
            https_proxy=options.https_proxy,
            no_verify_ssl=options.no_verify_ssl,
            log_level=options.log_level,
            output_format=options.output_format,
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

    def account_entry(self, username: str) -> Optional[AccountIndexEntry]:
        """Return the indexed account entry for a username, if present."""

        return load_accounts(self.session_root).get(username)

    def resolved_china_mainland(self, username: str) -> Optional[bool]:
        """Resolve China mainland mode for an account from command or stored state."""

        if self.china_mainland is not None:
            return self.china_mainland
        entry = self.account_entry(username)
        if entry is None:
            return None
        return entry.get("china_mainland")

    def remember_account(self, api: PyiCloudService, *, select: bool = True) -> None:
        """Persist an account entry for later local discovery."""

        remember_account(
            self.session_root,
            username=api.account_name,
            session_path=api.session.session_path,
            cookiejar_path=api.session.cookiejar_path,
            china_mainland=api.is_china_mainland,
            keyring_has=self.has_keyring_password,
        )
        if select:
            self._resolved_username = api.account_name

    def _resolve_username(self) -> str:
        """Resolve the Apple ID to use for the current CLI command."""
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

    def _password_for_login(self, username: str) -> tuple[Optional[str], Optional[str]]:
        """Return the password and its source for an interactive login flow."""
        if self.password:
            return self.password, "explicit"

        keyring_password = utils.get_password_from_keyring(username)
        if keyring_password:
            return keyring_password, "keyring"

        if not self.interactive:
            return None, None

        return utils.get_password(username, interactive=True), "prompt"

    def _configure_logging(self) -> None:
        """Apply the requested log level once for the current CLI process."""
        if self._logging_configured:
            return
        logging.basicConfig(level=self.log_level.logging_level())
        self._logging_configured = True

    def _stored_password_for_session(self, username: str) -> Optional[str]:
        """Return a non-interactive password for service-level reauthentication."""

        if self.password:
            return self.password
        return utils.get_password_from_keyring(username)

    def _prompt_index(self, prompt: str, count: int) -> int:
        """Prompt for a zero-based selection index when multiple choices exist."""
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
        """Complete Apple's HSA2 flow using a security key or code-based challenge."""
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
            try:
                if not api.request_2fa_code():
                    raise CLIAbort(
                        "This 2FA challenge requires a security key. Connect one and retry."
                    )

                notice = getattr(api, "two_factor_delivery_notice", None)
                if notice:
                    self.console.print(notice)

                delivery_method = getattr(api, "two_factor_delivery_method", "unknown")
                if delivery_method == "trusted_device":
                    self.console.print(
                        "Requested a 2FA prompt on your trusted Apple devices."
                    )
                elif delivery_method == "sms":
                    self.console.print("Requested a 2FA code by SMS.")
            except PyiCloudNoTrustedNumberAvailable as exc:
                raise CLIAbort(
                    "Two-factor authentication requires a trusted phone number, "
                    "but none was returned."
                ) from exc
            except PyiCloudTrustedDevicePromptException as exc:
                raise CLIAbort(
                    "Failed to request the 2FA trusted-device prompt."
                ) from exc
            except PyiCloudAPIResponseException as exc:
                raise CLIAbort("Failed to request the 2FA SMS code.") from exc
            max_attempts = 3
            for attempt in range(max_attempts):
                code = typer.prompt("Enter 2FA code")
                try:
                    is_valid = api.validate_2fa_code(code)
                except PyiCloudTrustedDeviceVerificationException as exc:
                    raise CLIAbort(
                        "Failed to verify the 2FA trusted-device code."
                    ) from exc
                if is_valid:
                    break
                remaining_attempts = max_attempts - attempt - 1
                if remaining_attempts <= 0:
                    raise CLIAbort("Failed to verify the 2FA code.")
                self.console.print(
                    f"Invalid 2FA code. {remaining_attempts} attempt(s) remaining."
                )
        if not api.is_trusted_session:
            api.trust_session()

    def _handle_2sa(self, api: PyiCloudService) -> None:
        """Complete Apple's legacy two-step authentication flow."""
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
        if not self.interactive:
            raise CLIAbort(
                "Two-step authentication is required, but interactive prompts are disabled."
            )
        if not api.send_verification_code(device):
            raise CLIAbort("Failed to send the 2SA verification code.")
        code = typer.prompt("Enter 2SA verification code")
        if not api.validate_verification_code(device, code):
            raise CLIAbort("Failed to verify the 2SA code.")

    def get_login_api(self) -> PyiCloudService:
        """Return a PyiCloudService, bootstrapping login if needed."""

        if self._api is not None:
            return self._api
        username = self._resolve_username()

        password, password_source = self._password_for_login(username)
        if not password:
            raise CLIAbort("No password supplied and no stored password was found.")

        self._configure_logging()

        try:
            api = PyiCloudService(
                apple_id=username,
                password=password,
                china_mainland=self.resolved_china_mainland(username),
                cookie_directory=self.session_dir,
                accept_terms=self.accept_terms,
                with_family=self.with_family,
            )
        except PyiCloudFailedLoginException as err:
            if password_source == "keyring" and utils.password_exists_in_keyring(
                username
            ):
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
            probe_api = self.build_probe_api(username)
            status = probe_api.get_auth_status()
            if not status["authenticated"]:
                raise CLIAbort(self.not_logged_in_for_account_message(username))
            api = self.build_session_api(username)
            if not self._hydrate_api_from_probe(api, probe_api):
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

        probe_api, _status = active_probes[0]
        api = self.build_session_api(probe_api.account_name)
        if not self._hydrate_api_from_probe(api, probe_api):
            status = api.get_auth_status()
            if not status["authenticated"]:
                raise CLIAbort(
                    self.not_logged_in_for_account_message(probe_api.account_name)
                )
        self._api = api
        self.remember_account(api)
        return api

    def build_probe_api(self, username: str) -> PyiCloudService:
        """Build a non-authenticating PyiCloudService for session probes."""

        self._configure_logging()
        return PyiCloudService(
            apple_id=username,
            password=self.password,
            china_mainland=self.resolved_china_mainland(username),
            cookie_directory=self.session_dir,
            accept_terms=self.accept_terms,
            with_family=self.with_family,
            authenticate=False,
        )

    def build_session_api(self, username: str) -> PyiCloudService:
        """Build a session-backed API that can satisfy service reauthentication."""

        self._configure_logging()
        return PyiCloudService(
            apple_id=username,
            password=self._stored_password_for_session(username),
            china_mainland=self.resolved_china_mainland(username),
            cookie_directory=self.session_dir,
            accept_terms=self.accept_terms,
            with_family=self.with_family,
            authenticate=False,
        )

    @staticmethod
    def _hydrate_api_from_probe(
        api: PyiCloudService, probe_api: Optional[PyiCloudService]
    ) -> bool:
        """Populate auth-derived state on a session-backed API from a probe."""

        if probe_api is None:
            return False

        probe_data = getattr(probe_api, "data", None)
        if not isinstance(probe_data, dict) or not probe_data:
            return False

        api.data = dict(probe_data)

        params = getattr(api, "params", None)
        ds_info = probe_data.get("dsInfo")
        if isinstance(params, dict) and isinstance(ds_info, dict) and "dsid" in ds_info:
            params.update({"dsid": ds_info["dsid"]})

        if "webservices" in probe_data:
            setattr(api, "_webservices", probe_data["webservices"])

        return True

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

    options = CLICommandOptions(**ctx.meta.get(COMMAND_OPTIONS_META_KEY, {}))
    resolved = CLIState.from_options(options)
    resolved.open()
    root_ctx.call_on_close(resolved.close)
    root_ctx.obj = resolved
    ctx.obj = resolved
    return resolved


def service_call(label: str, fn, *, account_name: Optional[str] = None):
    """Wrap a service call with user-facing service-unavailable handling."""

    try:
        return fn()
    except PyiCloudServiceUnavailable as err:
        raise CLIAbort(f"{label} service unavailable: {err}") from err
    except (PyiCloudAuthRequiredException, PyiCloudFailedLoginException) as err:
        if account_name:
            raise CLIAbort(
                f"{label} requires re-authentication for {account_name}. "
                f"Run: icloud auth login --username {account_name}"
            ) from err
        raise CLIAbort(
            f"{label} requires re-authentication. Run: icloud auth login."
        ) from err


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


def resolve_device(api: PyiCloudService, query: str, *, require_unique: bool = False):
    """Return a device matched by id or common display names."""

    lowered = query.strip().lower()
    devices = list(
        service_call("Find My", lambda: api.devices, account_name=api.account_name)
    )
    for device in devices:
        identifier = str(getattr(device, "id", "")).strip().lower()
        if identifier and identifier == lowered:
            return device

    matches = []
    seen_ids: set[str] = set()
    for device in devices:
        identifier = str(getattr(device, "id", "")).strip()
        candidates = [
            getattr(device, "name", ""),
            getattr(device, "deviceDisplayName", ""),
        ]
        if not any(
            str(candidate).strip().lower() == lowered for candidate in candidates
        ):
            continue
        dedupe_key = identifier or str(id(device))
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        matches.append(device)

    if not matches:
        raise CLIAbort(f"No device matched '{query}'.")
    if require_unique and len(matches) > 1:
        options = "\n".join(
            "  - "
            f"{getattr(device, 'id', '')} "
            f"({getattr(device, 'name', '')} / "
            f"{getattr(device, 'deviceDisplayName', '')})"
            for device in matches
        )
        raise CLIAbort(
            f"Multiple devices matched '{query}'. Use a device id instead.\n{options}"
        )
    return matches[0]


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


def _write_to_file(response: Any, file_out) -> None:
    """Write a download response to a file, streaming if possible."""
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


def write_response_to_path(response: Any, output: Path) -> None:
    """Stream a download response to disk."""

    can_stream = hasattr(response, "iter_content") or (
        hasattr(response, "raw") and hasattr(response.raw, "read")
    )
    if not can_stream:
        raise CLIAbort("The download response could not be streamed.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as file_out:
        _write_to_file(response, file_out)
