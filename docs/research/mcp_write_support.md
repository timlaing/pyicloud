# MCP write-support milestones

Roadmap: #495. User authorized write-support implementation after read work.
This authorizes code and synthetic tests, not actual account mutations.

## Verified facade inventory

| Service   | Existing public operations                                                              | Missing prerequisites                                                                                                               |
| --------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Calendar  | `add_calendar`, `remove_calendar`, `add_event`, `remove_event`                          | No dedicated update API; establish revision-safe updates from verified wire evidence rather than assuming `add_event` is an upsert. |
| Reminders | `create`, `update`, `delete` and linked-record mutations                                | Fresh model/revision checks and precise private/shared list routing.                                                                |
| Contacts  | Reads only                                                                              | A tested library mutation API and verified changeset contracts.                                                                     |
| Drive     | Folder creation, rename, move, trash, recover, permanent delete, upload                 | Fresh node/share context, etags, scoped destinations, and bounded upload source handling.                                           |
| Notes     | Reads/export/render only                                                                | A tested writer preserving encrypted body/CRDT, record revisions, folder and attachment semantics.                                  |
| Photos    | Album creation/rename/delete, asset delete and upload                                   | Reject virtual/smart collections; keep library ownership and revision checks.                                                       |
| Mail      | Draft creation/edit/discard, sender discovery, folders, moves, send, permanent deletion | Retain verified typed mailbox/draft handles and uncertain-write reconciliation.                                                     |

No unsupported operation may claim success or guess an Apple endpoint. Missing
Contacts/Notes/Calendar-update contracts are explicit blockers to those adapters.

## Approval foundation

`pyicloud.mcp.authorization` provides an internal fail-closed ledger with an empty
action allowlist by default. Previews snapshot canonical payloads; HMAC fingerprints
bind account, route, revision, action and target without exposing private bindings.
Limits are 64 pending requests, five-minute expiry, and 64 KiB payloads by default.
Consumption happens before dispatch and is single-use, including stale requests.
Missing revisions are refused by the foundation. Creation adapters must establish
the verified parent/container revision rather than inventing an object version.

This foundation exposes **no MCP approval or mutation tool** and executes nothing.
`approve_from_operator` is a trusted local hook, not evidence of human presence.
Before live tool integration, implement a separately trusted operator approval
channel; a model-generated `confirm=true` or a model-callable approval tool is
not acceptable. Describe the OS/host trust boundary explicitly. Do not claim
protection against privileged local code that can read credentials or alter the
server itself.

The local operator mailbox and standalone command are now implemented:
`icloud-mcp --approval-dir /private/approval-directory --approve-request REQUEST_ID`.
It reads the exact JSON-escaped preview and requires typing `APPROVE REQUEST_ID`
on a separate interactive terminal. No piped `--yes`, model boolean or MCP approval
tool exists. Directory/files must be owned by the operator and private; descriptor-
relative POSIX opens reject symlinks and records are size/count bounded. Receipt
consumption still passes through the HMAC-bound ledger and its expiry/revision rules.

This channel assumes a trusted OS/host and that the model has **no filesystem/shell
access to the approval directory through other tools**. It cannot distinguish a
human from privileged local code able to create a terminal or edit that directory;
do not claim hardware-backed presence. Windows live-write approval is not supported
until a reviewed native channel exists. Server publication/acceptance wiring and
live mutation tool registration are still outstanding; read-only remains default.

Runtime Reminders publication/acceptance wiring is now implemented behind explicit
`--write-action` flags plus `--service reminders` and `--approval-dir`. Only preview
and execute tools are exposed; no approval tool exists. Mailbox decisions and ledger
consumption execute on the same owner lane, and operator descriptors drain after
active work. All other writers/API-gap milestones remain incomplete.

The next verified runtime action is `drive.rename`: private discovered nodes only,
fresh account/ID/zone/etag binding, separate operator receipt, single-use dispatch
and post-write name verification. Shared mutation contracts, root/virtual targets
and other Drive writes remain unsupported rather than guessed. Rename leaf/base
name semantics preserve the existing extension metadata.

## Sequencing and validation

