# PyiCloud Documentation

[← Back to the project README](../README.md)

Full, topic-focused documentation for PyiCloud. The [project README](../README.md)
covers installation and a quick authentication example; the pages below go deeper.

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

## Research

Design notes and wire-protocol references for maintainers.

- [Invites service design](research/invites_service_design.md) — the
  invites/CloudKit record topology and dual-write pattern
