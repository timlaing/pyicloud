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

app = typer.Typer(help="Inspect iCloud account metadata.")


@app.command("summary")
def account_summary(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show high-level account information."""

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
def account_devices(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List devices associated with the account profile."""

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
def account_family(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List family sharing members."""

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
def account_storage(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show iCloud storage usage."""

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
