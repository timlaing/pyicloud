"""Shared Typer option profiles for CLI leaf commands."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, TypeVar

import click
import typer

from .context import COMMAND_OPTIONS_META_KEY, LogLevel
from .output import OutputFormat

CommandCallback = TypeVar("CommandCallback", bound=Callable[..., Any])

ACCOUNT_CONTEXT_PANEL = "Account Context"
AUTHENTICATION_PANEL = "Authentication"
NETWORK_PANEL = "Network"
OUTPUT_DIAGNOSTICS_PANEL = "Output & Diagnostics"
DEVICES_PANEL = "Devices"

USERNAME_OPTION_HELP = "Apple ID username."
PASSWORD_OPTION_HELP = (
    "Apple ID password. If omitted, pyicloud will use the system keyring or prompt "
    "interactively."
)
CHINA_MAINLAND_OPTION_HELP = "Use China mainland Apple web service endpoints."
INTERACTIVE_OPTION_HELP = "Enable or disable interactive prompts."
ACCEPT_TERMS_OPTION_HELP = "Automatically accept pending Apple iCloud web terms."
WITH_FAMILY_OPTION_HELP = "Include family devices in Find My device listings."
SESSION_DIR_OPTION_HELP = "Directory to store session and cookie files."
HTTP_PROXY_OPTION_HELP = "HTTP proxy URL for requests."
HTTPS_PROXY_OPTION_HELP = "HTTPS proxy URL for requests."
NO_VERIFY_SSL_OPTION_HELP = "Disable SSL verification for requests."
LOG_LEVEL_OPTION_HELP = "Logging level for pyicloud internals."
OUTPUT_FORMAT_OPTION_HELP = "Output format for command results."

PROFILE_ACCOUNT_CONTEXT = "account_context"
PROFILE_AUTHENTICATION = "authentication"
PROFILE_NETWORK = "network"
PROFILE_OUTPUT_DIAGNOSTICS = "output_diagnostics"
PROFILE_DEVICES = "devices"


def _parameter(
    name: str,
    annotation: Any,
    default: Any,
) -> inspect.Parameter:
    return inspect.Parameter(
        name,
        inspect.Parameter.KEYWORD_ONLY,
        annotation=annotation,
        default=default,
    )


def _account_context_parameters() -> list[inspect.Parameter]:
    return [
        _parameter(
            "username",
            str | None,
            typer.Option(
                None,
                "--username",
                help=USERNAME_OPTION_HELP,
                rich_help_panel=ACCOUNT_CONTEXT_PANEL,
            ),
        ),
        _parameter(
            "session_dir",
            str | None,
            typer.Option(
                None,
                "--session-dir",
                help=SESSION_DIR_OPTION_HELP,
                rich_help_panel=ACCOUNT_CONTEXT_PANEL,
            ),
        ),
    ]


def _authentication_parameters() -> list[inspect.Parameter]:
    return [
        _parameter(
            "password",
            str | None,
            typer.Option(
                None,
                "--password",
                help=PASSWORD_OPTION_HELP,
                rich_help_panel=AUTHENTICATION_PANEL,
            ),
        ),
        _parameter(
            "china_mainland",
            bool | None,
            typer.Option(
                None,
                "--china-mainland",
                help=CHINA_MAINLAND_OPTION_HELP,
                rich_help_panel=AUTHENTICATION_PANEL,
            ),
        ),
        _parameter(
            "interactive",
            bool,
            typer.Option(
                True,
                "--interactive/--non-interactive",
                help=INTERACTIVE_OPTION_HELP,
                rich_help_panel=AUTHENTICATION_PANEL,
            ),
        ),
        _parameter(
            "accept_terms",
            bool,
            typer.Option(
                False,
                "--accept-terms",
                help=ACCEPT_TERMS_OPTION_HELP,
                rich_help_panel=AUTHENTICATION_PANEL,
            ),
        ),
    ]


def _network_parameters() -> list[inspect.Parameter]:
    return [
        _parameter(
            "http_proxy",
            str | None,
            typer.Option(
                None,
                "--http-proxy",
                help=HTTP_PROXY_OPTION_HELP,
                rich_help_panel=NETWORK_PANEL,
            ),
        ),
        _parameter(
            "https_proxy",
            str | None,
            typer.Option(
                None,
                "--https-proxy",
                help=HTTPS_PROXY_OPTION_HELP,
                rich_help_panel=NETWORK_PANEL,
            ),
        ),
        _parameter(
            "no_verify_ssl",
            bool,
            typer.Option(
                False,
                "--no-verify-ssl",
                help=NO_VERIFY_SSL_OPTION_HELP,
                rich_help_panel=NETWORK_PANEL,
            ),
        ),
    ]


def _output_diagnostics_parameters() -> list[inspect.Parameter]:
    return [
        _parameter(
            "output_format",
            OutputFormat,
            typer.Option(
                OutputFormat.TEXT,
                "--format",
                case_sensitive=False,
                help=OUTPUT_FORMAT_OPTION_HELP,
                rich_help_panel=OUTPUT_DIAGNOSTICS_PANEL,
            ),
        ),
        _parameter(
            "log_level",
            LogLevel,
            typer.Option(
                LogLevel.WARNING,
                "--log-level",
                case_sensitive=False,
                help=LOG_LEVEL_OPTION_HELP,
                rich_help_panel=OUTPUT_DIAGNOSTICS_PANEL,
            ),
        ),
    ]


def _device_parameters() -> list[inspect.Parameter]:
    return [
        _parameter(
            "with_family",
            bool,
            typer.Option(
                False,
                "--with-family",
                help=WITH_FAMILY_OPTION_HELP,
                rich_help_panel=DEVICES_PANEL,
            ),
        ),
    ]


PROFILE_FACTORIES: dict[str, Callable[[], list[inspect.Parameter]]] = {
    PROFILE_ACCOUNT_CONTEXT: _account_context_parameters,
    PROFILE_AUTHENTICATION: _authentication_parameters,
    PROFILE_NETWORK: _network_parameters,
    PROFILE_OUTPUT_DIAGNOSTICS: _output_diagnostics_parameters,
    PROFILE_DEVICES: _device_parameters,
}


def _profile_parameters(*profiles: str) -> list[inspect.Parameter]:
    parameters: list[inspect.Parameter] = []
    seen: set[str] = set()
    for profile in profiles:
        for parameter in PROFILE_FACTORIES[profile]():
            if parameter.name in seen:
                continue
            seen.add(parameter.name)
            parameters.append(parameter)
    return parameters


def with_option_profiles(*profiles: str) -> Callable[[CommandCallback], CommandCallback]:
    """Inject the given command-local option profiles onto a leaf command."""

    def decorator(fn: CommandCallback) -> CommandCallback:
        signature = inspect.signature(fn)
        extra_parameters = _profile_parameters(*profiles)
        parameter_names = [parameter.name for parameter in extra_parameters]

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            ctx = kwargs.get("ctx")
            if ctx is None:
                ctx = next(
                    (arg for arg in args if isinstance(arg, click.Context)),
                    None,
                )
            if ctx is None:
                raise RuntimeError("CLI context was not provided.")

            command_options = ctx.meta.setdefault(COMMAND_OPTIONS_META_KEY, {})
            for name in parameter_names:
                if name in kwargs:
                    command_options[name] = kwargs.pop(name)
            return fn(*args, **kwargs)

        wrapper.__signature__ = signature.replace(
            parameters=list(signature.parameters.values()) + extra_parameters
        )
        return wrapper  # type: ignore[return-value]

    return decorator


with_auth_login_options = with_option_profiles(
    PROFILE_ACCOUNT_CONTEXT,
    PROFILE_AUTHENTICATION,
    PROFILE_NETWORK,
    PROFILE_OUTPUT_DIAGNOSTICS,
)
with_auth_session_options = with_option_profiles(
    PROFILE_ACCOUNT_CONTEXT,
    PROFILE_NETWORK,
    PROFILE_OUTPUT_DIAGNOSTICS,
)
with_service_command_options = with_option_profiles(
    PROFILE_ACCOUNT_CONTEXT,
    PROFILE_NETWORK,
    PROFILE_OUTPUT_DIAGNOSTICS,
)
with_devices_command_options = with_option_profiles(
    PROFILE_ACCOUNT_CONTEXT,
    PROFILE_NETWORK,
    PROFILE_OUTPUT_DIAGNOSTICS,
    PROFILE_DEVICES,
)
with_keyring_delete_options = with_option_profiles(
    PROFILE_ACCOUNT_CONTEXT,
    PROFILE_OUTPUT_DIAGNOSTICS,
)
