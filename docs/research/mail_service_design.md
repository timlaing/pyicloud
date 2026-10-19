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

The Sent Messages capture demonstrates a two-stage listing strategy:

1. Request detailed metadata with a limit of 50.
2. Request up to 1,000 lightweight records with a date-cutoff predicate.
3. Fetch detailed metadata by selected UID batches as the browser scrolls.

The first two responses contain 50 and 307 records, with one overlapping
identifier: 356 unique messages. Subsequent metadata requests cover all 356.
Deduplication is required even in this successful example.

Other query responses include `continuationMarker`, but no captured request
submits that marker. A complete continuation handshake is not established.
The date-cutoff example does not prove safe enumeration beyond the 1,000-record
limit or across equal timestamps. Do not implement an unbounded iterator that
silently drops boundary records or falsely claims complete results. A first
release should expose bounded results and explicit completeness limitations
until a reliable continuation strategy is verified.

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
