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
not additional live protocol evidence.

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
