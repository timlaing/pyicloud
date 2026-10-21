# Mail Service Investigation

<!-- cspell:words cachedpart geqs mailws mccgateway messagepart savedraft thrid -->

## Status and Evidence

### Write Coverage Audit (2026-10-21)

The initial write roadmap is implemented in the public library and mounted CLI.
This is not a claim that every iCloud Mail operation is supported, that the API
is stable, or that the pending changes have been merged upstream.

| Roadmap item                                   | Implementation and offline evidence                                                                                           | Authorized live evidence                                                                                                                         |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Read/unread and flag/unflag                    | `set_read`, `set_flagged`, `mail mark`; `test_mail_flags.py`                                                                  | Four implementation writes; original state restored.                                                                                             |
| Moves, archive destinations, Trash and restore | Exact-destination `move`, `mail move`; `test_mail_moves.py`                                                                   | Inbox/Testing/Trash round trips through library and CLI. No automatic archive-folder selection.                                                  |
| Folder create, rename and delete               | Folder methods and CLI commands; `test_mail_folder_writes.py`                                                                 | One Unicode disposable folder created, renamed and deleted.                                                                                      |
| Draft create, replace and discard              | Saved handles and CLI state; `test_mail_drafts.py`                                                                            | Recipient-free create/edit/Trash lifecycle, rotating UID and body preservation checked.                                                          |
| Recipients and ordinary attachments            | Explicit To/Cc/Bcc, upload and saved references; `test_mail_drafts.py`                                                        | Upload/create/edit/discard with exact attachment-byte and recipient preservation.                                                                |
| Send, reply and forward                        | `send_draft`, `create_reply_draft`, `create_forward_draft` and CLI; `test_mail_drafts.py`, `test_mail_conversation_drafts.py` | One direct implementation send; reply/forward implementation draft lifecycles. Browser sends separately established all three final-send shapes. |
| Permanent deletion                             | Confirmed `delete_permanently`, `mail delete-forever`; `test_mail_message_deletion.py`                                        | One exact synthetic Trash target destroyed through the implementation; UID/ID absence verified.                                                  |

The upstream-integrated suite passed with 3,851 tests and 98.38% aggregate
coverage. The changed
Mail source modules and CLI had complete statement and branch coverage. All
mandatory hooks and the strict documentation build passed. Offline mocks check
failure handling, but do not prove Apple's behavior for every account or server
failure. Reply/forward sending through this implementation was verified offline,
not by additional live implementation sends. Browser sends and direct-send
implementation verification are distinct evidence, as detailed below.

Deliberate boundaries remain: no Mail Drop uploads, Undo Send scheduling,
inferred recipients, automatic quoting/reply-all, Junk spam-state mutations,
bulk destruction, or empty-Trash operation. Forward edits can retain prior
versions, which are reported rather than silently deleted. Write preflight is
not atomic with the mutation. Explicit uncertain outcomes never authorize an
automatic retry or cleanup, and all recorded live approvals are exhausted.

### Additional Write Investigation (2026-10-21)

The ordinary-move library and CLI implementation uses the observations below.
Folder management now implements the captured create, rename, and delete shapes
with fresh identity guards and explicit irreversible-deletion confirmation.
Draft saves and recoverable discard now use these observed wire shapes with
fresh sender/namespace checks and rotating-UID handles. Recipient-bearing drafts
and ordinary attachment uploads/edit preservation are implemented and verified
as described below. Guarded final sending of saved drafts now has offline
and live coverage. Reply/forward draft creation now has offline implementation
coverage, including retained threading, cached uploads and source-bound ordinary
forward attachments. Live tests verified reply creation/edit/Trash reconciliation
and forward creation/edit/retention reporting/Trash reconciliation, with exact
attachment bytes preserved. Confirmed single-message Trash destruction now has
an implementation and offline coverage derived from the browser capture below.
Mail Drop uploads remain unsupported. Additional live sends and permanent
message deletion require separate authorization.

An authorized browser test permanently deleted exactly one previously verified
synthetic reply already in Trash, after separate action-time confirmation. The
request used `email/set` with `clientIntent=deleteMessagesGems`, `domain: email`
and a one-element `destroy` array. HTTP 200 returned the matching account and
exactly that ID in `destroyed`, with no created, updated or failure effects.
Target-only read checks subsequently found neither the UID nor the message ID.
No whole-Trash metadata scan or attachment/body download was used. The browser
approval is exhausted; this does not authorize further deletions.

After all offline checks passed, a separately authorized implementation test
permanently deleted one exact synthetic forward already in Trash through the
mounted CLI. Fresh preflight verified its subject, ID, UID and UID validity.
The guarded transport allowed exactly one matching `email/set` destruction and
blocked any broader message query, additional write, or retry. The public service
returned a typed permanent-deletion acknowledgement; its post-verification and
independent target-only checks found neither the UID nor ID. No other message
metadata, bodies or attachments were queried. No new mail was created or sent.
The private audit was persisted before dispatch and remains outside Git. This
one irreversible deletion approval is exhausted. These checks demonstrate one
account's observed behavior, not an atomic mailbox snapshot or recovery promise.

Three authorized browser sends on 2026-10-21 verified new/reply/forward submission
with disposable text, one ordinary text attachment, and a single controlled
recipient. Each used `schedule/create` with a 15-second delay, then `draft/send`
with `messageGuid: Drafts/<uid>` and null-context session headers. The final
response named Drafts, supplied a positive modification counter, reset capability
fields to zero, and contained one `* <sequence> EXPUNGE` event. A read-only fresh
Sent lookup found all three synthetic subjects, the expected recipient and an
attachment. This proves acknowledgement and Sent-copy presence, not SMTP delivery
or live direct sending without the scheduling step. No additional sends are
authorized by that exhausted three-send approval.

A separate bounded implementation approval subsequently verified direct sending
without scheduling. The test performed exactly three writes: a tiny synthetic
ordinary attachment upload, draft creation to the one controlled recipient, and
one mounted-CLI `send-draft --confirm`. The public service accepted the observed
acknowledgement and verified the source draft absent. Fresh Sent metadata and
read-only body/attachment downloads verified one matching copy, recipient, body,
and exact attachment bytes. Other observed metadata in Inbox, Drafts, Trash and
Sent was unchanged. No retries, reply/forward sends, or permanent deletion were
performed. The three-write, one-email approval is exhausted. SMTP delivery was
not independently checked. Private local audit files remain outside Git.

