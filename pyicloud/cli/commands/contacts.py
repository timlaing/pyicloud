"""Contacts commands."""

from __future__ import annotations

from itertools import islice

import typer

from pyicloud.cli.context import get_state, service_call
from pyicloud.cli.normalize import normalize_contact, normalize_me
from pyicloud.cli.options import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_OUTPUT_FORMAT,
    HttpProxyOption,
    HttpsProxyOption,
    LogLevelOption,
    NoVerifySslOption,
    OutputFormatOption,
    SessionDirOption,
    UsernameOption,
    store_command_options,
)
from pyicloud.cli.output import console_table

app = typer.Typer(help="Inspect iCloud contacts.")


@app.command("list")
def contacts_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum contacts to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List contacts."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
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
def contacts_me(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show the signed-in contact card."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)
    api = state.get_api()
    me_data = service_call("Contacts", lambda: api.contacts.me)
    if me_data is None:
        state.console.print("No contact card found.")
        raise typer.Exit(1)
    payload = normalize_me(me_data)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"{payload['first_name']} {payload['last_name']}")
    if payload["photo"]:
        photo = payload["photo"]
        url = photo.get("url") if isinstance(photo, dict) else photo
        if url:
            state.console.print(f"Photo URL: {url}")
