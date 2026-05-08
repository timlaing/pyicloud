These fixtures back the modern Photos CloudKit test suite.

They fall into three groups:

- synthetic protocol fixtures for private-library read and change flows
  - `photos_zones_list_response.json`
  - `photos_shared_library_private_zones_response.json`
  - `photos_shared_library_shared_zones_response.json`
  - `photos_shared_library_all_photos_*`
  - `photos_shared_library_favorites_*`
  - `photos_shared_library_zone_changes_*`
  - `photos_shared_library_unfavorite_*`
  - `photos_database_changes_response.json`
  - `photos_zone_changes_response.json`
  - `photos_all_photos_*`
  - `photos_recently_added_*`
  - `photos_favorites_*`
  - `photos_album_membership_*`
  - `photos_live_photo_response.json`
  - `photos_video_only_response.json`
  - `photos_missing_counterparts_response.json`
- sanitized browser-derived mutation fixtures in
  [`photos_browser_mutations`](photos_browser_mutations/README.md)
- sanitized upload-response fixtures captured from live upload flows
  - `photos_upload_skeletal_response.json`
  - `photos_upload_duplicate_response.json`

The tracked fixtures intentionally exclude raw HAR files, cookies, headers, and
binary media payloads. Any live captures used to derive these fixtures stay in
local workspace-only directories and are redacted before promotion into
`tests/fixtures/`.