Reply savedraft requests used `mode: reply`, `headerInReplyTo` and
`headerReferences` as arrays of strings. Forward requests used `mode: forward`
and a references array, without In-Reply-To. The forward retained a source
`messagepart:` attachment reference with null content ID and an encoded MIME
size; its save returned only a UID, with no cached-upload mapping array. These
are distinct from cached upload handles and must not be guessed or silently
stripped by reply/forward support. The implementation distinguishes cached and
stored references, retains a typed threading context in saved handles, and
checks fresh source metadata before reusing stored parts. It accepts explicit
full body content and recipients rather than inferring reply-all or quoting
private text automatically. No real identifiers, bodies,
recipient addresses, or signed URLs are included in tracked protocol evidence.

The first authorized seven-write threaded-draft test stopped after its sixth
write: forward edit returned a valid new UID but left the previous UID present.
The guard raised an uncertain outcome without retrying or discarding either
version. Fresh read-only checks verified both distinct records and the edited
version's recipients, references, body and exact original attachment bytes.
Checking exact UIDs from the earlier browser test also found both intermediate
forward versions still in Drafts, while the final sent version was absent.
Thus retention also occurs in Apple's own browser, not only in this client.
The implementation now reports retained versions explicitly in `MailDraft` and
send acknowledgements, without silently deleting or sending them. The stricter
old-UID absence rule remains for ordinary drafts and replies. The stopped
lifecycle must never be rerun wholesale.

A separately approved six-write test moved only those two new forward copies
to Trash, created/edited one new forward with its original stored attachment,
verified the returned retained-version ID/UID against fresh metadata, and moved
both new versions to Trash. Source absence and destination body/header/attachment
integrity were verified after each move. All four moves returned contradictory
acknowledgements that were reconciled read-only; no write was retried. Other
observed Inbox, Drafts, Trash and Sent metadata was unchanged. No email was sent,
uploaded or permanently deleted during this six-write test. The two historical
browser intermediate drafts were outside the cleanup scope and remain untouched.
The separate six-write approval is exhausted.

An authorized browser test moved one uniquely identified disposable message
from Inbox to an existing empty testing folder and back, then used Trash and
restored it to Inbox. The restored message was unread/unflagged, Inbox again
showed ten messages and three unread, and both test destinations were empty.
No message was opened or permanently deleted during these actions.

Ordinary moves used `email/set`, `clientIntent: moveMessagesGems`, and exactly
one `batchUpdate` operation. Its patch was only
`mboxRef: {replace: [destinationId]}`. Successful responses acknowledged the
submitted source identifier in `updated` with an **empty object**, unlike flag
writes' positive counter. Do not require or invent a counter for move results.
Fresh metadata after a move can have a different message identifier and local
UID. An acknowledgement does not establish the new destination identity, and
the old `MailMessage` must not be reused for a subsequent mutation.

The browser Trash command used `clientIntent: deleteMessagesGems` and added
`flags: {set: [seen]}` to the mailbox replacement. One real response contained
both `updated: {sourceId: {}}` and `notUpdated: {sourceId: {type: unknown,
description: ...}}`. This is an uncertain outcome, not success or an unambiguous
rejection. The request was not replayed. A fresh page load found the disposable
message in Trash, still unread; a fresh move restored it to Inbox. Offline tests
must retain this contradictory-outcome case and never automatically retry it.

The browser occasionally retained stale metadata immediately after a move.
An initial move-back interaction produced no captured mutation; a page reload
and fresh selection enabled restoration. UI disappearance and HTTP 200 alone
are not sufficient verification of write outcomes.

The previously captured browser JavaScript also shows additional spam-flag
changes for moves into or out of Junk. Do not silently equate those operations
with an ordinary mailbox-only move or guess localized system-folder names.

Creating one uniquely named disposable folder used `mailbox/set`,
`clientIntent: gemsCreateMailbox`, and
`{domain: mailbox, create: {mailbox: {name: requestedName}}}`. Its response
contained the created folder under the caller key `mailbox`, including actual
server identity, name, UID validity, counts, and system-folder boolean fields.
Renaming that same empty folder used `clientIntent: renameMailboxGems` and
`{domain: mailbox, update: {folderId: {name: newName}}}`. Its acknowledgement
was `updated: {folderId: null}`. Do not apply the flag or move acknowledgement
schema to folder operations. After a separate action-time confirmation, deleting
the verified empty test folder used `clientIntent: deleteMailboxGems` and
`{domain: mailbox, destroy: [folderId]}`. The response's `destroyed` array
contained exactly that folder ID; all other outcome maps were null. The folder
was absent afterward and the original message remained in Inbox.

A recipient-free draft was created, saved, reopened, edited, and saved again.
`message/savedraft` requests contained `date`, `from`, empty `to`/`cc`/`bcc`
arrays, `subject`, `textBody`, `htmlBody`, empty `attachments`,
`webmailClientBuild`, `isHME: false`, and session headers with null folder and
counters. An update supplied `draftGuid: Drafts/previousUid`; successful replies
contained a decimal-string `uid`. Each successful save returned a new UID,
including automatic saves while typing. Do not guess an idempotency key or
reuse the previous draft reference after a successful save. The sender and
client build must come from verified account/client context, not capture values.

#### Recipient and Attachment Investigation

A separately authorized browser draft addressed to one controlled test address
confirmed a nonempty `to` array of address strings in `message/savedraft`.
The browser saved the synthetic subject and body successfully and rotated the
draft UID. The `cc`, `bcc`, and `attachments` arrays were empty in that captured
request. This confirms recipient serialization, not delivery: no send was
requested or performed. After a native file-picker timeout and user-assisted
selection, the browser uploaded exactly one 77-byte synthetic text file.

The live upload confirmed `POST mailws2/v1/message/part`, `X-id` (a UUID),
`X-type`, `X-size`, and `X-name` query parameters, a `text/plain` content type,
and the raw text bytes as the body. Its successful modern response contained
`uid` matching the submitted UUID and `messagePart` with a `cachedpart:` UUID,
relative part URL, byte size, MIME type, base64 encoding, and filename. The
subsequent draft save supplied the absolute same-gateway part URL and cached
identity in both `guid` and `contentID`, with null expiration and a false Mail
Drop thumbnail flag. Its reply rotated the draft UID and returned an attachment
mapping with equal `requestGuid` and `newGuid`. Saving on composer close returned
another new UID with the same attachment mapping.

