"""Account commands."""

from __future__ import annotations

import typer

from pyicloud.cli.context import get_state, service_call
from pyicloud.cli.normalize import (
    normalize_account_device,
    normalize_account_summary,
    normalize_family_member,
    normalize_storage,
)
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table

app = typer.Typer(help="Inspect iCloud account metadata.")


@app.command("summary")
@with_service_command_options
def account_summary(ctx: typer.Context) -> None:
    """Show high-level account information."""

    state = get_state(ctx)
    api = state.get_api()
    account = service_call(
        "Account", lambda: api.account, account_name=api.account_name
    )
    payload = normalize_account_summary(api, account)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Account: {payload['account_name']}")
    state.console.print(f"Devices: {payload['devices_count']}")
    state.console.print(f"Family members: {payload['family_count']}")
    state.console.print(
        f"Storage: {payload['used_storage_percent']}% used "
        f"({payload['used_storage_bytes']} / {payload['total_storage_bytes']} bytes)"
    )


@app.command("devices")
@with_service_command_options
def account_devices(ctx: typer.Context) -> None:
    """List devices associated with the account profile."""

    state = get_state(ctx)
    api = state.get_api()
    payload = [
        normalize_account_device(device)
        for device in service_call(
            "Account",
            lambda: api.account.devices,
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Account Devices",
            ["Name", "Model", "Device Class", "ID"],
            [
                (
                    device["name"],
                    device["model_display_name"],
                    device["device_class"],
                    device["id"],
                )
                for device in payload
            ],
        )
    )


@app.command("family")
@with_service_command_options
def account_family(ctx: typer.Context) -> None:
    """List family sharing members."""

    state = get_state(ctx)
    api = state.get_api()
    payload = [
        normalize_family_member(member)
        for member in service_call(
            "Account",
            lambda: api.account.family,
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Family Members",
            ["Name", "Apple ID", "Age", "Parent"],
            [
                (
                    member["full_name"],
                    member["apple_id"],
                    member["age_classification"],
                    member["has_parental_privileges"],
                )
                for member in payload
            ],
        )
    )


@app.command("storage")
@with_service_command_options
def account_storage(ctx: typer.Context) -> None:
    """Show iCloud storage usage."""

    state = get_state(ctx)
    api = state.get_api()
    payload = normalize_storage(
        service_call(
            "Account", lambda: api.account.storage, account_name=api.account_name
        )
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        f"Used {payload['usage']['used_storage_in_percent']}% "
        f"of {payload['usage']['total_storage_in_bytes']} bytes."
    )
    state.console.print(
        console_table(
            "Storage Usage",
            ["Media", "Usage (bytes)", "Label"],
            [
                (key, usage["usage_in_bytes"], usage["label"])
                for key, usage in payload["usages_by_media"].items()
            ],
        )
    )
