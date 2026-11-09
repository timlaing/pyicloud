"""Notes commands."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Any

from pydantic import StrictStr, TypeAdapter, ValidationError
from rich.markup import escape
import typer

from pyicloud.base import PyiCloudService
from pyicloud.cli.command_options import CommandOptions, with_command_options
from pyicloud.cli.context import CLIAbort, CLIState, get_state, service_call
from pyicloud.cli.normalize import (
    normalize_sync_cursor,
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
from pyicloud.services.notes.encoding import MAX_INLINE_NOTE_BYTES
from pyicloud.services.notes.exceptions import NoteLockedError, NoteNotFound, NotesError
from pyicloud.services.notes.models.rich import RichNoteBody

app = typer.Typer(help="Inspect, render, export, and manage Notes.")

NOTES = "Notes"

_MAX_NOTES_TO_SHOW_HELP = "Maximum notes to show."
_NOTE_ID_HELP = "Note id."
_UPDATED_NOTE = "Updated note"
_FOLDER_ID_HELP = "Folder id."
_REVISION_HELP = "Current record_change_tag from notes get --format json."
_FOLDER_REVISION_HELP = "Current record_change_tag from notes folders --format json."
_TABLE_NOTE_REVISION_HELP = (
    "Current record_change_tag from notes get-table --format json."
)


class PreviewAppearance(str, Enum):
    """Supported Notes preview appearances."""

    LIGHT = "light"
    DARK = "dark"


class ExportMode(str, Enum):
    """Supported Notes export modes."""

    ARCHIVAL = "archival"
    LIGHTWEIGHT = "lightweight"


def _notes_service(api: PyiCloudService) -> Any:
    """Return the Notes service with reauthentication handling."""

    return service_call(NOTES, lambda: api.notes, account_name=api.account_name)


def _notes_call(api: PyiCloudService, fn: Callable[[], Any]) -> Any:
    """Wrap Notes service calls with note-specific user-facing errors."""

    try:
        return service_call(NOTES, fn, account_name=api.account_name)
    except (NoteNotFound, NoteLockedError) as err:
        raise CLIAbort(str(err)) from err


def _print_note_rows(state: CLIState, title: str, rows: list[Any]) -> None:
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
    limit: int = typer.Option(10, "--limit", min=1, help=_MAX_NOTES_TO_SHOW_HELP),
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
    folder_id: str | None = typer.Option(None, "--folder-id", help=_FOLDER_ID_HELP),
    all_notes: bool = typer.Option(False, "--all", help="Iterate all notes."),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Incremental sync cursor for --all.",
    ),
    limit: int = typer.Option(50, "--limit", min=1, help=_MAX_NOTES_TO_SHOW_HELP),
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
    limit: int = typer.Option(10, "--limit", min=1, help=_MAX_NOTES_TO_SHOW_HELP),
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
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
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


def _read_note_body(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_INLINE_NOTE_BYTES + 1)
        if len(data) > MAX_INLINE_NOTE_BYTES:
            raise CLIAbort("The note body file must not exceed 64 KiB.")
        return data.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        raise CLIAbort("Could not read the note body file as UTF-8.") from None


def _note_write_body(
    body_file: Path | None, rich_body_file: Path | None, *, required: bool
) -> str | RichNoteBody:
    if rich_body_file is not None:
        if body_file is not None:
            raise CLIAbort("Choose either --body-file or --rich-body-file, not both.")
        try:
            return RichNoteBody.model_validate_json(_read_note_body(rich_body_file))
        except ValidationError:
            raise CLIAbort(
                "Invalid rich note body. Expected paragraphs and text runs as JSON."
            ) from None
    if body_file is not None:
        return _read_note_body(body_file)
    if required:
        raise CLIAbort("Pass --body-file or --rich-body-file for the replacement body.")
    return ""


def _notes_write_call(
    ctx: typer.Context, fn: Callable[[Any], Any]
) -> tuple[CLIState, Any]:
    state = get_state(ctx)
    api = state.get_api()
    notes = _notes_service(api)
    try:
        return state, _notes_call(api, lambda: fn(notes))
    except (NotesError, ValueError) as err:
        raise CLIAbort(str(err)) from None


def _notes_write(ctx: typer.Context, label: str, fn: Callable[[Any], Any]) -> None:
    state, result = _notes_write_call(ctx, fn)
    if state.json_output:
        state.write_json(result)
        return
    state.console.print(f"{label}: {result.id}", markup=False)


@app.command("create")
@with_command_options
def notes_create(
    ctx: typer.Context,
    common: CommandOptions,
    title: str = typer.Option(..., "--title", help="Single-line note title."),
    folder_id: str = typer.Option(..., "--folder-id", help=_FOLDER_ID_HELP),
    body_file: Path | None = typer.Option(
        None, "--body-file", dir_okay=False, help="Read plain text from a UTF-8 file."
    ),
    rich_body_file: Path | None = typer.Option(
        None,
        "--rich-body-file",
        dir_okay=False,
        help="Read rich-text paragraphs from a UTF-8 JSON file.",
    ),
) -> None:
    """Create a plain- or rich-text note in a private iCloud Notes folder."""
    body = _note_write_body(body_file, rich_body_file, required=False)
    common.store(ctx)
    _notes_write(
        ctx,
        "Created note",
        lambda notes: notes.create_note(title, body, folder_id=folder_id),
    )


@app.command("update")
@with_command_options
def notes_update(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    title: str = typer.Option(..., "--title", help="Full replacement title."),
    body_file: Path | None = typer.Option(
        None, "--body-file", dir_okay=False, help="Full UTF-8 plain-text replacement."
    ),
    rich_body_file: Path | None = typer.Option(
        None,
        "--rich-body-file",
        dir_okay=False,
        help="Full UTF-8 rich-text JSON replacement.",
    ),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Replace the title and body of a supported note."""
    body = _note_write_body(body_file, rich_body_file, required=True)
    common.store(ctx)
    _notes_write(
        ctx,
        _UPDATED_NOTE,
        lambda notes: notes.update_note(
            note_id, title=title, text=body, expected_change_tag=if_match
        ),
    )


