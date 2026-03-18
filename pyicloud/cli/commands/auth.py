"""Authentication and session commands."""

from __future__ import annotations

import typer

from pyicloud.cli.context import CLIAbort, get_state
from pyicloud.cli.options import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_OUTPUT_FORMAT,
    AcceptTermsOption,
    ChinaMainlandOption,
    HttpProxyOption,
    HttpsProxyOption,
    InteractiveOption,
    LogLevelOption,
    NoVerifySslOption,
    OutputFormatOption,
    PasswordOption,
    SessionDirOption,
    UsernameOption,
    store_command_options,
)
from pyicloud.cli.output import console_kv_table, console_table

app = typer.Typer(help="Manage authentication and sessions.")
keyring_app = typer.Typer(help="Manage stored keyring credentials.")


def _group_root(ctx: typer.Context) -> None:
    """Show subgroup help when invoked without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


app.add_typer(
    keyring_app,
    name="keyring",
    invoke_without_command=True,
    callback=_group_root,
)


def _storage_path_display(path: object, exists: object) -> object:
    """Render a canonical storage path with an inline missing marker for text output."""

    if exists:
        return path
    return f"{path} (missing)"


def _auth_status_rows(payload: dict[str, object]) -> list[tuple[str, object]]:
    """Build text-mode rows for one auth status payload."""

    return [
        ("Account", payload["account_name"]),
        ("Authenticated", payload["authenticated"]),
        ("Trusted Session", payload["trusted_session"]),
        ("Requires 2FA", payload["requires_2fa"]),
        ("Requires 2SA", payload["requires_2sa"]),
        ("Password in Keyring", payload["has_keyring_password"]),
        (
            "Session File",
            _storage_path_display(
                payload["session_path"],
                payload["has_session_file"],
            ),
        ),
        (
            "Cookie Jar",
            _storage_path_display(
                payload["cookiejar_path"],
                payload["has_cookiejar_file"],
            ),
        ),
    ]


def _auth_payload(state, api, status: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "account_name": api.account_name,
        "has_keyring_password": state.has_keyring_password(api.account_name),
        **state.auth_storage_info(api),
        **status,
    }
    return payload


@app.command("status")
def auth_status(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show the current authentication and session status."""

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
    if not state.has_explicit_username:
        active_probes = state.active_session_probes()
        if not active_probes:
            if state.json_output:
                state.write_json({"authenticated": False, "accounts": []})
                return
            state.console.print(state.not_logged_in_message())
            return

        payloads = [_auth_payload(state, api, status) for api, status in active_probes]
        if state.json_output:
            if len(payloads) == 1:
                state.write_json(payloads[0])
            else:
                state.write_json({"authenticated": True, "accounts": payloads})
            return
        if len(payloads) == 1:
            payload = payloads[0]
            state.console.print(
                console_kv_table(
                    "Auth Status",
                    _auth_status_rows(payload),
                )
            )
            return
        state.console.print(
            console_table(
                "Active iCloud Sessions",
                [
                    "Account",
                    "Trusted Session",
                    "Password in Keyring",
                    "Session File Exists",
                    "Cookie Jar Exists",
                ],
                [
                    (
                        payload["account_name"],
                        payload["trusted_session"],
                        payload["has_keyring_password"],
                        payload["has_session_file"],
                        payload["has_cookiejar_file"],
                    )
                    for payload in payloads
                ],
            )
        )
        return

    api = state.get_probe_api()
    status = api.get_auth_status()
    if status["authenticated"]:
        state.remember_account(api)
    else:
        state.prune_local_accounts()
    payload = _auth_payload(state, api, status)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_kv_table(
            "Auth Status",
            _auth_status_rows(payload),
        )
    )


@app.command("login")
def auth_login(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    password: PasswordOption = None,
    china_mainland: ChinaMainlandOption = None,
    interactive: InteractiveOption = True,
    accept_terms: AcceptTermsOption = False,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Authenticate and persist a usable session."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        password=password,
        china_mainland=china_mainland,
        interactive=interactive,
        accept_terms=accept_terms,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)
    api = state.get_login_api()
    payload = _auth_payload(
        state,
        api,
        {
            "authenticated": True,
            "trusted_session": api.is_trusted_session,
            "requires_2fa": api.requires_2fa,
            "requires_2sa": api.requires_2sa,
        },
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print("Authenticated session is ready.")
    state.console.print(
        console_kv_table(
            "Auth Session",
            [
                ("Account", payload["account_name"]),
                ("Trusted Session", payload["trusted_session"]),
                ("Session File", payload["session_path"]),
                ("Cookie Jar", payload["cookiejar_path"]),
            ],
        )
    )


@app.command("logout")
def auth_logout(
    ctx: typer.Context,
    keep_trusted: bool = typer.Option(
        False,
        "--keep-trusted",
        help="Preserve trusted-browser state for the next login.",
    ),
    all_sessions: bool = typer.Option(
        False,
        "--all-sessions",
        help="Attempt to sign out all browser sessions.",
    ),
    remove_keyring: bool = typer.Option(
        False,
        "--remove-keyring",
        help="Delete the stored password for the selected account after logout.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Log out and clear local session persistence."""

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
    if state.has_explicit_username:
        api = state.get_probe_api()
        api.get_auth_status()
    else:
        active_probes = state.active_session_probes()
        if not active_probes:
            if state.json_output:
                state.write_json({"authenticated": False, "accounts": []})
                return
            state.console.print(state.not_logged_in_message())
            return
        if len(active_probes) > 1:
            raise CLIAbort(
                state.multiple_logged_in_accounts_message(
                    [api.account_name for api, _status in active_probes]
                )
            )
        api, _status = active_probes[0]

    state.remember_account(api)
    try:
        result = api.logout(
            keep_trusted=keep_trusted,
            all_sessions=all_sessions,
            clear_local_session=True,
        )
    except OSError as exc:
        raise CLIAbort("Failed to clear local session state.") from exc

    keyring_removed = False
    if remove_keyring:
        keyring_removed = state.delete_keyring_password(api.account_name)
    state.prune_local_accounts()

    payload = {
        "account_name": api.account_name,
        "session_path": api.session.session_path,
        "cookiejar_path": api.session.cookiejar_path,
        "stored_password_removed": keyring_removed,
        **result,
    }
    if state.json_output:
        state.write_json(payload)
        return
    if payload["remote_logout_confirmed"] and keyring_removed:
        state.console.print(
            "Logged out, cleared local session, and removed stored password."
        )
    elif payload["remote_logout_confirmed"]:
        state.console.print("Logged out and cleared local session.")
    elif keyring_removed:
        state.console.print(
            "Cleared local session, removed stored password; remote logout was not confirmed."
        )
    else:
        state.console.print("Cleared local session; remote logout was not confirmed.")


@keyring_app.command("delete")
def auth_keyring_delete(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete a stored keyring password."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)
    if not state.has_explicit_username:
        raise CLIAbort("The --username option is required for auth keyring delete.")

    deleted = state.delete_keyring_password(state.username)
    payload = {
        "account_name": state.username,
        "stored_password_removed": deleted,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        "Deleted stored password from keyring."
        if deleted
        else "No stored password was found for that account."
    )
