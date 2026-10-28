# Mail fixtures

These JSON envelopes are synthetic and manually minimized from observed iCloud
Mail HTTP capture shapes. All identifiers, contacts, headers, dates, and message
contents are invented. No raw captures, cookies, signed URLs, or binary files
are included. Binary download tests use mocked bytes.

`maildrop_message_response.json` models observed Mail Drop part metadata using
invented IDs, filenames, future expirations, and deliberately fake URL signatures.
No URL in the fixture is a usable capability. Tests use mocked byte streams and
verify capability redaction, isolated transport, and explicit size/expiry checks.

Load these fixtures through `tests.fixture_loader.load_fixture` so reads respect
the test filesystem sandbox. Search results are thread-grouped; listing fixtures
represent bounded snapshots, not a complete synchronization protocol.

`flag_update_request.json` and `flag_update_response.json` model the one-message
`email/set` shape observed through authorized browser read/unread and flag/unflag
actions. Only named `seen` and `flagged` set/clear deltas were captured. Account
and message identifiers and counters are invented; no raw captures or credentials
are included. Populated failure maps in tests are synthetic adversarial cases,
not additional live protocol evidence for flag writes.

`draft_save_response.json` is a synthetic recipient-free `message/savedraft`
acknowledgement, not a send response or an idempotency key. Server mocks generate
replacement UIDs; malformed/conflicting variants are robustness tests, not extra
live protocol evidence. No draft bodies, real UIDs, or captured senders are stored.

`sender_preferences_response.json` models only the sender-related account
preference shape. Every name, domain, address, and account identifier is synthetic.
Primary-address data was checked against a read-only live response; alias and
custom-domain shapes come from the captured browser's declared schemas. Those
synthetic cases do not establish live alias/custom-domain write support.

`folder_create_response.json`, `folder_rename_response.json`, and
`folder_delete_response.json` are synthetic, minimized `mailbox/set` envelopes
derived from authorized disposable-folder browser actions. Creation returns a
record under `mailbox`, rename acknowledges the exact ID with `null`, and delete
returns a destroyed-ID list. IDs, paths, account metadata, and UID validity are
fake; these fixtures contain no raw captures or credentials. Invalid/conflicting
variants generated in tests are robustness cases, not additional wire evidence.

`move_request.json` and `move_response.json` model captured ordinary mailbox-only
moves: the acknowledgement is an empty object, not a flag-update counter.
`move_conflicting_response.json` minimizes a real observed response containing
both a success and a failure for the same source ID. All identifiers and account
values are synthetic. That contradictory outcome requires fresh reconciliation,
not an automatic retry or a claim that nothing changed.

`thread_messages_response.json` models individual metadata records filtered by
`appleThrid` within one folder. Its marker is synthetic and must not be treated
as proof of completeness or followed automatically. The shape was verified
with controlled read-only metadata queries; all record values are invented.

`message_lookup_response.json` models a single exact metadata result verified
by controlled, read-only ID and UID queries. Its IDs and message fields are
synthetic; it is not a copied account response.

`preview_response.json` and `thread_filter_response.json` are minimized synthetic
shapes derived from user-authorized browser captures on 2026-10-19. Preview
identifiers, GUIDs, snippets, group IDs, timestamps, counts, and modification
sequences are invented. Thread-filter results are groups, not per-message flag
matches; the fixtures do not establish combined-filter or sync semantics.

`messages_uid_page_response.json` models a UID-ordered metadata page with equal
dates and a synthetic opaque marker. Tests exercise strict UID boundaries and
an explicit empty terminal page; the marker is not a synchronization cursor.

`thread_query_page_response.json` models the thread-domain `geqs/query` cursor
shape verified by bounded read-only queries. It contains only invented group IDs,
folder timestamps, and a synthetic cursor; it is not a full digest response.
Tests replay cursors with unchanged predicates and require a separate empty,
marker-free terminal response. No real cursor or account data is included.

## Private Synchronization Candidate

`sync_events_response.json` is synthetic metadata matching the structural forms
of read-only change polls. Counters, IDs, timestamps, flags, and ranges are fake.
It tests a private decoder, not end-to-end synchronization or a public sync API.

`sync_arrival_push.json` uses the observed new-message push envelope and the
UID-bearing `EXISTS` event form. All counters, IDs, timestamps, flags, and topic
values are synthetic. It contains no binary push frames, tokens, or alert text.

`sync_thread_invalidation_response.json` models the observed
`THREADUPDATE (<id> NIL <counter>)` form without a `FLAGS` section, alongside a
UID removal. All identifiers and counters are invented. The event invalidates
folder-local membership; it is not proof of permanent deletion or an empty flag
set. Tests refresh the thread and verify missing old members by exact UID.
