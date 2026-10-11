"""Coverage-focused tests for the Photos CloudKit sync engine.

These tests exercise the option validation, short-circuit guards, album
resolution, path helpers, and per-file sync branches that the broader
``tests/services/test_photos_sync.py`` suite does not reach. File-system
writes are confined to the ``python-test-results`` sandbox directory.
"""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from pyicloud.services.photos import (
    PhotoResource,
    PhotosServiceException,
    PhotoSyncOptions,
)
from pyicloud.services.photos_cloudkit.state import (
    SyncedPhotoResource,
    create_photo_sync_state,
)
from pyicloud.services.photos_cloudkit.sync import (
    PhotoSyncItem,
    PhotoSyncResult,
    _asset_datetime,
    _atomic_write_bytes,
    _can_short_circuit,
    _is_current_file,
    _iter_sync_assets,
    _render_relative_path,
    _resolve_library,
    _resolve_resource,
    _safe_relative_folder,
    _sanitize_path_component,
    _select_resources,
    _should_delete_remote_asset,
    _sync_cursor,
    _unique_relative_path,
    run_photo_sync,
    watch_photo_sync,
)
from tests.services.test_photos_sync import (
    TEST_BASE,
    DummyAlbum,
    DummyAsset,
    DummyService,
)

ACTIVE_CURSOR = "coverage-cursor"


@contextmanager
def _temp_dir(prefix: str) -> Generator[Path, None, None]:
    """Create a sandboxed temporary directory that is removed afterwards."""

    path = Path(tempfile.mkdtemp(prefix=prefix, dir=TEST_BASE))
    try:
        yield path
    finally:
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


def _service(cursor: str = ACTIVE_CURSOR) -> DummyService:
    """Return a one-library service holding a single downloadable asset."""

    return DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-1", "photo.jpg")]),
        cursor=cursor,
    )


def test_sync_item_and_result_as_dict() -> None:
    """The item and result dataclasses should serialise every field."""

    item = PhotoSyncItem(
        asset_id="asset-1",
        resource_key="original",
        path="photo.jpg",
        action="downloaded",
        reason="already-current",
    )
    assert item.as_dict() == {
        "asset_id": "asset-1",
        "resource_key": "original",
        "path": "photo.jpg",
        "action": "downloaded",
        "reason": "already-current",
    }

    result = PhotoSyncResult(
        directory="/tmp/out",
        state_path="/tmp/out/state.sqlite3",
        library="root",
        albums=["All Photos"],
        items=[item],
    )
    payload = result.as_dict()
    assert payload["items"] == [item.as_dict()]
    assert payload["sync_cursor"] is None


def test_watch_photo_sync_rejects_invalid_bounds() -> None:
    """The watch generator should validate its interval and iteration bounds."""

    service = _service()
    options = PhotoSyncOptions(directory=TEST_BASE / "unused-watch")

    invalid_interval = watch_photo_sync(service, options, interval_seconds=0)
    with pytest.raises(PhotosServiceException, match="at least 1 second"):
        next(invalid_interval)

    invalid_iterations = watch_photo_sync(
        service,
        options,
        interval_seconds=1,
        iterations=0,
    )
    with pytest.raises(PhotosServiceException, match="iterations must be at least 1"):
        next(invalid_iterations)


