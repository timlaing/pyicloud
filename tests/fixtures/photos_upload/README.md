These fixtures cover Apple's multi-step Photos upload protocol, which replaced
the withdrawn single-POST `uploadimagews` endpoint (see timlaing/pyicloud#316).

The shapes were captured from the issue and then confirmed against a live
account: every response below matched what Apple actually returned.

See also the top-level fixture guide in
[`tests/fixtures/README.md`](../README.md)
for how these files relate to the broader Photos protocol fixture set.

They are shaped from the protocol capture in the issue, with every token,
checksum, and account identifier replaced by stable placeholders. No raw HAR
files, binary bodies, or cookies are included.

The fixture set covers, in flow order:

- `create_upload_url_response.json` — reserved upload URLs, keyed by the client
  UUID that requested them
- `single_file_upload_response.json` — the content host's receipt for the stored
  bytes, echoed back verbatim when the asset is registered
- `put_asset_response.json` — a successful registration, carrying `cplMaster` and
  `cplAsset` record names directly
- `put_asset_duplicate_response.json` — a file iCloud already holds. Verified
  against a live account: Apple answers `409` with an `errorMessage`, omits
  `isRetryable` and `uploadJobId`, but still supplies both record names, so the
  existing asset can be returned instead of raising
- `put_asset_rejected_response.json` — the same shape with a retryable failure
  status, used to check that a genuinely bad registration raises
- `upload_status_response.json` — ingest progress for a known `uploadJobId`
- `upload_status_unknown_response.json` — an unrecognised job id. Apple answers
  `200` and marks the entry `errorCode: 404` rather than omitting it

The `uploadStatus` request body was not in the capture. It was determined by
probing a live account with a synthetic job id: of thirteen candidate shapes,
`{"uploadJobIds": [id]}` was the only one Apple accepted; every other body, and
the GET and query-string variants, answered `400`.

The client UUID is fixed at `11111111-2222-3333-4444-555555555555` so the
reserved-URL mapping in step 1 stays matchable; tests patch UUID generation to
that value.
