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
from pyicloud.cli.commands.photos import app as photos_app
from pyicloud.cli.context import CLIAbort

app = typer.Typer(
    help="Command line interface for pyicloud services.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _group_root(ctx: typer.Context) -> None:
    """Show mounted group help when invoked without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


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


def main() -> int:
    """Run the Typer application."""

    try:
        app()
    except CLIAbort as err:
        typer.echo(str(err), err=True)
        return 1
    return 0
