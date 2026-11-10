# MCP extension

The optional local Model Context Protocol (MCP) extension is under development.
Its current foundation exposes **local status and capabilities only**, without Apple authentication
or account data tools through the launcher.

This Calendar slice adds explicitly configured SDK tools `icloud_calendar_list`
and `icloud_calendar_events`, using bounded inclusive date ranges and metadata
projections. The launcher retains offline local status and capabilities until
the final integration slice adds trusted startup options. Calendar create/delete
adapters are internal; no runtime write or approval tool is exposed by this slice.

Internal Calendar adapters distinguish `mutation_unverified` (read-back does not
prove the requested result) from `mutation_uncertain` (dispatch or read-back
failed, including HTTP 410). Both retire the approval and prohibit replay;
neither result proves success or that retrying the mutation is safe.

This Reminders slice adds explicitly configured SDK list-discovery and scoped
reminder-read tools using expiring opaque handles. The launcher retains offline
local status and capabilities until final integration adds trusted startup
options. Existing create/update/soft-delete adapters are internal; this slice
exposes no runtime write or approval tool.

This Notes slice adds explicitly configured SDK recent-title listing, bounded
all-title search and separately opted-in untrusted plaintext reads. Metadata
permission never grants body access. The launcher retains offline local status
and capabilities until final integration adds trusted startup options. Notes
writes remain unsupported; this slice exposes no mutation or approval tool.

This Contacts slice adds explicitly configured SDK name-search and discovered
contact-detail tools. Email and phone fields require individual selection and
never appear in name-search results. The launcher retains offline local status
and capabilities until final integration adds trusted startup options. Contacts
writes remain unsupported; this slice exposes no mutation or approval tool.

Contact discovery includes person and company names, but never email/phone fields.
Selected contact details accept a zero-based `offset` and a per-field `limit`
of 1–20 (default 20). A truncated page reports `complete: false` and `next_offset`
in its contact record; use that offset with the same selected fields to continue.

This Photos slice adds explicitly configured SDK library/album/asset discovery,
bounded filename search and separately opted-in version-bound downloads. Existing
favorite, album, relation and upload adapters retain scoped account/route/revision
proof and independent receipts, but remain internal until final runtime wiring.
The launcher retains offline local status and capabilities; this slice exposes
no write or approval tool.

Internal Photos album creation reads at most 1,000 typed inventory records across
pages and folders and fails closed on overflow. Pre-create reads reserve a
new-record slot by limiting the existing
inventory to 999 raw records; post-create verification retains the 1,000 bound.
Inventory API failures become `inventory_unavailable` before approval consumption.
Nested albums may appear in
inventory, but rename targets remain top-level only. Upload reservation and final
registration share the upload deadline; custom sessions without a deadline-aware
adapter are refused. Download state for incomplete transfers is not evicted to
admit another version: if every registry slot is active, the new read fails with
`download_registry_full`. Completed or failed-before-start entries are inactive.
Active transfers abandoned for more than 600 seconds since their last successful
chunk may be reclaimed. Registration restores the session's prior deadline on
success and failure so its temporary limit does not affect later verification.

This Drive slice adds explicitly configured SDK metadata, Trash discovery,
bounded name search and separately opted-in version-bound downloads. Existing
write/upload adapters retain exact independent receipts and uncertain/no-replay
outcomes, but remain internal until final runtime integration. The launcher
retains offline local status and capabilities; this slice exposes no write or
approval tool.

Drive upload reservation and commit share one enforced deadline. Unsupported
custom transports are refused before registration dispatch. Before upload, the
selected destination must retire successfully after approval validation but
before approval and staged bytes are consumed; a failed retirement preserves
both. Rename retires its selected source before dispatch, so uncertain outcomes
require rediscovery rather than reusing stale names. Permanent deletion compares
Trash listing metadata with the independently fetched detail snapshot.
Both recovery and deletion reject shared metadata in either snapshot. A stage
that expires during refresh is rejected before destination retirement; restaging
uses a new token and requires a new preview and approval. Known retirement
failures return `target_unavailable`, not mutation uncertainty. Custom mutation
sessions must confirm retries are disabled before approval consumption.
Upload credentials are validated into a private snapshot before accepting or
consuming resources; cookie lookup failures do not spend approval or bytes.
Mutation POSTs disable redirects, and redirect responses are uncertain outcomes
without a second dispatch. Drive browse accepts `scan_limit` (1–2,000, default 500) and constructs only that bounded child prefix, including selective queries;
results remain explicitly incomplete rather than claiming an exhaustive search.
Trash listing uses the same `scan_limit` bounds. Forced folder refreshes discard
old inventories and child caches; missing inventories deny collision checks.
Publication failures revoke the hidden preview and its selectors. Actual moves
retire both discovered handles before dispatch; same-parent no-ops preserve them.

