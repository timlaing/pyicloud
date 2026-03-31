"""Reminders commands."""

from __future__ import annotations

from enum import Enum
from typing import Callable, TypeVar

import typer
from pydantic import ValidationError

from pyicloud.cli.context import CLIAbort, get_state, parse_datetime, service_call
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
from pyicloud.cli.output import console_kv_table, console_table, format_color_value
from pyicloud.services.reminders.client import RemindersApiError, RemindersAuthError
from pyicloud.services.reminders.models import (
    AlarmWithTrigger,
    ImageAttachment,
    RecurrenceFrequency,
    Reminder,
    URLAttachment,
)
from pyicloud.services.reminders.service import Attachment, Proximity

app = typer.Typer(help="Inspect and mutate Reminders.")
alarm_app = typer.Typer(help="Work with reminder alarms.")
attachment_app = typer.Typer(help="Work with reminder attachments.")
hashtag_app = typer.Typer(help="Work with reminder hashtags.")
recurrence_app = typer.Typer(help="Work with reminder recurrence rules.")

REMINDERS = "Reminders"
TRelated = TypeVar("TRelated")


class ProximityChoice(str, Enum):
    """CLI-facing proximity choice."""

    ARRIVING = "arriving"
    LEAVING = "leaving"


class RecurrenceFrequencyChoice(str, Enum):
    """CLI-facing recurrence frequency."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


PROXIMITY_MAP = {
    ProximityChoice.ARRIVING: Proximity.ARRIVING,
    ProximityChoice.LEAVING: Proximity.LEAVING,
}
RECURRENCE_FREQUENCY_MAP = {
    RecurrenceFrequencyChoice.DAILY: RecurrenceFrequency.DAILY,
    RecurrenceFrequencyChoice.WEEKLY: RecurrenceFrequency.WEEKLY,
    RecurrenceFrequencyChoice.MONTHLY: RecurrenceFrequency.MONTHLY,
    RecurrenceFrequencyChoice.YEARLY: RecurrenceFrequency.YEARLY,
}


def _group_root(ctx: typer.Context) -> None:
    """Show subgroup help when invoked without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


app.add_typer(
    alarm_app, name="alarm", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    hashtag_app, name="hashtag", invoke_without_command=True, callback=_group_root
)
app.add_typer(
    attachment_app,
    name="attachment",
    invoke_without_command=True,
    callback=_group_root,
)
app.add_typer(
    recurrence_app,
    name="recurrence",
    invoke_without_command=True,
    callback=_group_root,
)


def _normalize_prefixed_id(value: str, prefix: str) -> str:
    """Return an identifier with the expected record prefix."""

    normalized = str(value).strip()
    if not normalized:
        return normalized
    token = f"{prefix}/"
    if normalized.startswith(token):
        return normalized
    return f"{token}{normalized}"


def _id_matches(record_id: str, query: str) -> bool:
    """Return whether a record id matches a full or shorthand query."""

    normalized = str(query).strip()
    if not normalized:
        return False
    if record_id == normalized:
        return True
    if "/" in record_id and record_id.split("/", 1)[1] == normalized:
        return True
    return False


def _reminders_service(api):
    """Return the Reminders service with reauthentication handling."""

    return service_call(REMINDERS, lambda: api.reminders, account_name=api.account_name)


def _reminders_call(api, fn):
    """Wrap reminder calls with reminder-specific user-facing errors."""

    try:
        return service_call(REMINDERS, fn, account_name=api.account_name)
    except (
        LookupError,
        ValidationError,
        RemindersApiError,
        RemindersAuthError,
    ) as err:
        raise CLIAbort(str(err)) from err


def _resolve_reminder(api, reminder_id: str) -> Reminder:
    """Return one reminder by id."""

    reminders = _reminders_service(api)
    return _reminders_call(api, lambda: reminders.get(reminder_id))


