# Mail

Mail support is **experimental**, using the iCloud web interface's undocumented
HTTP API. Read operations have been verified on one account. Explicit
single-message read/unread, flag/unflag, and ordinary mailbox moves are available
separately, alongside explicit folder management and recipient-free draft saves;
ordinary reads never change flags. Sending, recipient/attachment draft editing,
permanent message deletion, and incremental synchronization are not supported.
The `icloud mail` commands expose the same bounded API.
The existing `icloud doctor --probe` command checks Mail by listing its folders.

## Recipient-Free Drafts

Draft creation and full replacement editing currently support **no recipients and
no attachments**. Select an enabled address with `api.mail.senders()`; the login
address is not a substitute:

```python
draft = api.mail.create_draft(
    sender=chosen_address, subject="Working copy", text="First version", html=""
)
draft = api.mail.edit_draft(
    draft, subject="Revised working copy", text="Revised version", html=""
)
```

Every successful save returns a **new `MailDraft` handle**, containing fresh
`message` metadata and a verified `sender`. Apple rotates the folder-local UID
on save; the old UID/message ID must not be reused. Editing replaces all three
subject/plain-text/HTML fields, so every replacement argument is required.
Apple treats HTML as the canonical body: plain-only input is converted to safely
escaped HTML, preserving line breaks, so empty HTML cannot silently discard it.
Caller-supplied HTML is not sanitized or rendered. The two inputs do not guarantee
independently preserved MIME alternatives. These methods do not send
mail, upload attachments, enable addresses, or use Hide My Email.

Before editing, fresh UID, message ID, Drafts namespace, sender, body identity,
headers, and attachment metadata are checked. Existing recipient headers,
attachments, unknown attachment status, stale handles, and unsupported body
metadata fail before dispatch instead of silently erasing data. These checks
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
Drafts/Trash metadata, do not retry automatically. Sending, nonempty-recipient
drafts, attachment editing, and permanent message destruction remain separate
work, not supported by these methods yet.

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
recipient/attachment editing guarantee.

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
validity. These checks protect the lookup, not every later operation: a message
may still be moved or deleted after it is returned.

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
meanings. The browser's canonical server names `Junk`, `Drafts`, `/VIP`, and
`/REMIND_ME` are rejected, independently of translated display labels. Junk and
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

An exact failure without successes raises `MailWriteRejected`. Timeouts,
malformed acknowledgements, wrong accounts or IDs, late response failures, or
contradictory success/failure entries raise `MailWriteUncertain`. The latter
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

`MailWriteRejected` means the server explicitly rejected this one update.
`MailWriteUncertain` means submission could not be confirmed, including timeouts,
non-200 responses, mismatched results, or late response failures. **The write may
already have taken effect. Refresh metadata before deciding whether to retry.**
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
state is retained. Preview strings are untrusted data: the CLI prints escaped
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
complete conversation. There is no automatic paging or cross-folder expansion.

Use a folder from `folders()` with positive `uid_validity`. The service checks
mailbox identity before and after the metadata request and raises `MailError`
if the folder disappears or is recreated, or if returned identities, folder,
thread, or response size violate the request. These checks do not make the
result atomic against concurrent message changes.

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

Mail Drop and other external-link attachments can appear in metadata, but their
downloads are not supported: selecting one raises `ValueError` before HTTP.
Opaque part identifiers can contain sensitive external URLs; they are omitted
from default representations but remain available through explicit attributes
and serialization. Do not follow those URLs automatically.

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
icloud mail search example --folder-id '<folder-id>' --field subject --limit 50 --format json
icloud mail thread '<thread-id>' --folder-id '<folder-id>' --limit 50 --format json
icloud mail preview '<message-id>' --folder-id '<folder-id>' --format json
icloud mail filter --folder-id '<folder-id>' --filter unread --format json
icloud mail filter --folder-id '<folder-id>' --filter flagged --format json
icloud mail search-folders example --folder-id '<inbox-id>' --folder-id '<sent-id>' --limit-per-folder 50 --format json
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message.json
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

`list` and `search` default to a limit of 50, with a maximum of 1,000. `body` and
`download` use exact folder-scoped message-ID lookup by default, including for
older messages. A missing match is an error, not an invitation to scan a mailbox.
For compatibility, supplying `--limit N` explicitly retains the earlier bounded
lookup within the newest N folder messages (1 through 1,000). Omitting that flag
uses exact lookup instead. Search IDs still identify threads, not messages.
Exact lookup also retrieves older message IDs returned by `mail thread`; bounded
lookups only apply when `--limit` is supplied explicitly. The library's returned
`MailMessage` objects can be used for explicit body retrieval without another
CLI lookup. `export` and `preview` instead default to a bounded lookup of the
newest 50 messages and accept the same explicit `--limit` option. Export writes
the explicitly selected message, not the whole listing.

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
rather than falling back to an overwriting copy. Mail Drop and external-link
downloads remain unsupported. Downloaded files are private email data; keep them
outside repositories.

## Errors and Privacy

`MailError`, a `PyiCloudException`, reports malformed Mail responses, unexpected
attachment content types, and stream failures. Read-side authentication and
connection exceptions retain their existing library types. Submitted flag writes
instead use the rejected/uncertain errors described above. There are no
automatic service-level retries; a caller may inspect chained causes.

Bodies, subjects, addresses, previews, and filenames are omitted from the models'
default representations where practical, but remain accessible through explicit
attributes and serialization. Treat them as private mail data. Raw captures and
authentication cookies must never be committed to a repository.