`upload_staging.py` is an internal byte-staging foundation, not a runtime upload
tool or approval path. Strict remote leaf names only, no caller filesystem paths or
URLs. Defaults reserve at most 4 MiB per file, 8 MiB aggregate and eight stages for
five minutes; reservation accounting includes incomplete declared sizes. Strict
Base64 chunks are bounded to 64 KiB and exact next offsets. Only exact final size
and independently computed SHA-256 matching the declaration can seal immutable
bytes. Sealed data cannot be appended or replaced, and take consumes it once.
Expiry, mismatch, discard and close release store references; this is not a promise
of cryptographic memory erasure. Representations do not expose contents. No file,
network, credential, destination or approval access exists in this module. Future
adapters must bind exact name/size/digest plus destination/account/route/revision to
independent elevated receipts, and validate verified upload responses/post-state.
Uploads remain runtime-disabled; no endpoint is guessed or enabled.

`drive_deletions.py` is selected by the separately opted-in elevated runtime
`drive.delete_forever_file` action. Only discovered typed private `drive-trash-file` handles reach fresh
identity/zone checks, literal top-level `TRASH_ROOT` membership and valid non-sentinel
etag/restore metadata. Ordinary handles, folders/shared/root targets and caller
confirmation payloads fail closed. Exact identity/name/restore context/account/route/
etag binds an elevated receipt. Consume and retire before one existing public
`delete_forever()` call. Success requires trash-list absence and an independent exact
lookup raising 404/410 under the same binding; tombstones/generic acknowledgements or
other errors remain uncertain, with no source/request replay. Restore paths stay in
private bindings, never caller-selected local paths. SDK lookup exceptions are
verified to carry HTTP status codes; no missing-response endpoint is invented.
Gateway/schema integration now enforces matching write/elevated startup choices.
Synthetic gateway tests deny model confirmation and standard receipts, and retire
uncertain sources after one fake dispatch. Installed permanent-delete-specific
qualification now verifies independent dual absence versus a lookup-visible tombstone,
elevated phrase requirements and retired source/request replay rejection. No live
file deletion was performed.

`draft_sends.py` is selected by the separately opted-in elevated runtime
`mail.send_draft` action.
Only verified server-created saved contexts with Mail content consent and exact
current body/sender/recipient roles may reach preview. Caller payload is empty;
the preview includes the entire saved approved content and current revision/route.
Separate high-risk ledger selection and elevated receipt are mandatory, including
explicit adapter checks against downgraded tiers. Consume and retire the source
before one public `send_draft(confirm=True)` invocation; confirmation is derived
only from the trusted receipt, never a model flag. Typed acknowledged source identity,
stable account/namespace and old-UID absence are required. Output acknowledges
submission with `delivery_confirmed: false`; uncertainty may already have sent mail
and cannot replay or recover a retry handle. No delivery/atomic-preflight claim.
Gateway/schema integration now requires matching ordinary/elevated startup selection
and Mail content consent. Synthetic gateway tests reject model confirmation and
standard receipts, permit one elevated fake dispatch and retire uncertain sources.
Installed send-specific qualification now verifies exact previews, ordinary terminal
phrase denial, one typed fake acknowledgement and one ambiguous consumed-source
submission, with request/source replay rejection. No live mail was used.

Deletion risk audit: Reminders `delete` is the verified CloudKit soft update
`Deleted: 1`, while Calendar `remove_event` submits DELETE/ifMatch without a reviewed
recovery contract. Calendar deletion is conservatively high risk. Runtime config
requires matching `high_risk_actions` and enabled high-risk actions, CLI adds
`--high-risk-action`, and the gateway forwards that separate selection to the ledger.
Synthetic private-terminal/gateway tests reject normal approval, allow one elevated
fake deletion and reject replay. No new send/upload/permanent-delete adapter is
enabled, and no live account operations occur.

Installed stdio qualification now additionally verifies elevated Calendar selection,
ordinary terminal phrase denial, one fake revision-bound deletion, independent exact
absence and consumed-request replay rejection. This is synthetic installed protocol
evidence, not live event recovery or permanent-deletion wire validation.

