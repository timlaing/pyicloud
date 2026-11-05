# Photo Library

[← Back to the documentation index](../index.md)

You can access the iCloud Photo Library through the `photos` property.

The public Photos classes remain in `pyicloud.services.photos.service` with
package re-exports unchanged. The private `queries` module owns typed query
construction and raw request serialization, including zone owner, projections
and continuation markers. Libraries and albums still own caches and request
ordering; materialization remains in `materialize`. These internal module
boundaries are not additional public import guarantees.

## Photos CLI

The Photos CLI is split into browse commands and sync commands:

- `icloud photos libraries`, `albums`, `list`, `get`, `changes`, and `sync-cursor`
  are read-focused inspection commands.
- `icloud photos sync` and `icloud photos watch` are the modern replacement path
  for `icloud_photos_downloader`.

The CloudKit-backed browse/sync path targets the private iCloud Photos library
and Shared Library zones discovered as `shared:<zoneName>` keys. Shared Albums
/ shared streams remain available through the shared-stream module in the
Photos package.

Current scope:

- private-library browsing, download, sync, watch, and mutation flows use the
  modern CloudKit-backed Photos service
- Shared Library CloudKit reads are exposed through `photos libraries` as
  `shared:<zoneName>` keys and are supported by `list`, `get`, `download`,
  `changes`, `sync-cursor`, `sync`, and `watch`
- Shared Library album filters are currently limited to the captured, tested
  smart albums `Library` and `Favorites`
- Shared Albums / shared streams continue to use the shared-stream module
  under the `shared` library key
- Shared Library album-scoped browsing and mixed `Both Libraries` semantics are
  still narrower than the private-library path and continue to rely on further
  captures

Support matrix:

- Private library `root`: full browse/download/sync/watch surface plus the
  implemented private-library mutations
- Shared Library `shared:<zoneName>`: library-scoped reads plus `Library` /
  `Favorites` album filters, `sync-cursor`, `sync`, `watch`, and
  favorite/unfavorite mutations
- Legacy Shared Albums `shared`: old shared-stream adapter only, not a valid
  CloudKit browse/sync target

Typical browse and sync examples:

```console
icloud photos libraries --username jappleseed@apple.com
icloud photos albums --username jappleseed@apple.com
icloud photos list --album Screenshots --limit 20 --username jappleseed@apple.com
icloud photos list --library 'shared:<zoneName>' --limit 20 --username jappleseed@apple.com
icloud photos list --library 'shared:<zoneName>' --album Favorites --limit 20 --username jappleseed@apple.com
icloud photos get photo-id-123 --format json --username jappleseed@apple.com
icloud photos get photo-id-123 --library 'shared:<zoneName>' --format json --username jappleseed@apple.com
icloud photos sync --directory ./downloads --recent 30 --folder-structure '{:%Y/%m}' --username jappleseed@apple.com
icloud photos sync --library 'shared:<zoneName>' --directory ./shared-downloads --username jappleseed@apple.com
icloud photos sync --directory ./downloads --album Favorites --size original --live-photo-size medium --username jappleseed@apple.com
icloud photos watch --directory ./downloads --recent 1 --interval 300 --username jappleseed@apple.com
icloud photos watch --library 'shared:<zoneName>' --directory ./shared-downloads --interval 300 --username jappleseed@apple.com
icloud photos changes --since '<sync-cursor>' --limit 100 --username jappleseed@apple.com
icloud photos changes --library 'shared:<zoneName>' --since '<sync-cursor>' --limit 100 --username jappleseed@apple.com
icloud photos sync-cursor --username jappleseed@apple.com
icloud photos sync-cursor --library 'shared:<zoneName>' --username jappleseed@apple.com
```

Library-key notes:

- `root` is the private iCloud Photos library
- `shared:<zoneName>` is a CloudKit-backed Shared Library zone
- `shared` is the Shared Albums / shared-stream module and is not a
  drop-in substitute for CloudKit library reads, `sync-cursor`, `sync`, or
  `watch`