After closing and saving that exact composer, a fresh Drafts view showed six
messages and the unique synthetic subject with its attachment indicator. The
list's Trash action moved only that selected draft: Drafts returned to five
messages, and Trash visibly contained the same synthetic subject and attachment.
The captured mutation was a mark-seen mailbox replacement, not `destroy`.
No email was sent, no permanent deletion was performed, and no other draft was
edited. As with the earlier discard, HTTP 207 is not a clean success envelope;
fresh source and destination observations verified the recoverable final state.

Offline inspection of the already captured browser JavaScript supplies the
following supporting implementation leads (only the ordinary modern path above
has live upload evidence):

- The draft serializer maps `to`, `cc`, and `bcc` to arrays of formatted address
  strings. Existing draft references still use `Drafts/previousUid`.
- Its attachment projection includes `guid`, `name`, `url`, `contentID`, `size`,
  `mailDropExpirationDate`, `isMailDropThumbnail`, and `datatype`. A cached-part
  identifier may also supply `contentID`. Mail Drop and ordinary uploads must
  not be conflated.
- The legacy upload helper posts a local file body to `wm/messagepart`, with
  `X-id`, `X-type`, `X-size`, and `X-name` query parameters, and `X-channel`,
  `Content-Type`, `X-File-Name`, and `X-File-Size` headers. It labels the content
  multipart, but the code alone does not establish the actual wire encoding.
  The helper also supports remote URLs; that is a separate capability, not
  authorization to fetch caller-supplied URLs.
- Its declared upload response contains `status`, `uid`, `url`, and `msgPart`;
  the part includes `type`, `guid`, `datatype`, `encoding`, `name`, `filename`,
  `url`, and `size`.
- A distinct modern helper posts to `mailws2/v1/message/part` on the discovered
  gateway. It uses the same `X-id`, `X-type`, `X-size`, and `X-name` query
  parameters and a local file body, but does not set the legacy helper's
  multipart headers in that function. Its response is instead `{uid,
messagePart}`, with `guid`, `datatype`, `encoding`, `name`, `url`, and `size`
  inside `messagePart`. Do not accept the legacy envelope as a modern reply.
- The draft-save response schema permits an optional `attachments` array with
  `requestGuid` and optional `newGuid` / `contentId`. Ignoring this remapping
  could leave an edit with stale attachment identities after UID rotation.

Implementation must follow the matched modern upload and draft-save captures,
not just whichever helper is easiest to call. Do not copy the legacy route,
disable timeouts, trust arbitrary attachment URLs, or silently discard existing
recipients/attachments merely because a code path exists in the bundle. Keep
raw captures, signed URLs, and account-specific identifiers out of fixtures.

The library and mounted CLI implementation was then exercised under a separate
four-write approval: one ordinary synthetic text upload, one new draft addressed
only to the controlled test address, one edit preserving both, and one recoverable
Trash discard. The upload, create and edit returned verified handles; the draft
UID rotated and the original UID disappeared. A streamed download of the created
attachment matched the original synthetic bytes, and fresh body reads confirmed
the edited text and expected recipient. The discard returned contradictory maps,
so the client raised an uncertain-outcome error without retry. Fresh listings
found exactly one matching test draft in Trash and none in Drafts, with other
observed ID/UID/flag/subject projections unchanged in Drafts, Trash, Sent and Inbox.
No send or permanent deletion was attempted. Afterward, read-only checks also
verified the edited text and exact attachment bytes on the recovered Trash
identity; no additional mutation was required. The earlier native file-picker
failure and a local pre-dispatch harness assertion did not consume live writes;
the audit manifest records exactly four submitted mutations. Cc/Bcc, multiple
attachments and other MIME types remain offline-tested rather than live-proven.

The captured browser reads sender preferences with a GET to its discovered `mcc`
root, `cc/mail/v1/account/<discovered-dsid>/preference/web/all`, using
`userEntryPoint=/mail/load`. A metadata-only probe confirmed the `account` object
with `dsId`, `emailId`, `fullName`, `supportedDomains`, `aliases`, and
`customDomains`. Domains explicitly advertise `allowSendFrom`; aliases also
advertise `isActive` in the browser's declared schema. `MailService.senders()`
now validates that projection, matches the account ID, and returns only explicitly
enabled identities. A fresh public-API probe returned the same enabled primary
address as the raw preferences projection without printing its value. No writes
were made. Alias/custom-domain tests are synthetic, based on browser-declared
shapes; this account had neither, so their live behavior remains unverified.

After action-time confirmation to discard only this test draft, the list's
Trash command issued the same mark-read-and-move patch as ordinary message
Trash, including the contradictory outcome shape. The draft disappeared from
Drafts, restoring the original five-draft count. No `destroy` request or send was
issued; do not describe this capture as permanent draft deletion. The discarded
test draft can remain recoverable in Trash. Permanent deletion was untested at
that stage; the separate later evidence above does not change discard semantics.

A read-only implementation probe established that `geqs/query` ignores the
`noSelect`, `labelMailbox`, and `specialUse...` properties returned by
`mailbox/set`; it omitted those fields. The browser's actual folder query uses
`identifier`, `name`, `uidValidity`, and integer `flags`. Preflight now requires
that captured shape and accepts only zero flags, without inventing flag bits.
The captured JavaScript maps canonical server names `Drafts` and `Junk` to
special roles independently of translated labels. Those names and virtual
mailboxes are excluded. A repeat metadata-only probe validated Inbox, Testing,
and Trash preflight and independently confirmed the restored disposable message
was neither seen nor flagged. The numeric message flags were not assumed to be
zero: mailbox moves can change internal metadata bits.

A subsequent, separately authorized implementation smoke test performed exactly
four mailbox-only writes: library Inbox-to-Testing, mounted CLI Testing-to-Inbox,
library Inbox-to-Trash, and mounted CLI Trash-to-Inbox. All four returned exact
source acknowledgements with no failure entries. Fresh folder listings and
bounded metadata snapshots after every write located the unique disposable
message only in the expected destination, matching its original header/date/size
signature despite changed IDs and UIDs. Named `SEEN` and `FLAGGED` queries
confirmed unread/unflagged state throughout. Other observed records in all three
folders retained their IDs, UIDs, numeric flags, and metadata signatures. A fresh
read-only process subsequently verified restoration independently.