Approval primitives now derive risk from fixed action policy rather than payload
fields. Existing classified actions retain standard behavior; new/unclassified
actions fail closed as high risk. The ledger requires a separate `high_risk_actions`
constructor selection (a subset of allowed actions), binds the tier into the HMAC
fingerprint, and refuses ordinary operator approval for elevated previews. Private
mailbox records carry the tier and high-risk decisions require
`APPROVE HIGH-RISK <request_id>` in a separate terminal. Downgraded displayed/receipt
metadata cannot downgrade the ledger-bound tier. No model-callable approval flag is
added, and no sending/permanent-deletion/upload runtime action is enabled by this
foundation. Runtime startup risk selection, action-specific risk audit and each
verified high-risk adapter still need qualification before enablement.

`draft_edits.py` is selected by the separately opted-in runtime `mail.edit_draft` action. Only opaque
server-created saved drafts with Mail content consent reach exact old-body/context
revalidation. Proposed subject/text/to/cc/bcc fully replace those fields; sender,
attachments and threading are not inferred or changed. Exact current state/new payload
bind a single-use receipt. After consume, an exact live registry selector is retired
before one public `edit_draft` call. New ID/UID must rotate, old UID must be absent,
and fresh account/route/folder/sender/roles/body/header proof must match before a
replacement handle is issued. Uncertain outcomes retire request and old handle,
without retry or compensating mutations. Preflight is not atomic with save.
Gateway/schema integration enforces Mail content opt-in at startup. Synthetic gateway
tests verify receipt denial, one uncertain dispatch, source retirement and replay
rejection. Installed edit-specific qualification now covers a verified rotated UID/
handle followed by an unverified-body edit. Old handles and requests cannot replay;
uncertainty exposes no replacement handle. All state is synthetic; no live access.

`draft_revalidation.py` is a read-only editing foundation, not an edit action. It
requires Mail metadata and content opt-ins before reading an opaque server-created
saved draft. Fresh account/route/Drafts/sender bindings must match saved expectations;
the public SDK preflight and exact current UID/folder/subject/canonical body/recipient/
threading/sender-header checks reject external changes. Private frozen state binds
current identity plus digests of approved payload and fresh body; fresh text/headers
are not exposed to MCP. Retained versions and unsupported contexts fail closed.
It has no mutation/approval method. Receipt-bound editing, old-handle retirement,
rotating UID/old absence and new-body post-proof remain pending; no live account used.

Verified `mail.create_draft` now returns a `mail-draft` opaque expiring handle in the
shared server registry after all body/identity/header checks pass. Private frozen
`SavedDraftContext` retains the typed draft, canonical approved payload JSON and
account/resource/revision bindings; payload access returns detached copies and its
representation excludes private contents. Other message/body handle kinds and other
registries cannot resolve it. Failed/uncertain saves never publish a context handle.
This avoids reconstructing expected sender/recipients/body from metadata IDs before
future editing. No edit/send action is enabled by this foundation; independent body
revision checks, approval and rotating-UID post-proof are still required.

Draft-edit investigation requires a saved typed `MailDraft`, not reconstruction from
the MCP metadata message projection. New `MailService.check_draft` exposes existing
read-only DraftWriter checks: fresh folder/UID/message identity, body GUID, sender,
recipients, attachment expectations and threading. Seven synthetic cases verify
successful fresh metadata and refusal of changed identity/namespace/body GUID/sender/
recipients/parts without mutation. It reads headers/body but not an exact prior text
revision, and is not atomic with edit. A future MCP adapter must retain scoped saved
context, obtain required content consent, bind exact current body/payload/revision,
consume independent approval, and verify rotating UID plus new body after one edit.
No draft-edit MCP action is enabled by this preflight API.

Mail move inspection confirms typed acknowledgements refer only to the old source
message ID; `MailMoveUpdate` deliberately does not map the new destination UID/ID.
Matching subject/sender or guessing rotated IDs is not independent identity proof.
`MailService.check_move` now exposes the existing verified read-only source and folder
capability preflight for future previews, returning the bound account identity without
mutation. `move` reuses it before the same single request, preserving public signature,
wire payload/order and no-retry behavior. Synthetic tests prove preflight rejects
unsafe contexts and invokes metadata reads only. MCP moves remain disabled until
exact destination identity/post-state correlation is established; no live access.