`sync` and `watch` support downloader-style options such as `--recent`,
`--until-found`, repeatable `--album`, `--folder-structure`, `--size`,
`--live-photo-size`, `--skip-videos`, `--skip-live-photos`, `--align-raw`,
`--xmp-sidecar`, `--set-exif-datetime`, `--only-print-filenames`, `--dry-run`,
`--auto-delete`, and `--keep-icloud-recent-days`.

## Migrating from `icloud_photos_downloader`

If you currently use `icloudpd`, the equivalent workflow in `pyicloud` is:

- Authenticate once with `icloud auth login`, then run `icloud photos sync` or
  `icloud photos watch`.
- Use `icloud photos sync` for one-shot materialization into a local directory.
- Use `icloud photos watch` for repeated polling with the same sync options.
- For private-library downloader workflows, `sync` and `watch` are the intended
  replacement path. Shared streams remain a separate surface.

Common option mappings:

- `icloudpd --directory DIR` -> `icloud photos sync --directory DIR`
- `icloudpd --recent N` -> `icloud photos sync --recent N`
- `icloudpd --until-found N` -> `icloud photos sync --until-found N`
- `icloudpd --album NAME` -> `icloud photos sync --album NAME`
- `icloudpd --folder-structure FORMAT` -> `icloud photos sync --folder-structure FORMAT`
- `icloudpd --size SIZE` -> `icloud photos sync --size SIZE`
- `icloudpd --live-photo-size SIZE` -> `icloud photos sync --live-photo-size SIZE`
- `icloudpd --skip-videos` -> `icloud photos sync --skip-videos`
- `icloudpd --skip-live-photos` -> `icloud photos sync --skip-live-photos`
- `icloudpd --align-raw MODE` -> `icloud photos sync --align-raw MODE`
- `icloudpd --xmp-sidecar` -> `icloud photos sync --xmp-sidecar`
- `icloudpd --set-exif-datetime` -> `icloud photos sync --set-exif-datetime`
- `icloudpd --auto-delete` -> `icloud photos sync --auto-delete`
- `icloudpd --only-print-filenames` -> `icloud photos sync --only-print-filenames`
- `icloudpd --watch-with-interval SECONDS` -> `icloud photos watch --interval SECONDS`

Unlike `icloudpd`, authentication and session management stay under
`icloud auth ...`; the Photos commands do not reimplement separate auth flags.

```pycon
>>> api.photos.all
<PhotoAlbum: 'All Photos'>
```

Individual albums are available through the `albums` property:

```pycon
>>> api.photos.albums["Screenshots"]
<PhotoAlbum: 'Screenshots'>
```

To delete an individual album, call the `delete` method.

```pycon
>>> api.photos.albums["MyAlbum"]
<PhotoAlbum: 'MyAlbum'>
>>> api.photos.albums["MyAlbum"].delete()
True
```

Shared streams are still available separately:

```pycon
>>> api.photos.shared_streams
<AlbumContainer: ...>
```

Those shared-stream albums continue to use the shared-stream module, even though the
private-library path is now CloudKit-backed.

Which you can iterate to access the photo assets. The "All Photos"
album is sorted by `added_date` so the most recently added
photos are returned first. All other albums are sorted by
`asset_date` (which represents the exif date) :

```pycon
>>> for photo in api.photos.albums['Screenshots']:
        print(photo, photo.filename)
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
```

To download a photo, use the `download` method, which will return a raw stream:

```python
photo = next(iter(api.photos.albums["Screenshots"]), None)
with open(photo.filename, "wb") as opened_file:
    opened_file.write(photo.download())
```

Information about each version can be accessed through the `versions`
property:

```pycon
>>> photo.versions.keys()
['medium', 'original', 'thumb']
```

To download a specific version of the photo asset, pass the version to
`download()`:

```python
with open(photo.versions["thumb"]["filename"], "wb") as thumb_file:
    thumb_file.write(photo.download("thumb"))
```

To upload a photo use the `upload` method. You can upload directly through an
album object, or use the top-level `api.photos.upload(...)` helper to target
the root library or a named album. Uploads to a specific album will also appear
automatically in your `All Photos` library. Each form returns the uploaded
PhotoAsset for further information, or `None` if iCloud has not finished
indexing it in time (see the note below).

