# Mail

Mail support is **experimental and read-only**, using the iCloud web interface's
undocumented HTTP API. It has been verified on one account. Sending, drafts,
folder changes, flag updates, moving/deleting messages, and incremental synchronization
are not supported. The read-only `icloud mail` commands expose the same bounded API.
The existing `icloud doctor --probe` command checks Mail by listing its folders.

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
| Unread/flagged thread groups       | `filter`                   | Bounded browser groups; grouped-filter pagination is not established.                                         |
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
rather than falling back to an overwriting copy. Mail Drop and external-link
downloads remain unsupported. Downloaded files are private email data; keep them
outside repositories.

Attachment reads guard known mailbox namespaces before the request, before
yielding bytes, and when the stream context exits normally. A failed final
check prevents the CLI from publishing its staged file. Library callers may
already have consumed bytes before a later failure; those bytes cannot be
retracted. A stable rename uses the current folder path. As with bodies, legacy
metadata without UID validity cannot provide namespace checks, and no stream
is an atomic snapshot of concurrent mailbox changes.

## Errors and Privacy

`MailError`, a `PyiCloudException`, reports malformed Mail responses, unexpected
attachment content types, and stream failures. Session authentication and
connection exceptions retain their existing library types. There are no
automatic service-level retries; a caller may inspect chained causes.

Bodies, subjects, addresses, previews, and filenames are omitted from the models'
default representations where practical, but remain accessible through explicit
attributes and serialization. Treat them as private mail data. Raw captures and
authentication cookies must never be committed to a repository.
