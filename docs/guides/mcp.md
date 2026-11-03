# MCP extension

The optional local Model Context Protocol (MCP) extension exposes bounded,
read-only Calendar, Reminders, Contacts, Drive, Notes, Photos and Mail tools.
Account access is disabled unless the
operator explicitly selects an account, saved-session directory, and services.

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
With no account options only status and capabilities are exposed. Do not supply
passwords or verification codes as host arguments or tool inputs.

## Account setup and recovery

Authenticate separately with the existing CLI, installed via `pyicloud[cli]` or
`pyicloud[all]`, before launching the host. Use its interactive login workflow and
complete Apple's challenges outside MCP. See [CLI authentication](cli.md).
The MCP extension never reads a password keyring, signs in with credentials,
requests verification codes, or accepts codes as tool inputs.

Use an explicit, existing private session directory (POSIX permissions `0700`).
Do not run the CLI and MCP server against that directory concurrently. Atomic
session saving does not provide a cross-file transaction, durable synchronization,
or protection from competing processes. See [authentication](authentication.md).

Configure your MCP host to launch the installed executable, for example:

```json
{
  "mcpServers": {
    "icloud": {
      "command": "/absolute/path/to/.venv/bin/icloud-mcp",
      "args": [
        "--username",
        "your-account@example.com",
        "--session-dir",
        "/absolute/path/to/private/session",
        "--service",
        "calendar",
        "--service",
        "reminders"
      ]
    }
  }
}
```

Host configuration formats vary; adapt this launch command to your host. No
password is included. Use `--china-mainland` for the corresponding account region.
Startup and status do not contact Apple. The first authorized data read opens the
saved state and uses the public saved-session probe; it does not fall back to login.
`authentication_required` or `verification_required` means stop the host, recover
authentication interactively, then restart it. No live Apple compatibility is
implied by the offline test suite.

The MCP transport also refuses PCS device-consent/cookie-negotiation endpoints;
lazy service getters cannot trigger unsolicited device notifications. Account
modes that require PCS negotiation on every access may keep protected services
unavailable under this policy, even with a valid login. This is not a reason to
disable account security; supporting that flow requires a separately reviewed
operator-approval mechanism, not a model-controlled bypass.

## Tools and limits

| Tool                     | Purpose                                                                    |
| ------------------------ | -------------------------------------------------------------------------- |
| `icloud_status`          | Last-known authentication status; never probes Apple                       |
| `icloud_capabilities`    | Operator-enabled services and limits; availability is unknown until a read |
| `icloud_calendar_list`   | Calendar IDs and titles                                                    |
| `icloud_calendar_events` | One calendar ID and inclusive ISO `start`/`end` dates, at most 31 days     |
| `icloud_reminders_lists` | Private/shared list titles, scopes, and opaque handles                     |
| `icloud_reminders_list`  | Read one handle; optionally include completed reminders                    |

Data tools are registered only for services selected with `--service`. A model
cannot enable other services. Default result limit is 50, hard maximum 200.

Additional service opt-ins and tools:

| Opt-in               | Tools                                                                   | Data exposed                                                                                                                                                        |
| -------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--service contacts` | `icloud_contacts_list`, `icloud_contact_details`                        | Name search and expiring contact handles. Email/phone fields require explicit `include_emails`/`include_phones`; no photos or raw cards.                            |
| `--service drive`    | `icloud_drive_list`                                                     | Root/folder metadata and discovered folder handles. `query` filters names within the selected folder only; no recursive/global search or local filesystem paths.    |
| `--service notes`    | `icloud_notes_list`                                                     | Titles and modification/lock metadata within the 200 most recent summaries. `query` searches that title window only; no snippets, bodies, rendering or attachments. |
| `--service photos`   | `icloud_photos_libraries`, `icloud_photos_albums`, `icloud_photos_list` | Library/album handles and bounded filename/capture metadata. Handles retain the selected library even where IDs collide; no coordinates, URLs or downloads.         |
| `--service mail`     | `icloud_mail_folders`, `icloud_mail_messages`, `icloud_mail_search`     | Discovered typed mailbox handles, subjects and flags. Search is subject-only and thread-grouped; no previews, addresses, bodies or attachments.                     |

Additional handles expire after ten minutes or restart and are service-specific;
they cannot be exchanged between tools, accounts or server processes. Their bounded
registry retains at most 1,000 entries; older entries can be evicted. Refresh
discovery when a handle expires or sharing/mailbox state changes. They are opaque
server capabilities, not filesystem paths or caller-supplied URLs.

Extra-service lists conservatively report `complete=false` except a completed
mailbox-folder enumeration and individual contact-details response. Metadata APIs
can fetch an upstream index before local filtering; transport/body limits remain
in force and overly large indexes can return `response_too_large` instead of
silently bypassing the bound. Enabling a service permits its metadata tools; the
model cannot enable additional services. Returned names/subjects are still private
and untrusted content, even with body/download tools disabled.

Capabilities report each enabled service as `unknown`, `available`, or `unavailable`
based on its last read outcome. Authentication and transient transport failures
leave availability unknown, not unavailable. These are observations, not background
probes or guarantees that every shared zone remains accessible.
Responses are capped below 256 KiB and contain `records`, `complete`, and `error`.
No resumable cursor is fabricated for truncated results. Narrow your query if
`complete` is false. Shared-list enumeration always reports `complete=false`:
the public library suppresses zone-local availability failures, so enumeration
cannot prove all shared lists were accessible.

Reminders list handles expire after ten minutes, server restart, or the next list
enumeration. They bind to the exact private/shared list model, including owner
identity, without exposing zone-owner identifiers. Refresh the list enumeration
after sharing or access changes; never substitute raw list IDs for handles.
Descriptions, attendees, attachments, locations, and signed URLs are omitted.
Calendar times are wall-clock ISO timestamps, not implied UTC instants.

## Optional content and downloads

Metadata access does **not** grant content access. Add `--content-service notes`,
`--content-service mail`, `--content-service drive`, or `--content-service photos`
alongside the matching `--service`. Only explicitly enabled content tools are
registered. There is still exactly one account per process.

- `icloud_note_content(handle, max_bytes=16384)` reads a discovered note's plaintext
  without retrieving attachments or rendering HTML. Locked/unavailable notes fail
  closed. Maximum text response is 32 KiB of UTF-8, with explicit incompleteness.
- `icloud_mail_content(handle, max_bytes=16384)` re-fetches the typed message scope
  and returns bounded MIME text parts. Headers are omitted. HTML is **untrusted
  data**, not sanitized/rendered content: do not execute scripts or fetch its links.
- `icloud_drive_download` and `icloud_photo_download` return base64 chunks, never
  write a local file and never accept caller URLs/paths. Discover file/asset handles
  first; default chunk is 64 KiB, maximum 128 KiB. Continue using `next_offset` only
  while `complete=false`. Resumed chunks require a stable strong ETag and valid
  Content-Range; changed resources fail with `stale_download`. If the upstream
  ignores ranges and returns more than the chunk budget, access fails rather than
  silently bypassing the bound. Photo URLs must be discovered HTTPS Apple resources.

Content is private and becomes visible to the host/model provider. Enabling content
is a distinct operator decision, not a flag the model can grant itself. Bodies and
downloads remain disabled unless selected. Arbitrary account IDs, URLs, local
output paths, and multi-account selectors are not exposed.

## Opt-in Reminders writes

Writes remain disabled by default. The verified first runtime actions are
`reminders.create`, `reminders.update`, and `reminders.delete` (soft deletion).
Select **each** allowed action at trusted startup alongside `--service reminders`
and a dedicated private `--approval-dir`. Example additional host arguments:

```text
--service reminders --write-action reminders.create --approval-dir /private/approvals
```

The directory must already exist, be POSIX operator-owned with mode `0700`, and
be inaccessible to agents through other filesystem/shell tools. The channel assumes
a trusted OS/host; it is not hardware-backed proof of human presence. Windows
write approval currently fails closed. Multi-account operation is not supported.

1. Discover the exact list handle via `icloud_reminders_lists`.
2. Call `icloud_write_preview` with the allowed action, list handle and exact
   payload. Create supports `title`, `description`, `completed`. Update supports
   those fields and requires the reminder ID from the selected list. Delete takes
   an empty payload and the reminder ID. No mutation occurs during preparation.
3. In a **separate operator terminal**, run:
   `icloud-mcp --approval-dir /private/approvals --approve-request REQUEST_ID`.
   Review the escaped exact preview and type `APPROVE REQUEST_ID`. No piped `--yes`,
   model confirmation flag or model-callable approval tool exists.
4. Call `icloud_write_execute(request_id=REQUEST_ID)`. The owner lane re-fetches
   scope/revision, accepts the private receipt and consumes the HMAC-bound approval
   before the mutation. Account/route/revision mismatch, expired decisions or replay
   are rejected. A missing revision cannot be invented or bypassed.

Previews expire after five minutes; requests and payloads are bounded. If execution
reports `mutation_uncertain`, reconcile remote state before preparing anything
again. Never blindly retry. Approval storage contains private payloads: manage it
as sensitive local state, not a shared agent workspace. The mailbox caps records
at 256 and can require operator cleanup after many expired previews.

Other service writers and the Contacts/Notes/Calendar-update API gaps are still
under development. They are not enabled by these flags, and unsupported actions
do not claim success. Sending mail, permanent deletion and uploads remain separate
high-risk future actions.

## Opt-in private Drive rename

`--service drive --write-action drive.rename --approval-dir /private/approvals`
enables only the verified rename action, under the same separate terminal workflow
above. Discover a file/folder handle, then call `icloud_write_preview` with
`action="drive.rename"`, `target_handle=HANDLE`, and `payload={"name":"New base name"}`.
The name is a remote leaf/base name, not a local path; existing file extension
metadata is preserved by the library.

The adapter refreshes the exact node ID/zone/etag before preview and execution,
refuses root/virtual targets and all shared-owner/share-context writes lacking a
verified mutation contract, and re-reads the target after rename. It does not
trust a generic `OK` envelope as proof. Unverified/uncertain results retire approval
and require manual reconciliation. Already-correct names consume approval without
an unnecessary mutation. Trash, restore, move, permanent delete and uploads are
not enabled by this flag.

## Opt-in private Drive file moves

Enable `--service drive`, `--write-action drive.move_file`, and the private approval
directory. `icloud_write_preview` takes a discovered file `target_handle`, a
discovered folder `destination_handle`, and an empty payload. No raw IDs, local
paths, shared targets, root destinations, trashed nodes or folder moves are accepted.
Destination name conflicts are rejected rather than implying overwrite support.

Both current node revisions, source parent, identities/zone, account and endpoint
are bound to the exact single-use receipt and revalidated before one move. A no-op
also consumes approval. The library sends **only the source etag** to the server:
destination revision/collision checks are preflight checks, not an atomic promise
against concurrent destination changes. After dispatch, fresh reads must preserve
identity/name and show the destination parent and exactly one matching destination
child. Failures are uncertain and cannot replay approval; never blindly retry.
Runtime gateway/schema and installed stdio tests use network-blocked synthetic
facades; live account validation is not claimed.

## Recently Deleted Drive metadata

With `--service drive`, `icloud_drive_trash_list` lists bounded Recently Deleted
names/types/modification times with optional `query` and `limit`. It never exposes
restoration paths, raw IDs, content or download URLs. Distinct expiring trash handles
cannot be used as ordinary Drive file/folder handles for writes or downloads.
Results are not claimed to be a complete inventory. Listing does not recover or
permanently delete anything. Recovery remains unavailable; permanent trash-file
deletion has a separate elevated opt-in workflow below.

## Opt-in elevated private Photos uploads

Select `--service photos`, `--write-action photos.upload_file`,
`--high-risk-action photos.upload_file`, `--approval-dir`, and one or more
`--upload-transfer-host HOST` options. Hosts are exact lowercase DNS names selected
by the trusted operator, not URLs, IPs, wildcards or names inferred from an upload
reservation. Do not approve a host merely because a model or service response
suggests it. Review the host policy independently before starting the server.

`icloud_upload_stage` accepts an `operation` and exact `payload`:

- `begin`: `name`, integer `size`, lowercase `sha256`; returns `stage_handle`.
- `append`: `stage_handle`, exact next integer `offset`, strict `base64` chunk.
- `seal`, `describe`, `discard`: `stage_handle` only.

Default limits are 4 MiB/file, 8 MiB aggregate, eight stages, five-minute expiry and
64 KiB decoded chunks. Bytes are staged in memory, not read from local paths. Seal
requires independently computed size/digest agreement. Shutdown releases staging
references; this does not promise secure memory erasure. Staging grants no approval.

Discover a private `PrimarySync` library handle, then use `icloud_write_preview`
with `action: photos.upload_file`, that `target_handle`, and payload containing
`stage_handle` and integer `modified_ms` (epoch milliseconds). UTC metadata is
explicit. The independent terminal requires `APPROVE HIGH-RISK REQUEST_ID`.

Approval binds exact name/size/digest/timestamp, account, both service routes, library
revision and transfer-host policy. Bytes and request are consumed before dispatch.
Transfers use an isolated credential-free session, no environment credentials or
proxies, redirects or retries, and bounded request/body budgets. Registration uses
raw no-retry account-service requests. Existing PCS consent/device-negotiation
endpoints remain refused, not automatically activated.

Success requires independently indexed matching asset/master IDs in the private
zone with a real change tag. A duplicate returns `duplicate: true`, not a new asset
claim. Indexing delay, absence or any post-dispatch ambiguity returns uncertainty;
never automatically restage/retry. Installed stdio tests use synthetic receipts and
independent fake indexed state with outbound sockets blocked, not live Apple uploads.

## Opt-in elevated single Mail Trash-message permanent deletion

Select `--service mail`, `--write-action mail.delete_forever_message`,
`--high-risk-action mail.delete_forever_message`, and a private approval directory.
Metadata consent suffices; bodies and attachments are not read or inferred.
Use one discovered Trash `mail-message` handle as `target_handle` and `{}` as the
preview payload. Caller confirmation, ID selectors and bulk deletion are not supported.
Exact ID/UID, mailbox incarnation, fresh metadata, account and service route are bound
to the single-use preview. The independent terminal requires the elevated phrase.
The source handle is retired before one SDK call; success requires exact typed
acknowledgement and independent UID and ID absence checks. Ambiguity is uncertain,
not a retry instruction. Revalidation is not atomic against external mailbox changes.
Gateway and deletion-specific installed stdio tests are synthetic and network-blocked.
They verify exact-once dispatch, dual absence, uncertainty and retired-handle/replay
rejection. No live destruction is claimed.

## Opt-in elevated private trash-file permanent deletion

Enable `--service drive`, `--write-action drive.delete_forever_file`,
`--high-risk-action drive.delete_forever_file`, and the private approval directory.
Preview a discovered file `target_handle` from `icloud_drive_trash_list` with an
empty payload. Only private top-level trash files with a real etag and current
restoration metadata are supported. Ordinary handles, folders, shared/root targets
and caller confirmation flags are rejected. Restore paths remain private bindings,
not caller-selected local paths.

Exact identity/name/location/revision/account/route bind an elevated receipt;
only `APPROVE HIGH-RISK <request_id>` in the separate terminal authorizes one delete.
Receipt and source handle are consumed before the SDK call. Success requires both
trash-list absence and an independent exact 404/410 lookup under the same binding.
Generic acknowledgements, tombstones or other lookup errors are uncertain with no
retry source or compensation. This operation is irreversible; do not blindly retry.
Gateway and permanent-delete-specific installed stdio tests use network-blocked fake
records; no live file deletion is claimed.

## Opt-in private Drive file trashing

Enable `--service drive`, `--write-action drive.trash_file`, and the private
approval directory. Preview a discovered file `target_handle` with an empty
payload. Only private files outside Recently Deleted are supported, not folders,
shared nodes, permanent deletion, or uploads. Approval binds the account, endpoint,
file/zone identity and fresh etag; it is consumed before the existing etag-bearing
`move_to_trash` call. An independent Recently Deleted listing must contain exactly
that item, and refreshed metadata must expose a nonempty `restorePath` and valid
etag. Generic acknowledgements or disappearance alone are not proof. Failures after
dispatch are uncertain and cannot replay approval. No live Drive account was used.

## Opt-in private Photos add-to-album

Enable `--service photos`, `--write-action photos.add_to_album`, and the private
approval directory. Preview a discovered asset `target_handle`, a discovered album
`destination_handle`, and an empty payload. Only typed private same-zone assets and
top-level user albums with exact container-scoped membership queries are supported.
Shared/nested/unscoped targets and unknown revisions are rejected. This does not
upload a photo, move it out of another album, remove a relation, or delete anything.

Fresh asset/master/zone identity, album identity/parent, both revisions, account/
endpoint and observed membership bind the single-use receipt. Approval is consumed
before one correlated add; fresh pair and scoped membership reads must verify the
exact asset is present under the same route. An already-present asset still requires
approval for a no-op. Reads and writes are not one atomic snapshot. Unverified
acknowledgements are uncertain and cannot replay approval; never blindly retry.
Gateway and relation-specific installed stdio tests use network-blocked synthetic
state; no live Apple validation is claimed.

## Opt-in typed-only private Photos album creation

Enable `--service photos`, `--write-action photos.create_album`, and the private
approval directory. Preview a discovered `target_handle` from
`icloud_photos_libraries` with exactly `{"name": "New album"}`. Only a supported
typed-client private library is accepted; the action never falls back to REST.
Names are bounded and path-like/control-bearing titles are rejected. Unknown,
shared, nested or user-album inventories without revisions fail closed, and existing
name collisions are rejected ignoring case.

Approval binds account/endpoint/private zone and a cache-independent album snapshot,
then is consumed before one correlated create. Snapshot preflight is not an atomic
server namespace condition. Success requires a new returned ID/name/revision and a
distinct independently observed matching album in `server_albums()`; a local pending
acknowledgement cannot supply proof. Missing visibility is uncertain and cannot
replay approval: do not blindly retry. Gateway and create-specific installed stdio
tests use network-blocked synthetic libraries; no live Apple validation is claimed.

## Opt-in private Photos album renames

Enable `--service photos`, `--write-action photos.rename_album`, and the private
approval directory. Preview a discovered custom album `target_handle` with exactly
`{"name": "Renamed album"}`. Only private top-level container albums with an explicit
zone and observed record change tag are supported. Virtual, nested, shared-owner or
targets without revisions and unbounded/path-like titles are rejected.

Account, Photos endpoint, zone/album identity/parent and fresh record tag/name bind
the exact single-use receipt. Read through the private library's `server_albums()`
before preview and apply, without merging pending local creation caches;
consume approval before one public rename. Independent post-refresh must preserve
identity/route/parent and verify the requested name with a valid tag. Reusing the
mutated album object is not proof, because local pending caches can survive refresh.
Unverified outcomes are uncertain and cannot replay approval; no-op also consumes
approval. Gateway and installed tests are network-blocked synthetic checks, not live
Apple validation. Album deletion and shared mutation remain unavailable; creation
has a separate typed-only opt-in workflow above.

## Opt-in private Photos favorites

With `--service photos`, `--write-action photos.set_favorite`, and the private
approval directory, preview a discovered photo `target_handle` with exactly
`{"favorite": true}` or `{"favorite": false}`. No raw IDs or caller-selected zones
are accepted. The adapter requires one current private library matching the asset
zone, unchanged asset/master identities, an explicit favorite state, and a current
asset record change tag. Shared-owner/unsupported zones fail closed.

Account, Photos endpoint, zone, asset/master identities and record revision are
bound to the single-use receipt and revalidated before the verified library
`set_favorite` method. Approval is consumed even for no-ops. After a mutation, an
independent library lookup must verify the desired state and valid revision;
an acknowledgement or local optimistic update alone is insufficient. Any failure
after dispatch is uncertain and cannot replay approval. This action does not
enable shared-library mutation, deletion, or upload, and has only synthetic tests.

## Opt-in custom Mail folder renames

Enable `--service mail`, `--write-action mail.rename_folder`, and the private
approval directory. Preview a discovered `target_handle` from `icloud_mail_folders`
with exactly `{"name": "Renamed Folder"}`. Only top-level custom folders with
positive UID validity and conservative ASCII names are supported. System folders,
nested/encoded names, unchanged names, folders with descendants, and case-insensitive
destination conflicts are rejected. No tree moves, deletion or implicit child
renaming are enabled.

Approval binds the exact source ID/name/UID validity, account/endpoint and current
namespace snapshot. The snapshot is a preflight check, not an atomic server revision
condition. Consume-before-dispatch permits one public rename; fresh namespace reads
must preserve the source ID/UID validity and expose exactly the requested name.
Unverified results are uncertain and cannot replay approval. Rediscover the folder
after success before requesting another change. Gateway and installed tests are
network-blocked synthetic checks, not live account validation.

## Opt-in top-level Mail folder creation

Enable `--service mail`, `--write-action mail.create_folder`, and the private approval
directory. The preview payload is exactly `{"name": "New Folder"}`. This action
creates a top-level custom folder only: ASCII letters/digits, spaces, underscores and
hyphens, at most 255 characters, no surrounding whitespace or system folder names.
Encoded paths, parent selectors, renaming and deletion are not supported.

Fresh namespace metadata must contain unique folder IDs and no name collision
ignoring case. Account, endpoint and a canonical folder identity/name/UID-validity
snapshot are bound to the single-use receipt. This is a preflight snapshot, not an
atomic server revision condition. After approval consumption and one create, fresh
metadata must contain exactly the new matching returned identity. Otherwise the
result is uncertain and cannot replay approval; never blindly retry. Network-blocked
installed tests cover verified and acknowledged-but-absent synthetic creations.
No live account validation is claimed.

## Opt-in elevated saved Mail draft submission

Enable `--service mail`, `--content-service mail`, `--write-action mail.send_draft`,
`--high-risk-action mail.send_draft`, and the private approval directory. Both action
selections and content consent are mandatory. Preview a verified server-created
saved `target_handle` with an empty payload; caller confirmation flags and recipient
changes are rejected. The preview includes the exact saved sender/subject/text and
to/cc/bcc roles, bound to fresh identity/body/route state.

Only `APPROVE HIGH-RISK <request_id>` in the separate private terminal authorizes
one submission. Consume the receipt and retire the source handle before invoking
the SDK; confirmation is derived from that trusted decision, not model input.
Success reports `acknowledged: true, delivery_confirmed: false`: it is not SMTP
delivery proof. Uncertainty may already have sent the message and leaves no retry
handle. Inspect Drafts/Sent independently before any new request; never blindly
retry. The SDK preflight is not atomic with another editor. Arbitrary account drafts,
attachments and scheduled sending are unsupported. Gateway and send-specific installed
stdio tests use network-blocked fake submissions; no live send is claimed.

## Opt-in saved Mail draft edits

Enable `--service mail`, `--content-service mail`, `--write-action mail.edit_draft`,
and the private approval directory. Missing Mail content consent is rejected at
startup. Preview a saved `target_handle` returned by a verified `mail.create_draft`
result with exactly `subject`, `text`, `to`, `cc`, and `bcc`. Arrays are explicit
even when empty. Sender remains fixed; arbitrary account drafts, HTML, attachments,
threading and retained-version handling are unsupported.

Exact last-approved content/context is revalidated before preview and execution;
external changes are rejected rather than overwritten. The new payload and current
body/identity revision bind a separate single-use receipt. Approval and old handle
are consumed before one edit. Success requires a rotated ID/UID, old-UID absence,
same folder/sender/roles and exact fresh canonical body, then returns a replacement
opaque handle. Uncertain outcomes retire both request and old handle with no retry.
The SDK preflight is not atomic with save. Gateway and edit-specific installed stdio
tests use network-blocked synthetic state; no live Apple validation is claimed.

## Opt-in Mail draft creation

Successful verified creation also returns an opaque `handle` for the saved draft.
It retains the typed saved context and exact approved payload privately for ten
minutes in the current server registry. It cannot resolve as an ordinary Mail
message/body handle or in another registry, and reveals no sender/body/binding data.
Uncertain saves do not publish a saved handle. A handle does not grant edit approval:
edits and submission require separate opt-in flags and receipts. Reconstruction of
existing account drafts remains unavailable.

Enable `--service mail`, `--write-action mail.create_draft`, and the private approval
directory to expose draft creation through the existing preview/execute tools.
The payload must contain exactly `sender`, `subject`, `text`, `to`, `cc`, and `bcc`.
Recipients are explicit arrays (including empty arrays), at most 50 per field;
subject/text are bounded to 4 KiB/32 KiB. HTML, attachments, uploading, sending,
editing, and deleting drafts are not supported by this action.

Preview discovery requires an enabled sender (never inferred from login) and one
canonical selectable Drafts mailbox with UID validity. Account, discovered Mail
route, mailbox identity/UID validity, and sender address/display name are bound to
the exact single-use operator receipt and revalidated on the serialized worker.
Approval is consumed before saving; saving is not atomic with the preceding read.

After saving once, the returned draft identity, sender, recipients and folder must
match, and a fresh body must contain exactly the canonical escaped plain-text HTML
and expected Drafts body-part identity. Alternate MIME layouts or server formatting
fail closed as `mutation_uncertain`, even if saving actually succeeded. Inspect
the mailbox independently before deciding whether to request another preview;
never blindly retry. HTML remains untrusted data and is never rendered.

## Opt-in Calendar event create/delete

Calendar deletion additionally requires
`--high-risk-action calendar.delete_event` alongside its ordinary `--write-action`
selection. The operator must type `APPROVE HIGH-RISK <request_id>` in the separate
private terminal; ordinary approval does not authorize deletion. This conservative
tier is required because no event recovery contract is verified here. Calendar
creation remains standard risk. New/unclassified action names default to high risk,
but upload and other permanent-delete target types remain unavailable. Private
trash-file deletion, single Mail Trash-message deletion and saved-draft submission
have separate elevated workflows above.

The supported action flags are `calendar.create_event` and
`calendar.delete_event`, alongside `--service calendar` and the private approval
directory. They use the same separate terminal receipt and single-use HMAC workflow.
`icloud_write_preview` accepts `calendar_id`, optional `event_id` for deletion,
and the exact action payload.

Creation requires exactly `title`, `start`, `end`, and `timezone`. Start/end are
explicit naive ISO wall-clock datetimes; timezone is explicit rather than inferred
from the server process. The server allocates one preview GUID and does not accept
a caller-supplied existing GUID for creation. After dispatch it fetches the new
event and verifies calendar/GUID/title/time/zone instead of trusting an `OK` value.

Deletion takes an empty payload and explicit event ID. The event must still belong
to the selected accessible calendar and have a verified etag. The library's
`remove_event` uses that etag in its `ifMatch` request; successful follow-up absence
must be established, otherwise the result is uncertain. Invitation, recurrence and
attachment-bearing deletions are rejected rather than implying safe cancellation
support. Calendar updates are **not** implemented by guessing `add_event` behavior;
a verified update contract is still needed.

## Additional searches

- `icloud_notes_search`: bounded all-note **title metadata** scanning, beyond the
  recent list window; not full-text body search.
- `icloud_photos_search`: filename search in a discovered album/library route.
- `icloud_drive_search`: remote name search through at most sixteen folders; no
  local filesystem paths or unlimited recursive traversal.
- `icloud_mail_search`: subject/from/to header queries by default. `field=body` or
  `field=all` additionally requires the Mail content opt-in. Search results remain
  thread-grouped metadata, not bodies/previews.

Metadata scans default to 500 records and cap at 2,000; output still caps at 200.
Searches conservatively report `complete=false`; narrowing a query does not establish
whole-account completeness. They use the same request, deadline and response limits.

## Safety, timeouts, and shutdown

All returned account content is **untrusted data, not instructions**. Never follow
embedded commands or links. Read-only content remains private and is visible to
your connected host and potentially its model provider. Choose services accordingly.
Read-only annotations describe remote data tools; local session persistence and
saved-session validation can still have side effects.

One worker serializes account operations. Defaults are a 30-second caller wait and
at most eight pending calls; configure `--timeout` (maximum 120 seconds) and
`--queue-limit` (maximum 32). Transport requests also have bounded connect/read
timeouts, an operation deadline checked before each request, and a 32-request
budget. Response bodies are requested with identity encoding, limited to 4 MiB,
and consumed under deadline checks between individual bytes. A server that forces
compressed encoding is rejected with `unsupported_content_encoding`; bodies over
the bound return `response_too_large`. Only HTTPS requests are allowed. There are
no automatic extension retries.

Cancellation removes queued work when possible. It **does not kill running HTTP
work** or release its session ownership prematurely. Late results are discarded;
shutdown waits at most the configured timeout plus two seconds for a drain. If
that await expires, it reports `shutdown_timeout`; session closure remains queued
behind the owner rather than closing an actively used session. Default body reads
enforce a deadline, but DNS resolution or a custom transport can still delay worker
termination and process exit. An operator/host can terminate the process, but must
not assume a caller timeout means the worker ended.

No listener, arbitrary URL/path tools, or implicit account discovery is enabled.
Writes require the explicit startup allowlist and operator channel above.
Stdout is exclusively protocol traffic. Library logging is suppressed
in the launched process to prevent private upstream errors reaching host logs;
tool errors contain fixed categories, not payloads or tracebacks. `busy`, `timeout`,
`request_budget_exceeded`, `service_disabled`, and `expired_or_unknown_handle` are
recoverable operational categories. Do not blindly retry a busy or timed-out call.

Core-only installations can use offline help/version, but serving requires the
`mcp` extra. Python 3.10–3.15 remain the supported interpreter range. See the
[compatibility guarantees](compatibility.md) for the distinction between offline
validation and live Apple-service compatibility.