```python
api.photos.upload(file_path)
api.photos.upload(file_path, album="Screenshots")
```

```python
api.photos.albums["Screenshots"].upload(file_path)
```

```pycon
>>> album = api.photos.albums["Screenshots"]
>>> album
<PhotoAlbum: 'Screenshots'>
>>> album.upload("./my_test_image.jpg")
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jdQ> my_test_image.jpg
>>> api.photos.upload("./my_test_image.jpg", album="Screenshots")
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jdQ> my_test_image.jpg
```

Note: Only limited media types are accepted; iCloud's own web uploader declares
`.jpeg`, `.jpg`, and `image/jpeg`. An unsupported file is rejected with HTTP 415
and raises a `PhotosApiError` naming the status.

Uploads run against Apple's `photosupload` webservice, which replaced the
withdrawn `uploadimagews` endpoint. The host is read from the account's
webservice list at login; on the rare account that does not publish it, uploads
raise "Photos uploads are not configured" while every other Photos feature keeps
working.

Registering an upload and being able to read it back are separate steps: iCloud
indexes a new asset a few seconds after it is stored, so `upload()` retries the
lookup until the asset is available before returning it. Against a live account
this took 14-20 seconds. Uploading a file iCloud already holds returns the
**existing** asset rather than raising, and resolves immediately.

If indexing has not finished within the 60-second budget, `upload()` returns
`None`. The file is stored in iCloud regardless and appears once indexing
completes, so treat `None` as "uploaded, not yet readable" rather than as a
failure. Both the budget and the retry interval are configurable:

```python
api = PyiCloudService(
    "jappleseed@apple.com",
    "password",
    upload_hydration_timeout=120.0,  # optional, default 60
    upload_hydration_interval=5.0,  # optional, default 2
)
```

To delete a photo, use the `delete` method on the PhotoAsset. It returns a bool indicating success.

```pycon
>>> photo = api.photos.albums["Screenshots"][0]
>>> photo
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
>>> photo.delete()
True
```

To add an existing photo to an album, use the `add_photo` method, which will link the PhotoAsset to the requested album.
It returns a bool indicating success.

```python
api.photos.albums["Screenshots"].add_photo(photo_asset)
```

```pycon
>>> photo = api.photos.albums["Screenshots"][0]
>>> photo
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
>>> my_album = api.photos.albums["MyAlbum"]
>>> my_album
<PhotoAlbum: 'MyAlbum'>
>>> my_album.add_photo(photo)
True
```

## Errors

Photos transport and API failures derive from `PhotosServiceException`, which
itself derives from `PyiCloudException`, so a single `except PyiCloudException`
block still catches everything:

- `PhotosAuthError` — authentication or entitlement problems (HTTP 401/403).
- `PhotosRateLimited` — HTTP 429; carries `retry_after` in seconds when Apple
  sends a `Retry-After` header.
- `PhotosApiError` — the catch-all API failure; carries the response `payload`
  when one is available.
- `PhotosZoneNotFoundError` — Apple reported the requested Photos zone as
  missing (`ZONE_NOT_FOUND`). It derives from `PhotosApiError` and carries the
  zone name in `zone_name`. It reaches the caller when the Photos service's
  root library is accessed (the indexing-state check) as well as from later
  queries, zone discovery, and writes.

```python
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.photos import PhotosZoneNotFoundError

try:
    photos = api.photos
    library = photos.libraries["root"]
    lists = photos.albums
except PhotosZoneNotFoundError:
    print("Photos is not available for this account")
except PyiCloudException as err:
    print(f"Photos failed: {err}")
```

## Album mutation identity metadata

Owned `PrimarySync` records may include `ownerRecordName` and `zoneType` even though
the primary library selector contains only the zone name. Private write paths admit
that metadata only when ownership matches the private zones inventory observed by
the service; no owner is inferred from an account ID. The passive
`PhotosService.private_primary_zone` property returns a detached snapshot bound to
the observed account and route and makes no request. Unknown/foreign owners, shared
zones and unsupported zone types remain refused. Approval bindings retain ownership
metadata even when zone-name matching ignores harmless optional decorations.

