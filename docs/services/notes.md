# Notes

[← Back to the documentation index](../index.md)

You can access your iCloud Notes through the `notes` property:

```python
notes = api.notes
```

The high-level Notes service exposes typed note, folder, and attachment models
for common workflows such as recent-note listings, full-note retrieval, HTML
rendering, and on-disk exports. Prefer `api.notes` for normal use and treat
`api.notes.raw` as an advanced/debug escape hatch when you need direct access to
the underlying CloudKit client.

_List recent notes:_

```python
notes = api.notes

for summary in notes.recents(limit=10):
    print(summary.id, summary.title, summary.modified_at)
```

_Iterate folders and list notes in one folder:_

```python
notes = api.notes

folder = next(iter(notes.folders()), None)
if folder:
    print(folder.id, folder.name, folder.has_subfolders)
    for summary in notes.in_folder(folder.id, limit=5):
        print(summary.title)
```

_Iterate all notes or capture a sync cursor for later incremental work:_

```python
notes = api.notes

cursor = notes.sync_cursor()
for summary in notes.iter_all():
    print(summary.id, summary.title)

print(cursor)
```

Persist the sync cursor from `sync_cursor()` and pass it back to
`iter_all(since=...)` or `iter_changes(since=...)` on a later run to enumerate
only newer changes.

_Fetch a full note with attachment metadata:_

```python
note_id = "YOUR_NOTE_ID"
note = api.notes.get(note_id, with_attachments=True)

print(note.title)
print(note.text)

for attachment in note.attachments or []:
    print(attachment.id, attachment.filename, attachment.uti, attachment.size)
```

_Render a note to an HTML fragment:_

```python
html_fragment = api.notes.render_note(
    note_id,
    preview_appearance="light",
    pdf_object_height=600,
)

print(html_fragment[:200])
```

`render_note()` returns an HTML fragment string and does not download assets or
write files to disk.

_Export a note to HTML on disk:_

```python
path = api.notes.export_note(
    note_id,
    "./exports/notes_html",
    export_mode="archival",
    assets_dir="./exports/assets",
    full_page=True,
)

print(path)
```

`export_note()` accepts `ExportConfig` keyword arguments such as
`export_mode`, `assets_dir`, `full_page`, `preview_appearance`, and
`pdf_object_height`.

- `export_mode="archival"` downloads assets locally and rewrites the HTML to
  use local file references for stable, offline-friendly output.
- `export_mode="lightweight"` skips local downloads and keeps remote/preview
  asset references for quick inspection.

_Save or stream an attachment:_

```python
note = api.notes.get(note_id, with_attachments=True)
attachment = next(iter(note.attachments or []), None)

if attachment:
    saved_path = attachment.save_to("./exports/notes_attachments", service=api.notes)
    print(saved_path)

    with open("./attachment-copy.bin", "wb") as file_out:
        for chunk in attachment.stream(service=api.notes):
            file_out.write(chunk)
```

Notes caveats:

- `get(note_id, password=...)` can read supported private notes locked with a
  custom Notes password. Without it, or if authentication fails, `get()`
  raises `NoteLockedError`. The password is used locally and is not sent to Apple.
- The returned note still has `is_locked=True`: reading it does not change
  its lock. Device-passcode locks, locked attachment access, and locked writes
  are not supported.
- `get()`, `render_note()`, and `export_note()` raise `NoteNotFound` when the
  note ID does not exist.
- `api.notes.raw` is available for advanced/debug workflows, but it is not the
  primary Notes API surface.

## Write Notes

Pick a private destination folder from `notes.folders()` and pass its ID explicitly:

```python
result = api.notes.create_note("Example", "First line", folder_id=folder_id)
print(result.id, result.record_change_tag)
```

Creation supports a single-line title and inline text, up to 64 KiB
including the title and line breaks. To edit, trash, or restore a supported note, pass the current
revision from `notes.get()` or the previous write result:

```python
edited = api.notes.update_note(
    result.id,
    title="Example",
    text="Second line",
    expected_change_tag=result.record_change_tag,
)
trashed = api.notes.trash_note(edited.id, expected_change_tag=edited.record_change_tag)
restored = api.notes.restore_note(
    trashed.id,
    folder_id=folder_id,
    expected_change_tag=trashed.record_change_tag,
)
```

Edits replace both the title and body. Trash moves the note to Recently Deleted;
restore requires an explicit destination. To irreversibly remove one supported
private note without attachments from Recently Deleted, call
`delete_note_forever(note_id, expected_change_tag=..., confirm=True)`.
Writing shared or locked notes is not supported. Full body replacement does not support
notes with attachments; see the targeted image-preserving edit below. If `NotesWriteUncertain`
is raised, look up its `note_id` before retrying: Apple may already have applied
the write. If the revision has changed, fetch the current note and review the
changes before submitting another write.

Pass a `RichNoteBody` instead of a string to create or replace a formatted
body. Supported paragraph styles are `body`, `heading`, `subheading`, `bullet`,
`dash`, `numbered`, and `checklist`. List items can specify `indent`, and checklist
items can set `checked=True`. Each run supports `bold`, `italic`, `underline`,
`strikethrough`, and an external HTTP(S) `link`. For example:

```python
from pyicloud.services.notes import NoteParagraph, NoteTextRun, RichNoteBody

body = RichNoteBody(
    paragraphs=(
        NoteParagraph(style="heading", runs=(NoteTextRun(text="Summary"),)),
        NoteParagraph(runs=(NoteTextRun(text="Important", bold=True),)),
        NoteParagraph(
            style="checklist",
            checked=True,
            runs=(NoteTextRun(text="Read the reference", link="https://example.com"),),
        ),
    )
)
result = api.notes.create_note("Example", body, folder_id=folder_id)
```

A plain-text update refuses to replace a formatted note. Use a rich body to
explicitly replace the formatting of a supported note. Unsupported formatting
or attachments still block a replacement rather than being silently discarded.

Use `set_checklist_item(note_id, item=1, checked=True, expected_change_tag=...)`
to check or uncheck an item without replacing the note. Items are numbered
from one in document order. Use `set_note_pinned(note_id, pinned=True,
expected_change_tag=...)` to change your own pin state without rewriting the
note or advancing its content revision.

Use `add_note_tag(note_id, tag="example", expected_change_tag=...)` to append an
ASCII tag without replacing the note. Use `remove_note_tag` to remove one tag
from that note without deleting the global hashtag. Tags are matched
case-insensitively. If a linked write raises `NotesLinkedWriteIncomplete`, inspect both
the note and the linked record before retrying; either record may already have
been written.

For a supported private note, use
`replace_note_text(note_id, old=..., new=..., expected_change_tag=...)` to
replace one unique, single-line fragment. Replacement text inherits the
formatting at the start of the match. This leaves the title, formatting outside
the match, and supported linked objects (including at most one JPEG or PNG) in place. It does
not add or remove attachments. `move_note(note_id, folder_id=..., expected_change_tag=...)`
moves an active private note, including a supported note with one existing image,
without changing its text or modified time.

Use `create_folder`, `rename_folder`, `move_folder`, and `delete_folder` for
private folders. Read `record_change_tag` from `notes.folders()` before changing
an existing folder. Pass `parent_id=None` to move a folder to the top level.
Folder deletion is irreversible. The client verifies that the folder is empty
before submitting the deletion, but Apple does not check emptiness atomically
with deletion. Do not add or move items into the folder concurrently.

## Notes CLI

The official Typer CLI exposes `icloud notes ...` for recent-note inspection,
folder browsing, title-based search, HTML rendering, note-id-based export,
and private note writes.

_List recent notes, folders, or one folder’s notes:_

```bash
icloud notes recent --username you@example.com
icloud notes folders --username you@example.com
icloud notes list --username you@example.com --folder-id FOLDER_ID
icloud notes list --username you@example.com --all --since PREVIOUS_CURSOR
```

_Create a plain-text note using a folder ID from `icloud notes folders`:_

```bash
icloud notes create --username you@example.com --folder-id FOLDER_ID \
  --title "Example" --body-file ./body.txt --format json
```

Omit `--body-file` to create a title-only note. The file must be UTF-8.

For rich text, pass `--rich-body-file ./body.json` instead of `--body-file`
to `notes create` or `notes update`. The UTF-8 JSON file uses the same
paragraph and run structure as the Python API:

```json
{ "paragraphs": [{ "style": "heading", "runs": [{ "text": "Summary" }] }] }
```

For existing notes, use the `record_change_tag` from `icloud notes get NOTE_ID
--format json` or the latest write result as `--if-match`. Use the new revision
after each successful write:

```bash
icloud notes update NOTE_ID --if-match REVISION --title "Example" --body-file ./body.txt
icloud notes trash NOTE_ID --if-match UPDATED_REVISION
icloud notes restore NOTE_ID --if-match TRASHED_REVISION --folder-id FOLDER_ID
icloud notes delete-forever NOTE_ID --if-match TRASHED_REVISION --confirm
icloud notes move NOTE_ID --if-match REVISION --folder-id DESTINATION_ID
icloud notes set-checklist-item NOTE_ID --if-match REVISION --item 1 --checked
icloud notes pin NOTE_ID --if-match REVISION
icloud notes pin NOTE_ID --if-match REVISION --unpin
icloud notes add-tag NOTE_ID --if-match REVISION --tag example
icloud notes remove-tag NOTE_ID --if-match REVISION --tag example
icloud notes replace-text NOTE_ID --if-match REVISION \
  --find-file ./old.txt --replacement-file ./new.txt
```

To append a table without replacing existing note content, pass a UTF-8 JSON
file containing equal-length rows, such as `[["Item", "Status"], ["Example", ""]]`.
Use `get-table` to read the grid, table ID, and both current revisions. Table,
row, and column numbers start at 1. An empty UTF-8 file clears a cell.

```bash
icloud notes add-table NOTE_ID --if-match NOTE_REVISION --rows-file ./rows.json
icloud notes get-table NOTE_ID --table 1 --format json
icloud notes set-table-cell NOTE_ID --table-id TABLE_ID --row 2 --column 2 \
  --if-match NOTE_REVISION --table-if-match TABLE_REVISION --body-file ./cell.txt
```

