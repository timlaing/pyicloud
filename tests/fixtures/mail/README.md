# Mail fixtures

These JSON envelopes are synthetic and manually minimized from observed iCloud
Mail HTTP capture shapes. All identifiers, contacts, headers, dates, and message
contents are invented. No raw captures, cookies, signed URLs, or binary files
are included. Binary download tests use mocked bytes.

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

`draft_attachment_upload_response.json`, `draft_attachment_reference.json`, and
`draft_attachment_save_response.json` are synthetic projections from an approved
browser draft with one tiny text attachment. The modern upload returns
`messagePart`, not the legacy `msgPart` envelope. The save response can remap
attachment identities as well as rotate the draft UID. All filenames, UUIDs,
sizes, and URLs here are invented; no file body, recipient, credentials, or raw
capture is included. These shapes do not establish Mail Drop uploads or sending.

`draft_send_request.json` and `draft_send_response.json` are separate synthetic
final-send envelopes derived from three explicitly authorized browser sends of
disposable text and a tiny ordinary attachment: a new email, a reply, and a
forward. All were addressed only to the user's controlled test address. The
browser scheduled Undo Send first, then issued `draft/send`; these fixtures model
only that final request. A separate three-write implementation test subsequently
verified direct sending without scheduling, including source disappearance and
Sent-copy body/attachment integrity, but not SMTP delivery. The EXPUNGE value is
a sequence number, not a UID. UIDs, counters, and all other fixture values are
invented.

`draft_forward_stored_reference.json` models the distinct existing-message-part
reference observed in the authorized synthetic forward. Its content ID is null,
its size can be MIME-encoded rather than decoded bytes, and its save can return
only a UID, without a cached-upload mapping. The source folder, UID, filename,
gateway and all identifiers are synthetic. No attachment body or signed URL is
stored. Tests also verify that mixed cached/stored references preserve the
cached mapping requirement and fail on unexpected mappings.

Read-only comparison with the earlier browser captures established that Apple
can retain intermediate forward drafts after edits and even after sending the
final version. Offline tests model that distinct behavior explicitly rather
than weakening ordinary draft/reply identity guards. A separate authorized
six-write live test verified retained-version reporting and Trash cleanup of
only its disposable forward copies, with exact attachment-byte preservation.

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
