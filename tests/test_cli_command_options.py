"""Shared option bundles retain Typer parsing, defaults, and validation order."""

import inspect
from typing import Annotated, Any
from unittest.mock import patch

import pytest
import typer
from typer.core import TyperCommand
from typer.testing import CliRunner

from pyicloud.cli.command_options import CommandOptions, with_command_options
from pyicloud.cli.context import COMMAND_OPTIONS_META_KEY, LogLevel
from pyicloud.cli.output import OutputFormat


@pytest.mark.parametrize("explicit", [False, True])
def test_common_options_preserve_cli_flags(explicit: bool) -> None:
    """The wrapper forwards specific parameters and stores all common choices."""
    app = typer.Typer()
    received: list[dict[str, Any]] = []

    @app.command()
    @with_command_options
    def command(
        ctx: typer.Context,
        common: CommandOptions,
        value: Annotated[str, typer.Option("--value")] = "synthetic",
    ) -> None:
        """Capture parsed options without authentication."""
        common.store(ctx)
        received.append({"value": value, **ctx.meta[COMMAND_OPTIONS_META_KEY]})

    arguments = []
    if explicit:
        arguments = [
            "--value",
            "chosen",
            "--username",
            "test@example.test",
            "--session-dir",
            "synthetic-session",
            "--http-proxy",
            "http://example.test",
            "--https-proxy",
            "https://example.test",
            "--no-verify-ssl",
            "--format",
            "json",
            "--log-level",
            "debug",
        ]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    expected = CommandOptions()
    if explicit:
        expected = CommandOptions(
            username="test@example.test",
            session_dir="synthetic-session",
            http_proxy="http://example.test",
            https_proxy="https://example.test",
            no_verify_ssl=True,
            output_format=OutputFormat.JSON,
            log_level=LogLevel.DEBUG,
        )
    context = typer.Context(TyperCommand("expected"))
    expected.store(context)
    assert received == [
        {
            "value": "chosen" if explicit else "synthetic",
            **context.meta[COMMAND_OPTIONS_META_KEY],
        }
    ]
    assert "common" not in inspect.signature(command).parameters
    help_result = CliRunner().invoke(app, ["--help"])
    assert help_result.exit_code == 0


def test_common_options_can_wait_until_after_validation() -> None:
    """Bundling options does not perform context setup ahead of local checks."""
    app = typer.Typer()

    @app.command()
    @with_command_options
    def command(ctx: typer.Context, common: CommandOptions) -> None:
        """Reject local input before using the common context."""
        raise ValueError("local validation")

    with patch("pyicloud.cli.command_options.store_command_options") as store:
        result = CliRunner().invoke(app, [])
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "local validation"
    store.assert_not_called()