The test transport permitted metadata queries and only one exact mailbox-only
payload per stage. Attempts were persisted before dispatch to prevent replay;
no flag patches, bodies, attachments, sends, or permanent deletions were allowed.
Private manifests stayed outside Git. The mounted CLI used real parsing,
lookups, move methods, HTTP, and JSON output while reusing the authenticated API
under its outer SSL context. TLS verification remained enabled. These are
single-account, small-folder happy-path observations, not evidence of atomic
preflight, large-folder completeness, or universal server behavior. Synthetic
offline tests cover rejected, uncertain, malformed, and contradictory outcomes.

All request and response examples above are structural descriptions. Account
identifiers, cookies, real message/folder IDs, and raw captures remain untracked.
Synthetic fixtures must use fake stable values. Each new public operation needs
strict input validation, exact fresh scope checks, one non-replayed dispatch,
operation-specific acknowledgement validation, rejected/uncertain outcomes,
offline transport/failure/CLI tests, documentation, and independently checked
live state where authorized. Preserve the existing separation between reads
and explicit writes.

### Explicit Single-Message Flag Writes (2026-10-21)

Four authorized browser actions on one disposable email captured `POST email/set`
with `domain: email` and one `batchUpdate` entry. Each operation supplies one
message ID and `patch.flags.set` or `patch.flags.clear` containing only `seen` or
`flagged`. This is a delta, not replacement of the integer metadata bitmask.
The query's `clientIntent` was `clearMessageFlag` for read and unflag, and
`setMessageFlag` for unread and flag. Preserve that observed mapping rather than
equating intent names to the inverse `seen` bit.
Every response acknowledged exactly that same ID in `updated`, with an increasing
positive `modseq`. The response account matched the request's discovered `dsid`.
`created`, `destroyed`, `notCreated`, `notUpdated`, `notDestroyed`, `oldState`, and
`newState` were null. Populated failures and malformed responses are synthetic
offline cases, not additional live observations.

The disposable message was restored unread and unflagged, then checked after a
fresh page load; the mailbox still showed ten messages and three unread. No move,
deletion, send, or attachment action was performed. An initial stale-page attempt
produced no captured mutation or persistent state change; refreshing resolved it.
No raw capture exports, private IDs, credentials, or message contents are tracked.

This branch adds explicit library/CLI flag writes separately from ordinary reads,
with exact UID/message preflight, scoped account acknowledgements, no automatic
write retry, and distinct rejected/uncertain errors. The unnormalized session
request path avoids response-status/authentication replay. The server has not
provided a verified conditional-write token; preflight is not atomic with a
concurrent move or flag change. Acknowledgements do not replace a fresh state read.
That initial flag-only implementation excluded sending, drafts, moves, deletion,
and bulk writes; the later explicitly authorized additions are recorded above.

### Live Library and CLI Flag Verification (2026-10-21)

A separately authorized live smoke test used the committed flag implementation
against one uniquely identified disposable Inbox message, originally unread and
unflagged. Four sequential stages each submitted exactly one guarded `email/set`
request: library `set_read(read=True)`, mounted CLI `mark --action unread`, library
`set_flagged(flagged=True)`, and mounted CLI `mark --action unflag`.

Each response acknowledged only the exact intended ID with an increasing counter.
Independent, exact message-ID queries using the server's named `SEEN` and
`FLAGGED` predicates confirmed every requested transition. These checks did not
infer numeric flag bits or merely trust HTTP 200. Fresh bounded UID scans after
each write found no change to the other Inbox message IDs, UIDs, or numeric flags;
the message count stayed ten. A final fresh-process read-only check confirmed the
target remained unread/unflagged with its exact original numeric flags restored.

The mounted CLI exercised real parsing, exact message lookup, service writes,
and JSON acknowledgement output. Only account acquisition and context opening
were patched to reuse an already authenticated real service instance under the
outer CLI SSL context; HTTP responses and service methods were not mocked. The
existing local proxy and trusted CA bundle were used without disabling TLS
verification or changing system settings. No captured credentials were replayed.

A transport guard permitted only metadata routes and one exact flag-only payload
per stage. Attempted submissions were recorded before dispatch; failed or
uncertain stages could not be silently replayed. No retries, message bodies,
attachment downloads, sends, moves, deletions, or folder writes occurred. Private
identities, original metadata, and local reports stayed outside Git. No HAR was
exported for this smoke test.

This verifies the four happy-path transitions on one small mailbox/account, not
all failure maps, concurrent writes, custom retry adapters, other accounts, or an
unattended standalone CLI authentication flow. The offline rejection, uncertainty,
identity, and no-replay tests remain necessary; the live test does not establish
server-side atomicity or a universal protocol guarantee.

The original bounded, read-only baseline was reviewed on 2026-10-17 against
upstream `ace0096`; explicit flag writes are described above. This document records protocol
observations and limitations, not an Apple protocol specification. See the
[Mail guide](../services/mail.md) for the experimental public API.

Evidence comes from user-authorized Proxyman captures of iCloud Mail in a
browser: folder and message listing, message reads, a PDF download, searches,
and scrolling through Sent Messages. Capture inspection did not replay any
requests. Raw captures remain outside the repository; none of the examples
below contain real account identifiers, contacts, message text, or credentials.

Browser success does not establish that every account or pyicloud session can
use these endpoints. An independent folder-list probe on the same date succeeded
with an existing pyicloud session, its default client parameters, and an endpoint
resolved from its own login response. No browser credentials were imported and
no message or folder mutations were sent. This is single-account feasibility
evidence, not proof of access for every account or session state.

## Transport and Authentication

The observed core API is a dedicated HTTP JSON service, not CloudKit:

```text
<discovered mccgateway URL>/mailws2/v1/<operation>
```

The login response advertises separate `mail`, `mcc`, and `mccgateway` entries.
Core message operations use `mccgateway`; some preference operations use `mcc`.
The older `mail` entry can point to another host. Resolve the required endpoint
through `PyiCloudService.get_webservice_url()` rather than constructing a host
or substituting the legacy Mail URL. Preserve existing HTTPS validation and
report a missing endpoint as a service-availability error.

Browser requests contain account/client query parameters and authenticated
cookies, including a Mail-specific cookie. This does not identify which cookies
are strictly required. Do not copy browser cookies, captured client identifiers,
or account identifiers into implementation code. A feasibility probe should
use the library's authenticated session and its own parameters first.

Responses containing JSON sometimes declare `text/plain`. Parse the expected
envelope rather than treating that content type as evidence of a non-JSON body.
Use bounded connection/read timeouts and normal TLS verification. A local proxy
certificate belongs in an explicitly selected development trust bundle, never
in package defaults; disabling verification is not an acceptable workaround.

