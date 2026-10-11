# Examples

Runnable scripts that exercise the library end to end live in the
[`examples/`](https://github.com/timlaing/pyicloud/tree/main/examples) directory
of the repository. They authenticate against a real iCloud account, so run them
with a throwaway account: several of them create, update, and delete real data.

## [`examples.py`](https://github.com/timlaing/pyicloud/blob/main/examples/examples.py)

A broad end-to-end tour of [`PyiCloudService`](api/pyicloud-service.md) and
the major services (account, devices, calendar, photos, and drive). It is the
best starting point for seeing how the pieces fit together against a live
account.

## [`example_reminders.py`](https://github.com/timlaing/pyicloud/blob/main/examples/example_reminders.py)

A comprehensive integration validator for the
[Reminders](services/reminders.md) service. It exercises list discovery and
the read/write surface (create, update, delete), location triggers, hashtags,
attachments, recurrence rules, and delete flows. Pass `--cleanup` to soft-delete
the reminders it generates.

## [`example_reminders_delta.py`](https://github.com/timlaing/pyicloud/blob/main/examples/example_reminders_delta.py)

A focused live validator for the Reminders delta-sync APIs, `sync_cursor()` and
`iter_changes(since=...)`. The [Reminders service page](services/reminders.md)
explains how the cursor workflow fits together.

## [`notes_cli.py`](https://github.com/timlaing/pyicloud/blob/main/examples/notes_cli.py)

A developer utility for searching, inspecting, and exporting iCloud
[Notes](services/notes.md). It is useful for exploring the rendering pipeline
and producing HTML exports, but its selection heuristics and debug output are
convenience behaviour rather than part of the Notes service contract.
