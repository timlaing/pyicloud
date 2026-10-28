# Mail

Mail support is **experimental**, using the iCloud web interface's undocumented
HTTP API. Read operations have been verified on one account. Explicit
single-message read/unread, flag/unflag, and ordinary mailbox moves are available
separately, alongside explicit folder management, draft saves, and ordinary draft
attachment uploads and explicitly confirmed sending of saved drafts; ordinary
reads never change flags. Mail Drop uploads and
incremental synchronization are not supported.
The `icloud mail` commands expose the same bounded API.
The existing `icloud doctor --probe` command checks Mail by listing its folders.

## Permanent Deletion

**Permanent deletion is irreversible.** `delete_permanently()` destroys exactly
one current message already in the discovered Trash (`Deleted Messages`) mailbox.
It never moves a message to Trash first, selects the newest message, deletes a
batch, or empties Trash. Use an explicit move or `discard_draft()` for recoverable
Trash disposal instead.

```python
message = api.mail.message(trash_folder, chosen_message_id)
if message is None:
    raise ValueError("The selected message is no longer in Trash")
acknowledgement = api.mail.delete_permanently(message, confirm=True)
assert acknowledgement.permanent
```

The equivalent CLI command requires an exact message ID, its Trash folder ID,
and explicit confirmation:

```shell
icloud mail delete-forever MESSAGE_ID --folder-id TRASH_FOLDER_ID --confirm --format json
```

Before writing, the service checks the account, fresh folder identity and UID
validity, ordinary mailbox capabilities, and the exact message ID/UID. It accepts
only the matching account's acknowledgement destroying that one ID, then checks
that the target is absent by both UID and ID. The typed result is not a recovery
token or proof of a consistent whole-mailbox snapshot.

An explicit, unambiguous server rejection raises `MailWriteRejected`. A timeout,
unexpected acknowledgement, or failed post-verification raises
`MailWriteUncertain`: the message may already be gone. No automatic retry, purge,
or fallback follows. Avoid concurrent moves or edits in other clients: preflight
checks are not atomic, and interrupting the command does not undo a submitted
deletion.

A separately authorized live CLI test verified one disposable forward already
in Trash: one destruction request, a matching acknowledgement, and subsequent
target-only absence checks by UID and ID, without retries. This is single-account
evidence, not a recovery or atomicity guarantee.

## Sending a Saved Draft

`api.mail.send_draft(draft, confirm=True)` submits exactly the saved draft from
the latest successful create/edit handle. It requires explicit recipients,
rechecks the sender, recipients, attachment metadata, Drafts identity and subject,
and does not implicitly save edits, upload files, or choose a draft for you.
It sends **immediately**, without the browser's separate Undo Send scheduling
delay. There is no recall or delivery guarantee.

```python
acknowledgement = api.mail.send_draft(draft, confirm=True)
assert acknowledgement.acknowledged
assert not acknowledgement.delivery_confirmed
```

The CLI requires both the private saved handle and explicit confirmation:

```shell
icloud mail send-draft '<current-message-id>' --folder-id '<drafts-id>' --sender '<enabled-address>' --draft-state ./draft.json --confirm --format json
```

Success means Apple acknowledged submission and the source draft disappeared.
The acknowledgement does not contain a guessed Sent message ID. The server's
EXPUNGE value is a mailbox sequence number, **not** the draft UID. An unexpected
response, timeout, or failed post-send verification raises `MailWriteUncertain`:
the email may already have been sent. Inspect Drafts and Sent before deciding
whether to retry; there is never an automatic retry or cleanup send.

Fresh preflight is not atomic. Another editor can change the body or other draft
state between inspection and submission; use a dedicated draft that is not open
elsewhere. This experimental direct-send path has offline regression coverage.
Browser captures verified the final send envelope for new, replied, and forwarded
synthetic messages after Apple's separate scheduling request. A separately
authorized live implementation test uploaded one tiny synthetic text attachment,
created one draft to the controlled test address, and sent it once through the
mounted CLI without scheduling. Fresh reads verified the source absent, one Sent
copy with the expected recipient and body, and byte-for-byte attachment contents.
Exactly three writes occurred; other observed metadata in Inbox, Drafts, Trash,
and Sent was unchanged. This is not independent SMTP delivery verification.

## Reply and Forward Drafts

Choose an exact source `MailMessage` from a discovered folder. Both methods
require explicit sender, recipients and the **full desired body**, including any
quotes or forwarded content. They do not choose Reply-To, reply-all recipients,
or automatically copy private source text into your outgoing body. Empty
recipient tuples can save an unfinished draft, but cannot be sent.

```python
reply = api.mail.create_reply_draft(
    source_message,
    sender=chosen_address,
    to=("recipient@example.test",),
    text="My complete reply body.",
    attachments=(uploaded_attachment,),
)
forward = api.mail.create_forward_draft(
    source_message,
    sender=chosen_address,
    to=("recipient@example.test",),
    text="My complete forward body, including the desired original content.",
)
```

Replies carry `In-Reply-To` and `References`; forwards carry only `References`.
The exact source is reread without marking it read. A single standard Message-ID
is required; malformed, duplicated or unsupported identifiers and reference
chains exceeding 100 entries fail explicitly rather than being guessed or
truncated. An existing standard `Re:` / `Fwd:` subject prefix is not duplicated.
The returned private handle retains typed threading expectations. Subsequent
`edit_draft()`, `send_draft()` and discard checks refuse changed headers or a
context-free handle instead of dropping the thread relationship.

Apple can retain earlier **forward** versions on edit, including in its own
browser. The new handle reports these explicitly in `retained_drafts`; CLI text
shows their IDs. These are previously observed draft records, not automatically
refreshed snapshots or permission to delete them. Edits accumulate the observed
versions instead of hiding them. Sending or discarding the current handle acts
only on its current message, never on retained versions. Send acknowledgements
also report `retained_draft_ids`, which were **not sent**. Inspect Drafts before
any manual cleanup; the library never silently deletes old versions. Unexpected
retention for ordinary drafts or replies still raises `MailWriteUncertain`.

Replies include only attachments you explicitly supply. Forwards retain all
recognized ordinary file attachments from the source by default, plus any
explicit uploaded handles. Set `include_original_attachments=False` to omit the
originals deliberately. Existing source parts have a null content ID and encoded
MIME size; they are **not** cached uploads and do not require an upload mapping
in Apple's save response. Their account, gateway, folder UID validity, source
message identity, part metadata and exact endpoint URL are checked before reuse.
The original must remain available; a move, deletion or changed attachment
refuses a later edit/send rather than silently removing the file. Duplicate
filenames, unknown attachment metadata and Mail Drop source attachments are not
silently converted or dropped. These methods do not automatically copy inline
body resources. As with all writes, checks are not atomic with submission.