## Observed Operations

All paths below are relative to `/mailws2/v1/`. HTTP 200 was observed for the
listed examples. Error behavior is not established by those successful samples.

| Operation                   | Method and path          | Observed response                                                  |
| --------------------------- | ------------------------ | ------------------------------------------------------------------ |
| Folder and metadata queries | `POST geqs/query`        | `domainObjects`, optional aggregate results or continuation marker |
| Thread discovery            | `POST thread/search`     | Thread list, session state, optional folder status and events      |
| Selected thread details     | `POST thread/list`       | Thread list, session state and events                              |
| Message metadata            | `POST message/list`      | `domainObjects`                                                    |
| Selected message body parts | `POST message/get`       | Headers, parts, session state and events                           |
| Text previews               | `POST message/preview`   | Preview results, session state and events                          |
| Attachment download         | `GET message/part`       | Binary content and attachment disposition                          |
| Change polling              | `POST event/list`        | Session state and event strings                                    |
| Message flags and moves     | `POST email/set`         | Per-operation success/failure maps                                 |
| Folder creation             | `POST mailbox/set`       | Per-operation success/failure maps                                 |
| Save a draft                | `POST message/savedraft` | Draft UID                                                          |
| Send a draft                | `POST draft/send`        | Session state and events                                           |

The endpoint name `thread/search` alone does not prove text searching: captured
folder listings use it without a search term. Actual text searches were sent
through `geqs/query`.

## Folders, Messages, and Identifiers

Folder queries use `domain: "mailbox"`, exclude deleted mailboxes, and select
properties such as `identifier`, `name`, counts, flags, and `uidValidity`.
Some queries request labels as well. Do not assume every folder is selectable
or that display names identify system folders reliably across languages.

Message metadata queries use `domain: "email"`. Observed fields include a
message identifier, folder reference, folder-local UID, thread identifier,
internal date (including fractional Unix seconds), flags, size, decoded header values, preview identifier, MIME
parts, and a modification sequence. Property selection matters: a lightweight
listing omits fields found in the detailed response. Missing fields must not
be confused with false values or proof that a message has no attachments.

Distinguish message identifiers, folder-local UIDs, thread identifiers, and
mailbox identifiers in models and method arguments. No capture establishes
identifier stability across every move, restore, or mailbox recreation.

### Exact Metadata Lookup Verification

On 2026-10-19, controlled read-only requests verified `POST message/list` with
an additional `eq` predicate on the `identifier` or `uid` property, combined
with the existing mailbox predicate and `DELETED == false` filter. The UID is
a JSON integer; the message identifier is a JSON string. Requests used a limit
of two to detect ambiguous matches rather than selecting the first result.

Both identity forms returned the same known older Sent message, independently
confirmed to be outside the newest 50-message listing. A wrong-folder ID query,
a synthetic absent ID, and an absent UID returned empty `domainObjects` arrays.
All five requests returned HTTP 200, and mailbox UID validity was unchanged.
No bodies, flag changes, or other mutations were requested.

This protocol check used an authenticated session, not a new browser capture.
The original browser HAR was no longer available locally, and Proxyman was
closed. The evidence establishes these exact metadata queries on one account,
not arbitrary cross-folder identity stability. Raw requests, message metadata,
and account identifiers remain outside Git. The implementation checks the
mailbox epoch on both sides of lookup and rejects inconsistent responses.

## Message Bodies and Read State

`message/get` requests a UID and selected MIME part identifiers, with folder
context and modification counters in `sessionHeaders`. Responses contain
`longHeader`, address lists, the enclosing content type, and selected `parts`
whose entries contain a part GUID and content string. A multipart message's
enclosing type does not by itself describe each returned selected part.

All inspected body requests set `dontMarkAsRead: true`. Opening a message in
the browser separately sends `email/set` to set the seen flag. A read-only
client must not reproduce that mutation. Repeated library reads should verify
that unread state remains unchanged, rather than assuming the request flag is
sufficient evidence of the entire end-to-end behavior.

### Live Unread-State Verification

On 2026-10-18, an authorized test used a newly delivered disposable email that
had not been opened in Apple Mail or iCloud webmail. Before any body retrieval,
read-only metadata queries using the server's `SEEN` predicate placed the test
message in unread results and excluded it from read results. The read and unread
result sets were disjoint and together matched the independent Inbox listing;
the test did not assume a numeric bit value for the seen flag.

The test retrieved the same message body first through `MailService.body()` and
then through `icloud mail body`. After each retrieval, the message remained in
the server's unread results, remained absent from read results, and retained
exactly the original numeric flags. A further check after a two-second delay
confirmed the same state. No flag-changing, send, move, or delete requests were
made by the test. The disposable message was left unread; no cleanup mutation
was needed.

This closes the previously unverified unread-body path for the tested account,
not a guarantee for every account, future server version, message type, or
longer observation period. Verification used server metadata rather than an
independent UI check, and the earlier already-read PDF test alone did not
establish unread preservation. TLS verification stayed enabled through a
process-local bundle containing the normal CA roots and Proxyman's installed
public CA certificate. No global trust or package defaults changed. Private
message content, identifiers, account details, and session data remain outside
the repository.

Treat message HTML as untrusted content. Returning HTML as data is distinct
from rendering it, opening remote images, or executing scripts. The first
release should not implement an HTML viewer.

## Attachments

The PDF sample exposes metadata including `partId`, `contentType`, `encoding`,
`size`, `fileName`, attachment status, and disposition. Its binary download
uses `GET message/part` with `guid`, `type`, `name`, and `size` query parameters.
The observed GUID shape was `messagepart:<folder>/<uid>-<partId>`. This is a
sample shape, not proof that unrestricted string concatenation works for all
folder names and MIME structures; prefer a returned part reference where
available and verify encoding with additional synthetic examples.

The response declares `application/pdf` and an attachment disposition. The
decoded HAR body contains a PDF header and end marker. Metadata reports
112,008 bytes with Base64 encoding, while downloaded binary content is 81,850
bytes. Do not require the metadata size to equal the decoded download length,
and do not Base64-decode an already decoded HTTP response.

A live read-only Sent-folder check found `cloudpart:` identifiers containing
external URLs in otherwise valid MIME metadata. Those identifiers are retained
as opaque metadata and hidden from default representations. The first release
only downloads ordinary numeric MIME part identifiers through the discovered
Mail endpoint, rejecting Mail Drop/external parts before network access. It
does not follow or refresh external attachment links.

