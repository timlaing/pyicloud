# Notes

[← Back to the documentation index](../README.md)

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

for summary in notes.iter_all():
    print(summary.id, summary.title)

cursor = notes.sync_cursor()
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

- `get()` raises `NoteLockedError` for passphrase-locked notes whose content
  cannot be read.
- `get()`, `render_note()`, and `export_note()` raise `NoteNotFound` when the
  note ID does not exist.
- `api.notes.raw` is available for advanced/debug workflows, but it is not the
  primary Notes API surface.

## Notes CLI

The official Typer CLI exposes `icloud notes ...` for recent-note inspection,
folder browsing, title-based search, HTML rendering, and note-id-based export.

_List recent notes, folders, or one folder’s notes:_

```bash
icloud notes recent --username you@example.com
icloud notes folders --username you@example.com
icloud notes list --username you@example.com --folder-id FOLDER_ID
icloud notes list --username you@example.com --all --since PREVIOUS_CURSOR
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

`icloud notes export` stays explicit by note id. Title filters are intentionally
handled by `icloud notes search` rather than by bulk export flags.

_Inspect incremental changes:_

```bash
icloud notes changes --username you@example.com --since PREVIOUS_CURSOR
icloud notes sync-cursor --username you@example.com
```

## Notes CLI Example

[`examples/notes_cli.py`](../../examples/notes_cli.py) is a local developer utility
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