def test_run_photo_sync_rejects_invalid_options() -> None:
    """Every invalid option combination should raise before touching disk."""

    service = _service()
    directory = TEST_BASE / "unused-validation"

    bad_size = PhotoSyncOptions(directory=directory, size="small")
    with pytest.raises(PhotosServiceException, match="Unsupported photo size"):
        run_photo_sync(service, bad_size)

    bad_live_size = PhotoSyncOptions(directory=directory, live_photo_size="small")
    with pytest.raises(PhotosServiceException, match="Unsupported live photo size"):
        run_photo_sync(service, bad_live_size)

    bad_align = PhotoSyncOptions(directory=directory, align_raw="rotate")
    with pytest.raises(PhotosServiceException, match="Unsupported RAW alignment"):
        run_photo_sync(service, bad_align)

    bad_auto_delete = PhotoSyncOptions(
        directory=directory,
        auto_delete=True,
        until_found=1,
    )
    with pytest.raises(PhotosServiceException, match="cannot be combined with"):
        run_photo_sync(service, bad_auto_delete)

    bad_keep_recent = PhotoSyncOptions(
        directory=directory,
        keep_icloud_recent_days=0,
        until_found=1,
    )
    with pytest.raises(PhotosServiceException, match="cannot be combined with"):
        run_photo_sync(service, bad_keep_recent)

    until_found_zero = PhotoSyncOptions(directory=directory, until_found=0)
    with pytest.raises(PhotosServiceException, match="until-found must be at least 1"):
        run_photo_sync(service, until_found_zero)

    recent_zero = PhotoSyncOptions(directory=directory, recent=0)
    with pytest.raises(PhotosServiceException, match="recent must be at least 1"):
        run_photo_sync(service, recent_zero)

    keep_recent_negative = PhotoSyncOptions(
        directory=directory,
        keep_icloud_recent_days=-1,
    )
    with pytest.raises(
        PhotosServiceException, match="keep-icloud-recent-days must be at least 0"
    ):
        run_photo_sync(service, keep_recent_negative)


def test_run_photo_sync_skips_assets_without_resources() -> None:
    """Assets with no selectable resources should be skipped silently."""

    skipped = DummyAsset("asset-movie", "movie.mov", item_type="movie")
    kept = DummyAsset("asset-photo", "photo.jpg")
    service = DummyService(
        DummyAlbum("All Photos", [skipped, kept]),
        cursor=ACTIVE_CURSOR,
    )

    with _temp_dir("photos-sync-none-") as temp_dir:
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
                skip_videos=True,
            ),
        )

        assert (temp_dir / "output" / "photo.jpg").exists()

    assert result.downloaded_count == 1


def test_run_photo_sync_reports_already_current_resources() -> None:
    """A second run should report already-current resources instead of re-fetching."""

    service = _service()

    with _temp_dir("photos-sync-current-") as temp_dir:
        options = PhotoSyncOptions(
            directory=temp_dir / "output",
            state_dir=temp_dir / "state",
        )
        run_photo_sync(service, options)
        second = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
                xmp_sidecar=True,
            ),
        )

    assert second.skipped_count == 1
    assert second.items[0].reason == "already-current"


def test_run_photo_sync_until_found_stops_on_current_resource() -> None:
    """Until-found should break out once enough current resources are seen."""

    service = _service()

    with _temp_dir("photos-sync-until-") as temp_dir:
        run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
            ),
        )
        second = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
                xmp_sidecar=True,
                until_found=1,
            ),
        )

    assert second.skipped_count == 1
    assert second.items[0].reason == "already-current"


def test_run_photo_sync_skips_missing_download_data() -> None:
    """A missing download payload should be recorded as a skipped resource."""

    service = _service()

    with (
        _temp_dir("photos-sync-nodata-") as temp_dir,
        patch.object(DummyAsset, "download", return_value=None),
    ):
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
            ),
        )

    assert result.skipped_count == 1
    assert result.items[0].reason == "missing-download-data"


def test_run_photo_sync_keeps_asset_when_remote_delete_fails() -> None:
    """A failed remote delete should not be counted as a deletion."""

    old_asset = DummyAsset("asset-old", "old.jpg", added_days_ago=10)
    service = DummyService(DummyAlbum("All Photos", [old_asset]), cursor=ACTIVE_CURSOR)

    with (
        _temp_dir("photos-sync-nodelete-") as temp_dir,
        patch.object(DummyAsset, "delete", return_value=False),
    ):
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
                keep_icloud_recent_days=0,
            ),
        )

    assert result.deleted_count == 0
    assert all(item.action != "deleted" for item in result.items)


