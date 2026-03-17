"""Hide My Email commands."""

from __future__ import annotations

import typer

from pyicloud.cli.context import get_state, service_call
from pyicloud.cli.normalize import normalize_alias
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table

app = typer.Typer(help="Manage Hide My Email aliases.")


@app.command("list")
@with_service_command_options
def hidemyemail_list(ctx: typer.Context) -> None:
    """List Hide My Email aliases."""

    state = get_state(ctx)
    api = state.get_api()
    payload = [
        normalize_alias(alias)
        for alias in service_call("Hide My Email", lambda: api.hidemyemail)
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Hide My Email",
            ["Alias", "Label", "Anonymous ID"],
            [
                (alias["email"], alias["label"], alias["anonymous_id"])
                for alias in payload
            ],
        )
    )


@app.command("generate")
@with_service_command_options
def hidemyemail_generate(ctx: typer.Context) -> None:
    """Generate a new relay address."""

    state = get_state(ctx)
    api = state.get_api()
    alias = service_call("Hide My Email", lambda: api.hidemyemail.generate())
    payload = {"email": alias}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(alias or "")


@app.command("reserve")
@with_service_command_options
def hidemyemail_reserve(
    ctx: typer.Context,
    email: str = typer.Argument(...),
    label: str = typer.Argument(...),
    note: str = typer.Option("Generated", "--note", help="Alias note."),
) -> None:
    """Reserve a generated relay address with metadata."""

    state = get_state(ctx)
    api = state.get_api()
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.reserve(email=email, label=label, note=note),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(payload.get("anonymousId", "reserved"))


@app.command("update")
@with_service_command_options
def hidemyemail_update(
    ctx: typer.Context,
    anonymous_id: str = typer.Argument(...),
    label: str = typer.Argument(...),
    note: str = typer.Option("Generated", "--note", help="Alias note."),
) -> None:
    """Update alias metadata."""

    state = get_state(ctx)
    api = state.get_api()
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.update_metadata(anonymous_id, label, note),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Updated {anonymous_id}")


@app.command("deactivate")
@with_service_command_options
def hidemyemail_deactivate(
    ctx: typer.Context, anonymous_id: str = typer.Argument(...)
) -> None:
    """Deactivate an alias."""

    state = get_state(ctx)
    api = state.get_api()
    payload = service_call(
        "Hide My Email", lambda: api.hidemyemail.deactivate(anonymous_id)
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deactivated {anonymous_id}")


@app.command("reactivate")
@with_service_command_options
def hidemyemail_reactivate(
    ctx: typer.Context, anonymous_id: str = typer.Argument(...)
) -> None:
    """Reactivate an alias."""

    state = get_state(ctx)
    api = state.get_api()
    payload = service_call(
        "Hide My Email", lambda: api.hidemyemail.reactivate(anonymous_id)
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Reactivated {anonymous_id}")


@app.command("delete")
@with_service_command_options
def hidemyemail_delete(
    ctx: typer.Context, anonymous_id: str = typer.Argument(...)
) -> None:
    """Delete an alias."""

    state = get_state(ctx)
    api = state.get_api()
    payload = service_call(
        "Hide My Email", lambda: api.hidemyemail.delete(anonymous_id)
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deleted {anonymous_id}")
