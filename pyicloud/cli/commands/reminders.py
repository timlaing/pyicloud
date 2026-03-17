"""Reminders commands."""

from __future__ import annotations

from itertools import islice
from typing import Optional

import typer

from pyicloud.cli.context import get_state, parse_datetime, service_call
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table, format_color_value

app = typer.Typer(help="Inspect and mutate Reminders.")


@app.command("lists")
@with_service_command_options
def reminders_lists(ctx: typer.Context) -> None:
    """List reminder lists."""

    state = get_state(ctx)
    api = state.get_api()
    rows = list(service_call("Reminders", lambda: api.reminders.lists()))
    if state.json_output:
        state.write_json(rows)
        return
    state.console.print(
        console_table(
            "Reminder Lists",
            ["ID", "Title", "Color", "Count"],
            [
                (row.id, row.title, format_color_value(row.color), row.count)
                for row in rows
            ],
        )
    )


@app.command("list")
@with_service_command_options
def reminders_list(
    ctx: typer.Context,
    list_id: Optional[str] = typer.Option(None, "--list-id", help="List id."),
    include_completed: bool = typer.Option(
        False, "--include-completed", help="Include completed reminders."
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum reminders to show."),
) -> None:
    """List reminders."""

    state = get_state(ctx)
    api = state.get_api()
    reminders = list(
        islice(
            service_call(
                "Reminders",
                lambda: api.reminders.reminders(
                    list_id=list_id,
                    include_completed=include_completed,
                ),
            ),
            limit,
        )
    )
    if state.json_output:
        state.write_json(reminders)
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
                for reminder in reminders
            ],
        )
    )


@app.command("get")
@with_service_command_options
def reminders_get(ctx: typer.Context, reminder_id: str = typer.Argument(...)) -> None:
    """Get one reminder."""

    state = get_state(ctx)
    api = state.get_api()
    reminder = service_call("Reminders", lambda: api.reminders.get(reminder_id))
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(f"{reminder.title} [{reminder.id}]")
    if reminder.desc:
        state.console.print(reminder.desc)
    if reminder.due_date:
        state.console.print(f"Due: {reminder.due_date}")


@app.command("create")
@with_service_command_options
def reminders_create(
    ctx: typer.Context,
    list_id: str = typer.Option(..., "--list-id", help="Target list id."),
    title: str = typer.Option(..., "--title", help="Reminder title."),
    desc: str = typer.Option("", "--desc", help="Reminder description."),
    due_date: Optional[str] = typer.Option(None, "--due-date", help="Due datetime."),
    priority: int = typer.Option(0, "--priority", help="Apple priority number."),
    flagged: bool = typer.Option(False, "--flagged", help="Flag the reminder."),
    all_day: bool = typer.Option(False, "--all-day", help="Mark as all-day."),
) -> None:
    """Create a reminder."""

    state = get_state(ctx)
    api = state.get_api()
    reminder = service_call(
        "Reminders",
        lambda: api.reminders.create(
            list_id=list_id,
            title=title,
            desc=desc,
            due_date=parse_datetime(due_date),
            priority=priority,
            flagged=flagged,
            all_day=all_day,
        ),
    )
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(reminder.id)


@app.command("set-status")
@with_service_command_options
def reminders_set_status(
    ctx: typer.Context,
    reminder_id: str = typer.Argument(...),
    completed: bool = typer.Option(True, "--completed/--not-completed"),
) -> None:
    """Mark a reminder completed or incomplete."""

    state = get_state(ctx)
    api = state.get_api()
    reminder = service_call("Reminders", lambda: api.reminders.get(reminder_id))
    reminder.completed = completed
    service_call("Reminders", lambda: api.reminders.update(reminder))
    if state.json_output:
        state.write_json(reminder)
        return
    state.console.print(f"Updated {reminder.id}: completed={completed}")


@app.command("delete")
@with_service_command_options
def reminders_delete(
    ctx: typer.Context, reminder_id: str = typer.Argument(...)
) -> None:
    """Delete a reminder."""

    state = get_state(ctx)
    api = state.get_api()
    reminder = service_call("Reminders", lambda: api.reminders.get(reminder_id))
    service_call("Reminders", lambda: api.reminders.delete(reminder))
    if state.json_output:
        state.write_json({"reminder_id": reminder.id, "deleted": True})
        return
    state.console.print(f"Deleted {reminder.id}")


@app.command("changes")
@with_service_command_options
def reminders_changes(
    ctx: typer.Context,
    since: Optional[str] = typer.Option(None, "--since", help="Sync cursor."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum changes to show."),
) -> None:
    """List reminder changes since a cursor."""

    state = get_state(ctx)
    api = state.get_api()
    events = list(
        islice(
            service_call("Reminders", lambda: api.reminders.iter_changes(since=since)),
            limit,
        )
    )
    if state.json_output:
        state.write_json(events)
        return
    state.console.print(
        console_table(
            "Reminder Changes",
            ["Type", "Reminder ID", "Has Reminder"],
            [
                (event.type, event.reminder_id, event.reminder is not None)
                for event in events
            ],
        )
    )
