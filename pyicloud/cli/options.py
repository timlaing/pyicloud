"""Shared execution-context options for Typer CLI leaf commands."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

import click
import typer

from .context import EXECUTION_CONTEXT_OVERRIDES_META_KEY, LogLevel
from .output import OutputFormat

CommandCallback = TypeVar("CommandCallback", bound=Callable[..., Any])
EXECUTION_CONTEXT_PANEL = "Execution Context"
EXECUTION_CONTEXT_PLACEMENT_HELP = (
    " Can be provided before the command or on the final command."
)

USERNAME_OPTION_HELP = "Apple ID username."
ROOT_USERNAME_OPTION_HELP = (
    USERNAME_OPTION_HELP
    + EXECUTION_CONTEXT_PLACEMENT_HELP
    + " Optional when a command can infer a single account context."
)
PASSWORD_OPTION_HELP = (
    "Apple ID password. If omitted, pyicloud will use the system keyring or prompt interactively."
)
CHINA_MAINLAND_OPTION_HELP = "Use China mainland Apple web service endpoints."
INTERACTIVE_OPTION_HELP = "Enable or disable interactive prompts."
ACCEPT_TERMS_OPTION_HELP = "Automatically accept pending Apple iCloud web terms."
WITH_FAMILY_OPTION_HELP = "Include family devices in Find My device listings."
SESSION_DIR_OPTION_HELP = "Directory to store session and cookie files."
NO_VERIFY_SSL_OPTION_HELP = "Disable SSL verification for requests."
LOG_LEVEL_OPTION_HELP = "Logging level for pyicloud internals."
OUTPUT_FORMAT_OPTION_HELP = "Output format for command results."
ROOT_OUTPUT_FORMAT_OPTION_HELP = (
    OUTPUT_FORMAT_OPTION_HELP + EXECUTION_CONTEXT_PLACEMENT_HELP
)


def _execution_context_parameters() -> list[inspect.Parameter]:
    """Return shared final-command execution-context parameters."""

    return [
        inspect.Parameter(
            "username",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[str],
            default=typer.Option(
                None,
                "--username",
                help=USERNAME_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "password",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[str],
            default=typer.Option(
                None,
                "--password",
                help=PASSWORD_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "china_mainland",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[bool],
            default=typer.Option(
                None,
                "--china-mainland",
                help=CHINA_MAINLAND_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "interactive",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[bool],
            default=typer.Option(
                None,
                "--interactive/--non-interactive",
                help=INTERACTIVE_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "accept_terms",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[bool],
            default=typer.Option(
                None,
                "--accept-terms",
                help=ACCEPT_TERMS_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "with_family",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[bool],
            default=typer.Option(
                None,
                "--with-family",
                help=WITH_FAMILY_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "session_dir",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[str],
            default=typer.Option(
                None,
                "--session-dir",
                help=SESSION_DIR_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "http_proxy",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[str],
            default=typer.Option(
                None,
                "--http-proxy",
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "https_proxy",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[str],
            default=typer.Option(
                None,
                "--https-proxy",
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "no_verify_ssl",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[bool],
            default=typer.Option(
                None,
                "--no-verify-ssl",
                help=NO_VERIFY_SSL_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "log_level",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[LogLevel],
            default=typer.Option(
                None,
                "--log-level",
                case_sensitive=False,
                help=LOG_LEVEL_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
        inspect.Parameter(
            "output_format",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[OutputFormat],
            default=typer.Option(
                None,
                "--format",
                case_sensitive=False,
                help=OUTPUT_FORMAT_OPTION_HELP,
                rich_help_panel=EXECUTION_CONTEXT_PANEL,
            ),
        ),
    ]


def with_execution_context_options(fn: CommandCallback) -> CommandCallback:
    """Inject shared execution-context options onto a leaf command."""

    signature = inspect.signature(fn)
    extra_parameters = _execution_context_parameters()
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

        overrides = ctx.meta.setdefault(EXECUTION_CONTEXT_OVERRIDES_META_KEY, {})
        for name in parameter_names:
            value = kwargs.pop(name, None)
            if value is not None:
                overrides[name] = value
        return fn(*args, **kwargs)

    wrapper.__signature__ = signature.replace(
        parameters=list(signature.parameters.values()) + extra_parameters
    )
    return wrapper  # type: ignore[return-value]
