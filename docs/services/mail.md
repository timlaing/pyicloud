# Mail

Mail support is **experimental and read-only**, using the iCloud web interface's
undocumented HTTP API. It has been verified on one account. Sending, drafts,
folder changes, flag updates, moving/deleting messages, and incremental synchronization
are not supported. The read-only `icloud mail` commands expose the same bounded API.
The existing `icloud doctor --probe` command checks Mail by listing its folders.

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
icloud mail thread '<thread-id>' --folder-id '<folder-id>' --all --limit 50 --max-pages 10000 --format json
icloud mail filter-messages --folder-id '<folder-id>' --filter unread --page-size 50 --max-pages 10000 --format json
icloud mail filter-messages --folder-id '<folder-id>' --filter flagged --format json
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

`mail thread --all` explicitly pages thread members in one folder; `--limit`
becomes the page size, while omitting `--all` keeps bounded thread expansion.
`mail filter-messages` always enumerates individual unread or flagged messages;
it uses `--page-size`, not a total limit. Both accept `--max-pages` (default
10,000). Successful JSON includes `messages`, `page_size`, `exhausted: true`,
and either `thread_id` or `filter_by`, without a `complete` claim. Text output
warns about concurrent changes. Both commands buffer metadata until the scan
succeeds, so a later error prints no successful partial result. Use the library
iterators for incremental processing without buffering the entire scan.

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
attachment content types, and stream failures. Session authentication and
connection exceptions retain their existing library types. There are no
automatic service-level retries; a caller may inspect chained causes.

Bodies, subjects, addresses, previews, and filenames are omitted from the models'
default representations where practical, but remain accessible through explicit
attributes and serialization. Treat them as private mail data. Raw captures and
authentication cookies must never be committed to a repository.