def test_run_photo_sync_auto_delete_skips_current_and_missing_paths() -> None:
    """Auto-delete should keep current entries and drop stale missing entries."""

    first_service = DummyService(
        DummyAlbum(
            "All Photos",
            [
                DummyAsset("asset-keep", "keep.jpg"),
                DummyAsset("asset-gone", "gone.jpg"),
            ],
        ),
        cursor=ACTIVE_CURSOR,
    )
    second_service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-keep", "keep.jpg")]),
        cursor=ACTIVE_CURSOR,
    )

    with _temp_dir("photos-sync-autodelete-") as temp_dir:
        options = PhotoSyncOptions(
            directory=temp_dir / "output",
            state_dir=temp_dir / "state",
        )
        run_photo_sync(first_service, options)
        (temp_dir / "output" / "gone.jpg").unlink()

        result = run_photo_sync(
            second_service,
            PhotoSyncOptions(
                directory=temp_dir / "output",
                state_dir=temp_dir / "state",
                auto_delete=True,
            ),
        )

    assert result.deleted_count == 1
    assert any(item.path == "gone.jpg" for item in result.items)


def test_resolve_library_errors() -> None:
    """Library resolution should surface every unsupported configuration."""

    class NonDictService:
        """Service exposing a non-mapping ``libraries`` attribute."""

        libraries = ["not", "a", "mapping"]

    class EmptyService:
        """Service exposing no libraries at all."""

        libraries: dict[str, Any] = {}

    class SharedService:
        """Service exposing a legacy shared stream library."""

        libraries: dict[str, Any] = {"shared": SimpleNamespace(scope="shared-stream")}

    non_dict_service = NonDictService()
    with pytest.raises(PhotosServiceException, match="does not expose syncable"):
        _resolve_library(non_dict_service, "root")

    empty_service = EmptyService()
    with pytest.raises(PhotosServiceException, match="No photo library matched"):
        _resolve_library(empty_service, "root")

    shared_service = SharedService()
    with pytest.raises(PhotosServiceException):
        _resolve_library(shared_service, "shared")


def test_sync_cursor_falls_back_to_service_then_none() -> None:
    """Cursor resolution should fall back from the library to the service."""

    class CursorlessLibrary:
        """Library without a ``sync_cursor`` method."""

    class CursorService:
        """Service that provides a sync cursor."""

        def sync_cursor(self) -> str:
            """Return a fixed cursor."""

            return "service-cursor"

    class EmptyService:
        """Service without a sync cursor."""

    assert _sync_cursor(CursorlessLibrary(), CursorService()) == "service-cursor"
    assert _sync_cursor(CursorlessLibrary(), EmptyService()) is None


def _short_circuit_state() -> Any:
    """Return a memory sync state primed with the active cursor."""

    state = create_photo_sync_state(
        TEST_BASE / "pcc-short-circuit.sqlite3", ephemeral=True
    )
    state.set_sync_cursor(ACTIVE_CURSOR)
    return state


def _short_circuit(state: Any, directory: Path) -> bool:
    """Invoke ``_can_short_circuit`` with all disqualifying flags disabled."""

    return _can_short_circuit(
        state=state,
        directory=directory,
        current_cursor=ACTIVE_CURSOR,
        auto_delete=False,
        dry_run=False,
        only_print_filenames=False,
        xmp_sidecar=False,
        set_exif_datetime=False,
        keep_icloud_recent_days=None,
    )


def test_can_short_circuit_requires_tracked_resources() -> None:
    """An empty manifest should never short-circuit."""

    state = create_photo_sync_state(
        TEST_BASE / "pcc-short-empty.sqlite3", ephemeral=True
    )
    state.set_sync_cursor(ACTIVE_CURSOR)

    assert (
        _can_short_circuit(
            state=state,
            directory=TEST_BASE,
            current_cursor=ACTIVE_CURSOR,
            auto_delete=False,
            dry_run=False,
            only_print_filenames=False,
            xmp_sidecar=False,
            set_exif_datetime=False,
            keep_icloud_recent_days=None,
        )
        is False
    )


