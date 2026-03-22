"""Calendar commands."""

from __future__ import annotations

from typing import Optional

import typer

from pyicloud.cli.context import get_state, parse_datetime, service_call
from pyicloud.cli.normalize import normalize_calendar, normalize_event
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

app = typer.Typer(help="Inspect calendars and events.")


@app.command("calendars")
def calendar_calendars(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List available calendars."""

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
        normalize_calendar(calendar)
        for calendar in service_call("Calendar", lambda: api.calendar.get_calendars())
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Calendars",
            ["GUID", "Title", "Color", "Share Type"],
            [
                (
                    calendar["guid"],
                    calendar["title"],
                    calendar["color"],
                    calendar["share_type"],
                )
                for calendar in payload
            ],
        )
    )


@app.command("events")
def calendar_events(
    ctx: typer.Context,
    from_dt: Optional[str] = typer.Option(None, "--from", help="Start datetime."),
    to_dt: Optional[str] = typer.Option(None, "--to", help="End datetime."),
    period: str = typer.Option("month", "--period", help="Calendar period shortcut."),
    calendar_guid: Optional[str] = typer.Option(
        None,
        "--calendar-guid",
        help="Only show events from one calendar. Filtering is applied client-side.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        help="Maximum events to show after filtering.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List calendar events."""

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
        normalize_event(event)
        for event in service_call(
            "Calendar",
            lambda: api.calendar.get_events(
                from_dt=parse_datetime(from_dt),
                to_dt=parse_datetime(to_dt),
                period=period,
            ),
        )
    ]
    if calendar_guid:
        payload = [
            event for event in payload if event["calendar_guid"] == calendar_guid
        ]
    payload = payload[:limit]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Events",
            ["GUID", "Calendar", "Title", "Start", "End"],
            [
                (
                    event["guid"],
                    event["calendar_guid"],
                    event["title"],
                    event["start"],
                    event["end"],
                )
                for event in payload
            ],
        )
    )