`photo_relations.py` adds a private add-to-album adapter selected by the separately
opted-in runtime `photos.add_to_album` action. It resolves discovered album/asset handles, revalidates album server records
and asset/master/zone/tag identity, checks typed correlated-add support, and obtains
exact scoped membership. Account/endpoint/zone/IDs, both revisions and observed
membership bind a single-use receipt. Approval is consumed before one correlated add;
fresh pair/membership reads must establish presence under the same identity/route.
An existing member still requires receipt consumption for a no-op. Acknowledgements
alone are uncertain and cannot replay. Reads and mutation are not one atomic snapshot.
Synthetic tests cover stale tags/master/zone/member/route/capability, receipt denial,
one verified add, uncertain no-replay and disabled/untyped/missing-revision cases.
Gateway/schema integration and synthetic missing-destination/receipt/verified-add/
replay tests are complete. Installed relation-specific qualification now covers one
verified membership-changing add, one acknowledgement-only uncertain add, and an
approved already-present no-op. All receipts reject replay, with exactly two fake
mutations and no live access.

Verified Photos album construction uses `ListTypeEnum.CONTAINER` and exactly
`parent_filter(album.id)` for container-scoped lookup. New `contains_photo_scoped`
requires that precise shape and private typed same-zone context before delegating to
the existing exact-ID lookup; extra/unscoped/foreign parent filters fail before reads.
Returned asset/master/zone identity must match. A non-mutating
`can_add_photo_correlated` guard now exposes observed support before operator preview
without duplicating private fields or probing transport. Synthetic tests prove
present/absent observations, foreign identity rejection, scope rejection and no
mutation. These observations are not atomic with a write; MCP relation adapter and
its exact snapshot/receipt/post-write proof qualification remain pending.

Photos relation investigation confirms existing `add_photo` submits a deterministic
`CPLContainerRelation` ID (`item-IN-album`) with explicit item/container IDs, but its
generic boolean acknowledgement is insufficient for an exact MCP outcome. New public
`add_photo_correlated()` uses that same verified typed operation without REST fallback
and requires one exact matching relation ID/type/item/container response. Private
same-zone typed routes only; shared/nested/unsupported contexts fail before dispatch.
Synthetic tests verify exact request construction, foreign/missing/multiple response
rejection, single dispatch and no unsupported fallback. This does not prove observed
membership or enable an MCP action: fresh album/asset revision bindings plus independent
container-scoped membership lookup remain required before adapter integration.

`photo_album_creates.py` is a typed-only creation adapter selected by the separately
opted-in runtime `photos.create_album` action. It resolves a discovered private `PhotoLibrary`, requires non-mutating
`correlated_album_creation_available`, and reads an independent bounded server album
inventory with real tags and unique IDs. Exact name, account/endpoint/private zone and
canonical inventory snapshot bind a single-use receipt. Names colliding ignoring
case are rejected. Approval is consumed before one `create_album_correlated` call;
success requires a new returned ID and requested name independently observed in
`server_albums()`, with matching private identity/route and a distinct object from
the creation acknowledgement cache. Missing/unverified visibility is uncertain and
never retried. Snapshot preflight is not a server-side atomic namespace condition.
Gateway/schema integration and synthetic receipt/new-ID/replay tests are complete.
Installed create-specific qualification additionally verifies independent server
visibility versus pending-only acknowledgements, using a typed synthetic private
library with no transport calls. No live account was used.

`PhotoLibrary.create_album_correlated()` exposes a typed-only private creation path
for future adapters. It refuses absent/untyped clients, mock/unsupported sessions,
shared scopes/owners and invalid names before invoking the existing correlated create
operation; there is no REST fallback. This verifies acknowledged ID/name/revision
only, not independently observed server state. Tests prove every unavailable case
dispatches neither typed creation nor REST, and the supported path invokes typed
creation once. No MCP create action or live account access is enabled by this API.