`PhotoAlbum.can_add_photo_correlated(photo)` checks observed typed private same-zone
support without network requests or approval. `contains_photo_scoped(photo)` performs
a read only when the album has the verified container list type and exact parent
filter, with no extra predicates that could hide membership. Returned assets must
match asset/master/zone identity. Presence or absence is a fresh observation, not an
atomic snapshot with a later write. These APIs back the separately opted-in MCP
`photos.add_to_album` action; see the [MCP guide](../guides/mcp.md).

`PhotoAlbum.add_photo_correlated(photo)` adds one relation only through a typed
private same-zone route. The returned acknowledgement must match the exact relation
ID, record type, asset item ID and album container ID. It refuses nested/shared or
unsupported transport contexts before dispatch and never falls back to REST.
This is acknowledgement correlation, not independent album membership proof;
refresh and query membership before claiming success, and never blindly retry an
uncertain result. Existing `add_photo()` remains unchanged. MCP `photos.add_to_album`
requires its own separate opt-in, receipt and fresh membership proof; see the
[MCP guide](../guides/mcp.md).

`PhotoLibrary.create_album_correlated(name)` is a private-library typed-client-only
creation entry point. It validates the name, refuses shared-owner or unsupported
transport contexts before dispatch, and never falls back to the legacy REST path.
The acknowledgement must match the submitted ID/name and contain a real revision.
It does not prove independent server visibility: use `server_albums()` afterward
and never blindly retry an uncertain result. Existing `create_album()` behavior
outside its typed-response verification is unchanged. MCP creation requires separate
opt-in action flags and exact independent operator receipts.

`correlated_album_creation_available` reports whether that typed private route is
available without a network probe. It does not grant operator approval or guarantee
the next request will succeed.

`PhotoLibrary.server_albums()` queries albums without merging pending local creation
acknowledgements or replacing existing caches. Use it when independent server
visibility is needed; `refresh_albums()` can retain locally pending albums. Virtual
albums remain present but do not supply valid mutation revisions.

`PhotoAlbum.record_change_tag`, `zone_id`, `parent_id`, and `object_type` expose the
observed identity/revision metadata without fetching data or mutating an album.
`zone_id` returns a detached copy; changing it cannot redirect later writes.
`record_change_tag` may be `None`: callers must not invent a revision for approval
or concurrency checks. These properties are snapshots, not fresh server reads.
Refresh the library's albums and verify post-write state independently when using
revision-bound mutation adapters. These metadata reads do not grant mutation approval;
MCP album rename has separate opt-in flags and operator receipts. Deletion is not
enabled by these properties.

## Integration metadata and correlated mutations

`PhotoAlbum.record_change_tag`, `zone_id`, `parent_id` and `object_type` expose
observed identity metadata without requests or approval. Zone mappings are detached;
missing revisions remain unknown. `PhotoLibrary.server_albums()` reads server album
records without merging pending local creation acknowledgements into visibility proof.

`correlated_album_creation_available` is a passive capability check.
`create_album_correlated(name)` supports typed private album creation only, without
a REST fallback. Typed creation acknowledgements must match the submitted ID, record
and album types, live deletion flag, encoded name and valid revision. Encoded name comparison uses the typed record's
wire representation, supporting both STRING and decoded ENCRYPTED_BYTES fields.
An unrelated response is uncertain, not permission to retry a possibly completed write.

`can_add_photo_correlated(photo)` checks typed private relation eligibility without
requests. `contains_photo_scoped(photo)` uses one exact container-scoped query, not a
full-album scan. Foreign assets, assets missing their master and query error items
raise an error instead of establishing membership. `add_photo_correlated(photo)` checks exact relation, item and container
acknowledgement identity. Acknowledgement is not independently observed membership:
query fresh state before asserting the final outcome; never blindly retry uncertainty.

`PhotosService.upload_root` exposes the observed modern upload route without a
fallback or mutation. `private_primary_zone` returns a detached passive snapshot of
private PrimarySync discovery bound to its account and service route. Owned primary
records are accepted only against that observation; foreign/shared/unverified owners
and unsupported zone types remain refused. No owner is inferred from a DSID.
