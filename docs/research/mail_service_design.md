# Mail Service Investigation

<!-- cspell:words geqs mailws mccgateway messagepart savedraft thrid -->

## Status and Evidence

Reviewed on 2026-10-17 against upstream `ace0096`. This branch implements the
bounded, read-only first release described below. This document records protocol
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

## Mail Drop Download Verification

User-authorized metadata captures for a disposable MOV test message established
`mailDropStreamingUrl`, `mailDropHeaderName`, millisecond `expirationDate`, and an
opaque `cloudpart:` ID containing the signed URL. Metadata also included the
ordinary filename, `video/quicktime` type, and the actual positive file size.
The capability URL expiration agreed with the metadata to within one second.
Refreshed metadata changed signature parameters but preserved the resource path.

The browser made a direct GET to `cvws.icloud-content.com/B/...`, with no Cookie
or Authorization header. The captured response was HTTP 200 `video/quicktime`,
with a matching content length and byte-range advertisement. Range/resume support
is not inferred from that advertisement and is not implemented here.

A live read-only CLI test retrieved the same resource using fresh owned-session
metadata and an independently created content session. The actual streamed byte
count matched metadata and the browser capture, the output was private `0600`,
selected message flags were unchanged, and the temporary file was removed.
No captured credentials were replayed and no uploads, sends, flag writes, token
creation, or republish endpoints were called.

The implementation deliberately supports only the verified HTTPS host, path,
container/zone parameters, expirations, and direct-response behavior. It enforces
a hard byte budget, rejects redirects, isolates cookies/auth/netrc/proxy state,
verifies TLS, and checks mailbox scope around content retrieval. It does not
implement arbitrary external URLs, expired-link renewal, or a generic Mail Drop
write client. The metadata HAR stays outside Git; the committed fixture is
synthetic and contains no usable bearer links. Normal serialization redacts
capability-bearing cloud-part IDs and excludes streaming URLs.

## Original MIME Download

The previously captured browser JavaScript contains an EML URL builder targeting
`mccgateway` at `GET mailws2/v1/message/download`, with `guid`, `dsid`, and
`filename` query parameters. The browser prefixes its folder-local message GUID
with `message:`. A controlled read-only request using the existing discovered
gateway and authenticated session verified `message:<folder-name>/<uid>`; no
browser cookies, captured credentials, or mutation endpoints were replayed.

Apple returned HTTP 200 with `Content-Type: message/rfc822`. Python's standard
`email.parser.BytesParser` parsed the in-memory response without MIME defects;
the subject matched independently fetched metadata, and the decoded PDF attachment
matched the ordinary attachment download byte-for-byte. The observed mailbox
flags were unchanged. No private response bytes, headers, filenames, IDs, hashes,
or account identifiers were saved in this research document or fixtures.

An additional live check selected an existing unread message through the server's
`SEEN: false` predicate, consumed its raw stream in memory, and repeated that
independent unread query. The selected message remained unread and the complete
observed unread-ID set was unchanged. A live CLI export of a separate multipart
message also matched a second library stream byte-for-byte, used private `0600`
permissions, and left no temporary output after cleanup. These checks requested
no flag writes or other Mail mutations.

This establishes a dedicated original-message response on one account, not
byte identity with the sender's original wire transmission. The implementation
streams Apple's returned message bytes unchanged instead of rebuilding MIME from
the existing JSON body/header API. It requires a known mailbox UID namespace,
checks it around retrieval, ignores remote filenames, rejects redirects and
non-RFC822 media types, and uses the existing private atomic CLI file writer.
No new dependency or MIME regeneration is needed. Other-account and unusual MIME
coverage remains a compatibility consideration; no server-side snapshot or
incremental-sync guarantee follows from this endpoint.

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

### Private Change-Decoding Candidate (2026-10-20)

Two bounded, read-only `event/list` calls were made using fresh authentication.
The initial request sent null message and thread counters; its successful
response included integer counters and 25 event strings. Replaying those returned
counters produced an empty event list with unchanged counters. A second bounded
probe confirmed these event shapes, with private values omitted:

- `* <count> EXISTS`: a count notification, not a UID identifying a new message.
- `* <sequence> FETCH (UID <uid> MODSEQ (<counter>) FLAGS (<flags>))`.
- `* VANISHED <uid-range>`: removed message IDs, independent of sequence numbers.
- `* THREADUPDATE (<thread-id> <timestamp> <counter> FLAGS (<flags>) NEW)`;
  the trailing `NEW` was absent on some updates.

The private `_sync.py` candidate decodes these distinct event types and validates
request/response checkpoint scope and counter bounds. Synthetic fixtures and
offline tests also exercise empty flags, `NIL` thread timestamps, `EARLIER`,
multiple/reversed UID ranges, invalid input, and resource limits. Those additional
variants are defensive candidate behavior, not claims of live verification.
Unknown event shapes fail explicitly; no event is silently discarded. UID ranges
remain compact even when they span billions of values. Errors do not echo raw
events, and decoding performs no network or filesystem operations.

This candidate is **not integrated into the public service or CLI**, and it does
not persist or automatically advance checkpoints. Its checkpoint is not proof
that a complete baseline inventory exists. Even a validated batch is not proof
of historical completeness or a mailbox snapshot. Account identity, mailbox ID,
path, and UID validity must come from verified caller context, and each response
must be paired with the exact request that produced it.

Remaining integration gates are a baseline/change-cursor handoff without a lost
update window, metadata reconciliation for count notifications, transactional
local application and checkpoint persistence, restart recovery, actual flag and
removal transitions, folder moves/recreation, and expired-counter/reset responses
such as `MODSEQ_TOO_OLD`. Push-event merging is not implemented. Do not replace
these checks with a highest-UID scan or infer a move from one folder's tombstone.
No synchronization completeness claim or Mail write operation follows from these
probes. The user's first delayed Inbox appearance after switching folders was
not correlated with a captured background notification.

The implemented decoder subsequently accepted all 25 live bootstrap events and
the empty follow-up poll. Logs retained only event counts, event kinds, and
redacted structural templates; no raw event values were saved as fixtures.
This verifies decoding against a real response, not the initial inventory
handoff, a newly arriving message, or successful recovery from a missed batch.

### New-Message Push Capture (2026-10-20)

A subsequent user-created disposable email appeared in Inbox without navigating
or refreshing. The approved, four-flow HAR contains one WebSocket connection and
three successful `thread/list` responses. Its binary WebSocket frames include
embedded JSON: a Mail change payload under `mboxEvents.INBOX`, followed by a
separate notification payload under `mail`. This is capture analysis, not a
production implementation of Apple's binary push framing.

The change payload has `prevModseq`, `modseq`, `prevThreadModseq`,
`threadModseq`, and two event strings. Both counters advance. Crucially, the
new-message event is `* <count> EXISTS (UID <uid>)`, followed by a new thread
update. The UID-bearing event must remain distinct from a count-only `EXISTS`
hint and from `FETCH` flag updates. It contains no individual message counter or
complete metadata. The candidate now decodes it as a separate arrival event and
rejects a batch announcing an arrival without an advancing message counter.

The later notification's thread ID matches the thread containing the exact test
subject in all three `thread/list` responses. Their ending message/thread counters
match the change push, and their event arrays are empty. The first of these
requests begins about 1.5 seconds after the change frame. No `event/list` request
is present in this browser capture. This establishes the captured push-to-thread
retrieval sequence, not a requirement to implement a persistent WebSocket client
instead of read-only polling.

The private decoder was checked against the actual two-event payload with its
advertised counter range, then a synthetic regression fixture was added. No raw
binary frames, push tokens, account data, real IDs, alert text, or message metadata
were copied into tracked files. This capture verifies one arrival path; flag
transitions, moves, deletions, reset handling, and the initial inventory handoff
remain separate integration gates.

### Private Inventory and State Candidate