```shell
icloud mail reply-draft '<source-message-id>' --folder-id '<source-folder-id>' --sender '<enabled-address>' --to recipient@example.test --text-file ./reply.txt --attachment-state ./attachment.json --format json
icloud mail forward-draft '<source-message-id>' --folder-id '<source-folder-id>' --sender '<enabled-address>' --to recipient@example.test --text-file ./forward.txt --format json
```

Repeat `--to`, `--cc`, `--bcc` or `--attachment-state` for additional explicit
values. `--html-file` optionally supplies the full HTML body; otherwise HTML is
derived from plain text. `--omit-original-attachments` is forward-only. Save the
returned JSON privately and use `--draft-state` for editing, discarding or
explicitly confirmed sending. Neither draft-creation command sends an email.
Offline tests cover the full reply/forward create/edit/send path. The live reply
draft lifecycle preserved headers and attachment bytes through editing and Trash
reconciliation. The first forward edit exposed Apple's retained older versions.
A separately authorized six-write test then cleaned up those two test copies,
created and edited a new forward, verified the reported retained version, and
moved both new versions to Trash. Fresh reads verified threading and exact
attachment bytes, with other observed mailbox metadata unchanged. No sending or
permanent deletion occurred in either threaded-draft test. Contradictory Trash
acknowledgements were reconciled read-only, never retried. Two intermediate
versions from the earlier browser-only forward capture were left unchanged;
they were outside that cleanup approval.

## Drafts and Ordinary Attachments

Select an enabled address with `api.mail.senders()`; the login address is not a
substitute. Recipient tuples support one individual mailbox address per string,
with optional display names; named address groups are not supported. Drafts are
saved, **never sent**, even when they have recipients:

```python
draft = api.mail.create_draft(
    sender=chosen_address,
    subject="Working copy",
    text="First version",
    html="",
    to=("recipient@example.test",),
)
draft = api.mail.edit_draft(
    draft, subject="Revised working copy", text="Revised version", html=""
)
```

Every successful save returns a **new `MailDraft` handle**, containing fresh
`message` metadata, a verified `sender`, explicit `to` / `cc` / `bcc` tuples, and
account-bound attachment handles. Apple rotates the folder-local UID on save;
use the new handle for subsequent edits. Forward drafts can retain old versions
as described above; do not mistake those for the current version. Editing replaces all three
subject/plain-text/HTML fields, so every replacement argument is required.
Apple treats HTML as the canonical body: plain-only input is converted to safely
escaped HTML, preserving line breaks, so empty HTML cannot silently discard it.
Caller-supplied HTML is not sanitized or rendered. The two inputs do not guarantee
independently preserved MIME alternatives. These methods do not send
mail, implicitly upload files, enable addresses, or use Hide My Email.

Upload ordinary attachment bytes in a **separate explicit write**, then include
the returned handle in a save:

```python
attachment = api.mail.upload_draft_attachment(
    b"Synthetic attachment content.\n",
    filename="synthetic.txt",
    content_type="text/plain",
)
draft = api.mail.create_draft(
    sender=chosen_address,
    to=("recipient@example.test",),
    subject="Working copy",
    text="Attached test content",
    attachments=(attachment,),
)
```

The uploader imposes a local **25 MiB per-file limit**, not a claim about Apple's
account quota. It accepts bytes, a control-free basename, and a bare MIME type,
not a URL, multipart body, Mail Drop item, or remote-fetch instruction. It uses a
bounded timeout and never follows redirects or automatically retries. The reply
must echo the submitted upload UUID, filename, byte size and MIME type. Part
URLs must match the discovered gateway, exact part endpoint and cached identity.
An interrupted upload may have created cached state; there is no automatic
cleanup. Cached references may expire, and upload success does not save a draft.

Edits **preserve** the saved handle's recipients and attachments when their
arguments are omitted. Supplying a tuple replaces that entire role/list; an
explicit empty tuple clears it. A full subject/plain-text/HTML replacement is
still required. Persist the most recent returned handle, including any attachment
identity mappings; do not reuse an earlier draft or pre-save attachment handle.
Duplicate attachment filenames or cached IDs are refused. Ordinary upload
support does not enable inline images, existing arbitrary attachment imports,
or Mail Drop uploads.

Before editing, fresh UID, message ID, Drafts namespace, sender, body identity,
headers, and attachment metadata are checked against the saved handle. Extra or
changed recipients/attachments, unknown attachment status, stale handles, and
unsupported body metadata fail before dispatch instead of silently erasing data.
Non-empty Reply-To, Sender, Return-Receipt-To, Disposition-Notification-To, and
Resent-* headers are refused rather than silently erased. Other original headers
not explicitly represented by the saved draft handle, such as X-* fields, are not
preserved by a full replacement.
To/Cc/Bcc headers are checked again after a recipient-bearing save. A missing Bcc
header cannot be assumed to prove a successful Bcc save. Attachment mappings
must be complete, unique and reusable; an unsupported mapping remains uncertain.
These checks
are **not atomic conditional writes**: another client can still change the
draft between inspection and submission. A failed post-save read or unverified
acknowledgement raises `MailWriteUncertain`; inspect Drafts before any explicit
retry, because the save may have succeeded and rotated the UID. There are no
automatic retries or cleanup deletions.

To discard this draft, select the exact discovered Trash folder ID yourself:

```python
trash = next(folder for folder in api.mail.folders() if folder.id == chosen_trash_id)
acknowledgement = api.mail.discard_draft(draft, trash)
```

Discard requires the canonical `Deleted Messages` mailbox and moves only this
draft there; it does **not** permanently destroy it or empty Trash. The captured
browser operation also requests the `seen` flag. Its source-only
`MailMoveUpdate` does not supply a new destination UID or message ID. Conflicting
success/error maps remain uncertain even if the draft moved; reconcile fresh
Drafts/Trash metadata, do not retry automatically. These draft methods never
permanently destroy a message; that requires the separate, explicitly confirmed
permanent-deletion operation.

CLI body input comes from explicit local UTF-8 files rather than body text in
shell arguments. `create-draft` defaults omitted bodies to empty strings;
`edit-draft` requires **both** body files. An empty HTML file derives HTML from
nonempty plain text; use two empty files to clear the body:

```text
icloud mail senders --format json
icloud mail create-draft --sender '<enabled-address>' --subject 'Working copy' --text-file ./body.txt --format json
icloud mail edit-draft '<current-message-id>' --folder-id '<drafts-id>' --sender '<enabled-address>' --subject 'Revised copy' --text-file ./body.txt --html-file ./empty.html --format json
icloud mail discard-draft '<current-message-id>' --folder-id '<drafts-id>' --sender '<enabled-address>' --trash-id '<trash-id>' --format json
```