Use `resize-table` to insert a blank row or column before `--index` (or append
at the current size plus one). To delete a row or column at that index, use
`--action delete`. Deleting populated cells requires `--confirm-nonempty`.
Refresh both revisions after each change.
Resizing also refuses tables whose object-replica clock cannot be joined (for
example, some browser-edited or multi-session tables), reporting "Could not
verify the Notes table structure or text history"; re-read the note and table
and, if the structure remains unsupported, re-create the table rather than
retrying blindly.

```bash
icloud notes resize-table NOTE_ID --table-id TABLE_ID --axis row --action insert \
  --index 2 --if-match NOTE_REVISION --table-if-match TABLE_REVISION
```

To remove a whole table and its contents from the note, use `remove_note_table`
or the following command. This unlinks the table; it does not delete the separate
table record.

```bash
icloud notes remove-table NOTE_ID --table-id TABLE_ID --if-match NOTE_REVISION \
  --table-if-match TABLE_REVISION --confirm
```

The Python equivalents are `add_note_table`, `get_note_table`,
`update_note_table_cell`, and `resize_note_table`. These commands work on
supported private notes. Cell formatting is not supported by the writer.
Table creation and edits each involve two records. If a write
reports a partial or uncertain result, read the note and table again before
deciding how to proceed. Do not automatically repeat the command.

Get the folder revision with `icloud notes folders --format json`, then use:

```bash
icloud notes create-folder --name "Example"
icloud notes rename-folder FOLDER_ID --name "New name" --if-match REVISION
icloud notes move-folder FOLDER_ID --parent-id PARENT_ID --if-match REVISION
icloud notes move-folder FOLDER_ID --to-root --if-match REVISION
icloud notes delete-folder FOLDER_ID --if-match REVISION --confirm-empty
```

_Search notes by title:_

```bash
icloud notes search --username you@example.com --title "Daily Plan"
icloud notes search --username you@example.com --title-contains "meeting"
```

`icloud notes search` is the official title-filter workflow. It uses a
recents-first search strategy and falls back to a full feed scan when needed.

_Fetch, render, and export one note by id:_

```bash
icloud notes get NOTE_ID --username you@example.com --with-attachments
icloud notes render NOTE_ID --username you@example.com --preview-appearance dark
icloud notes export NOTE_ID \
  --username you@example.com \
  --output-dir ./exports/notes_html \
  --export-mode archival \
  --assets-dir ./exports/assets
```

Use `icloud notes get NOTE_ID --unlock` for a supported locked note. The
custom Notes password is entered at a hidden terminal prompt, not on the
command line. Do not combine this with `--with-attachments`. The command
prints the decrypted note, so avoid redirecting the output to an insecure
file or using it in a recorded terminal.

`icloud notes export` stays explicit by note id. Title filters are intentionally
handled by `icloud notes search` rather than by bulk export flags.

_Inspect incremental changes:_

```bash
icloud notes changes --username you@example.com --since PREVIOUS_CURSOR
icloud notes sync-cursor --username you@example.com
```

## Notes CLI Example

[`examples/notes_cli.py`](https://github.com/timlaing/pyicloud/blob/main/examples/notes_cli.py) is a local developer utility
built on top of `api.notes`. It is useful for searching notes, inspecting the
rendering pipeline, and exporting HTML, but its selection heuristics and debug
output are convenience behavior rather than part of the Notes service contract.

_Archival export (downloads local assets):_

```bash
uv run python examples/notes_cli.py \
  --username you@example.com \
  --title "My Note" \
  --max 1 \
  --output-dir ./exports/notes_html \
  --assets-dir ./exports/assets \
  --export-mode archival \
  --full-page
```

_Lightweight export (skips local asset downloads):_

```bash
uv run python examples/notes_cli.py \
  --username you@example.com \
  --title-contains "meeting" \
  --max 3 \
  --output-dir ./exports/notes_html \
  --export-mode lightweight
```

Important CLI flags:

- `--title` filters by exact note title.
- `--title-contains` filters by case-insensitive title substring.
- `--max` limits how many matching notes are exported.
- `--output-dir` selects the directory for saved HTML output.
- `--export-mode archival|lightweight` controls whether assets are downloaded
  locally (`archival`) or left as remote/preview references (`lightweight`).
- `--assets-dir` selects the base directory for downloaded assets in archival
  mode.
- `--full-page` wraps saved output in a complete HTML page. If omitted, the CLI
  saves an HTML fragment.
- `--notes-debug` enables verbose Notes/export debugging.
- `--dump-runs` prints attribute runs and writes an annotated mapping under
  `workspace/notes_runs`.
- `--preview-appearance light|dark` selects the preferred preview variant when
  multiple appearances are available.
- `--pdf-height` sets the pixel height for embedded PDF `<object>` elements.

`--download-assets` is no longer supported in the example CLI. Use
`--export-mode` to choose between archival and lightweight export behavior.