`_sync_inventory.py` introduces an offline-tested initial-inventory algorithm
against a read-only source protocol, not a public synchronization API. A complete,
unfiltered enumeration must be bracketed by identical account/mailbox identity,
path, UID validity, and message/thread counters. A changing observation discards
the attempt and restarts within an explicit attempt budget. Incomplete reads,
duplicate identities, and message-budget exhaustion raise rather than publishing
a partial baseline. Account/mailbox identity stays pinned across retries. Folder
recreation or rename requires a fresh stable scan, not merging old and new UIDs.

This quiescent-window algorithm deliberately favors explicit failure on a busy
mailbox over claiming a complete inventory. It depends on a future verified source
whose counters cover every relevant change and whose iterator truly exhausts the
mailbox. Counter stability alone is not proof that Apple's server reads form an
atomic snapshot. A mutation after the final observation belongs to a later poll;
its checkpoint must not be silently skipped. No public synchronization API is
exposed while handoff, expiry, and server consistency assumptions remain unverified.

`_sync_state.py` stores only folder-local UIDs, message/thread IDs, numeric flags,
and the scoped checkpoint. Inventory replacement and checkpoint advancement share
one SQLite transaction. Exceptions during enumeration/SQL execution or commit
roll back both. A local monotonic revision and transaction-locked compare-and-swap
reject competing writers even when their remote counters are equal. A coherent
snapshot reader keeps records and checkpoint in the same transaction. Unsupported
schema versions and structurally invalid checkpoints/records fail explicitly
rather than appearing to be a fresh target. Same-namespace counter regression
and account/mailbox switches are rejected; intentional baseline replacement can
recover a new UID namespace.

The private backend accepts a dedicated, caller-owned SQLite connection. The
separate private storage opener described below provides file permissions and
connection lifetime management; the backend itself neither selects filesystem
paths nor encrypts databases. Tests use real in-memory SQLite and private sandbox files
to exercise restarts, two connections, injected commit failures, stale writers,
and rollback after partial insertion. Message bodies, headers, credentials, and
signed attachment links are not persistence fields. The private reader and
reconciler below use this backend; implicit remote reset recovery and a public
synchronization API remain absent.

### Private File Storage and Explicit Expiry Recovery

`_sync_storage.py` supplies a context-managed opener for an explicitly selected,
dedicated POSIX directory. The parent must already exist and is canonicalized;
its ancestors must be owned by the user or root and not writable by other users,
except for root-owned sticky temporary directories. The final directory must be
owned by the user with mode `0700`. Fixed database, journal, and lock names must
be regular, owner-only `0600` files, with one hard link and no symlinks. Unsafe
existing permissions, unknown directory entries, foreign databases, and corrupt
databases are rejected rather than repaired, removed, or silently initialized.
No process-wide umask is changed. There is no default state path or migration of
older experimental databases without the inventory application identifier.

A non-blocking advisory lock covers the complete session and is released on
connection or caller failure. SQLite has a finite configurable lock timeout
(default five seconds, maximum sixty), trusted schema execution disabled,
in-memory temporary storage, and rollback-journal mode. Crash recovery uses
SQLite's existing journal; it does not delete journal files first. The directory
and database modes protect its sidecars too. Tests check a real worker crash,
durable restart, rollback, lock contention, and unsafe path/file types. Same-user
malicious processes, ACL overrides, network-filesystem locking semantics, and
encryption at rest are outside this private POSIX-only boundary. A public CLI
still requires a deliberate supported-platform policy.

`_sync_refresh.py` establishes a new inventory or applies one incremental batch.
Only Apple's explicit `MODSEQ_TOO_OLD` becomes `SyncHistoryExpired`. By default
it propagates without a baseline read. An explicit `rebuild_expired=True` permits
a bounded stable full replacement pinned to the original local revision and
account/mailbox. Records and checkpoint are replaced together only after the
whole scan succeeds. Incomplete scans, moving counters, budget exhaustion, or a
competing local writer retain the previous state. Unknown events, malformed
responses, authorization/transport failures, corruption, and other reset reasons
never enter this recovery path. The separate count-hint inventory fallback
remains opt-in too. These helpers remain private; the guarded CLI below delegates
to them without exposing a new `MailService` synchronization API.