Mutation dispatch uses the existing service payload builders with a direct,
non-retrying adapter path, bypassing custom session replay and response hooks.
The adapter selected for each actual endpoint is checked before consuming any
approval. Upload registration and commit adapters must also enforce absolute
response-header deadlines. Staged-byte expiry is checked during atomic admission;
already-acquired immutable bytes remain valid for the admitted transfer.
HTTP/HTTPS proxy response headers, including CONNECT, use the same absolute budget;
unsupported proxy transports are rejected. Upload phases inherit the earlier owner
deadline instead of renewing it after metadata refresh. One-shot writes use a
scoped service clone, preserve safe session query defaults and response cookies,
and never assign the library service's read-only session property. Move-handle
admission is atomic; Drive mutations invalidate all aliases of each affected
identifier and zone. Rename no-ops compare the full displayed filename leaf.
Account mutations retain session/environment proxy routing and CA bundles without
enabling netrc or session replay; isolated upload transfers still inherit neither.
Bound Drive parameters and upload credentials override session query defaults.
Injected mutation adapters must be deadline-aware and use the owner's original
queue-inclusive deadline. A proven rejection before transport admission restores
the original upload approval, stage and destination aliases without extending any
TTL. After any dispatch attempt, resources remain consumed and failures are
uncertain; no mutation is automatically retried.
Retries after proven pre-dispatch rejection reuse the already accepted decision,
not the consumed receipt file. Registration dispatch pins its marked adapter;
separate reservation and commit adapters receive fresh owner budgets each time.
Individual header reads retain shorter adapter timeouts within the owner deadline.
Downloads stop streamed bodies at the requested chunk bound, and injected real
Drive sessions must use body-bounded HTTPS adapters before any SDK download read.
The final content response uses the requested chunk limit in the adapter itself;
token-discovery metadata keeps its separate bounded response policy. Metadata
endpoint adapters are also validated and owner-budgeted. Mutation POSTs pin the
preflight adapter, and final deletion/move evidence must stay in the approved
account and route. Identifier prefixes must agree with file/folder types.
Injected inventory facades must provide bounded `iter_children` production; eager
fallbacks are rejected. Download operations restore prior injected-adapter deadlines
on success and failure. Receipt acceptance cannot replace the bound account or
routes, and pinned dispatch still validates its URL and session policy. Evicting an
upload selector also revokes its ledger decision.
Inventory producers are closed on truncation and exceptions. Permanent Drive
deletion requires an item-correlated tombstone acknowledgement and reports
`acknowledged: true`, `final_state_verified: false`; a generic HTTP 404/410 is not
proof that a file was permanently deleted. Context is revalidated after receipt
acceptance before mutation dispatch. Photos uploads also validate registration
policy before consuming resources and retain them on proven pre-dispatch rejection.
Injected Drive operations restore prior adapter deadlines at operation completion;
download request budgets reset once per operation, not on each guard call. Metadata
roots must be HTTPS before the first SDK read. Photos stages are acquired atomically
with approval consumption, transfer deadlines inherit the owner budget, and accepted
receipts survive a proven pre-dispatch rejection without a second mailbox read.
Optional SOCKS routing is preserved when Requests' SOCKS support is installed;
deadline pools retain the original proxy connection class. Real Drive inventory
discovery requires a body-bounded metadata transport before the first SDK read.
Photos preview size limits and approved transfer hosts apply independently of any
injected pipeline policy. Accepted receipt decisions are cached before metadata
revalidation; failed publication revokes both decision and selector state.
Abandoned transfers can be released with `icloud_drive_download_release` or
`icloud_photo_download_release`, supplying the original handle and transfer token
(and Photos version). Release only retires that local token; it does not mutate
iCloud or reset another transfer's entity tag. Permanent deletion rejects Trash
inventories above 2,000 rows rather than claiming absence from a partial scan.
The underlying Drive library's metadata reads reject redirects for ordinary callers
as well as MCP verification requests.
Transfer release does not probe authentication or create an account client; it uses
the same serialized local lane even after session expiry. Release tools advertise
`readOnlyHint: false` because they invalidate local continuation tokens, while
`destructiveHint: true` describes that local invalidation and
`openWorldHint: false` reflects that they never mutate iCloud. Nested folder reads
reject a mismatched connection instead of borrowing an unchecked transport.
Metadata transports must retain the known Requests/PyiCloud request and send
methods as well as stable adapter selection. Photos URL resolution uses the pure
SDK `PhotoAsset` resolver; custom facades must supply explicit static `download_urls`
metadata rather than an arbitrary URL callback. Recovery and Trash verification
share the same complete-inventory bound used for permanent deletion.

