These fixtures are sanitized browser-derived CloudKit mutation payloads captured
from iCloud Photos web flows.

See also the top-level fixture guide in
[`tests/fixtures/README.md`](../README.md)
for how these files relate to the broader Photos protocol fixture set.

They intentionally exclude raw HAR files, binary responses, cookies, and account
identifiers. Stable placeholder values are used instead so request and response
relationships remain testable without exposing personal data.

The fixture set covers:

- photo upload follow-up mutation responses
- album create / rename / delete
- add photo to album
- remove photo from album
- delete photo from library

`album_remove_photo_*` represents removing an asset from an album by deleting the
`CPLContainerRelation` record. `photo_delete_*` represents deleting the asset from
the All Photos library by updating the `CPLAsset` record with `isDeleted = 1`.