For recipient/attachment drafts, save JSON output privately and supply it to the
next edit/discard using `--draft-state`. The state cannot override the explicit
message ID, folder ID, or sender. Without it, addressed or attached drafts are
refused rather than silently cleared. For example (replace the IDs after each
successful save):

```sh
umask 077
icloud mail upload-draft-attachment --file ./synthetic.txt --content-type text/plain --format json > attachment.json
icloud mail create-draft --sender '<enabled-address>' --to recipient@example.test --attachment-state ./attachment.json --subject 'Working copy' --format json > draft.json
icloud mail edit-draft '<current-message-id>' --folder-id '<drafts-id>' --sender '<enabled-address>' --draft-state ./draft.json --subject 'Revised copy' --text-file ./body.txt --html-file ./empty.html --format json > next-draft.json
```

Repeat `--to`, `--cc`, `--bcc`, or `--attachment-state` for multiple entries.
On edits, omitted options preserve saved expectations. Use `--clear-recipients`
or `--clear-attachments` for intentional removal; they cannot be combined with
replacement values. Body files remain mandatory for edits. State inputs are
bounded to 128 KiB and validated before authentication. The upload command reads
at most 25 MiB plus one byte to reject oversized local files. JSON handles contain
private addresses and cached-part references; keep them outside version control
and do not overwrite an input handle through shell redirection before reading it.

No command implicitly chooses the newest draft, a sender, or a Trash destination.
Save output reports replacement metadata, never the body text. Invalid local
file inputs fail before authentication or submission. The client build comes
from the authenticated session, not a captured browser constant.

An authorized live test used exactly three writes on one new recipient-free
draft: creation, one mounted-CLI edit, and discard. The first request exposed
Apple's HTML-canonical behavior: nonempty plain text with empty HTML saved an
empty body. Before the edit, the shared save builder was corrected to supply
escaped HTML for plain-only input; fresh body retrieval verified the edited text
and UID rotation. Both create/edit request paths have regression tests for the
fix, but no second live create was performed. Discard returned contradictory
success/error maps and correctly raised `MailWriteUncertain`, without retry.
Fresh reads verified the draft absent from Drafts and present in Trash, with
other observed mail unchanged during the edit/discard stages. No message was
sent or permanently deleted. This is one-account evidence, not an atomicity or
recipient/attachment editing guarantee. A subsequent authorized **browser**
capture verified a recipient-bearing draft with one tiny ordinary text upload,
the saved attachment reference and response mapping, and recoverable Trash
cleanup. A subsequent implementation test used exactly four writes: a mounted-CLI
upload, library draft creation addressed only to the authorized test address,
one mounted-CLI edit preserving recipients/attachments, and library Trash discard.
Fresh reads verified the created attachment bytes, edited body, rotating UID,
preserved recipient and attachment metadata, and recoverable Trash location.
Discard again reported an uncertain outcome without retry; fresh source and
destination observations verified the move. Other observed metadata in Drafts,
Trash, Sent, and Inbox was unchanged. The edited body and attachment bytes were
also verified again in Trash, using only read requests after the four-write
lifecycle. No message was sent or permanently deleted.
This proves one recipient and an ordinary tiny text attachment on one account;
Cc/Bcc and other attachment types have offline coverage, not equivalent live proof.

## Sender Identities

`api.mail.senders()` reads enabled sender addresses from the authenticated
account's discovered Mail settings endpoint. Each `MailSender` has `address` and
`display_name`. The Apple login address is not assumed to be a Mail address.
Inactive aliases and addresses without explicit sending permission are excluded;
no account preference is changed. A malformed, ambiguous, or wrong-account
response raises `MailError` rather than choosing or enabling an address.
These reads do not create drafts or send mail. Other returned account preferences
are neither exposed through these models nor persisted.

## Explicit Folder Management

Create and rename one custom folder at a time:

```python
folder = api.mail.create_folder("Research")
folder = api.mail.rename_folder(folder, "Reference")
# Optional nesting uses a discovered parent, not a slash-separated name:
child = api.mail.create_folder("Examples", parent=folder)
```

