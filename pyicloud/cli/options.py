"""Shared Typer option aliases and helpers for CLI leaf commands."""

from __future__ import annotations

from typing import Annotated

import typer

from .context import COMMAND_OPTIONS_META_KEY, LogLevel
from .output import OutputFormat

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

DEFAULT_LOG_LEVEL = LogLevel.WARNING
DEFAULT_OUTPUT_FORMAT = OutputFormat.TEXT

UsernameOption = Annotated[
    str | None,
    typer.Option(
        "--username",
        help=USERNAME_OPTION_HELP,
        rich_help_panel=ACCOUNT_CONTEXT_PANEL,
    ),
]
SessionDirOption = Annotated[
    str | None,
    typer.Option(
        "--session-dir",
        help=SESSION_DIR_OPTION_HELP,
        rich_help_panel=ACCOUNT_CONTEXT_PANEL,
    ),
]
PasswordOption = Annotated[
    str | None,
    typer.Option(
        "--password",
        help=PASSWORD_OPTION_HELP,
        rich_help_panel=AUTHENTICATION_PANEL,
    ),
]
ChinaMainlandOption = Annotated[
    bool | None,
    typer.Option(
        "--china-mainland",
        help=CHINA_MAINLAND_OPTION_HELP,
        rich_help_panel=AUTHENTICATION_PANEL,
    ),
]
InteractiveOption = Annotated[
    bool,
    typer.Option(
        "--interactive/--non-interactive",
        help=INTERACTIVE_OPTION_HELP,
        rich_help_panel=AUTHENTICATION_PANEL,
    ),
]
AcceptTermsOption = Annotated[
    bool,
    typer.Option(
        "--accept-terms",
        help=ACCEPT_TERMS_OPTION_HELP,
        rich_help_panel=AUTHENTICATION_PANEL,
    ),
]
HttpProxyOption = Annotated[
    str | None,
    typer.Option(
        "--http-proxy",
        help=HTTP_PROXY_OPTION_HELP,
        rich_help_panel=NETWORK_PANEL,
    ),
]
HttpsProxyOption = Annotated[
    str | None,
    typer.Option(
        "--https-proxy",
        help=HTTPS_PROXY_OPTION_HELP,
        rich_help_panel=NETWORK_PANEL,
    ),
]
NoVerifySslOption = Annotated[
    bool,
    typer.Option(
        "--no-verify-ssl",
        help=NO_VERIFY_SSL_OPTION_HELP,
        rich_help_panel=NETWORK_PANEL,
    ),
]
OutputFormatOption = Annotated[
    OutputFormat,
    typer.Option(
        "--format",
        case_sensitive=False,
        help=OUTPUT_FORMAT_OPTION_HELP,
        rich_help_panel=OUTPUT_DIAGNOSTICS_PANEL,
    ),
]
LogLevelOption = Annotated[
    LogLevel,
    typer.Option(
        "--log-level",
        case_sensitive=False,
        help=LOG_LEVEL_OPTION_HELP,
        rich_help_panel=OUTPUT_DIAGNOSTICS_PANEL,
    ),
]
WithFamilyOption = Annotated[
    bool,
    typer.Option(
        "--with-family",
        help=WITH_FAMILY_OPTION_HELP,
        rich_help_panel=DEVICES_PANEL,
    ),
]


def store_command_options(
    ctx: typer.Context,
    *,
    username: str | None = None,
    password: str | None = None,
    china_mainland: bool | None = None,
    interactive: bool = True,
    accept_terms: bool = False,
    with_family: bool = False,
    session_dir: str | None = None,
    http_proxy: str | None = None,
    https_proxy: str | None = None,
    no_verify_ssl: bool = False,
    log_level: LogLevel = DEFAULT_LOG_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
) -> None:
    """Persist leaf-command options into the Typer context for CLI state setup."""

    ctx.meta[COMMAND_OPTIONS_META_KEY] = {
        "username": username,
        "password": password,
        "china_mainland": china_mainland,
        "interactive": interactive,
        "accept_terms": accept_terms,
        "with_family": with_family,
        "session_dir": session_dir,
        "http_proxy": http_proxy,
        "https_proxy": https_proxy,
        "no_verify_ssl": no_verify_ssl,
        "log_level": log_level,
        "output_format": output_format,
    }
