"""Contacts commands."""

from __future__ import annotations

from itertools import islice

import typer

from pyicloud.cli.context import get_state, service_call
from pyicloud.cli.normalize import normalize_contact, normalize_me
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table

app = typer.Typer(help="Inspect iCloud contacts.")


@app.command("list")
@with_service_command_options
def contacts_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum contacts to show."),
) -> None:
    """List contacts."""

    state = get_state(ctx)
    api = state.get_api()
    payload = [
        normalize_contact(contact)
        for contact in islice(
            service_call("Contacts", lambda: api.contacts.all) or [],
            limit,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Contacts",
            ["First", "Last", "Phones", "Emails"],
            [
                (
                    contact["first_name"],
                    contact["last_name"],
                    ", ".join(contact["phones"]),
                    ", ".join(contact["emails"]),
                )
                for contact in payload
            ],
        )
    )


@app.command("me")
@with_service_command_options
def contacts_me(ctx: typer.Context) -> None:
    """Show the signed-in contact card."""

    state = get_state(ctx)
    api = state.get_api()
    payload = normalize_me(service_call("Contacts", lambda: api.contacts.me))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"{payload['first_name']} {payload['last_name']}")
    if payload["photo"]:
        state.console.print(f"Photo URL: {payload['photo'].get('url')}")