Drive and Photos downloads return an opaque `transfer_token` with their first
chunk. Supply that token for every nonzero-offset continuation, using the same
handle and selected version. Starting at offset zero creates a separate transfer;
it cannot replace another transfer's pinned entity tag. A changed remote version
is rejected instead of combining chunks from different versions.

This Mail slice adds explicitly configured SDK mailbox/message metadata and
field-aware searches. Body/all search and untrusted MIME reads require an
additional content opt-in. Existing guarded draft/folder/flag/move/send/deletion
adapters remain internal until final runtime wiring. The launcher retains
offline local status and capabilities; this slice exposes no write or approval
tool.

Install it with `pip install "pyicloud[mcp]"`, or install both user-facing
extensions with `pip install "pyicloud[all]"`. The `all` extra does not install
development tooling. The standalone CLI extra remains `pyicloud[cli]`.

Use the interpreter/executable from your installation environment:

```console
.venv/bin/icloud-mcp --help
.venv/bin/icloud-mcp --version
.venv/bin/icloud-mcp
```

On Windows use `.venv\Scripts\icloud-mcp.exe`. With no arguments the server
communicates over stdin/stdout using MCP; it is not an interactive terminal shell.
Configure your host to launch that executable with no arguments. Do not supply
passwords or verification codes as host arguments or tool inputs.

`icloud_status` returns structured local status and performs no Apple requests.
`icloud_capabilities` reports the selected services, availability state and fixed
read-only limits, also without contacting Apple.
No listener, write tools, account discovery, or implicit authentication is enabled.
Stdout is reserved for protocol traffic; diagnostics belong on stderr.

Core-only installations can use offline help/version, but serving requires the
`mcp` extra. Python 3.10–3.15 remain the supported interpreter range. See the
[compatibility guarantees](compatibility.md) for the distinction between offline
validation and live Apple-service compatibility.

## Internal approval lifecycle

The foundation does not expose runtime writes. Its private operator channel keeps
separate bounded preview and receipt budgets, so a full preview queue cannot
prevent the operator from approving an existing request. Accepted receipts bind
the exact displayed action, target, payload, digest, risk tier and deadline to the live
ledger; reconstructed or edited displays cannot authorize another operation.

Published records include a wall-clock deadline under the ledger's maximum
600-second lifetime; in-memory authorization still expires against monotonic
time. This keeps persisted records comparable across process restarts and reboots.
At capacity, cleanup removes only validated private regular
files whose deadline has elapsed or cannot belong to a current live lifetime.
Live files and unknown or unsafe entries are preserved. Malformed private regular
records are reclaimed only after the full 600-second maximum lifetime has elapsed
since modification. Failed writes remove only the inode they created, never a
replacement. A deliberate
`DECLINE REQUEST_ID` discards its pending record; a mistyped approval phrase does
not. Stored records without the new deadline/display-binding fields cannot be
accepted: an operator must archive legacy records and create fresh previews
before enabling the later runtime integration. No old receipt is reused after
an operator migration.

Mailbox changes use a shared, non-blocking process lock; a competing operation
fails with `approval_mailbox_busy` rather than retrying a mutation. The operator
input wait does not hold this lock. After input, the mailbox rechecks the exact
displayed record, its inode and its deadline before recording a decision.
Missing, boolean or non-finite deadlines are invalid records, never live
approvals; aged private invalid records follow the same bounded cleanup policy.
