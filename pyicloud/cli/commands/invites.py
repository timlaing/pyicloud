"""Apple Invites commands.

Read-only. Responding to an invitation and joining one from a link are writes
and are deliberately not exposed here yet; see the tracking issue.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import typer

from pyicloud.base import PyiCloudService
from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import (
    normalize_invite_event,
    normalize_invite_event_details,
    normalize_invite_rsvp,
    normalize_invite_share,
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
from pyicloud.cli.output import console_kv_table, console_table
from pyicloud.services.invites import Event, EventNotFound, InvitesError

app = typer.Typer(help="Inspect Apple Invites events.")

EVENT_ID_HELP = (
    "Event id, or any unambiguous prefix of one, as shown by `icloud invites list`."
)
SHORT_GUID_HELP = (
    "An invite link, or just its trailing part: either "
    "https://www.icloud.com/invites/008ABCDEFGHIJ or 008ABCDEFGHIJ."
)


def _format_when(payload: dict[str, Any]) -> str:
    """Render an event's start compactly enough to fit a table cell.

    The full ISO value keeps seconds and a UTC offset, which wraps onto three
    lines in an eighty-column terminal and pushes the event id out of view.
    """

    starts_at = payload.get("starts_at")
    if not isinstance(starts_at, datetime):
        return "" if starts_at is None else str(starts_at)
    if payload.get("is_all_day"):
        return f"{starts_at:%Y-%m-%d} (all day)"
    return f"{starts_at:%Y-%m-%d %H:%M}"


def _resolve_event_id(api: PyiCloudService, given: str) -> str:
    """Return the full event id for a full id or an unambiguous prefix.

    Listing truncates ids to fit the terminal, so requiring the full value
    would make the id column decorative. Prefixes work the way they do in git.
    """

    candidates = [
        event.event_id
        for event in api.invites.events()
        if event.event_id.lower().startswith(given.lower())
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CLIAbort(
            f"No event id starts with {given!r}. Run `icloud invites list` to "
            "see the events available to you."
        )
    listed = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise CLIAbort(f"{given!r} matches more than one event:\n{listed}")


def _short_guid_from(given: str) -> str:
    """Accept either a short guid or the invite URL it came from.

    People copy the whole link far more often than they pick the trailing
    segment out of it, and sending the URL to Apple only earns a 400.
    """

    candidate = given.strip().rstrip("/")
    if "/" in candidate:
        candidate = candidate.rsplit("/", 1)[-1]
    if not candidate:
        raise CLIAbort(
            "No invite id given. Pass the trailing part of an invite link, "
            "for example 008ABCDEFGHIJ from "
            "https://www.icloud.com/invites/008ABCDEFGHIJ."
        )
    return candidate


def _invites_call(api: PyiCloudService, fn: Callable[[], Any]) -> Any:
    """Wrap an Invites call so its errors reach the user as advice."""

    try:
        return service_call("Invites", fn, account_name=api.account_name)
    except InvitesError as err:
        raise CLIAbort(f"Invites: {err}") from err


def _lookup_event(api: PyiCloudService, event_id: str) -> Event:
    """Fetch one event, turning an unknown id into advice rather than a trace."""

    try:
        return api.invites.event(_resolve_event_id(api, event_id))
    except EventNotFound as err:
        raise CLIAbort(
            f"No event with id {event_id}. Run `icloud invites list` to see "
            "the events available to you."
        ) from err


@app.command("list")
def invites_list(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List events you host and events shared with you."""

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
        normalize_invite_event(event)
        for event in _invites_call(
            api,
            # The lambda is load-bearing: `api.invites` is a property that
            # builds the service and can raise PyiCloudServiceUnavailable.
            # Passing `api.invites.events` would evaluate it outside the
            # guard, and the failure would escape as a traceback.
            lambda: api.invites.events(),  # pylint: disable=unnecessary-lambda
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Invites",
            ["Scope", "Title", "When", "Host", "ID"],
            [
                (
                    event["scope"],
                    event["title"],
                    _format_when(event),
                    event["host_display_name"],
                    event["event_id"],
                )
                for event in payload
            ],
        )
    )


@app.command("show")
def invites_show(
    ctx: typer.Context,
    event_id: str = typer.Argument(..., help=EVENT_ID_HELP),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show one event in full, with its share details."""

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
    event = _invites_call(api, lambda: _lookup_event(api, event_id))
    payload = normalize_invite_event_details(event)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_kv_table(
            payload["title"] or "Event",
            [
                ("Scope", payload["scope"]),
                ("Host", payload["host_display_name"]),
                ("When", _format_when(payload)),
                ("Ends", payload["ends_at"]),
                ("Location", payload["location"]),
                ("City", payload["city"]),
                ("Notes", payload["notes"]),
                ("Cancelled", payload["is_cancelled"]),
                ("Published", payload["is_published"]),
                ("New RSVPs blocked", payload["block_new_rsvps"]),
                ("Participants", payload["participant_count"]),
                ("Invite link", payload["share_url"]),
                ("ID", payload["event_id"]),
            ],
        )
    )


@app.command("rsvps")
def invites_rsvps(
    ctx: typer.Context,
    event_id: str = typer.Argument(..., help=EVENT_ID_HELP),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List who has responded to an event."""

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
    payload = _invites_call(
        api,
        lambda: [
            normalize_invite_rsvp(rsvp)
            for rsvp in api.invites.rsvps(_lookup_event(api, event_id))
        ],
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "RSVPs",
            ["Name", "Status", "Adults", "Kids", "Message"],
            [
                (
                    rsvp["name"],
                    rsvp["status"],
                    rsvp["additional_adults"],
                    rsvp["additional_kids"],
                    rsvp["message"],
                )
                for rsvp in payload
            ],
        )
    )


@app.command("resolve")
def invites_resolve(
    ctx: typer.Context,
    short_guid: str = typer.Argument(..., help=SHORT_GUID_HELP),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Preview an invite link without joining it."""

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
    payload = normalize_invite_share(
        _invites_call(api, lambda: api.invites.resolve(_short_guid_from(short_guid)))
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_kv_table(
            "Invite",
            [
                ("Event", payload["event_id"]),
                (
                    "Host",
                    f"{payload['owner_given_name']} {payload['owner_family_name']}",
                ),
                ("Your status", payload["participant_status"]),
                ("Your role", payload["participant_type"]),
                ("Permission", payload["participant_permission"]),
                ("Short guid", payload["short_guid"]),
            ],
        )
    )
