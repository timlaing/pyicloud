# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog].

Releases from `2.0.0b6` up to `2.7.0` follow [Semantic Versioning](https://semver.org/). Starting with the next release after `2.7.0`, versions will follow [CalVer](https://calver.org/) format (`YYYY.M.PATCH`).

## [Unreleased]

### Fixed

- Explain fresh-state recovery for unsupported Mail sync histories and avoid duplicate folder discovery around exact UID refreshes while retaining both identity guards.
- Keep Mail Drop download TLS verification and proxy isolation independent of CLI SSL and proxy overrides.
- Accept observed flag-less NIL Mail thread invalidations through checked thread/UID metadata refreshes, without interpreting them as empty flags or permanent deletion.
- Preserve Mail Drop capability metadata for search-pagination messages, keep caller exceptions unchanged through `mail_drop_attachment()`, name the Mail Drop transport failure class instead of a generic message, and stop suggesting a nonexistent `--limit` option when a `download-maildrop` folder lacks UID validity.
- Accept a valid SMS two-factor code that Apple answers with HTTP 412 and a repair session, continuing to session trust instead of reporting an incorrect code.
- Guard Mail body and attachment reads against observed mailbox recreation and path changes when UID validity is available; reject in-flight renames for path-selected preview/filter reads too, use the current folder name, and prevent failed downloads from being published.
- Explain the explicit `--limit N` fallback when Mail CLI exact lookup cannot use a folder without positive UID validity; never silently broaden the lookup.
- Accept whitespace-padded plain-text and HTML body media types without changing the supported types or marking messages read.
- Reject whitespace-padded HTML attachment responses and report advertised Mail endpoints with credentials, query strings, or fragments as malformed in diagnostics.
- Parse the MFA bootstrap HTML when Apple's authentication endpoint returns HTTP 409, allowing two-factor authentication to continue instead of raising an API error containing the HTML page.
- Keep an absent optional Mail endpoint visible in `icloud doctor` without failing the account check, and skip its unavailable probe with `--probe`; malformed advertised endpoints and missing required services still fail.

### Added

- Add experimental, read-only `icloud mail sync` for a one-shot folder-scoped metadata inventory on POSIX, with explicit private state storage, bounded requests, content-free summaries, and separate opt-ins for expired-history rebuilds and count-hint rescans. It neither downloads content nor modifies mail, and is not an atomic backup or watcher.
- Add explicit single-message Mail moves through `MailService.move()` and `icloud mail move`, with fresh mailbox guards, mailbox-only patches, source-only acknowledgements, and no automatic retries. Trash/restore use explicit folder destinations; Junk, Drafts, labels, and nonzero-flag folders remain unsupported.
- Add explicit, size-bounded Mail Drop downloads through `MailService.mail_drop_attachment()` and `icloud mail download-maildrop`, using verified Apple capability URLs, isolated credential-free transport, mandatory content TLS verification, mailbox guards, and private non-overwriting output.
- Add explicit cursor enumeration of unread/flagged thread identities through `MailService.iter_filtered_threads()` and `icloud mail filter-threads`, with checked mailbox scope, typed metadata, empty terminal responses, and explicit failures on incomplete or non-progressing scans. Bounded thread digests and full-text search remain unchanged.
- Add original MIME/EML streaming with `MailService.raw_message()` and private, non-overwriting `icloud mail export-eml` downloads, preserving Apple's returned message bytes without reconstruction or new dependencies and checking mailbox identity around retrieval.
- Add `icloud mail get` for exact metadata-only lookup by message ID or folder-local UID, without scanning messages or fetching bodies.
- Add explicit single-message Mail read/unread and flag/unflag updates through `set_read()`, `set_flagged()`, and `icloud mail mark`, with fresh identity checks, targeted named-flag patches, typed acknowledgements, and distinct rejected/uncertain outcomes without automatic write retries.
- Add explicit folder-scoped Mail thread-member and unread/flagged individual-message UID pagination through `iter_thread_messages()`, `iter_filtered_messages()`, `icloud mail thread --all`, and `icloud mail filter-messages`, with checked scopes, empty-page exhaustion, and explicit request-budget failures. Existing bounded thread and grouped search behavior is unchanged; live scans are not atomic snapshots.
- Add exact folder-scoped Mail metadata lookup by message ID or UID, with mailbox UID-validity checks and explicit missing-message results.
- Add bounded, folder-scoped Mail thread expansion through `MailService.thread_messages()` and `icloud mail thread`, returning individual message metadata with explicit incomplete-result semantics and mailbox identity checks.
- Add bounded search across explicitly selected Mail folders, preserving per-folder thread identity and incomplete-result semantics through the library and `icloud mail search-folders`.
- Add versioned Mail message JSON export with metadata-only defaults, opt-in body data, and private non-overwriting output through `icloud mail export`.
- Add Mail snippets using advertised preview IDs and separate Unread/Flagged browser thread filters through the library and `icloud mail preview` / `icloud mail filter`, with mailbox identity checks and explicit bounded thread-group semantics.
- Add explicit `MailService.iter_messages()` UID-based mailbox enumeration and `icloud mail list --all`, with mailbox identity checks, validated paging boundaries, and explicit failure on incomplete scans. Default message listing and search remain bounded; enumeration is not an atomic snapshot or synchronization API.
- Add read-only `icloud mail` commands for folders, bounded message listings and thread-grouped search, body data, and attachment downloads to explicit non-overwriting paths.
- Add experimental `PyiCloudService.mail` reads for folders, bounded message and thread-grouped search results, body retrieval without marking messages read, and context-managed attachment streaming. Flag writes and ordinary moves are separate explicit operations; sending and permanent deletion remain unsupported.
- Add `InvitesService.create_event()` to create unpublished hosted events with a share, validate inputs before writes, and report partial creation failures with best-effort cleanup of the newly created zone (#431).
- Add experimental `InvitesService.invite_via_link()` for creating participant-specific one-time-link invitations to hosted events, with a typed guest/link result, current share tags, and explicit uncertain-outcome errors (#433).
- Add `InvitesService.publish(event, published=True)` to publish or unpublish a hosted event, updating EventDetails and its share atomically with the required encryption marker (#432).

### Changed

- Simplify Mail Drop capability validation and centralize shared Mail error messages and MIME types without changing download behavior.
- Redact capability-bearing Mail Drop URLs and cloud-part identifiers from serialized message metadata; select Mail Drop attachments by their 1-based position in the parts array instead of copying signed URLs into commands.
- Use exact message-ID lookup by default for Mail CLI preview and export commands too; explicit `--limit N` retains the bounded newest-message lookup.
- Use exact message-ID lookup by default for Mail CLI body and download commands; an explicit `--limit` retains the existing bounded newest-message lookup.

### Tests

- Verify Mail sync help stays offline and exposes its options in both plain and ANSI-colored output.
- Verify invalid Mail reconciliation budgets fail with the specific validation error before reading inventory state or polling remote metadata.
- Add private POSIX Mail inventory storage with owner-only files, bounded locking, restart guards, and explicitly opted-in recovery for expired change history.
- Integrate the private Mail sync engine with read-only polling and exact metadata readers, bounded incremental reconciliation, explicit history-reset failures, and atomic local checkpoint application.
- Add a private Mail baseline-inventory and transactional SQLite state backend with bounded stable-counter scans, restart/rollback tests, namespace guards, and competing-writer protection.
- Add a private, bounded Mail change-event decoder and checkpoint-validation helper with synthetic fixtures and offline recovery guards.
- Add offline Mail attachment regression coverage for interrupted and invalidly encoded streams, zero-byte downloads, opaque remote filenames, and MIME-type mismatches through the real service and CLI file writer.
- Harden a private Mail date-search pagination candidate with offline cases for timestamp ties, saturated boundary buckets, concurrent changes, identity failures, and request budgets. This does not add public search pagination or infer boundary completeness from short responses.

### Documentation

- Clarify the separate ordinary-attachment and Mail Drop download paths and capability redaction in serialized metadata.
- Document the observed iCloud Mail HTTP protocol research and capture limitations.
- Reconcile the Invites design document with the shipped reads, CLI, RSVP image downloads, and cancellation support; document the entitlement flow and remaining owner-write limitations (#434).

## [2026.10.1] - 2026-10-15

### Changed

- Publish pushes to `main` to TestPyPI using the `test` GitHub environment, alongside prereleases; stable releases continue to publish to PyPI
- **Breaking:** `RecurrenceFrequency` is now zero-based (`DAILY=0` ... `YEARLY=3`) to mirror Apple's wire encoding, so `int(frequency)` is the value stored in `RecurrenceRule.Frequency` and the value read back (#419)

### Added

- Add service and component labels, issue form selection, and automatic classification of issues and pull requests (#428)
- Add `--one-factor` to `icloud devices` `list`, `show`, `sound`, `message`, `lost-mode` and `export` for a password-only Find My sign-in that skips the two-factor prompt. The session lives for that one command only — its cookies go to a temporary directory that is removed when the command exits, so the stored session is never touched — and `devices erase` deliberately still requires a full session (#358)
- Accept an optional `event` in `PyiCloudService.confirm_security_key()` so callers can stop waiting for a security key touch
- Add `RemindersService.set_early_reminders()` for the "Early Reminder" setting (hours/days before the due date), stored in the reminder record's `DueDateDeltaAlertsData` field; the payload's account identifier is taken from an existing app-written payload or, by default, the signed-in account's directory id. `Reminder` now carries `early_reminders` (#419)
- Add `RemindersService.set_urgent()` for Urgent presentation alarms (iOS 26.2+), with a generic CloudKit asset upload (`assets/upload` token request plus content upload) shared by the container client; `Reminder` now carries `urgent` (#419)
- Add `icloud reminders update --early-reminder` / `--clear-early-reminders` and `--urgent/--not-urgent` options (#419)

### Fixed

- Raise a `PyiCloudException` from `PyiCloudService.confirm_security_key()` instead of leaking a non-pyiCloud error: `PyiCloudSecurityKeyTimeoutException` when the wait is aborted through the cancel event, and `PyiCloudSecurityKeyException` for a missing FIDO2 device or any other failure of the security key ceremony (the fido2 error, where there is one, is chained as `__cause__`)
- Write Reminder title and notes changes as CRDT edits on top of the server document, so Apple devices show them instead of keeping (and later restoring) the old text, and bump the field resolution counters instead of resetting them (#421). A title or notes text is only written when the caller changed it since the reminder was read, so updating another field on an older object no longer reverts a text edited on a device
- Edit title and notes documents that are wrapped in a bare CloudKit `Version`, as well as those in a `Document`, and raise on a stored copy whose structure cannot be edited instead of replacing it
- Reminders recurrence rules now map to the correct frequency on the wire: a weekly rule was stored (and shown in the app) as monthly, and a daily rule could not be created at all (#419)
- An explicit `RecurrenceFrequency.DAILY` is no longer dropped by `update_recurrence_rule()` in favour of the rule's current frequency (#419)
- Store a time zone with timed Reminder due dates created without `time_zone` (the `ZoneInfo` name, or `UTC` for naive and UTC values), so Apple devices no longer show the UTC clock time as local time; a reminder created from a naive or UTC due date now displays at its true local time rather than the literal clock time. `update()` fills in the zone of a `ZoneInfo` due date (#422)

## [2026.10.0] - 2026-10-12

### Added

- Add `InvitesService.cancel()` to cancel or reinstate an Invites event you host (#355)
- Expose guest photos attached to an Invites RSVP through `InvitesService.rsvp_image()` (#349)
- Add an opt-in `--probe` layer to `icloud doctor` that calls each service once to catch withdrawn endpoints (#347)
- Add an `icloud invites` command group for listing, showing, and resolving Invites events (#346)

### Fixed

- Derive the Invites, Notes, Reminders, and CloudKit error hierarchies from `PyiCloudException` so a single `except PyiCloudException` catches them (#415)
- Read Invites RSVPs and one-time link guests for events you own through the zone changes feed instead of a per-zone query Apple rejects (#352)
- Surface CloudKit per-record modify failures with Apple's error code and reason instead of discarding them (#354)
- Reject advertised service endpoints that do not use HTTPS (#401)
- Refuse Reminder updates after a text document fails to decode (#400)
- Correct Calendar end-date minutes remaining until midnight (#399)
- List nested Notes folders from the zone change feed (#398)
- Preserve Find My authentication cookies when loading a session (#397)
- Address shared-folder downloads using the document owner (#396)
- Preserve duplicated Photos assets that share a master (#395)
- Keep GPS fresh via locating monitor refreshes in Find My iPhone (#390)
- Accept SMS codes Apple validates with a 409 (#389)
- Parse Date alarm triggers in Reminders instead of dropping them (#392)
- Do not report an unreachable iCloud as a failed login (#357)
- Make rich optional so the core install imports cleanly (#369)
- Translate transport errors from the Invites public-scope endpoint and preserve Retry-After (#345)

### Refactored

- Reduce cognitive complexity and fix code smells (#409)
- Resolve SonarQube complexity and exception-test findings (#410)
- Resolve open SonarQube findings (S5713, S8513, S3776, S107, S2083) (#411)

### Changed

- Update package name to pyicloud
- Rename the Find My `monitor_locate` option to `locate` (it now also gates the construction fetch), treat `refresh_interval=None` as "no monitor", and start that monitor from the `icloud` CLI only for `devices list --refresh-interval` (#347) and add allowScripts configuration

### Documentation

- Split the monolithic README into focused guides and per-service docs under `docs/`, leaving a slim landing page
- Publish the docs and a generated API reference to Read the Docs via MkDocs, mkdocstrings, and a `.readthedocs.yaml`

### Features

- Update @opencode/cli version and clean up setup script

### Tests

- Raise branch coverage above 90% for all source files (#394, #406)
- Drop coverage pragmas and cover the exposed branches (#408)
- Isolate filesystem access behind a sanctioned load_fixture helper (#407)

### Misc

- Various dependency and infrastructure updates

## [2.7.0] - 2026-08-26

### Added

- Identify withdrawn endpoints instead of a generic response error (#334)
- Add an inventory of the webservices the library depends on (#335)
- Add `icloud doctor` CLI command for read-only service diagnostics (#336)
- Make family device polling delay and max retries configurable (#326)
- Support minimal (paused) 2FA auth for Find My operations (#325)
- Expose `deleted` on RemindersList (#319)

### Fixed

- Resolve SonarCloud findings on main (#340, #344)
- Port Photos uploads to Apple's photosupload endpoint (#331)
- Request PCS access so encrypted fields decode for Reminders (#317)
- Raise PyiCloudPCSTimeoutException when PCS retries are exhausted (#343)
- Request PCS access so encrypted fields decode for Notes (#318)
- Report an advertised webservice with no url as unavailable (#337)
- Read the shared scope zone by zone in invites (#339)
- Refresh webservices after token authentication (#332)
- Parse Apple's date arrays into datetimes for Calendar (#320)
- Handle Apple authenticationType key in 409 2FA responses (#323)
- Handle nested JSON boot context and auth-state wipe during SRP 2FA (#322)
- Declare CRDT lengths in UTF-16 code units, not codepoints for Reminders (#304)
- Fix China mainland auth endpoints and 2FA fallback (#285)
- Adjust Dependabot pip updates to only widen requirements when needed (#291)

### Changed

- Refactor setup/startup scripts: set -e + pipefail, opt-in trace (#327)
- Strict-mode cleanup (mypy/pylint) with SonarQube noqa suppressions (#321)

### Dependencies

- Bump actions/checkout from 4 to 7 (#329)
- Bump actions/setup-node from 4 to 7 (#328)
- Bump actions/setup-python from 5 to 7 (#330)
- Bump github/codeql-action/analyze/init from 4.36.2 to 4.37.8 (#314, #315)
- Bump actions/setup-python from 6 to 7 (#299)
- Update setuptools requirement from <82.1,>=77.0 to >=77.0,<84.1 (#309)
- Update ruff requirement from >=0.15.15 to >=0.15.20 (#280, #287)
- Bump ghcr.io/devcontainers/features/docker-in-docker from 3.1.0 to 4.0.0 (#286)
- Bump actions/checkout from 6 to 7 (#284)

### Contributors

@FezVrasta, @hackermengzhi, @MrJarnould, @timlaing, @YoraiLevi, [@copilot-swe-agent[bot]], [@dependabot[bot]], Cherry, @claude, @Copilot

## [2.6.5] - 2026-06-09

### Features

- Add additional video media versions for thumbnails and medium images (#277)

### Dependencies

- Update srp requirement from >=1.0.21 to >=1.0.22 (#271)
- Update pydantic requirement from <3,>=2.12 to >=2.13.4,<3 (#270)
- Bump actions/github-script from 8 to 9 (#275)
- Bump github/codeql-action from 4.36.0 to 4.36.2 (#278)
- Update click requirement from >=8.1.8 to >=8.4.1 (#273)
- Update isort requirement from >=5.11.5 to >=8.0.1 (#272)
- Update ruff requirement from >=0.15.14 to >=0.15.15 (#274)

## [2.6.4] - 2026-05-19

### Fixed

- Fix invalid credentials handling in response from iCloud (#269)
- Fix Service Unavailable error when logging in (#266)
- Handle preexisting session state better (#265)

### Dependencies

- Bump github/codeql-action from 4.28.20 to 4.36.0 (#264)
- Update pydantic requirement from <3,>=2.11 to >=2.12,<3 (#261)
- Update tzlocal requirement from >=5.3.1 to >=5.3.2 (#262)
- Update ruff requirement from >=0.15.9 to >=0.15.14 (#263)
- Bump actions/setup-python from 5.6.0 to 6.0.0 (#260)
- Bump actions/github-script from 7.0.1 to 8.0.0 (#259)
- Bump github/codeql-action from 4.28.17 to 4.28.20 (#258)
- Bump actions/checkout from 5.0.0 to 6.0.0 (#257)

## [2.6.3] - 2026-05-16

### Fixed

- Fix for Chinese mainland auth (#254)

## [2.6.2] - 2026-05-15

### Fixed

- Add missing test dependency for China mainland server test (#253)

## [2.6.1] - 2026-05-14

### Fixed

- Fix China mainland server detection (#252)

## [2.6.0] - 2026-05-14

### Added

- Add China mainland server support (#248)

### Dependencies

- Bump srp from 1.0.21 to 1.0.22 (#250)
- Bump ruff from >=0.15.9 to >=0.15.10 (#249)

## [2.6.0b0] - 2026-05-13

_Pre-release for testing China mainland support_

## [2.5.0] - 2026-04-30

### Added

- Add support for trusted device authentication with code delivery (#244)

### Fixed

- Fix and improve handling of 2FA scenarios (#243)
- Fix calendar service timezone handling (#236)

### Dependencies

- Bump github/codeql-action from 4.28.5 to 4.28.17 (#242, #241, #238)
- Bump ruff from >=0.15.3 to >=0.15.9 (#240, #239, #237)
- Bump pydantic from >=2.10.6 to >=2.11,<3 (#235)
- Bump tzlocal from >=5.3 to >=5.3.1 (#234)
- Bump actions/checkout from 4.2.2 to 5.0.0 (#233)
- Bump pytest from >=8.3.4 to >=8.4.1 (#232)

## [2.4.1] - 2026-03-26

### Fixed

- Fix HSA2 authentication flow for 2FA (#229)
- Fix calendar event parsing for all-day events (#228)

### Dependencies

- Bump ruff from >=0.15.1 to >=0.15.3 (#227)
- Bump click from >=8.1.8 to >=8.2.0 (#226)

## [2.4.0] - 2026-03-05

### Added

- Add Notes service (#219)
- Add Reminders service (#218)

### Fixed

- Fix Find My authentication and location refresh (#221)
- Fix Drive file download for shared items (#220)

### Dependencies

- Bump ruff from >=0.11.5 to >=0.15.1 (#217, #216)
- Bump pytest-cov from >=6.1.1 to >=6.2.1 (#215)
- Bump pydantic from >=2.10.6 to >=2.11.7 (#214)

## [2.3.0] - 2026-02-22

### Added

- Add support for iCloud Keychain and authentication tokens (#209)

### Fixed

- Fix Photos album handling (#208)
- Fix Drive upload issues (#207)

## [2.2.0] - 2026-02-08

### Added

- Add Calendar service (#205)

### Fixed

- Improve error handling for authentication failures (#204)

## [2.1.0] - 2026-01-25

### Added

- Add Find My iPhone service (#201)
- Add support for 2FA via SMS and email (#200)

### Fixed

- Fix session persistence (#199)

## [2.0.2] - 2025-12-15

### Fixed

- Bug fixes and stability improvements

## [2.0.0b6] - 2025-11-20

### Changed

- Beta release for v2.0.0