Download through the authenticated session to the discovered HTTPS endpoint.
Do not follow arbitrary hosts supplied in attachment metadata. Stream binary
responses with bounded timeouts; close responses on completion and failure.
File output must use an explicit destination and the repository's safe-path
principles. Remote filenames and disposition headers are untrusted inputs;
never permit absolute paths, traversal, or silent overwrites.

## Search

The captured query uses `domain: "email"`, mailbox scope, and a predicate that
excludes deleted messages. Text matching uses `type: "textMatch"`:

| User selection | Expression property  | Field     |
| -------------- | -------------------- | --------- |
| Subject        | `rfc822HeaderBytes`  | `subject` |
| Sender         | `rfc822HeaderBytes`  | `from`    |
| Recipient      | `rfc822HeaderBytes`  | `to`      |
| Copy recipient | `rfc822HeaderBytes`  | `cc`      |
| Body           | `rfc822MessageBytes` | `BODY`    |

The expression is `type: "fieldOf"`; its value selects the field, while the
predicate's value contains the search term. An unrestricted browser search
combines the five matching conditions with OR. Sender and subject-only queries
were captured independently; body matching was observed within the unrestricted
query rather than a separate body-only UI action.

Searches sort by internal date descending and use `collapseOn: "appleThrid"`.
Their result objects are therefore grouped by thread; result-object counts
must not be advertised as individual-message counts. Empty results are a
successful `domainObjects: []` response. Captures do not establish exact-address,
case-sensitivity, tokenization, phrase, or wildcard semantics for `textMatch`.

### Folder-Scoped Thread Expansion Verification

On 2026-10-19, saved search output was compared with message metadata and its
group IDs matched `appleThrid` values. Proxyman was closed and the original
`thread/list` request body was unavailable, so its payload was not guessed.
Instead, controlled authenticated read-only checks verified the already used
`message/list` query with an additional `eq` predicate on the `appleThrid`
property, alongside the existing mailbox and `DELETED == false` predicates.

One search-group ID expanded to two Inbox records and two Sent records, each
matching an independent metadata listing for its own folder. A synthetic
absent thread ID returned an empty result. Mailbox UID validity was unchanged;
the baselines fit below 1,000 records. No bodies or mutations were requested.
Raw requests, message records, and account identifiers remain outside Git.

This establishes bounded, folder-scoped metadata expansion on one account,
not the complete `thread/list` protocol or a cross-folder conversation API.
Search counts describe matching grouped results and cannot establish the total
number of thread members. Expansion must remain explicitly incomplete even for
short/empty pages or when an opaque continuation marker is absent.

## Client-Side Read-Only Tools

Multi-folder search composes the existing per-folder `geqs/query` calls; it does
not introduce a cross-folder server predicate. Requests remain bounded and
results preserve folder context rather than guessing global thread identity,
ranking, or completeness. JSON export serializes existing typed message data
and optionally calls `message/get` with the existing non-mutating body request.
Neither feature establishes raw MIME export or complete-mailbox backup support.

### Preview and Filter Captures

User-authorized browser captures on 2026-10-19 established `previewId` in detailed
message metadata and `POST message/preview` requests containing `folder`, an
array of advertised `previewIds`, and fresh `sessionHeaders`. Responses contain
`result` records with `messageGuid` (`<folder-name>/<uid>`) and `preview` strings,
plus folder context and events. Both a non-empty snippet and an explicitly
empty string were observed. Opaque preview IDs are passed through, not derived
from a guessed UID/part convention. No reading-pane actions are required to
request these list snippets.

The same capture established `POST thread/search` with `responseType` set to
`THREAD_DIGEST`, `maxResults`, `includeFolderStatus`, and a `filters` object:
Unread uses `unseen: true`; Flagged uses `flagged: true`. Replies contain
`threadList` digest records, `totalThreadsReturned`, session context, and folder
status. Digest IDs and counts describe thread groups, not individual matching
messages. The implementation does not interpret integer message flag bits or
persist returned modification sequences.

For one folder, Unread returned one group and Flagged returned two; selecting
both returned three groups, exactly their union. This evidence rules out
assuming AND for that combination, but does not establish arbitrary filter
combination semantics. The public API exposes each filter separately and
always labels results bounded/incomplete. Raw captures, snippet text, account
identifiers, and actual thread IDs remain outside Git; fixtures are synthetic.

### Live Tool Verification

Read-only library and CLI checks on 2026-10-19 verified all four tools. Searches
across two explicitly selected folders matched separate bounded searches, each
capped at five groups. Preview text from an advertised ID matched between the
library and CLI. Fresh-context Unread and Flagged queries matched the captured
browser group IDs exactly (one and two groups respectively); no captured
modification sequences or browser credentials were replayed.

Metadata-only and body-inclusive JSON exports of the known self-sent test
message had the expected versioned shape and private `0600` file permissions.
The selected message's integer flags were unchanged after preview and body
reads. No mutation endpoints, flag changes, or attachment downloads were
requested. Private output and comparisons remain outside Git; this verifies
one account, not every MIME structure or a globally atomic mail snapshot.

## Loading More Messages

### Scoped UID Verification

Authorized metadata-only checks on 2026-10-19 combined descending UID ordering
and strict lower-UID boundaries with an `appleThrid` equality predicate or a
`flags` field predicate (`SEEN == false` or `FLAGGED == true`). Page size one
returned two thread members, one unread message, and two flagged messages,
respectively, followed by empty terminal pages. Each identity set matched an
independent bounded query below its 1,000-record ceiling; mailbox UID validity
was checked around reads. No bodies, attachment downloads, or mutations were
requested. This is protocol evidence on one small account, not a large-mailbox
snapshot guarantee. Offline tests cover large pages, timestamp ties, bad scopes,
duplicates, changing membership, page errors, and explicit exhaustion.

The public scoped iterators share the mailbox iterator's UID progress and safety
checks. Their individual-message results must not be conflated with grouped
search or filtered thread results. Flags can change while scanning, so UID-range
exhaustion does not establish a single consistent view of matching messages.

These predicates do not establish full-text search pagination: a read-only
probe combining text matching with UID ordering returned HTTP 400, and a
date-ordered text query with a UID boundary returned HTTP 500. Neither error
means an empty result or completed scan. The observed browser search instead
uses overlapping date-bounded lightweight matches and UID detail batches.

### Search and Grouped-Filter Follow-Up