### Private Live Reader and Incremental Reconciliation

`_sync_reader.py` binds the source protocol to the existing authenticated Mail
service. Account name and DSID form a hashed scope identifier; each read checks
that identity and the discovered mailbox ID, path, and UID validity. Baseline
observations send null counters, but delta polls replay exactly their supplied
checkpoint. Both request types use the captured `event/list` protocol with
bounded timeouts and no redirects. Unsupported envelopes, missing/null counters,
changed feature flags, unknown events, and HTTP failures do not advance state.
`MODSEQ_TOO_OLD` raises an explicit reset requirement and never retries with null
counters. Authentication and connection exceptions retain their types. Returned
responses, including exceptional API/auth responses, are closed.

Complete inventories use `iter_messages()`, changed threads use
`iter_thread_messages()`, and exact refreshes use `message_by_uid()`. These are
read-only metadata operations, not body reads or bounded newest-N lookups. Numeric
flags come from authoritative metadata rather than guessing integer bits from
the event strings. Missing numeric flags are rejected as incomplete metadata.
Plain count-only notifications cannot identify a new message on their own.
By default they require an explicit inventory; a caller can opt into
`allow_inventory_fallback=True` for one bounded, stable full scan. This path pins
the original local revision too, and never triggers for expired/unknown history.

`_sync_apply.py` reads a coherent local snapshot and validates the polled stride
again. Changed threads are fully enumerated. Old members missing from a thread
are checked by exact UID, since a thread reassignment is not a mailbox deletion.
UID-addressed arrivals and flag updates are refreshed once. Compact, merged
`VANISHED` ranges remove only local folder membership; a returned live record
inside a removed range is treated as inconsistent. Duplicate identities, reused
UIDs, incomplete iterators, and disagreement between complete-thread and exact
lookups abort without publishing partial state.

Message and logical lookup budgets are explicit; per-thread HTTP paging also uses
the reader's configured page budget. A final fresh observation must equal the
batch's ending checkpoint, or all staged metadata is discarded. Stable results
reuse the backend's atomic inventory/checkpoint replacement and revision CAS.
No-op polls instead confirm the existing revision transactionally without
rewriting records. Local storage currently replaces the validated manifest as a
whole; this is incremental remote reading/reconciliation, not an optimized SQL
row-diff writer. Another writer's newer revision is never overwritten.

These operations remain private and metadata-only. They do not implement push
subscriptions, binary push framing, bodies/attachments synchronization, automatic
expired-history rebuilding, cross-folder move correlation, or a public Python sync
API. Synthetic tests cover change application and failure paths; live
verification is recorded separately below. Server history retention, large event
bursts without silent truncation, index consistency, and genuine live
flag/move/deletion transitions remain important integration gates.

#### Guarded Metadata Sync CLI

The experimental `mail sync` command now connects these private helpers to an
explicit folder ID and private state directory on POSIX. It runs once and prints
only a versioned summary after closing storage, without bodies, tokens, or event
text. Local validation/locking precedes authentication. It defaults to failing
on expired history and count-only notifications; independent `--rebuild-expired`
and `--allow-inventory-fallback` switches permit bounded stable inventories for
only those respective conditions. Count-hint scans also pin the observed batch's
namespace and minimum counters, not just the saved local revision. Read timeout
errors receive a safe connectivity hint rather than echoing possible signed URLs.
No live mutation tests, push subscription, public library sync API, or completeness
guarantee is implied by exposing this experimental metadata-only command.

#### Bounded Live Reader Verification (2026-10-20)

The private reader established a stable ten-message Inbox inventory and then
completed an unchanged incremental poll without rewriting local records/revision.
A poll from the approved arrival capture's earlier counters encountered a plain
count notification and was rejected in default mode, as designed. With the
explicit full-inventory fallback enabled, the replay restored the captured arrival
and matched a separate fresh, complete inventory at the same ending checkpoint.

