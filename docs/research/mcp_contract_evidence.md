# Remaining MCP write-contract evidence

This is an implementation acceptance checklist, not permission for account access
or live writes. All pending operations remain disabled. Synthetic fixtures can test
an established contract; invented success envelopes cannot establish one.

MCP write scope is limited to existing API/CLI operations. Contacts writes, Notes
writes and Calendar updates are excluded, not completion requirements. Their notes
below are background only; do not add library writers to satisfy MCP.

`pyicloud/mcp/upload_transport.py` now provides an internal path-free bounded-byte
transfer foundation: exact trusted HTTPS hosts, no URL credentials/redirects,
isolated credential-free sessions with environment inheritance disabled, raw service
registration with no authentication replay, bounded streamed JSON and response cleanup.
It is not wired to a runtime tool. Transfer hosts still require independently reviewed
policy; never derive the allowlist from an untrusted reservation response.

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