def test_can_short_circuit_rejects_unsafe_and_missing_paths() -> None:
    """Unsafe stale paths and missing files should both block short-circuiting."""

    with _temp_dir("photos-sync-shortcircuit-") as temp_dir:
        unsafe = create_photo_sync_state(temp_dir / "unsafe.sqlite3", ephemeral=True)
        unsafe.set_sync_cursor(ACTIVE_CURSOR)
        unsafe.upsert_resource(
            resource=SyncedPhotoResource(
                asset_id="asset-unsafe",
                resource_key="original",
                relative_path="../escape.jpg",
                size=None,
                checksum=None,
                downloaded_at="2026-04-01T00:00:00+00:00",
            )
        )
        assert (
            _can_short_circuit(
                state=unsafe,
                directory=temp_dir / "output",
                current_cursor=ACTIVE_CURSOR,
                auto_delete=False,
                dry_run=False,
                only_print_filenames=False,
                xmp_sidecar=False,
                set_exif_datetime=False,
                keep_icloud_recent_days=None,
            )
            is False
        )

        missing = create_photo_sync_state(temp_dir / "missing.sqlite3", ephemeral=True)
        missing.set_sync_cursor(ACTIVE_CURSOR)
        missing.upsert_resource(
            resource=SyncedPhotoResource(
                asset_id="asset-missing",
                resource_key="original",
                relative_path="absent.jpg",
                size=None,
                checksum=None,
                downloaded_at="2026-04-01T00:00:00+00:00",
            )
        )
        output = temp_dir / "output"
        output.mkdir()
        assert (
            _can_short_circuit(
                state=missing,
                directory=output,
                current_cursor=ACTIVE_CURSOR,
                auto_delete=False,
                dry_run=False,
                only_print_filenames=False,
                xmp_sidecar=False,
                set_exif_datetime=False,
                keep_icloud_recent_days=None,
            )
            is False
        )


def test_can_short_circuit_handles_sizeless_and_unreadable_files() -> None:
    """A sizeless tracked file short-circuits; an unreadable one does not."""

    with _temp_dir("photos-sync-shortcircuit-") as temp_dir:
        output = temp_dir / "output"
        output.mkdir()
        (output / "present.jpg").write_bytes(b"abc")

        sizeless = create_photo_sync_state(
            temp_dir / "sizeless.sqlite3", ephemeral=True
        )
        sizeless.set_sync_cursor(ACTIVE_CURSOR)
        sizeless.upsert_resource(
            resource=SyncedPhotoResource(
                asset_id="asset-present",
                resource_key="original",
                relative_path="present.jpg",
                size=None,
                checksum=None,
                downloaded_at="2026-04-01T00:00:00+00:00",
            )
        )
        assert (
            _can_short_circuit(
                state=sizeless,
                directory=output,
                current_cursor=ACTIVE_CURSOR,
                auto_delete=False,
                dry_run=False,
                only_print_filenames=False,
                xmp_sidecar=False,
                set_exif_datetime=False,
                keep_icloud_recent_days=None,
            )
            is True
        )

        sized = create_photo_sync_state(temp_dir / "sized.sqlite3", ephemeral=True)
        sized.set_sync_cursor(ACTIVE_CURSOR)
        sized.upsert_resource(
            resource=SyncedPhotoResource(
                asset_id="asset-present",
                resource_key="original",
                relative_path="present.jpg",
                size=3,
                checksum=None,
                downloaded_at="2026-04-01T00:00:00+00:00",
            )
        )
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "stat", side_effect=OSError("unreadable")),
        ):
            assert (
                _can_short_circuit(
                    state=sized,
                    directory=output,
                    current_cursor=ACTIVE_CURSOR,
                    auto_delete=False,
                    dry_run=False,
                    only_print_filenames=False,
                    xmp_sidecar=False,
                    set_exif_datetime=False,
                    keep_icloud_recent_days=None,
                )
                is False
            )


