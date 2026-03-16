"""Notes commands."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Optional

import typer

from pyicloud.cli.context import get_state, service_call
from pyicloud.cli.normalize import select_recent_notes
from pyicloud.cli.output import console_table

app = typer.Typer(help="Inspect, render, and export Notes.")


@app.command("recent")
def notes_recent(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum notes to show."),
    include_deleted: bool = typer.Option(
        False,
        "--include-deleted",
        help="Include notes from Recently Deleted.",
    ),
) -> None:
    """List recent notes."""

    state = get_state(ctx)
    api = state.get_api()
    rows = service_call(
        "Notes",
        lambda: select_recent_notes(api, limit=limit, include_deleted=include_deleted),
    )
    if state.json_output:
        state.write_json(rows)
        return
    state.console.print(
        console_table(
            "Recent Notes",
            ["ID", "Title", "Folder", "Modified"],
            [(row.id, row.title, row.folder_name, row.modified_at) for row in rows],
        )
    )


@app.command("folders")
def notes_folders(ctx: typer.Context) -> None:
    """List note folders."""

    state = get_state(ctx)
    api = state.get_api()
    rows = list(service_call("Notes", lambda: api.notes.folders()))
    if state.json_output:
        state.write_json(rows)
        return
    state.console.print(
        console_table(
            "Note Folders",
            ["ID", "Name", "Parent", "Has Subfolders"],
            [(row.id, row.name, row.parent_id, row.has_subfolders) for row in rows],
        )
    )


@app.command("list")
def notes_list(
    ctx: typer.Context,
    folder_id: Optional[str] = typer.Option(None, "--folder-id", help="Folder id."),
    all_notes: bool = typer.Option(False, "--all", help="Iterate all notes."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum notes to show."),
    since: Optional[str] = typer.Option(
        None, "--since", help="Incremental sync cursor for iter_all()."
    ),
) -> None:
    """List notes."""

    state = get_state(ctx)
    api = state.get_api()
    if folder_id:
        rows = list(
            service_call("Notes", lambda: api.notes.in_folder(folder_id, limit=limit))
        )
    elif all_notes:
        rows = list(
            islice(
                service_call("Notes", lambda: api.notes.iter_all(since=since)), limit
            )
        )
    else:
        rows = list(service_call("Notes", lambda: api.notes.recents(limit=limit)))
    if state.json_output:
        state.write_json(rows)
        return
    state.console.print(
        console_table(
            "Notes",
            ["ID", "Title", "Folder", "Modified"],
            [(row.id, row.title, row.folder_name, row.modified_at) for row in rows],
        )
    )


@app.command("get")
def notes_get(
    ctx: typer.Context,
    note_id: str = typer.Argument(...),
    with_attachments: bool = typer.Option(False, "--with-attachments"),
) -> None:
    """Get one note."""

    state = get_state(ctx)
    api = state.get_api()
    note = service_call(
        "Notes",
        lambda: api.notes.get(note_id, with_attachments=with_attachments),
    )
    if state.json_output:
        state.write_json(note)
        return
    state.console.print(f"{note.title} [{note.id}]")
    if note.text:
        state.console.print(note.text)
    if with_attachments and note.attachments:
        state.console.print(
            console_table(
                "Attachments",
                ["ID", "Filename", "UTI", "Size"],
                [(att.id, att.filename, att.uti, att.size) for att in note.attachments],
            )
        )


@app.command("render")
def notes_render(
    ctx: typer.Context,
    note_id: str = typer.Argument(...),
    preview_appearance: str = typer.Option("light", "--preview-appearance"),
    pdf_height: int = typer.Option(600, "--pdf-height"),
) -> None:
    """Render a note to HTML."""

    state = get_state(ctx)
    api = state.get_api()
    html = service_call(
        "Notes",
        lambda: api.notes.render_note(
            note_id,
            preview_appearance=preview_appearance,
            pdf_object_height=pdf_height,
        ),
    )
    if state.json_output:
        state.write_json({"note_id": note_id, "html": html})
        return
    state.console.print(html, soft_wrap=True)


@app.command("export")
def notes_export(
    ctx: typer.Context,
    note_id: str = typer.Argument(...),
    output_dir: Path = typer.Argument(...),
    export_mode: str = typer.Option("archival", "--export-mode"),
    assets_dir: Optional[Path] = typer.Option(None, "--assets-dir"),
    full_page: bool = typer.Option(True, "--full-page/--fragment"),
    preview_appearance: str = typer.Option("light", "--preview-appearance"),
    pdf_height: int = typer.Option(600, "--pdf-height"),
) -> None:
    """Export a note to disk."""

    state = get_state(ctx)
    api = state.get_api()
    path = service_call(
        "Notes",
        lambda: api.notes.export_note(
            note_id,
            str(output_dir),
            export_mode=export_mode,
            assets_dir=str(assets_dir) if assets_dir else None,
            full_page=full_page,
            preview_appearance=preview_appearance,
            pdf_object_height=pdf_height,
        ),
    )
    if state.json_output:
        state.write_json({"note_id": note_id, "path": path})
        return
    state.console.print(path)


@app.command("changes")
def notes_changes(
    ctx: typer.Context,
    since: Optional[str] = typer.Option(None, "--since", help="Sync cursor."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum changes to show."),
) -> None:
    """List note changes since a cursor."""

    state = get_state(ctx)
    api = state.get_api()
    rows = list(
        islice(
            service_call("Notes", lambda: api.notes.iter_changes(since=since)), limit
        )
    )
    if state.json_output:
        state.write_json(rows)
        return
    state.console.print(
        console_table(
            "Note Changes",
            ["Type", "Note ID", "Folder", "Modified"],
            [
                (
                    row.type,
                    row.note.id if row.note else row.note_id,
                    row.note.folder_name if row.note else None,
                    row.note.modified_at if row.note else None,
                )
                for row in rows
            ],
        )
    )
