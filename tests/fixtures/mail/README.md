# Mail fixtures

These JSON envelopes are synthetic and manually minimized from observed iCloud
Mail HTTP capture shapes. All identifiers, contacts, headers, dates, and message
contents are invented. No raw captures, cookies, signed URLs, or binary files
are included. Binary download tests use mocked bytes.

Load these fixtures through `tests.fixture_loader.load_fixture` so reads respect
the test filesystem sandbox. Search results are thread-grouped; listing fixtures
represent bounded snapshots, not a complete synchronization protocol.

`thread_messages_response.json` models individual metadata records filtered by
`appleThrid` within one folder. Its marker is synthetic and must not be treated
as proof of completeness or followed automatically. The shape was verified
with controlled read-only metadata queries; all record values are invented.

`message_lookup_response.json` models a single exact metadata result verified
by controlled, read-only ID and UID queries. Its IDs and message fields are
synthetic; it is not a copied account response.

`messages_uid_page_response.json` models a UID-ordered metadata page with equal
dates and a synthetic opaque marker. Tests exercise strict UID boundaries and
an explicit empty terminal page; the marker is not a synchronization cursor.