The MCP album-rename adapter now uses `server_albums()` for every fresh preview,
apply and post-write read rather than `refresh_albums()` with pending local merging.
Tests deliberately keep valid-looking albums (including distinct optimistic copies)
in ordinary refresh results while server reads are empty; neither preview nor success
proof may use those copies. Post-dispatch absence remains uncertain with replay
denied. Existing installed rename fixtures use the same independent read boundary.

Typed Photos create-album acknowledgements must additionally preserve the exact
submitted `albumNameEnc` and contain a nonempty, unpadded, non-sentinel revision tag
before conversion/cache/return. Missing or mismatched name/tag data is uncertain
after one dispatch, not proof that creation failed. Synthetic regressions cover these
cases with matching request ID/type to isolate payload/revision verification. No MCP
creation action is enabled yet: independent fresh post-create proof must still avoid
pending local caches, and REST creation remains unqualified for that adapter.

`PhotoLibrary.server_albums()` now exposes the existing raw album-read path without
pending-creation merging or cache replacement. Synthetic tests prove acknowledged
local albums are absent from an empty server read and caches remain unchanged. This
provides an independent read boundary for future create/rename post-state verification;
it does not itself enable another MCP mutation or prove any live account behavior.

Typed Photos album creation now requires exactly one `CPLAlbum` response with the
record ID already generated for the submitted create operation before returning or
caching a handle. Unrelated IDs/types or multiple records are uncertain after one
dispatch and must not be blindly retried. Existing success fixtures now correlate
their response to the actual submitted ID rather than returning an unrelated fixed
ID. The REST path and independent post-create/cache verification still need evidence
before an MCP create-album action is enabled.

Mail filtered-message scans evaluate flags per page and are explicitly not consistent
membership snapshots. Absence from a bounded unread/flagged scan cannot establish an
exact individual flag state, so no flag-bit masks or negative-state proof are guessed.

Drive recovery investigation found literal `etag: "null"` in existing deleted-state
fixtures (`tests/const/const_drive.py`). A nonempty sentinel is not a safe observed
mutation revision. Drive adapters now reject non-string, empty, whitespace-padded and
`null` etags before approval binding. Rename post-write verification additionally
rechecks account/endpoint binding and a real revision, not just the requested name.
Synthetic tests verify no preview for malformed/sentinel tags and uncertain/no-replay
outcomes for unbound or sentinel post-state.

Recovery remains unavailable: the existing `putBackItemsFromTrash` call accepts only
source ID/etag and does not select a destination. `restorePath` and recovery fixtures
show where some items were restored, but do not establish a general pre-dispatch
destination identity/collision/overwrite contract. Do not infer an arbitrary folder
from a path string or mistake absence from trash for exact restoration proof.

Drive recovery investigation requires explicitly discovered Recently Deleted targets,
not reusing ordinary file handles. `icloud_drive_trash_list` now projects bounded
metadata from the existing public trash listing with separate `drive-trash-file`/
`drive-trash-folder` selectors. Restore paths/raw IDs/content are not exposed, and
these handles cannot resolve as ordinary Drive targets. Synthetic adapter tests and
installed read fixtures qualify the discovery boundary only. Recovery and permanent
deletion remain disabled pending exact route/revision/restore-target/post-state proof.

Photos create-album inspection shows generated typed record names and a REST path
without caller-bound record IDs plus pending local album caching. Creation is not
enabled by this milestone; acknowledgement/local cache alone cannot prove the exact
new album or independently verified post-state.

`photo_album_writes.py` is a private top-level album-rename adapter selected by the
separately opted-in runtime `photos.rename_album` action. It resolves typed discovered albums, rejects unsupported
object types/parents/shared owners, and requires exactly one matching private library
and refreshed album identity with a real revision tag. Account/endpoint/zone/ID/parent
and fresh tag/name bind the exact single-use receipt. One verified public rename is
followed by another independent album refresh requiring matching identity/name and
valid tag. Reusing the mutated album object is rejected: locally pending albums can
survive a refresh, so their optimistic state is not independent proof. No-op consumes
approval without dispatch; unverified post-state is uncertain and cannot replay.
Gateway/schema and installed album-rename qualification now cover receipt denial,
one approved fresh-snapshot verified rename, acknowledgement-only uncertainty and
replay rejection. No live Photos account or CloudKit mutation was used.

