# Photo Library

[← Back to the documentation index](../index.md)

You can access the iCloud Photo Library through the `photos` property.

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
  zone name in `zone_name`. It reaches the caller when `api.photos` builds the
  service (the indexing-state check) as well as from later queries, zone
  discovery, and writes.

```python
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.photos import PhotosZoneNotFoundError

try:
    photos = api.photos
    lists = photos.albums
except PhotosZoneNotFoundError:
    print("Photos is not available for this account")
except PyiCloudException as err:
    print(f"Photos failed: {err}")
```