def test_iter_sync_assets_rejects_container_without_find() -> None:
    """An album container lacking ``find`` cannot be used for album sync."""

    class Container:
        """Album container without a ``find`` method."""

    class Library:
        """Library whose album container cannot resolve names."""

        albums = Container()
        scope = "private"

    options = PhotoSyncOptions(directory=TEST_BASE, albums=("Vacation",))
    library = Library()

    assets = _iter_sync_assets(object(), library, options)
    with pytest.raises(PhotosServiceException, match="does not support album-based"):
        list(assets)


class BareLibrary:
    """Library exposing neither albums nor a default feed."""

    albums = None
    recently_added = None
    all = None
    scope = "private"


def test_iter_sync_assets_requires_an_album_container() -> None:
    """A missing album container should raise a helpful error."""

    options = PhotoSyncOptions(directory=TEST_BASE, albums=("Holidays",))
    library = BareLibrary()

    assets = _iter_sync_assets(object(), library, options)
    with pytest.raises(PhotosServiceException, match="does not support album-based"):
        list(assets)


def test_iter_sync_assets_shared_library_missing_album() -> None:
    """A shared-library album miss should raise the shared-library message."""

    class Container:
        """Album container that never finds an album."""

        def find(self, _name: str | None) -> None:
            """Return no album for any name."""

            return None

    class SharedLibrary:
        """Library scoped to the shared library."""

        albums = Container()
        scope = "shared-library"

    options = PhotoSyncOptions(directory=TEST_BASE, albums=("Missing",))
    library = SharedLibrary()

    assets = _iter_sync_assets(object(), library, options)
    with pytest.raises(PhotosServiceException):
        list(assets)


def test_iter_sync_assets_private_library_missing_album() -> None:
    """A private-library album miss should raise a plain not-found error."""

    class Container:
        """Album container that never finds an album."""

        def find(self, _name: str | None) -> None:
            """Return no album for any name."""

            return None

    class PrivateLibrary:
        """Library scoped to the private library."""

        albums = Container()
        scope = "private"

    options = PhotoSyncOptions(directory=TEST_BASE, albums=("Missing",))
    library = PrivateLibrary()

    assets = _iter_sync_assets(object(), library, options)
    with pytest.raises(PhotosServiceException, match="No album named"):
        list(assets)


def test_iter_sync_assets_dedupes_album_assets() -> None:
    """Assets shared between selected albums should only be yielded once."""

    asset = DummyAsset("asset-dup", "dup.jpg")
    albums = {
        "One": DummyAlbum("One", [asset]),
        "Two": DummyAlbum("Two", [asset]),
    }

    class Container:
        """Album container resolving names from a mapping."""

        def find(self, name: str | None) -> DummyAlbum | None:
            """Return the album registered for the given name."""

            return albums.get(name or "")

    class Library:
        """Library exposing a name-resolving album container."""

        scope = "private"

        def __init__(self) -> None:
            self.albums = Container()

    options = PhotoSyncOptions(directory=TEST_BASE, albums=("One", "Two"))

    assert list(_iter_sync_assets(object(), Library(), options)) == [asset]


def test_iter_sync_assets_requires_default_feed() -> None:
    """A library without any usable feed should raise a clear error."""

    options = PhotoSyncOptions(directory=TEST_BASE)
    assets = _iter_sync_assets(object(), BareLibrary(), options)
    with pytest.raises(PhotosServiceException, match="default asset feed"):
        list(assets)


def test_iter_sync_assets_dedupes_default_feed() -> None:
    """The default feed should drop duplicate asset ids."""

    asset = DummyAsset("asset-dup", "dup.jpg")

    class Source:
        """Feed yielding the same asset twice."""

        photos = [asset, asset]

    class Library:
        """Library exposing only a default ``all`` feed."""

        recently_added = None
        all = Source()

    assert list(
        _iter_sync_assets(
            object(),
            Library(),
            PhotoSyncOptions(directory=TEST_BASE),
        )
    ) == [asset]