Photos album rename uses the verified `PhotoAlbum.rename` method and one revision-
bearing CloudKit update. Album revision, zone, parent and type previously required
private-field access, so read-only `record_change_tag`, `zone_id`, `parent_id` and
`object_type` properties now expose only observed metadata. Zone mappings are copied,
missing revisions are not inferred, and reads do not dispatch transport. These are
snapshots: a future MCP adapter must refresh private-library albums before preview/
execution and independently verify the same album ID/parent/zone and requested name
after rename. No album MCP action is enabled by this metadata milestone.

`mail_folder_renames.py` is an internal adapter for conservative top-level custom
folder renames through verified `MailService.rename_folder`, now selected by the
separately opted-in runtime `mail.rename_folder` action.
Sources are discovered typed handles with positive UID validity and safe custom
ASCII names. System folders, encoded/nested names, unchanged names, descendants and
case-insensitive destination conflicts are rejected. Namespace snapshot, exact
source ID/name/UID validity, account and endpoint bind a single-use operator receipt;
snapshot preflight is not atomic. After one approved dispatch, fresh metadata must
preserve ID/UID validity and contain exactly the requested name with no alias. A
typed acknowledgement alone is uncertain and cannot replay approval. Gateway/schema
and installed rename qualification now cover receipt denial, exact-once verified
rename, acknowledged-but-unchanged uncertainty and replay rejection. The installed
fixture rediscovers the renamed folder before requesting another change. No live
Mail account was accessed.

Mail folder preview system-name checks reuse the verified library `SYSTEM_FOLDERS`
set rather than an incomplete duplicate. In particular, `Sent Messages` and
`Deleted Messages` are rejected ignoring case before namespace discovery or approval
publication. The library already protects these names before dispatch; the adapter
now rejects the invalid request at its earlier non-mutating boundary as well.

`pyicloud/mcp/mail_folders.py` adds an internal top-level folder-creation adapter,
now selected by the opt-in runtime `mail.create_folder` action. It uses the existing verified `MailService.create_folder`
facade and independently requires a new matching typed folder in fresh namespace
metadata. Account/endpoint and a canonical snapshot of folder IDs/names/UID validity
are approval bindings; this snapshot is not an atomic server-side revision condition.
For this first conservative adapter only ASCII letters/digits, spaces, underscores
and hyphens are accepted, excluding system names and surrounding whitespace. This
avoids guessing encoded server-name transformations. Existing names collide ignoring
case. Single-use approval is consumed before one create; uncertain post-dispatch
proof must not cause retries. Gateway tests and installed stdio qualification cover
receipt denial, one approved verified creation, acknowledged-but-absent uncertainty
and consumed-request replay rejection, using fake network-blocked namespace data.

Additional investigation: current Drive `create_folders` tests only acknowledge a
name, without proving response correlation to the request's temporary client ID.
Mail flag methods verify typed acknowledgement/modseq, but no inspected fixture
establishes numeric flag-bit meanings for independent exact-state verification.
Neither missing contract is guessed or enabled by this milestone.

`pyicloud/mcp/drive_moves.py` is a private file-only move adapter. The existing
`move_nodes_to_node` method supplies the source
etag to `/moveItems`; `parentId` and destination children are established by existing
Drive fixtures. The adapter resolves two discovered handles, rejects unsupported
private/root/shared/trashed contexts and name conflicts, and binds source parent plus
both identities/revisions and account/endpoint. Destination etag is a preflight
binding, **not** a server-side atomic destination condition: the facade sends only
source etag. Exact independent parent/membership verification is required after one
approved dispatch, with uncertain/non-retry outcomes. Synthetic tests exercise stale
revisions, parent changes, collisions, no-ops and ambiguous acknowledgements. Runtime
`drive.move_file` gateway/schema integration now selects this adapter using separate
source/destination handles. Synthetic gateway tests verify denial before receipt,
one approved verified dispatch and replay denial. Installed move-specific stdio
tests now exercise one authoritative move and one acknowledged-but-unchanged move,
with receipt denial, exact-once dispatches and replay rejection. No live account
or endpoint access is involved.

