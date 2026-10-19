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

**Listing and search are bounded snapshots, not complete enumerations.** Limits
must be integers from 1 through 1,000. Results have `complete=False` even when
fewer records were returned: continuation and completeness are not established
for every mailbox. No hidden requests fetch additional pages. This API is not
suitable for a complete-mailbox backup.

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
icloud mail search example --folder-id '<folder-id>' --field subject --limit 50 --format json
icloud mail preview '<message-id>' --folder-id '<folder-id>' --format json
icloud mail filter --folder-id '<folder-id>' --filter unread --format json
icloud mail filter --folder-id '<folder-id>' --filter flagged --format json
icloud mail search-folders example --folder-id '<inbox-id>' --folder-id '<sent-id>' --limit-per-folder 50 --format json
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message.json
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message-with-body.json --include-body
icloud mail body '<message-id>' --folder-id '<folder-id>' --limit 50 --format json
icloud mail download '<message-id>' --folder-id '<folder-id>' --part-id 2 --output ./attachment.pdf
```

Use exact folder IDs from `mail folders` and message IDs from `mail list`.
JSON listings include MIME parts, attachment part IDs, and `complete: false`.
Search returns **thread-group IDs**, which cannot be passed as message IDs to
`body` or `download`. All six search fields described above are supported.

`list` and `search` default to a limit of 50, with a maximum of 1,000. `body` and
`download` first look for the requested message within the newest `--limit`
messages in that folder (also 50 by default). A missing match produces an error
instead of silently scanning the whole mailbox. Increase the explicit limit if
needed; there is still no automatic paging or completeness guarantee. `export` and `preview`
use the same bounded message lookup and `--limit` option as `body` and
`download`. Export writes the explicitly selected message, not the whole listing.

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