Names are Unicode leaf names, encoded using the browser's
[modified UTF-7 convention](https://www.rfc-editor.org/rfc/rfc3501#section-5.1.3).
Returned `MailFolder.name` remains the raw server path, as in `folders()`;
do not feed an encoded path back as a new leaf name. Parent paths are preserved,
not encoded twice. Names cannot contain `/`, `?`, `*`, `%`, controls, or start
with `#`; the full encoded path is limited to 512 characters. System mailbox
names, labels, virtual folders, collisions, and unknown folder flags are refused.
Renaming and deletion refuse folders with children: no recursive operation is
implicit. Renaming stays within the current parent.

```text
icloud mail create-folder Research --format json
icloud mail rename-folder Reference --folder-id '<id>' --format json
icloud mail create-folder Examples --parent-id '<id>' --format json
```

**Folder deletion is irreversible and may permanently delete its messages.**
Delete children separately first. The library requires `confirm=True`; the CLI
requires repeating the exact folder ID:

```python
acknowledgement = api.mail.delete_folder(folder, confirm=True)
```

```text
icloud mail delete-folder --folder-id '<id>' --confirm-folder-id '<id>' --format json
```

By default, deletion refuses a folder observed to contain messages, including
deleted-flag messages. `allow_nonempty=True` (CLI `--allow-nonempty`) explicitly
permits deleting an observed nonempty folder. **The emptiness and child checks
are not atomic**: new mail or children arriving after inspection can still be
deleted. Confirmation acknowledges that risk even without the nonempty option.
There is no verified server-side empty-only delete condition or recovery promise.

Each operation checks fresh ID, name, UID validity, and flags before submission,
then verifies the result through a fresh folder listing. `MailFolderDeletion`
reports the folder ID and `permanent: true`, not a count of deleted messages.
`MailWriteRejected` means a matching structured rejection without success;
`MailWriteUncertain` means success could not be established, including a failed
post-write read or conflicting outcomes. Refresh before deciding what to do:
there are no automatic retries, compensating operations, or cleanup deletions.
Folder inventories requiring continuation or reaching the 1,000-record bound
are refused rather than treated as complete.

An authorized one-account live test created one Unicode-named disposable folder,
renamed it through the mounted CLI, verified it empty, and deleted it: exactly
three writes without retries. Fresh inventory confirmed its absence and unchanged
identities of other folders. The browser-shaped inventory omits `limit`: a
read-only comparison returned the same 46 folders without a marker when omitted,
but with a marker when `limit: 1000` was supplied. Neither observation establishes
atomicity, large-account completeness, or nonempty deletion behavior.

## Read-Operation Status

The supported read commands are deliberately narrower than every feature of
the Mail website. "Supported" does not imply an atomic backup or unbounded
server-side search. The current boundaries are:

| Operation                          | CLI                        | Coverage and limits                                                                                           |
| ---------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Folder metadata                    | `folders`                  | Lists active folders; fails on unsupported continuation rather than silently truncating.                      |
| Exact message metadata             | `get`                      | Select one message ID or folder-local UID, with mailbox identity checks; no content reads.                    |
| Message listing                    | `list`, `list --all`       | Bounded date-ordered listing or explicit UID enumeration, not an atomic snapshot.                             |
| Thread members                     | `thread`, `thread --all`   | Bounded metadata or UID enumeration within the selected folder, not a cross-folder conversation.              |
| Individual unread/flagged messages | `filter-messages`          | Explicit UID enumeration; changing flags can change membership during the scan.                               |
| Text search                        | `search`, `search-folders` | Bounded thread groups; exhaustive full-text search is not established.                                        |
| Unread/flagged thread groups       | `filter`                   | Bounded browser digests, without automatic pagination.                                                        |
| Unread/flagged thread identities   | `filter-threads`           | Explicit cursor enumeration of IDs and folder timestamps; no digests, bodies, or atomic snapshot.             |
| Preview                            | `preview`                  | Advertised snippets only, with exact message lookup by default; no body fallback.                             |
| Body data                          | `body`                     | Advertised plain-text/HTML parts only, without requesting a read-state change or rendering HTML.              |
| Attachments                        | `download`                 | Ordinary numeric MIME parts only; no Mail Drop/external URLs; private non-overwriting output.                 |
| Mail Drop attachments              | `download-maildrop`        | Explicit part selection, verified Apple host, isolated session, hard size limit; no redirects or renewal.     |
| JSON export                        | `export`                   | Exact metadata selection by default; body data only by explicit opt-in; not raw MIME/EML or a mailbox backup. |
| Original MIME/EML export           | `export-eml`               | Streams Apple's returned message bytes unchanged, with exact lookup and private non-overwriting output.       |

Full-text search pagination remains a separate protocol-research blocker.
Arbitrary external attachment downloads and incremental synchronization are
not implemented. Only the explicit, restricted Mail Drop path below is supported;
do not infer support for arbitrary URLs or synchronization
from the read commands above. There is no public exhaustive search iterator.
Mail writes require separate implementation, review, and
explicit authorization for any live mutation tests.

## Folders and Messages

Use your normal authenticated `PyiCloudService` instance. The service discovers
its endpoint from your account; do not supply captured browser cookies or URLs.
An account without the required endpoint raises
`PyiCloudServiceNotActivatedException`.
If the server requires an unsupported continuation for the folder list, the
service raises `MailError` instead of silently returning a partial list.

```python
folders = api.mail.folders()
for folder in folders:
    print(folder.id, folder.name, folder.message_count)

inbox = next(folder for folder in folders if folder.name == "INBOX")
page = api.mail.messages(inbox, limit=50)
for message in page.messages:
    print(message.id, message.uid, message.subject)
```

Message UIDs are folder-local and must not be used with a different folder.
`MailMessage` retains the folder context used to retrieve it. List results
deduplicate repeated message identifiers within the response.

**`messages()` and search are bounded snapshots, not complete enumerations.** Limits
must be integers from 1 through 1,000. Results have `complete=False` even when
fewer records were returned: continuation and completeness are not established
for every mailbox. No hidden requests fetch additional pages. This API is not
suitable for a complete-mailbox backup.

## Exact Message Lookup

Look up older messages without scanning the newest messages or enumerating a
mailbox:

```python
message = api.mail.message(inbox, "<message-id>")
if message is not None:
    body = api.mail.body(message)

message = api.mail.message_by_uid(inbox, 7)
```

The metadata-only CLI equivalent accepts exactly one message ID or UID:

```sh
icloud mail get '<message-id>' --folder-id '<folder-id>' --format json
icloud mail get --uid 7 --folder-id '<folder-id>' --format json
```

Both methods return `None` when the exact non-deleted message is absent from
the specified folder. They retrieve metadata only, not bodies or attachments.
There is no newest-message limit or fallback scan. Use an individual message
ID from `messages()`, not a thread-group ID from search.

Use a folder from `folders()` with positive `uid_validity`. Each lookup checks
the mailbox identity before and after its metadata request. Disappearance or a
changed UID validity raises `MailError`, as do ambiguous, incomplete, wrong-folder,
or mismatched message responses. A rename within the same UID namespace updates
the returned message's folder context.

UIDs are local to a mailbox incarnation. When saving a UID, retain its folder ID
and UID validity; never attach an old UID to a newly recreated mailbox's UID
validity. Lookup checks cannot make subsequent reads atomic: a message may still
be moved or deleted after it is returned. Content reads add their own namespace
checks when UID validity is known, as described below.

## Explicit Message Moves

`MailService.move(message, destination)` moves one exact folder-bound message.
Use fresh `MailFolder` objects from `folders()` and exact metadata lookup:

```python
folders = api.mail.folders()
source = next(folder for folder in folders if folder.id == source_folder_id)
destination = next(folder for folder in folders if folder.id == destination_folder_id)
message = api.mail.message(source, message_id)
if message is not None:
    acknowledgement = api.mail.move(message, destination)
```

```console
icloud mail move '<message-id>' --folder-id '<source-id>' --destination-id '<destination-id>' --format json
```

Both folder IDs are explicit; there is no implicit destination, newest-message
scan, bulk move, or automatic retry. Fresh metadata must identify the source
message and both mailbox namespaces. Capability queries use the browser-observed
`name` and integer `flags` properties. Only zero-flag ordinary folders are
supported; nonzero or missing flags fail closed without guessing individual bit
meanings. The browser's canonical server names `Junk` and `Drafts`, and every
`$category$_`- or `/`-prefixed label or virtual path, are rejected independently
of translated display labels, even with zero flags. Junk and
Drafts have additional write semantics that are not implemented here.

The submitted patch changes only mailbox membership. It does not explicitly
change read/unread or flagged state. Apple may update internal metadata flags
and assigns a new folder-local UID; the message identifier can also change.
`MailMoveUpdate` contains only `source_message_id`, `source_folder_id`, and
`destination_folder_id`. It acknowledges the submitted old ID, **not** the new
destination identity or current state. Never reuse the old `MailMessage` for a
second write. Refresh destination metadata and independently verify identity;
subjects alone are not unique identifiers.

Moving to a verified Trash folder is recoverable movement. Restoring means an
explicit move from Trash to the intended destination. No permanent deletion
endpoint is called, even when the source is Trash. This differs from the
browser's Trash shortcut, which also requests a read-state change. Archive can
likewise be an explicit ordinary-folder destination.

An exact failure without successes raises `MailWriteRejected`: the failure map
must contain only this message's key, and its value must be a dict whose
`type`, `reason`, or `description` is a non-empty, non-whitespace string. Every
other failure shape raises `MailWriteUncertain`: extra or foreign failure keys,
any other value, timeouts, malformed acknowledgements, wrong accounts or IDs,
late response failures, or contradictory success/failure entries. The latter
contradiction was observed in a real Trash response even though the message
actually moved. Inspect both folders before deciding whether to retry; never
assume an exception means nothing happened. Preflight is not an atomic
conditional write, and no compensating move or POST retry is performed. Custom
transport adapters must not retry mutation POSTs.

An authorized live test exercised two library moves and two mounted CLI moves,
restoring one disposable message to Inbox. Fresh metadata verified every
destination and unread/unflagged state; other observed records in the three
small test folders were unchanged. Exactly four mailbox-only writes occurred,
without retries. This does not establish atomicity or large-folder guarantees.

## Explicit Message Flag Updates

These are **writes**, not read options. Use a message returned by an exact
folder-scoped lookup, never a thread/search identifier or a guessed UID:

```python
message = api.mail.message(inbox, "<message-id>")
if message is not None:
    acknowledgement = api.mail.set_read(message, read=True)
    # read=False marks it unread. These are separate, explicit operations:
    acknowledgement = api.mail.set_flagged(message, flagged=True)
    # flagged=False removes the flag.
```

The boolean keyword is required. Each call changes only `seen` or `flagged`
through an observed named-bit patch, without replacing any other flags. The
service requires known positive mailbox UID validity and the discovered account
identifier, then re-reads the exact UID and checks its message ID before writing.
Missing, moved, mismatched, or recreated identities fail before submission. These
checks are not a server-side conditional transaction: a concurrent move, deletion,
or flag change can still race the request. A rename does not change a message ID.

`MailFlagUpdate` contains `message_id`, `flag`, `enabled`, and the acknowledged
`modseq`. It is a server acknowledgement, not a current-state snapshot or sync
token. The input message remains unchanged; refresh metadata after writing.
Only one message is submitted, and every outcome field and acknowledgement
identity is checked. Unknown response fields or malformed envelopes fail closed.

`MailWriteRejected` means the server explicitly rejected this one update: the
failure map contains only this message's key, and its value is a dict whose
`type`, `reason`, or `description` is a non-empty, non-whitespace string.
`MailWriteUncertain` means submission could not be confirmed, including timeouts,
non-200 responses, mismatched results, extra or foreign failure keys, failure
values that do not meet those requirements, or late response failures. **The
write may already have taken effect. Refresh metadata before deciding whether
to retry.**
Both inherit `MailError`. Server failure details and account IDs are not printed
in these errors. No automatic retry or authentication replay is performed by the
write path. Custom transport adapters must not be configured to retry POST writes.

The CLI requires an exact folder ID, individual message ID, and action:

```console
icloud mail mark '<message-id>' --folder-id '<folder-id>' --action read --format json
icloud mail mark '<message-id>' --folder-id '<folder-id>' --action unread
icloud mail mark '<message-id>' --folder-id '<folder-id>' --action flag
icloud mail mark '<message-id>' --folder-id '<folder-id>' --action unflag
```

There is no default action, bulk selection, `--limit` fallback, implicit folder
selection, or write attached to reading a message. Test on disposable messages
first. Browser captures verified the four request/acknowledgement shapes.
An authorized live smoke test then exercised library read/flag and mounted CLI
unread/unflag writes over real HTTP on one disposable message. Independent
named-flag queries verified every transition; a fresh-process final check
confirmed the original flags and other Inbox records were unchanged. CLI tests
reused a real authenticated service instance, not mocked HTTP responses. This
single-account check does not verify all server failures or concurrent changes.

## Enumerating a Mailbox

Opt into UID-based pagination explicitly:

```python
for message in api.mail.iter_messages(inbox, page_size=50, max_pages=10000):
    print(message.id, message.uid)
```

The iterator requests metadata in descending **UID order**, which can differ
from date order. Each subsequent page requests only UIDs strictly below the last
UID of the previous page. Equal internal timestamps therefore do not create
boundary gaps. Page sizes remain limited to 1 through 1,000, but the total number
of messages is not limited to 1,000. No bodies or attachments are fetched.

Only an empty response establishes exhaustion: neither a short page nor a
missing continuation marker is sufficient. Exact repeated identities within a
page are deduplicated. Inconsistent identities, wrong folders, ignored UID
boundaries, or incorrect ordering raise `MailError` instead of silently dropping
messages or looping. Server continuation markers are not synchronization tokens
and are not needed by this UID-boundary strategy.

Use a fresh folder from `folders()` with a positive `uid_validity`. The iterator
checks the mailbox before the first request and after every page. A disappeared
folder or changed UID validity aborts the scan so records from a recreated
mailbox are not mixed into it. A rename within the same UID namespace updates
the folder context on yielded messages. These checks require extra folder-list
requests; pages are otherwise fetched lazily, with no background prefetching.

`max_pages` is a positive request budget, including the final empty-page request.
Exhausting it raises an error rather than returning a successful partial scan.
An exception after earlier yielded records does not retract those records;
callers needing all-or-nothing output must stage their results until iteration
finishes. The iterator retains seen identifiers to detect cross-page conflicts.

**This is a live enumeration, not an atomic snapshot or incremental sync.** New
higher-UID messages arriving after the scan starts are outside its remaining
range; messages deleted or moved during the scan may disappear. UID validity
checks detect observed mailbox recreation, not every concurrent message change.
Reaching an empty page does not prove a consistent point-in-time backup. The
protocol remains experimental and was verified on one account.

## Bodies

```python
message = page.messages[0]
body = api.mail.body(message)
for part in body.parts:
    print(part.content)
```

Body retrieval requests the advertised plain-text and HTML parts with
`dontMarkAsRead=True`. It does not issue the separate flag-changing request used
by the browser. Messages without supported text parts raise `ValueError`.
Reading a message in the browser separately may still change its read status.

When the message's folder has UID validity, body reads recheck that namespace
before and after the content request. A stable rename uses the current folder
name; an observed recreation, disappearance, or rename during the request fails
instead of returning a successful body result. These checks add folder-list
requests and do not create an atomic snapshot. Legacy metadata without UID
validity retains the existing bounded-read behavior but cannot provide these
namespace checks; refresh folder metadata whenever possible.

Headers and HTML are untrusted data. This API does not render or sanitize HTML,
run scripts, load remote images, or execute attachments. Never insert returned
HTML into a privileged browser context without appropriate sanitization.

## Message Previews

```python
message = api.mail.messages(inbox, limit=50).messages[0]
snippet = api.mail.preview(message)
if snippet is not None:
    print(snippet.text)
```

This fetches a snippet from `message/preview`, not the message body. Fresh
metadata must advertise a non-empty `preview_id`; the client never guesses one
from a UID or opens the body as a fallback. An empty response returns `None`;
an explicitly empty snippet is a `MailPreview` whose `text` is `""`.

The folder must have positive UID validity. Mailbox identity is checked before
and after the request, and the returned folder and message GUID must match.
Ambiguous or mismatched replies raise `MailError`. These checks do not make the
request atomic against every concurrent message change. No synchronization
state is retained. A stable pre-request rename uses the current folder path;
a rename observed during the request fails because that path may have been
reassigned. The same path guard applies to grouped thread filters.
Preview strings are untrusted data: the CLI prints escaped
JSON even in text mode, never HTML. Retrieval does not request a read-state
mutation; opening a message separately in the browser may change its read state.

## Search

```python
matches = api.mail.search(inbox, "example", field="subject", limit=50)
for match in matches.results:
    print(match.id, match.subject)
```

Fields are `subject`, `from`, `to`, `cc`, `body`, and `all` (the default). `all`
matches any of the other five fields. Search is restricted to one folder and
excludes deleted messages. Text matching is not a promise of exact-address,
case-sensitive, phrase, or wildcard semantics.

Results are grouped by thread, newest first, and are not interchangeable with
`MailMessage` objects. A result count is not the number of matching individual
messages. No cross-folder search or automatic result expansion is performed.

## Thread Messages

Expand a search-result thread ID into individual messages in the same folder:

```python
matches = api.mail.search(inbox, "example", field="subject", limit=50)
if matches.results:
    page = api.mail.thread_messages(inbox, matches.results[0].id, limit=50)
    for message in page.messages:
        print(message.id, message.uid, message.subject)
```

`thread_messages()` returns a `MailThreadPage` with `thread_id`, `messages`,
`limit`, and `complete=False`. It fetches metadata only, including MIME part
information, not bodies or attachments. Returned `MailMessage` objects retain
their folder context and can be passed to the library's `body()` or
`attachment()` methods explicitly.

The thread ID is the opaque group ID from search, not a message ID or a subject
line. Expansion includes non-deleted members of that thread in the **selected
folder**, even if a member did not match the original search text. Messages in
Sent, Archive, or another folder are not included automatically.

Limits are 1 through 1,000, defaulting to 50; the request asks for newest-first
metadata. Results are always incomplete snapshots. A short or empty response,
an absent continuation marker, or a search hit's count does not establish a
complete conversation. This bounded method does not automatically page or expand
across folders.

Use a folder from `folders()` with positive `uid_validity`. The service checks
mailbox identity before and after the metadata request and raises `MailError`
if the folder disappears or is recreated, or if returned identities, folder,
thread, or response size violate the request. These checks do not make the
result atomic against concurrent message changes.

To explicitly enumerate that folder's thread members, use:

```python
for message in api.mail.iter_thread_messages(
    inbox, matches.results[0].id, page_size=50, max_pages=10000
):
    print(message.id, message.uid)
```

Call this only when the search returned a hit. The iterator uses descending UID
order, not date order, and continues through short pages until a verified empty
page. It retains the thread, folder, and non-deleted predicates on every request
and rejects records belonging to another thread before yielding any page data.
It shares the mailbox iterator's identity checks and explicit page-budget errors.
The budget includes the terminal empty request. Already yielded records cannot
be retracted after a later failure. Exhaustion covers this live folder UID scan,
not a complete cross-folder conversation or a consistent snapshot.

## Individual-Message Filters

```python
for message in api.mail.iter_filtered_messages(
    inbox, filter_by="unread", page_size=50, max_pages=10000
):
    print(message.id, message.uid)
```

`filter_by` must be `unread` or `flagged`. This explicitly paged API returns
individual `MailMessage` objects, not thread groups. Each request applies the
selected server-side flag predicate, excludes deleted messages, and advances by
strictly decreasing UID. Page sizes are 1 through 1,000; budgets are positive
integers and include the final empty request. Missing mailbox UID validity,
inconsistent pages, identity changes, and exhausted budgets fail explicitly.

Flags can change during iteration: a higher UID that starts matching after its
range has been scanned can be missed, and a lower UID that stops matching can
disappear. Even successful exhaustion is not a snapshot of filter membership.
Neither iterator requests bodies, downloads attachments, or changes flags.
There is no full-text search pagination or grouped-filter pagination implied by
these methods; existing bounded search semantics are unchanged.

## Unread and Flagged Thread Groups

```python
page = api.mail.filtered_threads(inbox, "unread", limit=50)
flagged = api.mail.filtered_threads(inbox, "flagged", limit=50)
for hit in page.results:
    print(hit.id, hit.subject)
```

These methods use the observed browser `thread/search` filters, not guessed
bits in the integer message `flags` field. Select exactly one filter:
`"unread"` sends `unseen: true`, and `"flagged"` sends `flagged: true`.
The return value is a `MailThreadFilterPage` containing `MailThreadDigest`
groups, **not individual messages matching a flag**. Counts are group metadata,
not numbers of unread/flagged messages. Thread IDs are not message IDs.

Limits are 1 through 1,000 (default 50), and `complete` is always `False`, even
for short or empty replies. No additional pages, message bodies, flag writes,
or incremental synchronization are requested. The service checks mailbox UID
validity before and after the request and validates folder context, group
identity, response size, and returned count before exposing a result.

Filters cannot be combined through this API. In the observed browser capture,
Unread plus Flagged returned the union of the separately filtered groups,
not their intersection; arbitrary combinations are not assumed to mean AND.

### Enumerating Filtered Thread Identities

```python
for thread in api.mail.iter_filtered_threads(
    inbox, filter_by="flagged", page_size=50, max_pages=10000
):
    print(thread.id, thread.timestamp)
```

This separate API returns lightweight `MailFilteredThread` identities and the
selected folder's Unix timestamps in milliseconds, **not full digests**, previews, counts, or individual
messages. The `filter-threads` CLI command exposes the same scan. It uses the
observed thread-domain query with an opaque continuation marker and an unchanged
folder-ID/flag predicate on every page, rather than advancing timestamp boundaries.
Filters remain separate; there is no combined-filter or text-search mode.

Only an empty, marker-free response completes enumeration. A short page is not
terminal. A nonempty page without a marker, an empty page with a marker, repeated
markers or identities, oversized or wrong-folder pages, mailbox disappearance or
recreation, malformed responses, and spent request budgets raise errors. No
automatic retries or cursor persistence occur. Page sizes are 1 through 1,000;
budgets include the terminal request. Positive mailbox UID validity is required.

Successful exhaustion means the server's live cursor was exhausted, **not an
atomic snapshot**: concurrent flag changes or new mail can change membership.
Earlier library yields cannot be retracted if a later request fails. The CLI
buffers results until the entire scan succeeds and reports `exhausted: true`
with `snapshot: false`; it prints no partial success data on failure. Stable
folder renames use the same folder ID. No bodies or snippets are requested.

## Searching Selected Folders

```python
folders_by_id = {folder.id: folder for folder in api.mail.folders()}
selected = [folders_by_id["<first-folder-id>"], folders_by_id["<second-folder-id>"]]
result = api.mail.search_folders(
    selected, "example", field="subject", limit_per_folder=50
)
for page in result.pages:
    for hit in page.results:
        print(page.folder.id, hit.id, hit.subject)
```

Select between 1 and 20 unique folders explicitly; no implicit all-folder search
is performed. Each folder receives one ordinary bounded search request, with
an independent limit of 1 through 1,000 (default 50). All arguments are checked
before any searches start. Results are `MailMultiSearchPage.pages`, each a
`MailFolderSearchPage` retaining its folder, thread-group results, and limit.

The same thread ID can appear in several folders and is preserved separately.
Results are ordered within each folder, not globally ranked or deduplicated.
Both the aggregate and its pages always have `complete=False`, including empty
pages. Search counts are not individual-message counts. No bodies, attachments,
or further pages are fetched. A failure in any folder raises rather than
returning a successful partial result. These reads are not an atomic snapshot.

## Private JSON Export

```python
message = api.mail.messages(inbox, limit=50).messages[0]
metadata_only = api.mail.export_message(message)
with_body = api.mail.export_message(message, include_body=True)
```

The library returns a JSON-serializable `MailMessageExport`; it writes no files.
Its `format_version` is 1, `message` contains folder-bound metadata, and `body`
is `None` unless explicitly requested. Body export uses the existing read-only
body method, never fetches attachments, and never renders HTML. A body-fetch
failure raises instead of silently producing a metadata-only export. If there
are no supported text parts, requesting a body also raises as for `body()`.

For a private local file, use `icloud mail export` with an explicit new output
path in an existing directory. It writes UTF-8 JSON with private permissions,
stages it beside the destination, and publishes without overwriting existing
files or symlinks. Failures remove the staging file. Console output reports the
path and optionally the byte count, not exported subjects or body text.

This format is **not raw MIME/EML, an atomic message snapshot, or a mailbox
backup**. Message changes between metadata and body reads remain possible.
Keep exports outside Git: they contain private mail data even without bodies.

## Original MIME/EML Export

```sh
icloud mail export-eml '<message-id>' --folder-id '<folder-id>' --output ./message.eml
```

Unlike JSON export, this downloads the original MIME bytes returned by Apple's
dedicated message-download endpoint. Headers, folding, MIME boundaries, transfer
encodings, line endings, and embedded attachments are not parsed and regenerated.
No additional Python package is needed. It preserves the bytes Apple serves,
not a cryptographic guarantee of exactly what the sender originally transmitted.

The command uses exact message-ID lookup and requires known positive mailbox UID
validity; it has no bounded-scan fallback. It creates a private `0600` staging
file and publishes to the explicitly chosen path only after the stream and final
mailbox checks succeed. Existing files and symlinks are never overwritten, even
if the destination appears during the download. Remote filenames and message
subjects are not used as local paths. Only an HTTP 200 `message/rfc822` response
is accepted; redirects, HTML, JSON, and unspecified binary types are rejected.

Library callers can consume the original byte stream directly:

```python
with api.mail.raw_message(message) as chunks:
    byte_count = sum(len(chunk) for chunk in chunks)
```

Always use the context manager, including when consuming only part of a message.
The response closes on early exit and failure. An observed mailbox recreation or
in-flight path change raises; a stable rename before download uses the refreshed
path. Earlier library yields cannot be retracted after a late failure, so callers
must handle partial files themselves or use the CLI's atomic publication. The
stream is not a MIME validator, and private message bytes are untrusted data:
do not automatically render HTML, execute attachments, or commit exports to Git.
This is a single-message export, not synchronization or an atomic mailbox backup.

## Mail Drop Attachments

```sh
icloud mail get '<message-id>' --folder-id '<folder-id>' --format json
icloud mail download-maildrop '<message-id>' --folder-id '<folder-id>' --part-index 2 --output ./attachment.mov
```

Use the **1-based position in the full message `parts` array**, including body
parts, and select an entry with `is_mail_drop: true`. This is a separate command:
ordinary `download` remains restricted to numeric MIME part IDs. Do not copy a
signed URL or a `cloudpart:` identifier into commands. Capability URLs are excluded
from serialized metadata, and embedded cloud-part IDs are redacted. Raw Python
attributes remain private capability data; do not log them.

The default maximum is 100 MiB; explicitly set `--max-bytes` to authorize a larger
download. Both advertised and streamed lengths are checked. Unknown or zero
metadata sizes do not disable the hard limit. Truncation, inconsistent lengths,
HTTP failures, unexpected MIME types/encoding, expired links, and final mailbox
checks fail without publishing partial files. Files are private (`0600`) and
non-overwriting; remote filenames are never local paths.

The downloader accepts only observed HTTPS `cvws.icloud-content.com/B/` capability
URLs for `com.apple.largeattachment` / `Apple-Webmail`, with known unexpired metadata
and URL expiry. It does not follow redirects, renew links, republish attachments,
or accept generic links from email HTML. Refresh message metadata if a link is
expired; if that does not produce a valid link, retrieval remains unsupported.

Content requests use a new HTTP session with no iCloud cookies, account auth,
session parameters, referrer, netrc credentials, or inherited environment proxies.
TLS verification is mandatory for content downloads, even if account requests
use `--no-verify-ssl`. `REQUESTS_CA_BUNDLE` can select a trusted CA file. This
restricted path may not work on networks requiring an explicit proxy.

```python
with api.mail.mail_drop_attachment(message, 2, max_bytes=104857600) as chunks:
    downloaded_bytes = sum(len(chunk) for chunk in chunks)
```

Use the context manager even for partial reads. Earlier library yields cannot be
retracted after a late length or mailbox failure; callers must manage partial
outputs or use the CLI writer. The verified path is a single direct download,
not range/resume support, an atomic message snapshot, or support for all Apple
content hosts. It performs no upload, send, flag change, or republish operation.

## Attachments

```python
attachment = next(part for part in message.parts if part.is_attachment)
with api.mail.attachment(message, attachment.id) as chunks:
    with open("attachment.bin", "xb") as output:
        for chunk in chunks:
            output.write(chunk)
```

The context manager closes the response on completion, early exit, and errors.
It streams bytes without creating files, following redirects, or retrying failed
downloads. The example uses a deliberately chosen destination and exclusive
creation; never use an untrusted attachment filename directly as a filesystem
path. Partial local output is possible if the stream fails; the caller owns
cleanup of any file it creates.

The ordinary `attachment()` method accepts numeric MIME parts only; selecting
a Mail Drop or other external-link part raises `ValueError` before HTTP. Use
the restricted `mail_drop_attachment()` method described above for supported
Mail Drop parts. Other external-link downloads remain unsupported.
Opaque part identifiers can contain sensitive external URLs. Mail Drop
capability URLs are excluded from serialization, and serialized `cloudpart:`
identifiers become `cloudpart:<redacted>`. Explicit attributes retain the
private values internally; do not follow those URLs automatically.

Metadata size can describe Base64-encoded content, while the stream contains
decoded bytes. Do not decode those bytes again or equate the metadata size with
the downloaded size. Missing optional MIME type metadata defaults to
`application/octet-stream`; the response is then only checked to exclude empty
content types and HTML error pages, since there is no advertised type to match.

## Command-Line Interface

Install the optional CLI dependencies and authenticate using `icloud auth login`.
Shared account, session, proxy, logging, and `--format text|json` options belong on
the final command, just as with the other services.

```console
icloud mail folders --format json
icloud mail list --folder-id '<folder-id>' --limit 50 --format json
icloud mail list --folder-id '<folder-id>' --all --limit 50 --format json
icloud mail get '<message-id>' --folder-id '<folder-id>' --format json
icloud mail get --uid 7 --folder-id '<folder-id>' --format json
icloud mail search example --folder-id '<folder-id>' --field subject --limit 50 --format json
icloud mail thread '<thread-id>' --folder-id '<folder-id>' --limit 50 --format json
icloud mail thread '<thread-id>' --folder-id '<folder-id>' --all --limit 50 --max-pages 10000 --format json
icloud mail filter-messages --folder-id '<folder-id>' --filter unread --page-size 50 --max-pages 10000 --format json
icloud mail filter-messages --folder-id '<folder-id>' --filter flagged --format json
icloud mail preview '<message-id>' --folder-id '<folder-id>' --format json
icloud mail filter --folder-id '<folder-id>' --filter unread --format json
icloud mail filter --folder-id '<folder-id>' --filter flagged --format json
icloud mail filter-threads --folder-id '<folder-id>' --filter flagged --page-size 50 --max-pages 10000 --format json
icloud mail search-folders example --folder-id '<inbox-id>' --folder-id '<sent-id>' --limit-per-folder 50 --format json
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message.json
icloud mail export-eml '<message-id>' --folder-id '<folder-id>' --output ./message.eml
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message-with-body.json --include-body
icloud mail body '<message-id>' --folder-id '<folder-id>' --format json
icloud mail download '<message-id>' --folder-id '<folder-id>' --part-id 2 --output ./attachment.pdf
```

Use exact folder IDs from `mail folders` and message IDs from `mail list`.
JSON listings include MIME parts, attachment part IDs, and `complete: false`.
Search returns **thread-group IDs**, which cannot be passed as message IDs to
`body` or `download`. Pass a thread ID to `mail thread` to retrieve bounded
individual message metadata instead. Its JSON includes `thread_id`, `messages`,
`limit`, and `complete: false`; text output also warns that the result is
incomplete and folder-scoped. All six search fields described above are supported.

`mail list --all` opts into UID enumeration; `--limit` then sets the **page
size**, not the total number of messages. Successful JSON output has `messages`,
`page_size`, and `exhausted: true`, rather than the bounded page's `complete`
field. Output is printed only after enumeration succeeds; a later-page error
does not emit a successful partial JSON result. This CLI mode holds the metadata
in memory; use the library iterator for processing large mailboxes incrementally.
It does not change the exact lookup default or explicit bounded fallback used
by `body` and `download`.

`mail thread --all` explicitly pages thread members in one folder; `--limit`
becomes the page size, while omitting `--all` keeps bounded thread expansion.
`mail filter-messages` always enumerates individual unread or flagged messages;
it uses `--page-size`, not a total limit. Both accept `--max-pages` (default
10,000). Successful JSON includes `messages`, `page_size`, `exhausted: true`,
and either `thread_id` or `filter_by`, without a `complete` claim. Text output
warns about concurrent changes. Both commands buffer metadata until the scan
succeeds, so a later error prints no successful partial result. Use the library
iterators for incremental processing without buffering the entire scan.

`list` and `search` default to a limit of 50, with a maximum of 1,000. `body`,
`download`, `preview`, and `export` use exact folder-scoped message-ID lookup by default, including for
older messages. A missing match is an error, not an invitation to scan a mailbox.
For compatibility, supplying `--limit N` explicitly retains the earlier bounded
lookup within the newest N folder messages (1 through 1,000). Omitting that flag
uses exact lookup instead. Search IDs still identify threads, not messages.
Exact lookup also retrieves older message IDs returned by `mail thread`; bounded
lookups only apply when `--limit` is supplied explicitly. The library's returned
`MailMessage` objects can be used for explicit body retrieval without another
CLI lookup. Export writes the explicitly selected message, not the whole listing.
`get` is always exact: it accepts one message ID or `--uid`, requires known UID
validity, never scans, and never retrieves bodies, previews, or attachments.

Body output is escaped JSON data even in text mode: HTML is never rendered and
remote content is never loaded. Folder and message tables display remote text
literally rather than interpreting terminal controls or Rich markup. The same
read-only body request is used as in the library API.

Downloads require an explicit `--output` file in an existing directory. A remote
filename is never used as a local path. Bytes are staged in a private temporary
file beside the destination, then published without overwriting existing files
or symlinks, even if they appear during the download. Failed streams remove their
temporary bytes and leave the destination untouched. Publishing requires a
filesystem that supports hard links; unsupported filesystems produce an error
rather than falling back to an overwriting copy. Ordinary `download` rejects
Mail Drop and other external-link parts; use `download-maildrop` for the
explicitly supported Mail Drop path. Other external-link downloads remain
unsupported. Downloaded files are private email data; keep them outside
repositories.

Attachment reads guard known mailbox namespaces before the request, before
yielding bytes, and when the stream context exits normally. A failed final
check prevents the CLI from publishing its staged file. Library callers may
already have consumed bytes before a later failure; those bytes cannot be
retracted. A stable rename uses the current folder path. As with bodies, legacy
metadata without UID validity cannot provide namespace checks, and no stream
is an atomic snapshot of concurrent mailbox changes.

## Errors and Privacy

`MailError`, a `PyiCloudException`, reports malformed Mail responses, unexpected
attachment content types, and stream failures. Read-side authentication and
connection exceptions retain their existing library types. Submitted Mail writes
use the rejected/uncertain errors described above. There are no
automatic service-level retries; a caller may inspect chained causes.

Bodies, subjects, addresses, previews, and filenames are omitted from the models'
default representations where practical, but remain accessible through explicit
attributes and serialization. Treat them as private mail data. Raw captures and
authentication cookies must never be committed to a repository.