def test_select_resources_covers_movie_and_live_branches() -> None:
    """Resource selection should honour video and live-photo skip flags."""

    options = PhotoSyncOptions(directory=TEST_BASE)

    movie = DummyAsset("asset-movie", "movie.mov", item_type="movie")
    assert not _select_resources(
        movie,
        PhotoSyncOptions(directory=TEST_BASE, skip_videos=True),
    )

    empty_movie = SimpleNamespace(item_type="movie", resources={}, is_live_photo=False)
    assert not _select_resources(empty_movie, options)

    empty_image = SimpleNamespace(item_type="image", resources={}, is_live_photo=False)
    assert not _select_resources(empty_image, options)

    live = DummyAsset("asset-live", "live.jpg", is_live_photo=True)
    assert not _select_resources(
        live,
        PhotoSyncOptions(directory=TEST_BASE, skip_live_photos=True),
    )

    selected = _select_resources(live, options)
    assert [key for key, _ in selected] == ["original"]


def test_resolve_resource_skips_url_less_candidates() -> None:
    """Candidates without a download URL should be skipped in order."""

    url_less = PhotoResource(
        key="original",
        filename="photo.jpg",
        url="",
        size=1,
        type="public.jpeg",
    )
    usable = PhotoResource(
        key="medium",
        filename="photo.jpg",
        url="https://example.com/medium",
        size=1,
        type="public.jpeg",
    )

    assert _resolve_resource({"original": url_less}, ["original"]) is None
    resolved = _resolve_resource(
        {"original": url_less, "medium": usable},
        ["original", "medium"],
    )
    assert resolved is not None
    assert resolved[0] == "medium"


def test_render_relative_path_uses_strftime_and_empty_folders() -> None:
    """Folder structures should support strftime tokens and collapse empties."""

    asset_date = datetime(2026, 4, 21, tzinfo=timezone.utc)
    asset = DummyAsset("asset-1", "photo.jpg", asset_date=asset_date)

    dated = _render_relative_path(asset, asset.resources["original"], "%Y/%m")
    assert dated.startswith(str(asset_date.year))
    assert dated.endswith("photo.jpg")

    collapsed = _render_relative_path(asset, asset.resources["original"], ".")
    assert collapsed == "photo.jpg"


def test_unique_relative_path_assigns_discriminators() -> None:
    """Colliding paths should gain stable, incremented discriminators."""

    reserved: set[str] = set()
    tracked: dict[str, tuple[str, str]] = {}

    first = _unique_relative_path(
        candidate="photo.jpg",
        asset_id="abcdefgh1234",
        resource_key="original",
        reserved_paths=reserved,
        tracked_paths=tracked,
    )
    assert first == "photo.jpg"

    reserved.add(first)
    second = _unique_relative_path(
        candidate="photo.jpg",
        asset_id="abcdefgh",
        resource_key="original",
        reserved_paths=reserved,
        tracked_paths=tracked,
    )
    assert second == "photo_abcdefgh.jpg"

    reserved.add(second)
    third = _unique_relative_path(
        candidate="photo.jpg",
        asset_id="abcdefgh",
        resource_key="original",
        reserved_paths=reserved,
        tracked_paths=tracked,
    )
    assert third == "photo_abcdefgh_2.jpg"


def test_safe_relative_folder_and_sanitizer_fallbacks() -> None:
    """Empty folder components and unsafe path components fall back safely."""

    assert _safe_relative_folder(".") == ""
    assert _sanitize_path_component(".", fallback="fallback") == "fallback"
    assert _sanitize_path_component("..", fallback="fallback") == "fallback"


