# PyiCloud Documentation

Full, topic-focused documentation for PyiCloud. The
[project README](https://github.com/timlaing/pyicloud#readme) covers installation
and a quick authentication example; the pages below go deeper.

## Guides

Cross-cutting, task-oriented guides for using the library and CLI.

- [Authentication](guides/authentication.md) — logging in, `pause_2fa`, and the
  two-step / two-factor (2SA/2FA) flow
- [Command-Line Interface](guides/cli.md) — the `icloud` command, auth and
  session management, and keyring password storage
- [Diagnostics](guides/diagnostics.md) — `icloud doctor` and its exit codes

## Services

One page per iCloud service, mirroring `pyicloud/services/`.

- [Account](services/account.md) — account info, storage usage, and summary plan
- [Devices & Find My](services/devices.md) — listing devices, location, play
  sound, lost mode, and erase
- [Calendar](services/calendar.md) — calendars, events, alarms, and invitees
- [Contacts](services/contacts.md) — the address book and the user's MeCard
- [File Storage (Drive)](services/drive.md) — iCloud Drive and the legacy
  Ubiquity API
- [Photos](services/photos.md) — browsing, syncing, uploading, and modifying the
  photo library
- [Hide My Email](services/hidemyemail.md) — generating and managing email aliases
- [Reminders](services/reminders.md) — lists, reminders, alarms, hashtags,
  attachments, and recurrence rules
- [Notes](services/notes.md) — folders, note rendering, and HTML export
- [Invites](services/invites.md) — reading events and responding to invitations

## API Reference

Auto-generated reference for the public Python API and the `icloud` command.

- [PyiCloudService](api/pyicloud-service.md) — the main entry point and its
  service properties
- [Services](api/services.md) — the per-service classes
- [Exceptions](api/exceptions.md) — the exception hierarchy
- [Command-Line Interface](api/cli.md) — the generated `icloud` command reference
