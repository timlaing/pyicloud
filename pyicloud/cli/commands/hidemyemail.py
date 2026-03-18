"""Hide My Email commands."""

from __future__ import annotations

import typer

from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import normalize_alias
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

app = typer.Typer(help="Manage Hide My Email aliases.")


def _require_generated_alias(alias: str | None) -> str:
    """Return a generated alias or abort on an empty response."""

    if isinstance(alias, str) and alias:
        return alias
    raise CLIAbort("Hide My Email generate returned an empty alias.")


def _require_mutation_result(payload: dict, operation: str) -> str:
    """Return the alias id from a successful mutator response."""

    anonymous_id = payload.get("anonymousId")
    if isinstance(anonymous_id, str) and anonymous_id:
        return anonymous_id
    raise CLIAbort(
        f"Hide My Email {operation} returned an invalid response: {payload!r}"
    )


@app.command("list")
def hidemyemail_list(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List Hide My Email aliases."""

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
    payload = service_call(
        "Hide My Email",
        lambda: [normalize_alias(alias) for alias in api.hidemyemail],
        account_name=api.account_name,
    )
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
def hidemyemail_generate(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Generate a new relay address."""

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
    alias = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.generate(),
        account_name=api.account_name,
    )
    alias = _require_generated_alias(alias)
    payload = {"email": alias}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(alias)


@app.command("reserve")
def hidemyemail_reserve(
    ctx: typer.Context,
    email: str = typer.Argument(...),
    label: str = typer.Argument(...),
    note: str = typer.Option("Generated", "--note", help="Alias note."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Reserve a generated relay address with metadata."""

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
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.reserve(email=email, label=label, note=note),
        account_name=api.account_name,
    )
    reserved_id = _require_mutation_result(payload, "reserve")
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(reserved_id)


@app.command("update")
def hidemyemail_update(
    ctx: typer.Context,
    anonymous_id: str = typer.Argument(...),
    label: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note", help="Alias note."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Update alias metadata."""

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
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.update_metadata(anonymous_id, label, note),
        account_name=api.account_name,
    )
    updated_id = _require_mutation_result(payload, "update")
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Updated {updated_id}")


@app.command("deactivate")
def hidemyemail_deactivate(
    ctx: typer.Context,
    anonymous_id: str = typer.Argument(...),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Deactivate an alias."""

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
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.deactivate(anonymous_id),
        account_name=api.account_name,
    )
    deactivated_id = _require_mutation_result(payload, "deactivate")
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deactivated {deactivated_id}")


@app.command("reactivate")
def hidemyemail_reactivate(
    ctx: typer.Context,
    anonymous_id: str = typer.Argument(...),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Reactivate an alias."""

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
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.reactivate(anonymous_id),
        account_name=api.account_name,
    )
    reactivated_id = _require_mutation_result(payload, "reactivate")
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Reactivated {reactivated_id}")


@app.command("delete")
def hidemyemail_delete(
    ctx: typer.Context,
    anonymous_id: str = typer.Argument(...),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete an alias."""

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
    payload = service_call(
        "Hide My Email",
        lambda: api.hidemyemail.delete(anonymous_id),
        account_name=api.account_name,
    )
    deleted_id = _require_mutation_result(payload, "delete")
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deleted {deleted_id}")
