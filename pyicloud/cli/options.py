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
                help="Apple ID username.",
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
                help="Apple ID password.",
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
                help="Use China mainland Apple web service endpoints.",
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
                help="Enable or disable interactive prompts.",
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
                help="Automatically accept pending Apple iCloud web terms.",
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
                help="Include family devices in Find My device listings.",
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
                help="Directory to store session and cookie files.",
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
                help="Disable SSL verification for requests.",
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
                help="Logging level for pyicloud internals.",
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
                help="Output format for command results.",
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