A second replay fed the actual captured two-event push batch to the reconciler,
while retaining live exact/thread metadata reads and the real final checkpoint
observation. This ran only because the captured ending counters still equaled
the current mailbox's counters; the test did not relabel old events as current.
The incrementally reconciled minimal inventory also matched the independent scan.
Both replays seeded disposable in-memory state from current metadata with the
captured arrival omitted. That is a targeted recovery example, **not** a recovered
historical mailbox snapshot or a newly observed live delivery during the run.

No body reads, attachment downloads, flag writes, moves, deletions, or sends were
performed. Logs retained only counts, status, and generic safety reasons. No
private capture contents were added to tracked fixtures. Two direct connection
attempts timed out before reaching the sync reader; the successful checks used
the already enabled local Proxyman proxy with TLS verification and the existing
trusted CA bundle, without changing system settings. These small-mailbox checks
do not verify large event bursts, history expiry, actual live removal/flag/move
transitions, or unattended recovery behavior.

Mutation responses expose `notCreated`, `notUpdated`, and `notDestroyed` fields.
No populated failure maps or HTTP failure examples were captured. Future writers
must validate operation outcomes, distinguish uncertain submission outcomes,
and avoid automatic retries of non-idempotent operations. These observations
do not authorize implementing or exercising writers in the read-only phase.

### Live Flag, Move, and Recoverable Deletion Verification (2026-10-21)

A controlled test used one uniquely identified disposable development email and
three existing folders: Inbox (ten messages), Testing (empty), and Trash (empty).
Initial and subsequent scoped inventories were compared with separately scanned,
stable-counter inventories. The mounted CLI ran against a reused authenticated
service with real HTTP responses, not mocked data. A Python-side route allowlist
restricted requests to metadata queries and change polling. Mail mutations were
performed separately through the iCloud Mail browser UI. Verified TLS through
the existing local proxy was retained; no system proxy/trust setting changed.

Preflight uncovered a previously unsupported event form:
`* THREADUPDATE (<thread-id> NIL <counter>)`, without `FLAGS`. This occurred in
existing removal history even for empty folders. It now decodes to a distinct
thread-invalidation event, not an empty replacement flag set or proof of global
deletion. The reconciler refreshes the complete folder-local thread and checks
missing old members by exact UID. Synthetic fixtures and tests cover this shape,
malformed variants, out-of-stride counters, absence, and thread reassignment.

The following live transitions matched fresh independent inventories:

| UI action                    | Inbox | Testing | Trash | Read-side behavior                                     |
| ---------------------------- | ----- | ------- | ----- | ------------------------------------------------------ |
| Mark read, flag, then unflag | 10    | 0       | 0     | Each flag update applied incrementally, without rescan |
| Move from Inbox to Testing   | 9     | 1       | 0     | Incremental source removal; destination count fallback |
| Return from Testing to Inbox | 10    | 0       | 0     | Incremental source removal; destination count fallback |
| Move from Inbox to Trash     | 9     | 0       | 1     | Incremental source removal; destination count fallback |
| Restore from Trash to Inbox  | 10    | 0       | 0     | Incremental source removal; destination count fallback |

Destination arrival polls supplied count-only notifications, so default sync
refused to advance those inventories. The explicitly enabled, bounded stable
inventory fallback then produced matching results. These tests do **not** prove
that every move/arrival can be applied without a full scan. The disposable
message's original numeric flags were restored and checked against the saved
pre-test value. The initial browser context-menu return move did not complete;
fresh listings detected that failure, and explicit selection plus the main Move
toolbar completed the return. A browser auto-selection temporarily marked the
next development email read during the Trash action. Its unread state was
restored, and common non-target records matched their pre-action inventories.

The test covered recoverable removal to Trash and restoration, **not permanent
deletion or emptying Trash**. No mail was sent; the Python client performed no
Mail mutations or content downloads. Private temporary state and sanitized local
reports remain outside Git. This small, single-account verification does not
establish cross-folder identity correlation, server history completeness, large
event-burst safety, or exhaustive full-text search pagination.

## Proposed First Release and Verification

The initial scope is endpoint discovery, folder listing, bounded message
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