Private file-only Drive trashing uses the existing `move_to_trash`/`moveItemsToTrash`
contract with etag. Existing `tests/const/const_drive.py` trash fixtures establish
Recently Deleted `restorePath` metadata, also used by the public recover method.
The adapter requires exact trash-list membership plus fresh identity/zone/etag and
nonempty restore path after one approved dispatch. No folder/permanent deletion or
guessed delete endpoint is enabled. Installed trash-specific protocol also verifies
membership and uncertain-outcome replay denial using network-blocked fake data.

Drive, Calendar and Reminders approval resource bindings additionally include the
discovered service root. Changing the endpoint after preview now invalidates the
receipt even when account/resource IDs and revisions are identical; missing or
non-string endpoints fail closed. Synthetic regressions prove no mutation dispatch
for changed, empty or absent endpoints in all three adapters. This complements the
existing endpoint bindings in the Mail and Photos adapters.

Private Photos `set_favorite` uses the existing revision-aware public method, which
dispatches one modify operation and does not retry mutation. The MCP adapter adds
opaque discovered handles, fresh private-zone lookup, asset/master identity and
account/endpoint/revision bindings, independent receipts and post-read verification.
The facade can optimistically update local fields after an incomplete response, so
the adapter never treats that local state or a boolean acknowledgement as proof.
Shared-owner zones, deletion and uploads remain unavailable; no live Photos account
was accessed. Installed Photos-specific receipts and independent post-read checks
are also qualified using network-blocked synthetic assets.

Mail draft preparation now has an internal exact-body verification foundation
(`pyicloud/mcp/mail_writes.py`). The first foundation milestone reproduced the
library's escaped plain-only HTML conversion, then requires matching Drafts UID,
one matching body part, `text/html`, and byte-for-byte canonical content. Alternate
MIME layouts and server transformations fail closed; post-dispatch rejection must
remain uncertain and must never trigger a retry. Synthetic tests qualify this
policy only, not live server behavior. The runtime `mail.create_draft` adapter now
binds a freshly discovered enabled sender and canonical selectable Drafts identity/
UID validity, account and Mail route to the exact operator preview before consuming
approval and saving once. It rechecks returned draft identity and fresh body headers/
content; any post-dispatch failure is uncertain with no approval replay. Sending,
attachments, edits and deletions remain unsupported. No live Mail account was used.

Verified Calendar `add_event`/`remove_event` now have runtime create/delete adapters.
Creation fixes its GUID during preview and checks the fetched post-state; deletion
uses the fresh etag/ifMatch path and requires follow-up absence proof. Account,
calendar and event revisions remain part of single-use approval. No update/upsert
endpoint or high-risk invitation/recurrence semantics were invented.

The first executable adapter module, `pyicloud.mcp.reminder_writes`, now supports
verified Reminders create/update/delete callbacks with synthetic tests. Preview
paths do not mutate; scoped list/reminder state is re-read before approval
consumption, account/route/revision changes fail closed, and uncertain callbacks
retire the approval before reporting a sanitized failure. It is intentionally
**not registered as a live MCP tool** until the independent operator channel is
implemented. The other service writers and documented API gaps remain outstanding.

1. Finish independent read validation; defer CodeRabbit until implementation end.
2. Validate approval snapshots, expiration, replay, account/route mismatch and
   revision changes using synthetic data. No runtime writes enabled at this phase.
3. Wire independent operator preview/approval and per-action allowlists.
4. Implement existing supported Reminders/Calendar actions first; never dispatch
   mutations from the preview path or retry uncertain acknowledgements.
5. Add Drive, Photos and Mail adapters at verified boundaries. Sending, uploads
   and permanent deletion need separate high-risk action selection/approval.
6. Add missing Contacts/Notes library contracts with offline wire fixtures before
   their MCP adapters. Preserve public imports, signatures and generated sources.
7. Run full suites sequentially, strict per-file coverage, all hooks, installed
   package checks and docs; commit/push relevant milestones. Final hosted gates
   remain required. No real-account tests without separately approved targets.
