"""Typer-based command line interface for pyicloud."""

from __future__ import annotations

import typer

from pyicloud.cli.commands.account import app as account_app
from pyicloud.cli.commands.calendar import app as calendar_app
from pyicloud.cli.commands.contacts import app as contacts_app
from pyicloud.cli.commands.devices import app as devices_app
from pyicloud.cli.commands.drive import app as drive_app
from pyicloud.cli.commands.hidemyemail import app as hidemyemail_app
from pyicloud.cli.commands.notes import app as notes_app
from pyicloud.cli.commands.photos import app as photos_app
from pyicloud.cli.commands.reminders import app as reminders_app
from pyicloud.cli.context import CLIAbort, CLIState, LogLevel
from pyicloud.cli.output import OutputFormat

app = typer.Typer(
    help="Command line interface for pyicloud services.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

app.add_typer(account_app, name="account")
app.add_typer(devices_app, name="devices")
app.add_typer(calendar_app, name="calendar")
app.add_typer(contacts_app, name="contacts")
app.add_typer(drive_app, name="drive")
app.add_typer(photos_app, name="photos")
app.add_typer(hidemyemail_app, name="hidemyemail")
app.add_typer(reminders_app, name="reminders")
app.add_typer(notes_app, name="notes")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    username: str = typer.Option("", "--username", help="Apple ID username."),
    password: str | None = typer.Option(
        None,
        "--password",
        help="Apple ID password. If omitted, pyicloud will use the system keyring or prompt interactively.",
    ),
    china_mainland: bool = typer.Option(
        False,
        "--china-mainland",
        help="Use China mainland Apple web service endpoints.",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--non-interactive",
        help="Enable or disable interactive prompts.",
    ),
    delete_from_keyring: bool = typer.Option(
        False,
        "--delete-from-keyring",
        help="Delete the stored password for --username and exit if no command is given.",
    ),
    accept_terms: bool = typer.Option(
        False,
        "--accept-terms",
        help="Automatically accept pending Apple iCloud web terms.",
    ),
    with_family: bool = typer.Option(
        False,
        "--with-family",
        help="Include family devices in Find My device listings.",
    ),
    session_dir: str | None = typer.Option(
        None,
        "--session-dir",
        help="Directory to store session and cookie files.",
    ),
    http_proxy: str | None = typer.Option(None, "--http-proxy"),
    https_proxy: str | None = typer.Option(None, "--https-proxy"),
    no_verify_ssl: bool = typer.Option(
        False,
        "--no-verify-ssl",
        help="Disable SSL verification for requests.",
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.WARNING,
        "--log-level",
        case_sensitive=False,
        help="Logging level for pyicloud internals.",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT,
        "--format",
        case_sensitive=False,
        help="Output format for command results.",
    ),
) -> None:
    """Initialize shared CLI state."""

    state = CLIState(
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
    state.open()
    ctx.call_on_close(state.close)
    ctx.obj = state

    if delete_from_keyring:
        deleted = state.delete_stored_password()
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
