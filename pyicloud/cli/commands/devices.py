"""Find My device commands."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import time
from typing import Any

import typer

from pyicloud.cli.context import get_state, resolve_device, service_call
from pyicloud.cli.normalize import normalize_device_details, normalize_device_summary
from pyicloud.cli.options import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_OUTPUT_FORMAT,
    HttpProxyOption,
    HttpsProxyOption,
    LogLevelOption,
    NoVerifySslOption,
    OutputFormatOption,
    RefreshIntervalOption,
    SessionDirOption,
    UsernameOption,
    WithFamilyOption,
    store_command_options,
)
from pyicloud.cli.output import (
    console_kv_table,
    console_table,
    print_json_text,
    to_json_string,
    write_json_file,
)

app = typer.Typer(help="Work with Find My devices.")

FIND_MY = "Find My"
DEVICE_ID_HELP = "Device id or name."


def _fetch_device_summaries(api: Any, *, locate: bool) -> list[dict[str, Any]]:
    """Fetch normalized device summaries from Find My."""

    # Iterating the manager can trigger a refresh (when its monitor has
    # stopped), so keep it inside service_call's error handling.
    summaries: list[dict[str, Any]] = service_call(
        FIND_MY,
        lambda: [
            normalize_device_summary(device, locate=locate) for device in api.devices
        ],
        account_name=api.account_name,
    )
    return summaries


def _render_device_summary_table(state: Any, payload: list[dict[str, Any]]) -> None:
    """Render one device summary listing as a console table."""

    state.console.print(
        console_table(
            "Devices",
            ["ID", "Name", "Display", "Model", "Battery", "Status"],
            [
                (
                    row["id"],
                    row["name"],
                    row["display_name"],
                    row["device_model"],
                    row["battery_level"],
                    row["battery_status"],
                )
                for row in payload
            ],
        )
    )


def _print_device_watch_wait(
    state: Any,
    *,
    interval_seconds: float,
    next_iteration: int,
    iterations: int | None,
) -> None:
    """Print a progress message between device listing watch runs."""

    if iterations is None:
        state.console.print(
            f"Waiting {interval_seconds:g}s before device listing run "
            f"{next_iteration}..."
        )
        return
    state.console.print(
        f"Waiting {interval_seconds:g}s before device listing run "
        f"{next_iteration} of {iterations}..."
    )


def _iter_device_watch_payloads(
    state: Any,
    api: Any,
    *,
    interval_seconds: float,
    iterations: int | None,
    locate: bool,
) -> Iterator[list[dict[str, Any]]]:
    """Locate devices and yield a fresh summary on every watch run."""

    completed = 0
    while iterations is None or completed < iterations:
        if completed > 0:
            if not state.json_output:
                _print_device_watch_wait(
                    state,
                    interval_seconds=interval_seconds,
                    next_iteration=completed + 1,
                    iterations=iterations,
                )
            time.sleep(interval_seconds)
        # Locate before every run: the manager's initial initClient request
        # omits the locate fields, so the first listing would be stale.
        service_call(
            FIND_MY,
            lambda: api.devices.refresh(locate=True),
            account_name=api.account_name,
        )
        yield _fetch_device_summaries(api, locate=locate)
        completed += 1


def _watch_device_summaries(
    state: Any,
    api: Any,
    *,
    interval_seconds: float,
    iterations: int | None,
    locate: bool,
) -> None:
    """Re-locate and re-list devices at the interval until interrupted."""

    payloads = _iter_device_watch_payloads(
        state,
        api,
        interval_seconds=interval_seconds,
        iterations=iterations,
        locate=locate,
    )
    try:
        if state.json_output:
            if iterations is None:
                for payload in payloads:
                    # Compact JSON per run; markup off so device names like
                    # "[bold]Phone[/bold]" survive verbatim.
                    state.console.print(to_json_string(payload), markup=False)
                return
            state.write_json(list(payloads))
            return

        for payload in payloads:
            _render_device_summary_table(state, payload)
    except KeyboardInterrupt as err:
        raise typer.Exit(code=130) from err


@app.command("list")
def devices_list(
    ctx: typer.Context,
    locate: bool = typer.Option(
        False, "--locate", help="Fetch current device locations."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
    iterations: int | None = typer.Option(
        None,
        "--iterations",
        min=1,
        help="Stop after N listing runs (requires --refresh-interval; "
        "default: watch until interrupted).",
    ),
) -> None:
    """List Find My devices."""

    if iterations is not None and refresh_interval is None:
        raise typer.BadParameter("The --iterations option requires --refresh-interval.")

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()

    if refresh_interval is None:
        payload = _fetch_device_summaries(api, locate=locate)
        if state.json_output:
            state.write_json(payload)
            return
        _render_device_summary_table(state, payload)
        return

    _watch_device_summaries(
        state,
        api,
        interval_seconds=refresh_interval,
        iterations=iterations,
        locate=locate,
    )


@app.command("show")
def devices_show(
    ctx: typer.Context,
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    locate: bool = typer.Option(
        False, "--locate", help="Fetch current device location."
    ),
    raw: bool = typer.Option(False, "--raw", help="Show the raw device payload."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Show detailed information for one device."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device)
    payload = idevice.data if raw else normalize_device_details(idevice, locate=locate)
    if state.json_output:
        state.write_json(payload)
        return
    if raw:
        print_json_text(state.console, payload)
        return
    state.console.print(
        console_kv_table(
            f"Device: {payload['name']}",
            [
                ("ID", payload["id"]),
                ("Display Name", payload["display_name"]),
                ("Device Class", payload["device_class"]),
                ("Device Model", payload["device_model"]),
                ("Battery Level", payload["battery_level"]),
                ("Battery Status", payload["battery_status"]),
                ("Location", payload["location"]),
            ],
        )
    )


@app.command("sound")
def devices_sound(
    ctx: typer.Context,
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    subject: str = typer.Option("Find My iPhone Alert", "--subject"),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Play a sound on a device."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device)
    service_call(
        FIND_MY,
        lambda: idevice.play_sound(subject=subject),
        account_name=api.account_name,
    )
    payload = {"device_id": idevice.id, "subject": subject}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Requested sound alert for {idevice.name}.")


@app.command("message")
def devices_message(
    ctx: typer.Context,  # noqa: S107
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    message: str = typer.Argument(..., help="Message to display."),
    subject: str = typer.Option("A Message", "--subject"),
    silent: bool = typer.Option(False, "--silent", help="Do not play a sound."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Display a message on a device."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device)
    service_call(
        FIND_MY,
        lambda: idevice.display_message(
            subject=subject, message=message, sounds=not silent
        ),
        account_name=api.account_name,
    )
    payload = {
        "device_id": idevice.id,
        "subject": subject,
        "message": message,
        "silent": silent,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Requested message for {idevice.name}.")


@app.command("lost-mode")
def devices_lost_mode(
    ctx: typer.Context,  # noqa: S107
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    phone: str = typer.Option("", "--phone", help="Phone number shown in lost mode."),
    message: str = typer.Option(
        "This iPhone has been lost. Please call me.",
        "--message",
        help="Lost mode message.",
    ),
    passcode: str = typer.Option("", "--passcode", help="New device passcode."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Enable lost mode for a device."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device, require_unique=True)
    service_call(
        FIND_MY,
        lambda: idevice.lost_device(number=phone, text=message, newpasscode=passcode),
        account_name=api.account_name,
    )
    payload = {
        "device_id": idevice.id,
        "phone": phone,
        "message": message,
        "passcode": passcode,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Requested lost mode for {idevice.name}.")


@app.command("erase")
def devices_erase(
    ctx: typer.Context,
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    message: str = typer.Option(
        "This iPhone has been lost. Please call me.",
        "--message",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Request a remote erase for a device."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device, require_unique=True)
    if not force and not typer.confirm(
        f"This will PERMANENTLY ERASE all data on {idevice.name}. Continue?"
    ):
        raise typer.Abort()
    service_call(
        FIND_MY,
        lambda: idevice.erase_device(message),
        account_name=api.account_name,
    )
    payload = {"device_id": idevice.id, "message": message}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Requested remote erase for {idevice.name}.")


@app.command("export")
def devices_export(
    ctx: typer.Context,  # noqa: S107
    device: str = typer.Argument(..., help=DEVICE_ID_HELP),
    output: Path = typer.Option(..., "--output", help="Destination JSON file."),
    raw: bool | None = typer.Option(
        None,
        "--raw/--no-raw",
        help="Write the raw device payload.",
    ),
    normalized: bool = typer.Option(
        False,
        "--normalized",
        hidden=True,
        help="Write normalized device fields instead of the raw payload.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
    with_family: WithFamilyOption = False,
    refresh_interval: RefreshIntervalOption = None,
) -> None:
    """Export a device snapshot to JSON."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
        with_family=with_family,
        refresh_interval=refresh_interval,
    )
    state = get_state(ctx)
    api = state.get_api()
    idevice = resolve_device(api, device)
    if raw and normalized:
        raise typer.BadParameter("Choose either --raw or --normalized, not both.")

    use_raw = raw is True and not normalized
    payload = (
        idevice.data if use_raw else normalize_device_details(idevice, locate=False)
    )
    write_json_file(output, payload)
    if state.json_output:
        state.write_json({"device_id": idevice.id, "path": str(output), "raw": use_raw})
        return
    state.console.print(str(output))
