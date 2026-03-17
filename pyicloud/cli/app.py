"""Typer-based command line interface for pyicloud."""

from __future__ import annotations

import typer

from pyicloud.cli.commands.account import app as account_app
from pyicloud.cli.commands.auth import app as auth_app
from pyicloud.cli.commands.calendar import app as calendar_app
from pyicloud.cli.commands.contacts import app as contacts_app
from pyicloud.cli.commands.devices import app as devices_app
from pyicloud.cli.commands.drive import app as drive_app
from pyicloud.cli.commands.hidemyemail import app as hidemyemail_app
from pyicloud.cli.commands.notes import app as notes_app
from pyicloud.cli.commands.photos import app as photos_app
from pyicloud.cli.commands.reminders import app as reminders_app
from pyicloud.cli.context import CLIAbort, CLIInvocationDefaults, CLIState, LogLevel
from pyicloud.cli.options import (
    ACCEPT_TERMS_OPTION_HELP,
    CHINA_MAINLAND_OPTION_HELP,
    INTERACTIVE_OPTION_HELP,
    LOG_LEVEL_OPTION_HELP,
    NO_VERIFY_SSL_OPTION_HELP,
    PASSWORD_OPTION_HELP,
    ROOT_OUTPUT_FORMAT_OPTION_HELP,
    ROOT_USERNAME_OPTION_HELP,
    SESSION_DIR_OPTION_HELP,
    WITH_FAMILY_OPTION_HELP,
)
from pyicloud.cli.output import OutputFormat

app = typer.Typer(
    help=(
        "Command line interface for pyicloud services. Execution-context options "
        "may be provided either before the command or on the final command."
    ),
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _group_root(ctx: typer.Context) -> None:
    """Show mounted group help when invoked without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


app.add_typer(account_app, name="account", invoke_without_command=True, callback=_group_root)
app.add_typer(auth_app, name="auth", invoke_without_command=True, callback=_group_root)
app.add_typer(devices_app, name="devices", invoke_without_command=True, callback=_group_root)
app.add_typer(calendar_app, name="calendar", invoke_without_command=True, callback=_group_root)
app.add_typer(contacts_app, name="contacts", invoke_without_command=True, callback=_group_root)
app.add_typer(drive_app, name="drive", invoke_without_command=True, callback=_group_root)
app.add_typer(photos_app, name="photos", invoke_without_command=True, callback=_group_root)
app.add_typer(
    hidemyemail_app, name="hidemyemail", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    reminders_app, name="reminders", invoke_without_command=True, callback=_group_root
)
app.add_typer(notes_app, name="notes", invoke_without_command=True, callback=_group_root)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    username: str = typer.Option(
        "",
        "--username",
        help=ROOT_USERNAME_OPTION_HELP,
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        help=PASSWORD_OPTION_HELP,
    ),
    china_mainland: bool = typer.Option(
        False,
        "--china-mainland",
        help=CHINA_MAINLAND_OPTION_HELP,
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--non-interactive",
        help=INTERACTIVE_OPTION_HELP,
    ),
    delete_from_keyring: bool = typer.Option(
        False,
        "--delete-from-keyring",
        help="Delete the stored password for --username and exit if no command is given.",
    ),
    accept_terms: bool = typer.Option(
        False,
        "--accept-terms",
        help=ACCEPT_TERMS_OPTION_HELP,
    ),
    with_family: bool = typer.Option(
        False,
        "--with-family",
        help=WITH_FAMILY_OPTION_HELP,
    ),
    session_dir: str | None = typer.Option(
        None,
        "--session-dir",
        help=SESSION_DIR_OPTION_HELP,
    ),
    http_proxy: str | None = typer.Option(None, "--http-proxy"),
    https_proxy: str | None = typer.Option(None, "--https-proxy"),
    no_verify_ssl: bool = typer.Option(
        False,
        "--no-verify-ssl",
        help=NO_VERIFY_SSL_OPTION_HELP,
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.WARNING,
        "--log-level",
        case_sensitive=False,
        help=LOG_LEVEL_OPTION_HELP,
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT,
        "--format",
        case_sensitive=False,
        help=ROOT_OUTPUT_FORMAT_OPTION_HELP,
    ),
) -> None:
    """Initialize shared CLI state."""

    defaults = CLIInvocationDefaults(
        username=username,
        password=password,
        china_mainland=china_mainland,
        interactive=interactive,
        delete_from_keyring=delete_from_keyring,
        accept_terms=accept_terms,
        with_family=with_family,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        log_level=log_level,
        output_format=output_format,
    )
    ctx.obj = defaults

    if delete_from_keyring:
        state = CLIState.from_invocation(defaults)
        state.open()
        try:
            deleted = state.delete_stored_password()
        finally:
            state.close()
        if ctx.invoked_subcommand is None:
            state.console.print(
                "Deleted stored password from keyring."
                if deleted
                else "No stored password was found for that username."
            )
            raise typer.Exit()

    if ctx.invoked_subcommand is None:
        state.console.print(ctx.get_help())
        raise typer.Exit()


def main() -> int:
    """Run the Typer application."""

    try:
        app()
    except CLIAbort as err:
        typer.echo(str(err), err=True)
        return 1
    return 0