Additional metadata-only checks on 2026-10-19 tested the general `geqs/query`
endpoint, without changing the public API. Full-text matching with UID ordering
returned HTTP 400 there too. Adding a strict lower-UID predicate returned HTTP
500 both with normal date ordering and with no ordering. These failures do not
establish a usable UID continuation strategy for search.

An independent bounded comparison found 357 matching Sent messages and 275
grouped search hits. The group IDs equalled the distinct `appleThrid` values in
the matching messages. This confirms the distinction between individual matches
and grouped results on this account; neither count proves general pagination.

A strict older-date predicate matched the expected subset. However, date
equality was not an exact timestamp discriminator: it returned two baseline
records when only one had the selected timestamp. A separate one-millisecond
date-window probe using `ge` and `lt` returned HTTP 400. The production predicate
model was not extended for this probe. Do not assume date equality can exhaust
timestamp ties or infer undocumented date precision from this small sample.

Grouped-filter probes compared `THREAD_DIGEST` with `maxResults: 1` against
`THREAD_ID_AND_DATE` with `maxResults: 1000`. Unread returned one group in both;
flagged returned one digest but two lightweight groups. The digest IDs were
subsets of the lightweight IDs. Neither response advertised a continuation
field, demonstrating a truncated group response without a continuation token.
Fresh session headers were used, not replayed as paging cursors. No active
captured Mail request submitted a `continuationMarker` during this inspection.

Search and grouped-filter enumeration therefore remain unimplemented. A safe
next step requires verified boundary/tie semantics or a real continuation
handshake, including behavior at the 1,000-result ceiling. Never treat missing
markers, short pages, server errors, or change tokens as proof of exhaustion.
All these follow-up checks excluded bodies, attachment downloads, and mutations;
raw captures and account-specific metadata remain outside Git.

### Browser Date Ranges and Search Paging Candidate

A later user capture distinguished literal text `Last week` from a recognized
date-search token. The recognized range used two `stateInternalDate` predicates,
strict `gt` and strict `lt`, with integer millisecond boundaries. Combining the
range with unrestricted text matching returned the same observed date scope.
This is different from the previously rejected `ge` probe; it does not require
extending the production predicate model until a public date-search API exists.

Bounded read-only probes using the captured `gt`/`lt` form partitioned 357
matching Sent messages into 178 older and 179 newer-or-boundary records, each
matching its independent baseline subset. An open bracket from one millisecond
below an integer timestamp to one millisecond above it isolated the expected
boundary record. Date equality alone is still not a reliable discriminator:
an earlier sample included another record with a different timestamp.

An exploratory scan then requested 25 date-ordered matches at a time, fetched
each boundary timestamp bucket with the strict bracket, and continued strictly
before that timestamp. Fourteen pages of 25, a page of seven, and an empty page
yielded all 357 baseline identities without gaps. All 15 boundary buckets had
one record. Mailbox identity was checked around reads; no opaque marker was
replayed, and no bodies, attachment downloads, or mutations were requested.

This verifies a candidate sequence on one account, not general search
completeness. The data had no real timestamp ties. Before shipping, add tests
for tied timestamps split across pages, server-side bucket truncation, saturated
1,000-record buckets, unsupported timestamp precision, concurrent changes,
identity conflicts, response-scope/order failures, and request-budget errors.
The probe explicitly rejects saturated buckets rather than claiming exhaustion.
An under-limit bucket alone must not be assumed complete without verified
server behavior; larger or tied buckets still need continuation evidence.
Individual matching messages must also remain distinct from the existing
grouped search results. No public search-pagination API was added here.

### Mailbox Loading

The Sent Messages capture demonstrates a two-stage listing strategy:

1. Request detailed metadata with a limit of 50.
2. Request up to 1,000 lightweight records with a date-cutoff predicate.
3. Fetch detailed metadata by selected UID batches as the browser scrolls.

The first two responses contain 50 and 307 records, with one overlapping
identifier: 356 unique messages. Subsequent metadata requests cover all 356.
Deduplication is required even in this successful example.

Other query responses include `continuationMarker`, but no captured request
submits that marker. A complete opaque-marker handshake is not established.
The date-cutoff example does not prove safe enumeration beyond the 1,000-record
limit or across equal timestamps.

Additional authorized, read-only checks on 2026-10-18 used the library's own
session, not browser credentials. With a date ordering and limit of two, both
`message/list` and `geqs/query` returned two records with no continuation marker
from mailboxes known to contain more messages. Marker absence therefore cannot
establish exhaustion. Ordering `message/list` by `uid` descending and adding a
`type: "lt"` predicate on `uid` returned strictly lower UIDs on the next page in
both Inbox and Sent. Those responses included markers, but UID-boundary paging
does not require interpreting or replaying them.

The explicit `iter_messages()` path uses the verified UID comparison rather
than an unverified date cutoff or opaque-marker handshake. It validates page
ordering and identity, checks mailbox UID validity around reads, and requires an
empty terminal response. A request budget fails explicitly instead of silently
truncating. Bounded `messages()` and `search()` retain their previous semantics.
This remains a live scan, not an atomic snapshot, and the mailbox checks cannot
detect every concurrent change. See the Mail guide for the full limitations.

The same day's read-only comparison exhausted seven Inbox records and 357 Sent
records, ending on empty pages and matching independent bounded listings without
duplicates. The implemented library iterator matched the Sent baseline and CLI
`mail list --all` matched the Inbox baseline. No bodies or mutations were used.
These live baselines fit within 1,000 records; larger-mailbox coverage currently
comes from synthetic offline tests, not a live mailbox exceeding that size.

## Offline Search Paging Candidate

`pyicloud/services/mail/_search_pagination.py` contains a private state machine
for hardening the date-based search candidate. It has no live adapter, exported
symbol, service method, or CLI command. Existing bounded grouped search and
mailbox enumeration are unchanged. All tests use synthetic in-memory metadata;
no account data, captures, bodies, or binary assets are included.

The source contract retains the same bound search, folder, non-deleted scope,
and descending date order on every request. Ordinary pages continue strictly
before an integer millisecond timestamp; boundary queries use the observed
strict `gt(timestamp - 1)` / `lt(timestamp + 1)` bracket. Each page and its entire
boundary bucket are validated before any of their metadata is yielded.