def _list_reminder_rows(
    api,
    *,
    list_id: str | None = None,
    include_completed: bool,
    limit: int,
) -> list[Reminder]:
    """Return reminder rows using compound snapshots to preserve completion filtering."""

    reminders = _reminders_service(api)
    results_limit = max(limit, 200)
    if list_id:
        snapshot = _reminders_call(
            api,
            lambda: reminders.list_reminders(
                list_id=_normalize_prefixed_id(list_id, "List"),
                include_completed=include_completed,
                results_limit=results_limit,
            ),
        )
        return snapshot.reminders[:limit]

    rows: list[Reminder] = []
    seen_ids: set[str] = set()
    for reminder_list in _reminders_call(api, lambda: list(reminders.lists())):
        snapshot = _reminders_call(
            api,
            lambda lid=reminder_list.id: reminders.list_reminders(
                list_id=lid,
                include_completed=include_completed,
                results_limit=results_limit,
            ),
        )
        for reminder in snapshot.reminders:
            if reminder.id in seen_ids:
                continue
            seen_ids.add(reminder.id)
            rows.append(reminder)
            if len(rows) >= limit:
                return rows
    return rows


def _resolve_related_record(
    api,
    reminder_id: str,
    query: str,
    *,
    label: str,
    fetch_rows: Callable[[Reminder], list[TRelated]],
) -> tuple[Reminder, TRelated]:
    """Return one reminder child record matched by full or shorthand id."""

    reminder = _resolve_reminder(api, reminder_id)
    rows = _reminders_call(api, lambda: fetch_rows(reminder))
    for row in rows:
        row_id = getattr(row, "id", "")
        if _id_matches(row_id, query):
            return reminder, row
    raise CLIAbort(f"No {label} matched '{query}' for reminder {reminder.id}.")


def _attachment_kind(attachment: Attachment) -> str:
    """Return a compact attachment type label."""

    if isinstance(attachment, URLAttachment):
        return "url"
    if isinstance(attachment, ImageAttachment):
        return "image"
    return type(attachment).__name__.lower()


def _proximity_label(proximity: Proximity | None) -> str | None:
    """Return a human-readable proximity label."""

    if proximity is None:
        return None
    return proximity.name.lower()


def _frequency_label(frequency: RecurrenceFrequency | None) -> str | None:
    """Return a human-readable recurrence frequency label."""

    if frequency is None:
        return None
    return frequency.name.lower()


def _sync_cursor_payload(state, cursor: str) -> None:
    """Render a sync cursor in JSON or text mode."""

    if state.json_output:
        state.write_json({"cursor": cursor})
        return
    state.console.print(cursor)


@app.command("lists")
def reminders_lists(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List reminder lists."""

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
    reminders = _reminders_service(api)
    payload = _reminders_call(api, lambda: list(reminders.lists()))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Lists",
            ["ID", "Title", "Color", "Count"],
            [
                (
                    row.id,
                    row.title,
                    format_color_value(row.color),
                    row.count,
                )
                for row in payload
            ],
        )
    )


@app.command("list")
def reminders_list(
    ctx: typer.Context,
    list_id: str | None = typer.Option(None, "--list-id", help="Reminder list id."),
    include_completed: bool = typer.Option(
        False,
        "--include-completed",
        help="Include completed reminders.",
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum reminders to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List reminders."""

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
    payload = _list_reminder_rows(
        api,
        list_id=list_id,
        include_completed=include_completed,
        limit=limit,
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminders",
            ["ID", "Title", "Completed", "Due", "Priority"],
            [
                (
                    reminder.id,
                    reminder.title,
                    reminder.completed,
                    reminder.due_date,
                    reminder.priority,
                )
                for reminder in payload
            ],
        )
    )


