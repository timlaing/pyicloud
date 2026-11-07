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