Crucially, a short boundary response, absent continuation marker, or repeated
identical response is not proof of exhaustion. `_SearchPage.boundary_exhausted`
defaults to false. Only the closed-world test source can supply that evidence;
no live HTTP adapter currently has verified grounds to do so. The candidate
raises on unverified buckets, any continuation marker, raw buckets at the
1,000-record ceiling (including duplicate-heavy responses), unsupported date
precision/range, wrong scopes/order, conflicting metadata or identities, and
changed or disappearing mailbox namespaces. It never silently truncates.

The request budget counts ordinary, boundary, and empty-terminal requests.
Earlier yielded metadata cannot be retracted on later failure. Detectable
mutations such as a missing boundary member, a changed timestamp, or changed
metadata fail before the affected page is yielded. Other changes can remain
invisible: new matches above the cursor and disappearing older matches do not
turn a live scan into an atomic snapshot. Synthetic tests explicitly model that
limitation instead of claiming every concurrent change can be detected.

Before exposing any search iterator publicly, establish how a real source can
prove complete timestamp buckets, including tied and saturated buckets. Tests
can verify enforcement of that contract, but cannot prove Apple's responses
meet it. Do not satisfy the gate by setting the evidence flag for under-limit
HTTP responses. Individual matching messages must remain distinct from the
existing grouped search results.

### Captured Browser Paging Logic

A read-only inspection of the captured Mail browser bundle `2638Hotfix77`
on 2026-10-19 found separate paging paths. The decoded bundle's SHA-256 is
`3f2f88d6a331a8ee061d7888140d10ad6b7a8d411940ff66215450e75f6deaff`.
The bundle and its HAR remain outside Git; no browser code or headers are
included here. These are static observations about that version, not a verified
server contract or evidence that every feature-flagged path was exercised.

- The query builder selects strict limits for text, subject, and people search
  tokens. That path uses a date offset and omits the supplied continuation
  marker. The individual-message listing wrapper also forces strict limits.
- Other query paths can request `strictLimit: false` and carry continuation
  markers. The grouped-response parser distinguishes that mode from strict
  limits: it uses marker absence rather than a short response to mark the
  range exhausted. This does not establish a full-text continuation handshake.
- The individual-message response parser treats a response shorter than the
  requested limit as exhausted. Otherwise, it retains the last record's date.
  The next-range coordinator requests dates before that boundary plus one
  millisecond, retaining overlap for deduplication.
- When a returned range has the same upper and lower timestamp, the coordinator
  retries with its maximum range size (1,000). If that still cannot advance, it
  decreases the lower boundary by one millisecond. There is no bucket-completeness
  proof in that fallback. With more matching records at that timestamp than
  the server returns, advancing this way could omit the remaining matches.

The saved search capture contains 50 initial records and 308 lightweight records
with one overlapping UID, followed by detail batches covering all 357 discovered
UIDs. All 37 response envelopes contain only `domainObjects`; none provides a
completeness count or continuation marker. No distinct discovered UIDs share a
timestamp, so this capture cannot validate the tied-bucket case. Detail batches
use UID membership without repeating the text predicate; they fetch already
discovered matches and do not discover omitted matches.

Therefore, do not use the browser's short-page assumption or timestamp-skipping
fallback as evidence for `_SearchPage.boundary_exhausted`. Further investigation
needs a server-backed completeness signal or a verified way to partition and
exhaust a tied bucket without dropping matches. Ordinary scrolling of the same
small, untied result set will not close that gap.

### Finite UID Search Partition Probes

Further read-only checks on 2026-10-19 tested whether known UIDs could partition
full-text matching independently of date ties. An unrestricted, date-ordered
individual-message search returned 357 records below its requested 1,000-record
limit. The probes selected one known matching UID for equality and three known
matching UIDs for membership, retained the same mailbox, non-deleted, and text
predicates, and requested at most ten metadata records per restricted query.

Both `message/list` and `geqs/query` rejected both `uid eq` and `uid in`
combinations with HTTP 500. Mailbox UID validity was checked around successful
reads; no message bodies, attachments, or mutations were requested. The UID-list
payload used a private research envelope rather than widening the public query
model. An initial attempt to construct membership with that public model failed
local validation before submission and is not counted as a server rejection.

These four failures do not prove that every possible UID/text combination is
unsupported. They do establish that this tested finite-partition strategy cannot
currently attest timestamp-bucket completeness. The successful unrestricted
query is only a bounded baseline, not independent proof of exhaustive search.
Do not translate HTTP errors into empty results, silently fall back to local
substring matching, or enable a live adapter based on these checks.

## Synchronization and Failures

`sessionHeaders` carry folder context, modification sequences and feature flags.
`event/list` can return event strings with IMAP-style `EXISTS`, `FLAGS`, and
`VANISHED` notifications; these are not ordinary JSON change-record objects.
Incremental synchronization requires its own parser, reset/recovery rules, and
tests. It is outside the first release.

Mutation responses expose `notCreated`, `notUpdated`, and `notDestroyed` fields.
No populated failure maps or HTTP failure examples were captured. Future writers
must validate operation outcomes, distinguish uncertain submission outcomes,
and avoid automatic retries of non-idempotent operations. These observations
do not authorize implementing or exercising writers in the read-only phase.

## Original First-Release Proposal and Verification

The original read-only scope was endpoint discovery, folder listing, bounded message
metadata listing, selected body retrieval, mailbox-scoped search, and streamed
attachment downloads. It excludes sending, drafts, flag changes, moves, folder
mutations, HTML rendering, and incremental synchronization. No dedicated Mail
CLI group is included; the existing diagnostic probe lists folders read-only.

Use service-specific Pydantic request/response models and domain models, with
synthetic fixtures for observed response shapes. Reuse the existing session
and error hierarchy, not the CloudKit client: these envelopes are different.
Avoid making every service internal generic merely because it transports JSON.

Tests must cover selected/omitted fields, empty results, malformed envelopes,
body selection, non-mutating reads, thread-grouped search, overlapping batches,
bounded results, transport failures, and attachment streaming/path safety. Raw
captures, real mail content, and binary attachments must not become fixtures.
Follow the repository's fixture loader, filesystem sandbox, offline tests,
per-file coverage requirement, and full check suite.

The initial authentication checkpoint passed for folder listing with an ordinary
authenticated pyicloud session, without browser credentials or captured client
parameters. The first attempt failed certificate verification while local HTTPS
inspection was active; a process-local bundle containing the normal CA roots
and Proxyman's installed certificate allowed verification to succeed. No global
trust setting or package default changed.

After implementation, run explicitly read-only smoke tests and independently
check unread flags. Keep live data and reports sanitized and outside Git.
