# Remaining MCP write-contract evidence

## User-approved outcome semantics

The user explicitly approved "acknowledged, final state not verified" for remaining
existing API/CLI writers. Missing final-state correlation is therefore not itself
an implementation blocker: adapters may return `acknowledged: true` with
`final_state_verified: false` only after validating the existing operation's exact
acknowledgement. They must not invent identities, return verified success, mint
unverified destination handles, retry ambiguous writes or weaken preflight/approval
and transport safeguards. Stronger existing verification remains unchanged.

Mail flags/moves now implement these semantics using typed facade acknowledgements,
metadata/namespace-bound receipts, one dispatch and pre-dispatch source retirement.
The evidence gaps listed below describe stronger optional verification, not required
final-state proof for these acknowledged-only operations.

This is an implementation acceptance checklist, not permission for account access
or live writes. Unqualified operations remain disabled. Synthetic fixtures can test
an established contract; invented success envelopes cannot establish one.

MCP write scope is limited to existing API/CLI operations. Contacts writes, Notes
writes and Calendar updates are excluded, not completion requirements. Their notes
below are background only; do not add library writers to satisfy MCP.

`pyicloud/mcp/upload_transport.py` now provides an internal path-free bounded-byte
transfer foundation: exact trusted HTTPS hosts, no URL credentials/redirects,
isolated credential-free sessions with environment inheritance disabled, raw service
registration with no authentication replay, bounded streamed JSON and response cleanup.
Transfer hosts require independently reviewed operator startup policy; never derive
the allowlist from an untrusted reservation response. Isolated transfers now inherit
a bounded no-retry adapter and the upload operation deadline.

`photo_upload_bytes.py` now runs the existing typed Photos reservation, raw byte
transfer and registration sequence without filesystem paths. It requires a private
PrimarySync zone, exact staged digest/size/expiry, explicit modification time and
validated account identity. It checks reservation UUID, receipt size/fields and one
typed registration with asset/master identities; 409 duplicates preserve the existing
identity semantics. UTC metadata is explicit. Failures never retry or silently clean
up potentially stored bytes. It returns registration only, not verified upload success.
The byte pipeline alone grants no approval or verified success; no upload tool is
exposed by the byte pipeline alone. Adapter qualification is described below.

`photo_uploads.py` now implements the internal elevated adapter: exact staged-byte
metadata/timestamp, private PrimarySync library revision, account and both service
routes plus independently selected transfer hosts are bound to approval. Stage bytes
are consumed before dispatch, and independent private-library asset lookup must match
both registration identities with a real change tag. Missing indexing is uncertain,
not a retry instruction. Source byte staging and request replay are unavailable after
dispatch. Existing owner transport refuses PCS consent/device negotiation requests;
library reads failing authentication cannot produce a preview. Runtime configuration,
staging/gateway/schema and installed upload-specific qualification now exercise one
independently indexed fake upload and one uncertain registration without indexed proof, exact
dispatch counts, elevated approval and stage/request retirement with replay rejection.
No live upload is claimed.

## Drive upload

Local evidence: `pyicloud/services/drive.py` reserves a document ID, transfers to a
returned URL, then commits with `create_short_guid: true` and `allow_conflict: true`.
`tests/services/test_drive_upload_params.py` uses only `{"status": "OK"}` as the
commit response. It establishes operation-local parameters, not final identity.

Required protocol evidence:

- A redacted reservation, transfer receipt and commit response from the same upload.
- Independent exact-node lookup and parent listing after that commit, preserving
  the relationship between allocation ID, final ID, zone, parent and revision.
- Collision behaviour: whether commit can rename or overwrite despite preflight.
- Empty-file behaviour and explicit rejection/ambiguous commit responses.

Required implementation work after identity is established: bounded immutable bytes,
private destination binding, approved transfer-host policy, no redirects or mutation
retries, no credential forwarding to untrusted transfer hosts, and exact post-state.
Name and size matching alone are not sufficient evidence.

## Photos upload

Local evidence: `pyicloud/services/photos/upload.py` and
`tests/fixtures/photos_upload/put_asset_response.json` establish typed `cplMaster`,
`cplAsset` and upload-job identities. This is stronger identity evidence than Drive's
commit fixture, but does not qualify the existing uploader for MCP.

Required work/evidence:

- A path-free bounded byte interface or a separately qualified private staging
  mechanism; no caller filesystem selector.
- No-retry raw transport, redirect refusal and transfer-host/credential isolation.
- Exact fresh private-library/zone and indexed-record lookup correlation.
- Duplicate-result semantics and uncertain reservation/transfer/registration cases.
- Explicit protected-mode rejection without PCS consent/device negotiation.

Typed registration is not independent indexing proof. Upload status alone is not
proof that the exact approved asset is usable in the approved library.
Existing `tests/services/test_photos.py` covers exact returned master/asset lookup in
the selected zone and bounded indexing retries; reuse that correlation contract.

## Contacts writes

Local evidence: `pyicloud/services/contacts.py` exposes reads only. A URL containing
`changeset` is not a mutation contract.

Required: redacted create/update/delete requests and responses, field schema,
identity correlation, revision conflict semantics and independent exact lookup.
Do not implement a guessed changeset payload.

## Notes writes

Local evidence: Notes supports reading, decoding and export, not a verified writer.

Required: redacted mutation/acknowledgement/lookup sequences establishing body
serialization, encryption or encoded-body handling, CRDT preservation, record
revisions, folder ownership and attachment relationships. Include stale-revision
rejection and uncertain-write outcomes. A decoded text export is not a writable body.

## Calendar updates

Local evidence: `add_event` is a creation API; no dedicated update contract exists.

Required: a captured update of an existing event, exact stable event identity,
revision precondition, response and fresh lookup; include conflicts and recurrence
instance versus series semantics. Do not assume creation is an upsert.

## Mail moves and flag changes

Required for moves: an acknowledgement or authoritative lookup mapping the exact
old ID/UID to the new destination ID/UID and mailbox UID validity. Subject/sender
matching is not identity proof.

Required for flags: verified flag encoding and authoritative per-message state
verification, including unset flags and concurrent changes. Absence from paginated
SEEN/FLAGGED scans is not exact flag-state proof.
`tests/test_mail_flags.py` already establishes named `seen`/`flagged` set/clear patches
and exact typed acknowledgement; no numeric bit inference is needed to dispatch.

## Drive folder creation and recovery

Creation requires the `createFolders` temporary client-ID to final node-ID mapping,
independent exact lookup, parent membership and collision behaviour.

Recovery requires exact restored identity, destination parent, revision and namespace
verification, plus missing-parent and name-collision/overwrite behaviour. A successful
`putBackItemsFromTrash` response alone cannot bind the approved destination.
`DRIVE_TRASH_RECOVER_WORKING` in `tests/const/const_drive.py` already supplies restored
ID, parent, zone and etag; independent post-state/conflict qualification remains.

## Safe evidence submission

Only use already obtained, authorized, redacted evidence. Do not collect anything
from a live account under the code-and-synthetic-tests authorization. Remove tokens,
cookies, signed URLs and personal content; replace account/object IDs consistently
across related requests and responses so correlations remain testable. Keep schema,
types, status codes, revisions and conflict relationships intact. Record the source
and supported scope; do not label newly invented test data as observed wire evidence.
