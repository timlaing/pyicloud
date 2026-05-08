"""Typer-based command line interface for pyicloud."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

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
from pyicloud.cli.context import CLIAbort

app = typer.Typer(
    help="Command line interface for pyicloud services.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _installed_version() -> str:
    """Return the installed pyicloud package version."""

    try:
        return package_version("pyicloud")
    except PackageNotFoundError:
        return "unknown"


def _version_callback(value: bool) -> None:
    """Print the installed pyicloud version and exit."""

    if value:
        typer.echo(_installed_version())
        raise typer.Exit()


def _group_root(ctx: typer.Context) -> None:
    """Show mounted group help when invoked without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.callback()
def root_callback(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed pyicloud version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Handle root CLI options before subcommand dispatch."""


app.add_typer(
    account_app, name="account", invoke_without_command=True, callback=_group_root
)
app.add_typer(auth_app, name="auth", invoke_without_command=True, callback=_group_root)
app.add_typer(
    devices_app, name="devices", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    calendar_app, name="calendar", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    contacts_app, name="contacts", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    drive_app, name="drive", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    photos_app, name="photos", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    hidemyemail_app,
    name="hidemyemail",
    invoke_without_command=True,
    callback=_group_root,
)
app.add_typer(
    reminders_app, name="reminders", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    notes_app, name="notes", invoke_without_command=True, callback=_group_root
)


def main() -> int:
    """Run the Typer application."""

    try:
        app()
    except CLIAbort as err:
        typer.echo(str(err), err=True)
        return 1
    return 0
