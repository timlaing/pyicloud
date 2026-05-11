These fixtures back service tests that need stable CloudKit or rendering payloads.

They are organized by service where practical:

- Root `photos_*.json` files are existing Photos CloudKit read, upload, and
  shared-library fixtures. They remain flat for compatibility with the current
  Photos tests.
- `photos_browser_mutations/` contains sanitized browser-derived Photos mutation
  request and response fixtures.
- `notes/` contains synthetic Notes CloudKit fixtures and the Notes rendering
  fixture.
- `reminders/` contains synthetic Reminders CloudKit fixtures.

Tracked fixtures intentionally exclude raw HAR files, cookies, headers, account
identifiers, live signed URLs, and binary media payloads. Any live captures used
to derive fixtures stay in local workspace-only directories and are redacted or
rewritten with stable synthetic values before promotion into `tests/fixtures/`.
