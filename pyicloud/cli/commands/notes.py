"""Notes commands."""

from __future__ import annotations

from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Optional

import typer

from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import (
    search_notes_by_title,
    select_recent_notes,
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
from pyicloud.services.notes.service import NoteLockedError, NoteNotFound

app = typer.Typer(help="Inspect, render, and export Notes.")

NOTES = "Notes"


class PreviewAppearance(str, Enum):
    """Supported Notes preview appearances."""

    LIGHT = "light"
    DARK = "dark"


class ExportMode(str, Enum):
    """Supported Notes export modes."""

    ARCHIVAL = "archival"
    LIGHTWEIGHT = "lightweight"


def _notes_service(api):
    """Return the Notes service with reauthentication handling."""

    return service_call(NOTES, lambda: api.notes, account_name=api.account_name)


def _notes_call(api, fn):
    """Wrap Notes service calls with note-specific user-facing errors."""

    try:
        return service_call(NOTES, fn, account_name=api.account_name)
    except (NoteNotFound, NoteLockedError) as err:
        raise CLIAbort(str(err)) from err


def _print_note_rows(state, title: str, rows) -> None:
    """Render note summary rows in text mode."""

    state.console.print(
        console_table(
            title,
            ["ID", "Title", "Folder", "Modified", "Deleted"],
            [
                (
                    row.id,
                    row.title,
                    row.folder_name,
                    row.modified_at,
                    getattr(row, "is_deleted", False),
                )
                for row in rows
            ],
        )
    )


@app.command("recent")
def notes_recent(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum notes to show."),
    include_deleted: bool = typer.Option(
        False,
        "--include-deleted",
        help="Include notes from Recently Deleted.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List recent notes."""

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
    notes = _notes_service(api)
    payload = _notes_call(
        api,
        lambda: select_recent_notes(
            notes,
            limit=limit,
            include_deleted=include_deleted,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    _print_note_rows(state, "Recent Notes", payload)


@app.command("folders")
def notes_folders(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List note folders."""

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
    notes = _notes_service(api)
    payload = _notes_call(api, lambda: list(notes.folders()))
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Note Folders",
            ["ID", "Name", "Has Subfolders", "Count"],
            [(row.id, row.name, row.has_subfolders, row.count) for row in payload],
        )
    )


@app.command("list")
def notes_list(
    ctx: typer.Context,
    folder_id: Optional[str] = typer.Option(None, "--folder-id", help="Folder id."),
    all_notes: bool = typer.Option(False, "--all", help="Iterate all notes."),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="Incremental sync cursor for --all.",
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum notes to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List notes."""

    if folder_id and all_notes:
        raise typer.BadParameter("Choose either --folder-id or --all, not both.")
    if since and not all_notes:
        raise typer.BadParameter("The --since option requires --all.")

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
    notes = _notes_service(api)
    if folder_id:
        payload = _notes_call(
            api, lambda: list(notes.in_folder(folder_id, limit=limit))
        )
    elif all_notes:
        payload = _notes_call(
            api,
            lambda: list(islice(notes.iter_all(since=since), limit)),
        )
    else:
        payload = _notes_call(
            api,
            lambda: select_recent_notes(
                notes,
                limit=limit,
                include_deleted=False,
            ),
        )
    if state.json_output:
        state.write_json(payload)
        return
    _print_note_rows(state, "Notes", payload)


@app.command("search")
def notes_search(
    ctx: typer.Context,
    title: str = typer.Option("", "--title", help="Exact note title."),
    title_contains: str = typer.Option(
        "",
        "--title-contains",
        help="Case-insensitive note title substring.",
    ),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum notes to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Search notes by title."""

    if not title.strip() and not title_contains.strip():
        raise CLIAbort("Pass --title or --title-contains to search notes.")

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
    notes = _notes_service(api)
    payload = _notes_call(
        api,
        lambda: search_notes_by_title(
            notes,
            title=title,
            title_contains=title_contains,
            limit=limit,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    _print_note_rows(state, "Matching Notes", payload)


@app.command("get")
def notes_get(
    ctx: typer.Context,
    note_id: str = typer.Argument(..., help="Note id."),
    with_attachments: bool = typer.Option(
        False,
        "--with-attachments",
        help="Include attachment metadata.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Get one note."""

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
    notes = _notes_service(api)
    note = _notes_call(
        api, lambda: notes.get(note_id, with_attachments=with_attachments)
    )
    if state.json_output:
        state.write_json(note)
        return
    state.console.print(f"{note.title} [{note.id}]")
    if note.folder_name:
        state.console.print(f"Folder: {note.folder_name}")
    if note.modified_at:
        state.console.print(f"Modified: {note.modified_at}")
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
    note_id: str = typer.Argument(..., help="Note id."),
    preview_appearance: PreviewAppearance = typer.Option(
        PreviewAppearance.LIGHT,
        "--preview-appearance",
        help="Preview appearance preference.",
    ),
    pdf_height: int = typer.Option(
        600,
        "--pdf-height",
        min=1,
        help="Embedded PDF height in pixels.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Render a note to HTML."""

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
    notes = _notes_service(api)
    html = _notes_call(
        api,
        lambda: notes.render_note(
            note_id,
            preview_appearance=preview_appearance.value,
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
    note_id: str = typer.Argument(..., help="Note id."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Destination directory."),
    export_mode: ExportMode = typer.Option(
        ExportMode.ARCHIVAL,
        "--export-mode",
        help="Export mode.",
    ),
    assets_dir: Path | None = typer.Option(
        None,
        "--assets-dir",
        help="Directory for downloaded assets in archival mode.",
    ),
    full_page: bool = typer.Option(
        True,
        "--full-page/--fragment",
        help="Wrap exported output in a full HTML page.",
    ),
    preview_appearance: PreviewAppearance = typer.Option(
        PreviewAppearance.LIGHT,
        "--preview-appearance",
        help="Preview appearance preference.",
    ),
    pdf_height: int = typer.Option(
        600,
        "--pdf-height",
        min=1,
        help="Embedded PDF height in pixels.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Export a note to disk."""

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
    notes = _notes_service(api)
    path = _notes_call(
        api,
        lambda: notes.export_note(
            note_id,
            str(output_dir),
            export_mode=export_mode.value,
            assets_dir=str(assets_dir) if assets_dir else None,
            full_page=full_page,
            preview_appearance=preview_appearance.value,
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
    """List note changes since a cursor."""

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
    notes = _notes_service(api)
    payload = _notes_call(
        api,
        lambda: list(islice(notes.iter_changes(since=since), limit)),
    )
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Note Changes",
            ["Type", "Note ID", "Folder", "Modified"],
            [
                (row.type, row.note.id, row.note.folder_name, row.note.modified_at)
                for row in payload
            ],
        )
    )


@app.command("sync-cursor")
def notes_sync_cursor(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Print the current Notes sync cursor."""

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
    notes = _notes_service(api)
    cursor = _notes_call(api, lambda: notes.sync_cursor())
    if state.json_output:
        state.write_json({"cursor": cursor})
        return
    state.console.print(cursor)