@app.command("set-checklist-item")
@with_command_options
def notes_set_checklist_item(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    item: int = typer.Option(
        ..., "--item", min=1, help="Checklist item number in document order."
    ),
    checked: bool | None = typer.Option(
        None, "--checked/--unchecked", help="Set the item's checked state."
    ),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Check or uncheck a checklist item without replacing the note."""
    if checked is None:
        raise CLIAbort("Choose --checked or --unchecked.")
    common.store(ctx)
    _notes_write(
        ctx,
        _UPDATED_NOTE,
        lambda notes: notes.set_checklist_item(
            note_id,
            item=item,
            checked=checked,
            expected_change_tag=if_match,
        ),
    )


@app.command("add-tag")
@with_command_options
def notes_add_tag(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    tag: str = typer.Option(..., "--tag", help="Tag text, with an optional leading #."),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Add a tag without replacing the note's existing content."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Tagged note",
        lambda notes: notes.add_note_tag(
            note_id, tag=tag, expected_change_tag=if_match
        ),
    )


@app.command("add-table")
@with_command_options
def notes_add_table(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    rows_file: Path = typer.Option(
        ..., "--rows-file", dir_okay=False, help="UTF-8 JSON array of text rows."
    ),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Append a plain-text table without replacing existing note content."""
    try:
        rows = TypeAdapter(list[list[StrictStr]]).validate_json(
            _read_note_body(rows_file)
        )
    except ValidationError:
        raise CLIAbort(
            "Expected a JSON array of rows containing only strings."
        ) from None
    common.store(ctx)
    _notes_write(
        ctx,
        "Added table to note",
        lambda notes: notes.add_note_table(
            note_id, rows=rows, expected_change_tag=if_match
        ),
    )


@app.command("get-table")
@with_command_options
def notes_get_table(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    table: int = typer.Option(1, "--table", min=1, help="One-based table number."),
) -> None:
    """Read a plain-text table and its edit revisions."""
    common.store(ctx)
    state, result = _notes_write_call(
        ctx, lambda notes: notes.get_note_table(note_id, table=table)
    )
    if state.json_output:
        state.write_json(result)
        return
    columns = [str(index) for index in range(1, len(result.rows[0]) + 1)]
    rows = [[escape(cell) for cell in row] for row in result.rows]
    state.console.print(console_table("Notes table", columns, rows))


@app.command("set-table-cell")
@with_command_options
def notes_set_table_cell(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    table_id: str = typer.Option(
        ..., "--table-id", help="Table ID from notes get-table."
    ),
    row: int = typer.Option(..., "--row", min=1, help="One-based row number."),
    column: int = typer.Option(..., "--column", min=1, help="One-based column number."),
    body_file: Path = typer.Option(
        ...,
        "--body-file",
        dir_okay=False,
        help="Read the replacement cell text from a UTF-8 file.",
    ),
    if_match: str = typer.Option(..., "--if-match", help=_TABLE_NOTE_REVISION_HELP),
    table_if_match: str = typer.Option(
        ...,
        "--table-if-match",
        help="Current table_change_tag from notes get-table --format json.",
    ),
) -> None:
    """Replace one plain-text table cell without changing the rest of the table."""
    body = _read_note_body(body_file)
    common.store(ctx)
    _notes_write(
        ctx,
        "Updated table cell in note",
        lambda notes: notes.update_note_table_cell(
            note_id,
            table_id=table_id,
            row=row,
            column=column,
            text=body,
            expected_change_tag=if_match,
            expected_table_change_tag=table_if_match,
        ),
    )


@app.command("pin")
@with_command_options
def notes_pin(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    unpin: bool = typer.Option(False, "--unpin", help="Remove the note's pin instead."),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Pin or unpin an active private note without changing its body."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Unpinned note" if unpin else "Pinned note",
        lambda notes: notes.set_note_pinned(
            note_id,
            pinned=not unpin,
            expected_change_tag=if_match,
        ),
    )


@app.command("trash")
@with_command_options
def notes_trash(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Move a supported note to Recently Deleted."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Trashed note",
        lambda notes: notes.trash_note(note_id, expected_change_tag=if_match),
    )


@app.command("restore")
@with_command_options
def notes_restore(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    folder_id: str = typer.Option(..., "--folder-id", help=_FOLDER_ID_HELP),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Restore a supported note to an explicit private folder."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Restored note",
        lambda notes: notes.restore_note(
            note_id, folder_id=folder_id, expected_change_tag=if_match
        ),
    )


@app.command("replace-text")
@with_command_options
def notes_replace_text(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    find_file: Path = typer.Option(
        ..., "--find-file", dir_okay=False, help="Single-line UTF-8 text to find."
    ),
    replacement_file: Path = typer.Option(
        ...,
        "--replacement-file",
        dir_okay=False,
        help="Single-line UTF-8 replacement text.",
    ),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Replace one text fragment without removing an existing image."""
    old = _read_note_body(find_file).removesuffix("\n")
    new = _read_note_body(replacement_file).removesuffix("\n")
    common.store(ctx)
    _notes_write(
        ctx,
        _UPDATED_NOTE,
        lambda notes: notes.replace_note_text(
            note_id,
            old=old,
            new=new,
            expected_change_tag=if_match,
        ),
    )


@app.command("move")
@with_command_options
def notes_move(
    ctx: typer.Context,
    common: CommandOptions,
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
    folder_id: str = typer.Option(..., "--folder-id", help=_FOLDER_ID_HELP),
    if_match: str = typer.Option(..., "--if-match", help=_REVISION_HELP),
) -> None:
    """Move an active private note to a different private folder."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Moved note",
        lambda notes: notes.move_note(
            note_id, folder_id=folder_id, expected_change_tag=if_match
        ),
    )


@app.command("create-folder")
@with_command_options
def notes_create_folder(
    ctx: typer.Context,
    common: CommandOptions,
    name: str = typer.Option(..., "--name", help="Name of the new folder."),
    parent_id: str | None = typer.Option(
        None, "--parent-id", help="Parent folder ID, if nested."
    ),
) -> None:
    """Create a private Notes folder."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Created folder",
        lambda notes: notes.create_folder(name, parent_id=parent_id),
    )


@app.command("rename-folder")
@with_command_options
def notes_rename_folder(
    ctx: typer.Context,
    common: CommandOptions,
    folder_id: str = typer.Argument(..., help=_FOLDER_ID_HELP),
    name: str = typer.Option(..., "--name", help="Replacement folder name."),
    if_match: str = typer.Option(..., "--if-match", help=_FOLDER_REVISION_HELP),
) -> None:
    """Rename a private Notes folder."""
    common.store(ctx)
    _notes_write(
        ctx,
        "Renamed folder",
        lambda notes: notes.rename_folder(
            folder_id, name, expected_change_tag=if_match
        ),
    )


@app.command("move-folder")
@with_command_options
def notes_move_folder(
    ctx: typer.Context,
    common: CommandOptions,
    folder_id: str = typer.Argument(..., help=_FOLDER_ID_HELP),
    parent_id: str | None = typer.Option(
        None, "--parent-id", help="Destination parent folder ID."
    ),
    to_root: bool = typer.Option(
        False, "--to-root", help="Move the folder to the top level."
    ),
    if_match: str = typer.Option(..., "--if-match", help=_FOLDER_REVISION_HELP),
) -> None:
    """Move a private Notes folder to a parent folder or the top level."""
    if (parent_id is None) == (not to_root):
        raise CLIAbort("Choose exactly one of --parent-id and --to-root.")
    common.store(ctx)
    _notes_write(
        ctx,
        "Moved folder",
        lambda notes: notes.move_folder(
            folder_id, parent_id=parent_id, expected_change_tag=if_match
        ),
    )


@app.command("delete-folder")
@with_command_options
def notes_delete_folder(
    ctx: typer.Context,
    common: CommandOptions,
    folder_id: str = typer.Argument(..., help=_FOLDER_ID_HELP),
    if_match: str = typer.Option(..., "--if-match", help=_FOLDER_REVISION_HELP),
    confirm_empty: bool = typer.Option(
        False,
        "--confirm-empty",
        help="Permanently delete the folder after checking that it is empty.",
    ),
) -> None:
    """Permanently delete a verified empty private Notes folder."""
    if not confirm_empty:
        raise CLIAbort("Pass --confirm-empty to permanently delete an empty folder.")
    common.store(ctx)
    state, deleted = _notes_write_call(
        ctx,
        lambda notes: notes.delete_folder(
            folder_id, expected_change_tag=if_match, confirm_empty=True
        ),
    )
    if state.json_output:
        state.write_json({"id": folder_id, "deleted": deleted})
        return
    state.console.print(f"Deleted folder: {folder_id}", markup=False)


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
    ctx: typer.Context,  # noqa: S107
    note_id: str = typer.Argument(..., help=_NOTE_ID_HELP),
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
    cursor = _notes_call(api, notes.sync_cursor)
    if state.json_output:
        state.write_json(normalize_sync_cursor(cursor))
        return
    state.console.print(cursor)
