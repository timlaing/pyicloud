"""Authentication and session commands."""

from __future__ import annotations

import typer

from pyicloud.cli.context import CLIAbort, get_state
from pyicloud.cli.output import console_kv_table, console_table

app = typer.Typer(
    help="Manage authentication and sessions for the selected or inferred account."
)


def _auth_payload(state, api, status: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "account_name": api.account_name,
        "has_keyring_password": state.has_keyring_password(api.account_name),
        **state.auth_storage_info(api),
        **status,
    }
    return payload


@app.command("status")
def auth_status(ctx: typer.Context) -> None:
    """Show the current authentication and session status."""

    state = get_state(ctx)
    if not state.has_explicit_username:
        active_probes = state.active_session_probes()
        if not active_probes:
            if state.json_output:
                state.write_json({"authenticated": False, "accounts": []})
                return
            state.console.print(
                "You are not logged into any iCloud accounts. To log in, run: "
                "icloud --username <apple-id> auth login"
            )
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
                    [
                        ("Account", payload["account_name"]),
                        ("Authenticated", payload["authenticated"]),
                        ("Trusted Session", payload["trusted_session"]),
                        ("Requires 2FA", payload["requires_2fa"]),
                        ("Requires 2SA", payload["requires_2sa"]),
                        ("Stored Password", payload["has_keyring_password"]),
                        ("Session File", payload["session_path"]),
                        ("Session File Exists", payload["has_session_file"]),
                        ("Cookie Jar", payload["cookiejar_path"]),
                        ("Cookie Jar Exists", payload["has_cookiejar_file"]),
                    ],
                )
            )
            return
        state.console.print(
            console_table(
                "Active iCloud Sessions",
                [
                    "Account",
                    "Trusted Session",
                    "Stored Password",
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
            [
                ("Account", payload["account_name"]),
                ("Authenticated", payload["authenticated"]),
                ("Trusted Session", payload["trusted_session"]),
                ("Requires 2FA", payload["requires_2fa"]),
                ("Requires 2SA", payload["requires_2sa"]),
                ("Stored Password", payload["has_keyring_password"]),
                ("Session File", payload["session_path"]),
                ("Session File Exists", payload["has_session_file"]),
                ("Cookie Jar", payload["cookiejar_path"]),
                ("Cookie Jar Exists", payload["has_cookiejar_file"]),
            ],
        )
    )


@app.command("login")
def auth_login(ctx: typer.Context) -> None:
    """Authenticate and persist a usable session."""

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
) -> None:
    """Log out and clear local session persistence."""

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
            state.console.print(
                "You are not logged into any iCloud accounts. To log in, run: "
                "icloud --username <apple-id> auth login"
            )
            return
        if len(active_probes) > 1:
            accounts = "\n".join(
                f"  - {api.account_name}" for api, _status in active_probes
            )
            raise CLIAbort(
                "Multiple logged-in iCloud accounts were found; pass --username to choose one.\n"
                f"{accounts}"
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
