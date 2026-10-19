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

## Loading More Messages

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