@app.command("get")
def reminders_get(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Get one reminder."""

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
    reminder = _resolve_reminder(api, reminder_id)
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(
        console_kv_table(
            f"Reminder: {reminder.title}",
            [
                ("ID", reminder.id),
                ("List ID", reminder.list_id),
                ("Description", reminder.desc),
                ("Completed", reminder.completed),
                ("Due Date", reminder.due_date),
                ("Priority", reminder.priority),
                ("Flagged", reminder.flagged),
                ("All Day", reminder.all_day),
                ("Time Zone", reminder.time_zone),
                ("Parent Reminder", reminder.parent_reminder_id),
            ],
        )
    )


@app.command("create")
def reminders_create(
    ctx: typer.Context,
    list_id: str = typer.Option(..., "--list-id", help="Target list id."),
    title: str = typer.Option(..., "--title", help="Reminder title."),
    desc: str = typer.Option("", "--desc", help="Reminder description."),
    completed: bool = typer.Option(
        False,
        "--completed/--not-completed",
        help="Create the reminder as completed or incomplete.",
    ),
    due_date: str | None = typer.Option(None, "--due-date", help="Due datetime."),
    priority: int = typer.Option(0, "--priority", help="Apple priority number."),
    flagged: bool = typer.Option(
        False,
        "--flagged/--not-flagged",
        help="Create the reminder flagged or unflagged.",
    ),
    all_day: bool = typer.Option(
        False,
        "--all-day/--not-all-day",
        help="Create the reminder as all-day or timed.",
    ),
    time_zone: str | None = typer.Option(None, "--time-zone", help="IANA time zone."),
    parent_reminder_id: str | None = typer.Option(
        None,
        "--parent-reminder-id",
        help="Parent reminder id for a subtask.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Create a reminder."""

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
    reminders = _reminders_service(api)
    reminder = _reminders_call(
        api,
        lambda: reminders.create(
            list_id=_normalize_prefixed_id(list_id, "List"),
            title=title,
            desc=desc,
            completed=completed,
            due_date=parse_datetime(due_date),
            priority=priority,
            flagged=flagged,
            all_day=all_day,
            time_zone=time_zone,
            parent_reminder_id=parent_reminder_id,
        ),
    )
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(reminder.id)


@app.command("update")
def reminders_update(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    title: str | None = typer.Option(None, "--title", help="Reminder title."),
    desc: str | None = typer.Option(None, "--desc", help="Reminder description."),
    completed: bool | None = typer.Option(
        None,
        "--completed/--not-completed",
        help="Mark the reminder completed or incomplete.",
    ),
    due_date: str | None = typer.Option(None, "--due-date", help="Due datetime."),
    clear_due_date: bool = typer.Option(
        False,
        "--clear-due-date",
        help="Clear the due date.",
    ),
    priority: int | None = typer.Option(None, "--priority", help="Apple priority."),
    flagged: bool | None = typer.Option(
        None,
        "--flagged/--not-flagged",
        help="Flag or unflag the reminder.",
    ),
    all_day: bool | None = typer.Option(
        None,
        "--all-day/--not-all-day",
        help="Mark as all-day or timed.",
    ),
    time_zone: str | None = typer.Option(None, "--time-zone", help="IANA time zone."),
    clear_time_zone: bool = typer.Option(
        False,
        "--clear-time-zone",
        help="Clear the time zone.",
    ),
    parent_reminder_id: str | None = typer.Option(
        None,
        "--parent-reminder-id",
        help="Set the parent reminder id.",
    ),
    clear_parent_reminder: bool = typer.Option(
        False,
        "--clear-parent-reminder",
        help="Clear the parent reminder id.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Update one reminder."""

    if due_date and clear_due_date:
        raise typer.BadParameter(
            "Choose either --due-date or --clear-due-date, not both."
        )
    if time_zone and clear_time_zone:
        raise typer.BadParameter(
            "Choose either --time-zone or --clear-time-zone, not both."
        )
    if parent_reminder_id and clear_parent_reminder:
        raise typer.BadParameter(
            "Choose either --parent-reminder-id or --clear-parent-reminder, not both."
        )

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    changed = False

    if title is not None:
        reminder.title = title
        changed = True
    if desc is not None:
        reminder.desc = desc
        changed = True
    if completed is not None:
        reminder.completed = completed
        changed = True
    if due_date is not None:
        reminder.due_date = parse_datetime(due_date)
        changed = True
    elif clear_due_date:
        reminder.due_date = None
        changed = True
    if priority is not None:
        reminder.priority = priority
        changed = True
    if flagged is not None:
        reminder.flagged = flagged
        changed = True
    if all_day is not None:
        reminder.all_day = all_day
        changed = True
    if time_zone is not None:
        reminder.time_zone = time_zone
        changed = True
    elif clear_time_zone:
        reminder.time_zone = None
        changed = True
    if parent_reminder_id is not None:
        reminder.parent_reminder_id = parent_reminder_id
        changed = True
    elif clear_parent_reminder:
        reminder.parent_reminder_id = None
        changed = True

    if not changed:
        raise CLIAbort("No reminder updates were requested.")

    _reminders_call(api, lambda: reminders.update(reminder))
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(f"Updated {reminder.id}")


@app.command("set-status")
def reminders_set_status(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    completed: bool = typer.Option(
        True,
        "--completed/--not-completed",
        help="Mark the reminder completed or incomplete.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Mark a reminder completed or incomplete."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    reminder.completed = completed
    _reminders_call(api, lambda: reminders.update(reminder))
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(f"Updated {reminder.id}: completed={completed}")


@app.command("delete")
def reminders_delete(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete a reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    _reminders_call(api, lambda: reminders.delete(reminder))
    if state.json_output:
        state.write_json({"reminder_id": reminder.id, "deleted": True})
        return
    state.console.print(f"Deleted {reminder.id}")


@app.command("snapshot")
def reminders_snapshot(
    ctx: typer.Context,
    list_id: str = typer.Option(..., "--list-id", help="Reminder list id."),
    include_completed: bool = typer.Option(
        False,
        "--include-completed",
        help="Include completed reminders.",
    ),
    results_limit: int = typer.Option(
        200,
        "--results-limit",
        min=1,
        help="Maximum reminders to request from the compound query.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Fetch a compound reminder snapshot for one list."""

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
    reminders = _reminders_service(api)
    payload = _reminders_call(
        api,
        lambda: reminders.list_reminders(
            list_id=_normalize_prefixed_id(list_id, "List"),
            include_completed=include_completed,
            results_limit=results_limit,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_kv_table(
            "Reminder Snapshot",
            [
                ("List ID", _normalize_prefixed_id(list_id, "List")),
                ("Reminders", len(payload.reminders)),
                ("Alarms", len(payload.alarms)),
                ("Triggers", len(payload.triggers)),
                ("Attachments", len(payload.attachments)),
                ("Hashtags", len(payload.hashtags)),
                ("Recurrence Rules", len(payload.recurrence_rules)),
            ],
        )
    )
    state.console.print(
        console_table(
            "Snapshot Reminders",
            ["ID", "Title", "Completed", "Due", "Priority"],
            [
                (
                    reminder.id,
                    reminder.title,
                    reminder.completed,
                    reminder.due_date,
                    reminder.priority,
                )
                for reminder in payload.reminders
            ],
        )
    )


@app.command("changes")
def reminders_changes(
    ctx: typer.Context,
    since: str | None = typer.Option(None, "--since", help="Sync cursor."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum changes to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List reminder changes since a cursor."""

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
    reminders = _reminders_service(api)
    payload = _reminders_call(
        api,
        lambda: list(reminders.iter_changes(since=since))[:limit],
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Changes",
            ["Type", "Reminder ID", "Title", "Completed"],
            [
                (
                    event.type,
                    event.reminder_id,
                    event.reminder.title if event.reminder else None,
                    event.reminder.completed if event.reminder else None,
                )
                for event in payload
            ],
        )
    )


@app.command("sync-cursor")
def reminders_sync_cursor(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Print the current Reminders sync cursor."""

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
    reminders = _reminders_service(api)
    cursor = _reminders_call(api, lambda: reminders.sync_cursor())
    _sync_cursor_payload(state, cursor)


@alarm_app.command("list")
def reminders_alarm_list(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List alarms for one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(api, lambda: reminders.alarms_for(reminder))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Alarms",
            [
                "Alarm ID",
                "Trigger ID",
                "Title",
                "Address",
                "Radius",
                "Proximity",
            ],
            [
                (
                    row.alarm.id,
                    row.trigger.id if row.trigger else None,
                    row.trigger.title if row.trigger else None,
                    row.trigger.address if row.trigger else None,
                    row.trigger.radius if row.trigger else None,
                    _proximity_label(row.trigger.proximity if row.trigger else None),
                )
                for row in payload
            ],
        )
    )


@alarm_app.command("add-location")
def reminders_alarm_add_location(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    title: str = typer.Option(..., "--title", help="Location title."),
    address: str = typer.Option(..., "--address", help="Location address."),
    latitude: float = typer.Option(..., "--latitude", help="Location latitude."),
    longitude: float = typer.Option(..., "--longitude", help="Location longitude."),
    radius: float = typer.Option(100.0, "--radius", min=0.0, help="Radius in meters."),
    proximity: ProximityChoice = typer.Option(
        ProximityChoice.ARRIVING,
        "--proximity",
        help="Trigger direction.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Add a location alarm to a reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    alarm, trigger = _reminders_call(
        api,
        lambda: reminders.add_location_trigger(
            reminder,
            title=title,
            address=address,
            latitude=latitude,
            longitude=longitude,
            radius=radius,
            proximity=PROXIMITY_MAP[proximity],
        ),
    )
    payload = AlarmWithTrigger(alarm=alarm, trigger=trigger)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Created {alarm.id} with trigger {trigger.id}")


@hashtag_app.command("list")
def reminders_hashtag_list(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List hashtags for one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(api, lambda: reminders.tags_for(reminder))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Hashtags",
            ["ID", "Name", "Reminder ID"],
            [(row.id, row.name, row.reminder_id) for row in payload],
        )
    )


@hashtag_app.command("create")
def reminders_hashtag_create(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    name: str = typer.Argument(..., help="Hashtag name."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Create a hashtag on one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(api, lambda: reminders.create_hashtag(reminder, name))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(payload.id)


@hashtag_app.command("update")
def reminders_hashtag_update(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    hashtag_id: str = typer.Argument(..., help="Hashtag id."),
    name: str = typer.Option(..., "--name", help="Updated hashtag name."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Update a hashtag name."""

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
    reminders = _reminders_service(api)
    _reminder, hashtag = _resolve_related_record(
        api,
        reminder_id,
        hashtag_id,
        label="hashtag",
        fetch_rows=lambda reminder: reminders.tags_for(reminder),
    )
    _reminders_call(api, lambda: reminders.update_hashtag(hashtag, name))
    hashtag.name = name
    if state.json_output:
        state.write_json(hashtag)
        return
    state.console.print(f"Updated {hashtag.id}")


@hashtag_app.command("delete")
def reminders_hashtag_delete(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    hashtag_id: str = typer.Argument(..., help="Hashtag id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete a hashtag from one reminder."""

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
    reminders = _reminders_service(api)
    reminder, hashtag = _resolve_related_record(
        api,
        reminder_id,
        hashtag_id,
        label="hashtag",
        fetch_rows=lambda row: reminders.tags_for(row),
    )
    _reminders_call(api, lambda: reminders.delete_hashtag(reminder, hashtag))
    payload = {"reminder_id": reminder.id, "hashtag_id": hashtag.id, "deleted": True}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deleted {hashtag.id}")


@attachment_app.command("list")
def reminders_attachment_list(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List attachments for one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(api, lambda: reminders.attachments_for(reminder))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Attachments",
            ["ID", "Type", "URL", "Filename", "UTI", "Size"],
            [
                (
                    row.id,
                    _attachment_kind(row),
                    getattr(row, "url", None),
                    getattr(row, "filename", None),
                    row.uti,
                    getattr(row, "file_size", None),
                )
                for row in payload
            ],
        )
    )


@attachment_app.command("create-url")
def reminders_attachment_create_url(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    url: str = typer.Option(..., "--url", help="Attachment URL."),
    uti: str = typer.Option("public.url", "--uti", help="Attachment UTI."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Create a URL attachment on one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(
        api,
        lambda: reminders.create_url_attachment(reminder, url=url, uti=uti),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(payload.id)


@attachment_app.command("update")
def reminders_attachment_update(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    attachment_id: str = typer.Argument(..., help="Attachment id."),
    url: str | None = typer.Option(None, "--url", help="Updated attachment URL."),
    uti: str | None = typer.Option(None, "--uti", help="Updated attachment UTI."),
    filename: str | None = typer.Option(
        None,
        "--filename",
        help="Updated attachment filename.",
    ),
    file_size: int | None = typer.Option(
        None,
        "--file-size",
        min=0,
        help="Updated attachment size.",
    ),
    width: int | None = typer.Option(
        None,
        "--width",
        min=0,
        help="Updated attachment width.",
    ),
    height: int | None = typer.Option(
        None,
        "--height",
        min=0,
        help="Updated attachment height.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Update one attachment."""

    if all(value is None for value in (url, uti, filename, file_size, width, height)):
        raise CLIAbort("No attachment updates were requested.")

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
    reminders = _reminders_service(api)
    _reminder, attachment = _resolve_related_record(
        api,
        reminder_id,
        attachment_id,
        label="attachment",
        fetch_rows=lambda reminder: reminders.attachments_for(reminder),
    )
    _reminders_call(
        api,
        lambda: reminders.update_attachment(
            attachment,
            url=url,
            uti=uti,
            filename=filename,
            file_size=file_size,
            width=width,
            height=height,
        ),
    )
    if url is not None and hasattr(attachment, "url"):
        attachment.url = url
    if uti is not None:
        attachment.uti = uti
    if filename is not None and hasattr(attachment, "filename"):
        attachment.filename = filename
    if file_size is not None and hasattr(attachment, "file_size"):
        attachment.file_size = file_size
    if width is not None and hasattr(attachment, "width"):
        attachment.width = width
    if height is not None and hasattr(attachment, "height"):
        attachment.height = height
    if state.json_output:
        state.write_json(attachment)
        return
    state.console.print(f"Updated {attachment.id}")


@attachment_app.command("delete")
def reminders_attachment_delete(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    attachment_id: str = typer.Argument(..., help="Attachment id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete one attachment."""

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
    reminders = _reminders_service(api)
    reminder, attachment = _resolve_related_record(
        api,
        reminder_id,
        attachment_id,
        label="attachment",
        fetch_rows=lambda row: reminders.attachments_for(row),
    )
    _reminders_call(api, lambda: reminders.delete_attachment(reminder, attachment))
    payload = {
        "reminder_id": reminder.id,
        "attachment_id": attachment.id,
        "deleted": True,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deleted {attachment.id}")


@recurrence_app.command("list")
def reminders_recurrence_list(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List recurrence rules for one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(api, lambda: reminders.recurrence_rules_for(reminder))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Reminder Recurrence Rules",
            ["ID", "Frequency", "Interval", "Occurrence Count", "First Day"],
            [
                (
                    row.id,
                    _frequency_label(row.frequency),
                    row.interval,
                    row.occurrence_count,
                    row.first_day_of_week,
                )
                for row in payload
            ],
        )
    )


@recurrence_app.command("create")
def reminders_recurrence_create(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    frequency: RecurrenceFrequencyChoice = typer.Option(
        RecurrenceFrequencyChoice.DAILY,
        "--frequency",
        help="Recurrence frequency.",
    ),
    interval: int = typer.Option(1, "--interval", min=1, help="Recurrence interval."),
    occurrence_count: int = typer.Option(
        0,
        "--occurrence-count",
        min=0,
        help="Occurrence count; 0 means unlimited.",
    ),
    first_day_of_week: int = typer.Option(
        0,
        "--first-day-of-week",
        min=0,
        max=6,
        help="First day of week; 0 is Sunday.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Create a recurrence rule on one reminder."""

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
    reminders = _reminders_service(api)
    reminder = _resolve_reminder(api, reminder_id)
    payload = _reminders_call(
        api,
        lambda: reminders.create_recurrence_rule(
            reminder,
            frequency=RECURRENCE_FREQUENCY_MAP[frequency],
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(payload.id)


@recurrence_app.command("update")
def reminders_recurrence_update(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    rule_id: str = typer.Argument(..., help="Recurrence rule id."),
    frequency: RecurrenceFrequencyChoice | None = typer.Option(
        None,
        "--frequency",
        help="Recurrence frequency.",
    ),
    interval: int | None = typer.Option(
        None,
        "--interval",
        min=1,
        help="Recurrence interval.",
    ),
    occurrence_count: int | None = typer.Option(
        None,
        "--occurrence-count",
        min=0,
        help="Occurrence count; 0 means unlimited.",
    ),
    first_day_of_week: int | None = typer.Option(
        None,
        "--first-day-of-week",
        min=0,
        max=6,
        help="First day of week; 0 is Sunday.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Update one recurrence rule."""

    if all(
        value is None
        for value in (frequency, interval, occurrence_count, first_day_of_week)
    ):
        raise CLIAbort("No recurrence updates were requested.")

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
    reminders = _reminders_service(api)
    _reminder, recurrence_rule = _resolve_related_record(
        api,
        reminder_id,
        rule_id,
        label="recurrence rule",
        fetch_rows=lambda reminder: reminders.recurrence_rules_for(reminder),
    )
    _reminders_call(
        api,
        lambda: reminders.update_recurrence_rule(
            recurrence_rule,
            frequency=(
                RECURRENCE_FREQUENCY_MAP[frequency] if frequency is not None else None
            ),
            interval=interval,
            occurrence_count=occurrence_count,
            first_day_of_week=first_day_of_week,
        ),
    )
    if frequency is not None:
        recurrence_rule.frequency = RECURRENCE_FREQUENCY_MAP[frequency]
    if interval is not None:
        recurrence_rule.interval = interval
    if occurrence_count is not None:
        recurrence_rule.occurrence_count = occurrence_count
    if first_day_of_week is not None:
        recurrence_rule.first_day_of_week = first_day_of_week
    if state.json_output:
        state.write_json(recurrence_rule)
        return
    state.console.print(f"Updated {recurrence_rule.id}")


@recurrence_app.command("delete")
def reminders_recurrence_delete(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(..., help="Reminder id."),
    rule_id: str = typer.Argument(..., help="Recurrence rule id."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Delete one recurrence rule."""

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
    reminders = _reminders_service(api)
    reminder, recurrence_rule = _resolve_related_record(
        api,
        reminder_id,
        rule_id,
        label="recurrence rule",
        fetch_rows=lambda row: reminders.recurrence_rules_for(row),
    )
    _reminders_call(
        api,
        lambda: reminders.delete_recurrence_rule(reminder, recurrence_rule),
    )
    payload = {
        "reminder_id": reminder.id,
        "recurrence_rule_id": recurrence_rule.id,
        "deleted": True,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(f"Deleted {recurrence_rule.id}")