def test_is_current_file_branches() -> None:
    """Every short-circuit condition in the current-file check should be reached."""

    resource = PhotoResource(
        key="original",
        filename="photo.jpg",
        url="https://example.com/photo",
        size=3,
        type="public.jpeg",
        checksum="checksum-1",
    )
    assert _is_current_file(Path("/tmp/missing"), None, resource, "photo.jpg") is False

    wrong_path = SyncedPhotoResource(
        asset_id="asset-1",
        resource_key="original",
        relative_path="other.jpg",
        size=3,
        checksum="checksum-1",
        downloaded_at="2026-04-01T00:00:00+00:00",
    )
    assert (
        _is_current_file(Path("/tmp/missing"), wrong_path, resource, "photo.jpg")
        is False
    )

    with _temp_dir("photos-sync-current-file-") as temp_dir:
        target = temp_dir / "photo.jpg"
        target.write_bytes(b"abc")
        correct_path = SyncedPhotoResource(
            asset_id="asset-1",
            resource_key="original",
            relative_path="photo.jpg",
            size=3,
            checksum="checksum-1",
            downloaded_at="2026-04-01T00:00:00+00:00",
        )
        assert _is_current_file(target, correct_path, resource, "photo.jpg") is True

        stale_checksum = SyncedPhotoResource(
            asset_id="asset-1",
            resource_key="original",
            relative_path="photo.jpg",
            size=3,
            checksum="checksum-2",
            downloaded_at="2026-04-01T00:00:00+00:00",
        )
        absent_path = SyncedPhotoResource(
            asset_id="asset-1",
            resource_key="original",
            relative_path="photo.jpg",
            size=3,
            checksum="checksum-1",
            downloaded_at="2026-04-01T00:00:00+00:00",
        )
        assert _is_current_file(target, stale_checksum, resource, "photo.jpg") is False
        assert (
            _is_current_file(
                temp_dir / "absent.jpg", absent_path, resource, "photo.jpg"
            )
            is False
        )


def test_atomic_write_bytes_removes_temp_file_on_failure() -> None:
    """A failed rename should still clean up the temporary file."""

    with _temp_dir("photos-sync-atomic-") as temp_dir:
        target = temp_dir / "photo.jpg"
        with (
            patch(
                "pyicloud.services.photos_cloudkit.sync.os.replace",
                side_effect=OSError("no rename"),
            ),
            pytest.raises(OSError),
        ):
            _atomic_write_bytes(target, b"payload")

        assert not list(temp_dir.glob(".pyicloud-sync-*"))


def test_asset_datetime_treats_naive_values_as_utc() -> None:
    """Naive datetimes should be normalised to UTC."""

    naive = datetime(2026, 4, 21, 12, 0, 0)
    asset = SimpleNamespace(asset_date=naive)

    assert _asset_datetime(asset, "asset_date") == naive.replace(tzinfo=timezone.utc)
    assert _asset_datetime(asset, "missing") is None


def test_should_delete_remote_asset_guard_branches() -> None:
    """The remote-delete guard should honour dry-run and readiness checks."""

    asset = DummyAsset("asset-old", "old.jpg", added_days_ago=10)
    now_local = datetime.now(timezone.utc).astimezone()

    assert (
        _should_delete_remote_asset(
            asset=asset,
            options=PhotoSyncOptions(
                directory=TEST_BASE,
                keep_icloud_recent_days=0,
            ),
            asset_ready_for_delete=True,
            asset_confirmed_local=True,
            now_local=now_local,
        )
        is True
    )

    assert (
        _should_delete_remote_asset(
            asset=asset,
            options=PhotoSyncOptions(
                directory=TEST_BASE,
                keep_icloud_recent_days=0,
                dry_run=True,
            ),
            asset_ready_for_delete=True,
            asset_confirmed_local=True,
            now_local=now_local,
        )
        is False
    )

    assert (
        _should_delete_remote_asset(
            asset=asset,
            options=PhotoSyncOptions(
                directory=TEST_BASE,
                keep_icloud_recent_days=0,
            ),
            asset_ready_for_delete=False,
            asset_confirmed_local=True,
            now_local=now_local,
        )
        is False
    )
