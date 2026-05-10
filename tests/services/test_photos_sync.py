"""Tests for the Photos sync engine and state backend."""

from __future__ import annotations

import base64
import struct
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import pytest

from pyicloud.services.photos import (
    PhotoResource,
    PhotosServiceException,
    PhotoSyncOptions,
    run_photo_sync,
    watch_photo_sync,
)
from pyicloud.services.photos_cloudkit import materialize as materialize_module
from pyicloud.services.photos_cloudkit import sync as sync_module
from pyicloud.services.photos_cloudkit.state import (
    MemoryPhotoSyncState,
    SQLitePhotoSyncState,
    SyncedPhotoResource,
    create_photo_sync_state,
)

TEST_BASE = Path(tempfile.gettempdir()) / "python-test-results"
TEST_BASE.mkdir(parents=True, exist_ok=True)
MINIMAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQF"
    "BgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEI"
    "I0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNk"
    "ZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLD"
    "xMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEB"
    "AQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJB"
    "UQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZH"
    "SElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaan"
    "qKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oA"
    "DAMBAAIRAxEAPwD3+iiigD//2Q=="
)


class DummyAlbumContainer(list):
    """Album container fixture for sync tests."""

    def find(self, name: Optional[str]):
        if name is None:
            return None
        for album in self:
            if album.name == name:
                return album
        return None


class DummyAlbum:
    """Album fixture for sync tests."""

    def __init__(self, name: str, assets: list["DummyAsset"]) -> None:
        self.name = name
        self.fullname = f"/{name}"
        self._assets = assets

    @property
    def photos(self):
        return iter(self._assets)


class DummyLibrary:
    """Library fixture for sync tests."""

    def __init__(self, album: DummyAlbum, *, cursor: str) -> None:
        self.all = album
        self.albums = DummyAlbumContainer([album])
        self._cursor = cursor

    def sync_cursor(self) -> str:
        return self._cursor

    def recently_added(self):
        return self.all


class DummyService:
    """Minimal service surface consumed by the sync engine."""

    def __init__(self, album: DummyAlbum, *, cursor: str) -> None:
        self.all = album
        self.albums = DummyAlbumContainer([album])
        self.libraries = {"root": DummyLibrary(album, cursor=cursor)}
        self._cursor = cursor

    def sync_cursor(self) -> str:
        return self._cursor


class DummyAsset:
    """Photo asset fixture for sync-engine tests."""

    def __init__(
        self,
        asset_id: str,
        filename: str,
        *,
        item_type: str = "image",
        is_live_photo: bool = False,
        added_days_ago: int = 0,
        asset_date: Optional[datetime] = None,
        added_date: Optional[datetime] = None,
        resources: Optional[dict[str, PhotoResource]] = None,
        asset_record: Optional[dict] = None,
        payloads: Optional[dict[str, bytes]] = None,
    ) -> None:
        self.id = asset_id
        self.filename = filename
        self.item_type = item_type
        self.is_live_photo = is_live_photo
        resolved_asset_date = asset_date
        if resolved_asset_date is None:
            resolved_asset_date = datetime.now(timezone.utc) - timedelta(
                days=added_days_ago
            )
        self.asset_date = resolved_asset_date
        self.added_date = added_date if added_date is not None else resolved_asset_date
        self.downloaded_versions: list[str] = []
        self.deleted = False
        self._asset_record = asset_record or {"fields": {"assetDate": {"value": 0}}}
        self._payloads = payloads or {}
        self.resources = resources or {
            "original": PhotoResource(
                key="original",
                filename=filename,
                url=f"https://example.com/{asset_id}/original",
                size=len(f"{asset_id}:original".encode()),
                type="public.jpeg",
                checksum=f"checksum-{asset_id}",
            )
        }

    def download(self, version: str = "original", **kwargs) -> bytes:
        _ = kwargs
        self.downloaded_versions.append(version)
        return self._payloads.get(version, f"{self.id}:{version}".encode())

    def delete(self) -> bool:
        self.deleted = True
        return True


def test_sqlite_photo_sync_state_round_trip() -> None:
    """The SQLite sync state should persist manifest rows and sync cursors."""

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-state-", dir=TEST_BASE))
    try:
        db_path = temp_dir / "photos-sync.sqlite3"
        with SQLitePhotoSyncState(db_path) as state:
            state.set_sync_cursor("cursor-1")
            state.upsert_resource(
                resource=SimpleNamespace(
                    asset_id="asset-1",
                    resource_key="original",
                    relative_path="2026/04/photo.jpg",
                    size=42,
                    checksum="checksum-1",
                    downloaded_at="2026-04-01T00:00:00+00:00",
                )
            )
            row = state.get_resource("asset-1", "original")

        assert row is not None
        assert row.relative_path == "2026/04/photo.jpg"
        assert row.checksum == "checksum-1"

        with SQLitePhotoSyncState(db_path) as state:
            assert state.get_sync_cursor() == "cursor-1"
            assert state.resource_count() == 1
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_create_photo_sync_state_selects_expected_backend() -> None:
    """Sync runs should choose ephemeral or SQLite state through the factory."""

    db_path = TEST_BASE / "photos-sync-factory.sqlite3"

    assert isinstance(create_photo_sync_state(db_path), SQLitePhotoSyncState)
    assert isinstance(
        create_photo_sync_state(db_path, ephemeral=True),
        MemoryPhotoSyncState,
    )


def test_run_photo_sync_downloads_and_persists_manifest() -> None:
    """A sync run should write files, manifest entries, and the latest cursor."""

    asset = DummyAsset("asset-1", "photo.jpg")
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-1")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-run-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )

        assert result.downloaded_count == 1
        assert (output_dir / "photo.jpg").read_bytes() == b"asset-1:original"
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            manifest = state.get_resource("asset-1", "original")
            assert manifest is not None
            assert manifest.relative_path == "photo.jpg"
            assert state.get_sync_cursor() == "cursor-1"
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_sanitizes_remote_filenames() -> None:
    """Remote filenames must not escape the configured output directory."""

    asset = DummyAsset("asset-escape", "../escape.jpg")
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-escape")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-escape-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )

        assert result.downloaded_count == 1
        assert (output_dir / "escape.jpg").read_bytes() == b"asset-escape:original"
        assert not (temp_dir / "escape.jpg").exists()
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            manifest = state.get_resource("asset-escape", "original")
            assert manifest is not None
            assert manifest.relative_path == "escape.jpg"
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_sanitizes_folder_structure_paths() -> None:
    """Folder structure output should stay under the configured directory."""

    asset = DummyAsset(
        "asset-folder-escape",
        "photo.jpg",
        asset_date=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )
    service = DummyService(
        DummyAlbum("All Photos", [asset]), cursor="cursor-folder-escape"
    )

    temp_dir = Path(
        tempfile.mkdtemp(prefix="photos-sync-folder-escape-", dir=TEST_BASE)
    )
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                folder_structure="../{:%Y}/../../nested",
            ),
        )

        assert result.downloaded_count == 1
        assert (output_dir / "2026" / "nested" / "photo.jpg").exists()
        assert not (temp_dir / "nested" / "photo.jpg").exists()
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            manifest = state.get_resource("asset-folder-escape", "original")
            assert manifest is not None
            assert manifest.relative_path == "2026/nested/photo.jpg"
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_auto_delete_removes_stale_files() -> None:
    """Auto-delete should remove previously tracked files absent from the latest run."""

    first_service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-1", "old.jpg")]),
        cursor="cursor-1",
    )
    second_service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-2", "new.jpg")]),
        cursor="cursor-2",
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-delete-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        run_photo_sync(
            first_service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )
        result = run_photo_sync(
            second_service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                auto_delete=True,
            ),
        )

        assert result.deleted_count == 1
        assert not (output_dir / "old.jpg").exists()
        assert (output_dir / "new.jpg").exists()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_auto_delete_ignores_unsafe_stale_paths() -> None:
    """Auto-delete must not remove paths outside the configured output directory."""

    service = DummyService(DummyAlbum("All Photos", []), cursor="cursor-clean")

    temp_dir = Path(
        tempfile.mkdtemp(prefix="photos-sync-unsafe-delete-", dir=TEST_BASE)
    )
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        output_dir.mkdir()
        outside_path = temp_dir / "outside.jpg"
        outside_path.write_bytes(b"keep")

        options = PhotoSyncOptions(directory=output_dir, state_dir=state_dir)
        with SQLitePhotoSyncState(options.state_path()) as state:
            state.upsert_resource(
                SyncedPhotoResource(
                    asset_id="asset-stale",
                    resource_key="original",
                    relative_path="../outside.jpg",
                    size=None,
                    checksum=None,
                    downloaded_at="2026-04-21T00:00:00+00:00",
                )
            )

        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                auto_delete=True,
            ),
        )

        assert outside_path.read_bytes() == b"keep"
        assert result.deleted_count == 0
        assert result.skipped_count == 1
        assert result.items[0].reason == "unsafe-path"
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            assert state.get_resource("asset-stale", "original") is None
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_skips_resource_when_safe_target_path_rejects() -> None:
    """Unexpected path validation failures should skip the resource and continue."""

    service = DummyService(
        DummyAlbum(
            "All Photos",
            [
                DummyAsset("asset-unsafe", "unsafe.jpg"),
                DummyAsset("asset-safe", "safe.jpg"),
            ],
        ),
        cursor="cursor-path-validation",
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-path-reject-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        original_safe_target_path = sync_module._safe_target_path

        def reject_one_path(directory: Path, relative_path: str) -> Path:
            if relative_path == "unsafe.jpg":
                raise PhotosServiceException("unsafe test path")
            return original_safe_target_path(directory, relative_path)

        with patch(
            "pyicloud.services.photos_cloudkit.sync._safe_target_path",
            side_effect=reject_one_path,
        ):
            result = run_photo_sync(
                service,
                PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
            )

        assert result.skipped_count == 1
        assert result.downloaded_count == 1
        assert any(item.reason == "unsafe-path" for item in result.items)
        assert not (output_dir / "unsafe.jpg").exists()
        assert (output_dir / "safe.jpg").exists()
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            assert state.get_sync_cursor() is None
            assert state.get_resource("asset-unsafe", "original") is None
            assert state.get_resource("asset-safe", "original") is not None
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_auto_delete_continues_when_unlink_fails() -> None:
    """Auto-delete should skip locked files without corrupting sync state."""

    first_service = DummyService(
        DummyAlbum(
            "All Photos",
            [
                DummyAsset("asset-old-1", "old-1.jpg"),
                DummyAsset("asset-old-2", "old-2.jpg"),
            ],
        ),
        cursor="cursor-1",
    )
    second_service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-new", "new.jpg")]),
        cursor="cursor-2",
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-delete-error-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        run_photo_sync(
            first_service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )

        original_unlink = Path.unlink

        def flaky_unlink(path_obj: Path, *args, **kwargs) -> None:
            if path_obj.name == "old-1.jpg":
                raise OSError("locked")
            return original_unlink(path_obj, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=flaky_unlink):
            result = run_photo_sync(
                second_service,
                PhotoSyncOptions(
                    directory=output_dir,
                    state_dir=state_dir,
                    auto_delete=True,
                ),
            )

        assert result.deleted_count == 1
        assert (output_dir / "old-1.jpg").exists()
        assert not (output_dir / "old-2.jpg").exists()
        with SQLitePhotoSyncState(Path(result.state_path)) as state:
            assert state.get_resource("asset-old-1", "original") is not None
            assert state.get_resource("asset-old-2", "original") is None
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_dry_run_does_not_create_state() -> None:
    """Preview-only sync runs should avoid creating output directories or state."""

    service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-1", "preview.jpg")]),
        cursor="cursor-preview",
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-preview-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir, dry_run=True),
        )

        assert result.listed_count == 1
        assert not output_dir.exists()
        assert not (output_dir / "preview.jpg").exists()
        assert not Path(result.state_path).exists()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_only_print_does_not_create_output_or_state() -> None:
    """Filename-only previews should leave the filesystem untouched."""

    service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-1", "preview.jpg")]),
        cursor="cursor-print-preview",
    )

    temp_dir = Path(
        tempfile.mkdtemp(prefix="photos-sync-print-preview-", dir=TEST_BASE)
    )
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                only_print_filenames=True,
            ),
        )

        assert result.listed_count == 1
        assert not output_dir.exists()
        assert not Path(result.state_path).exists()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_rejects_remote_delete_with_until_found() -> None:
    """Remote deletion must not combine with partial until-found scans."""

    service = DummyService(
        DummyAlbum("All Photos", [DummyAsset("asset-1", "photo.jpg")]),
        cursor="cursor-until-delete",
    )

    with pytest.raises(
        PhotosServiceException,
        match="--keep-icloud-recent-days cannot be combined with --until-found",
    ):
        run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=Path("/tmp/unused"),
                keep_icloud_recent_days=0,
                until_found=1,
            ),
        )


def test_run_photo_sync_live_photos_respect_video_flags() -> None:
    """Live photo sync should fetch both resources unless video downloads are skipped."""

    live_asset = DummyAsset(
        "asset-live",
        "live.jpg",
        is_live_photo=True,
        resources={
            "original": PhotoResource(
                key="original",
                filename="live.jpg",
                url="https://example.com/live/original",
                size=10,
                type="public.jpeg",
            ),
            "original_video": PhotoResource(
                key="original_video",
                filename="live.mov",
                url="https://example.com/live/video",
                size=20,
                type="com.apple.quicktime-movie",
            ),
        },
    )
    service = DummyService(DummyAlbum("All Photos", [live_asset]), cursor="cursor-live")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-live-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        first = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )
        second = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=Path(temp_dir) / "skip-output",
                state_dir=Path(temp_dir) / "skip-state",
                skip_videos=True,
            ),
        )

        assert first.downloaded_count == 2
        assert any(item.path.endswith("live.mov") for item in first.items)
        assert second.downloaded_count == 1
        assert all(not item.path.endswith("live.mov") for item in second.items)
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_watch_photo_sync_repeats_runs_and_sleeps_between_iterations() -> None:
    """Watch mode should rerun sync and sleep only between completed iterations."""

    asset = DummyAsset("asset-1", "watch.jpg")
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-watch")
    slept: list[float] = []

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-watch-run-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        results = list(
            watch_photo_sync(
                service,
                PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
                interval_seconds=7,
                iterations=2,
                sleep_fn=slept.append,
            )
        )

        assert len(results) == 2
        assert results[0].downloaded_count == 1
        assert results[0].short_circuited is False
        assert results[1].downloaded_count == 0
        assert results[1].short_circuited is True
        assert slept == [7]
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_short_circuit_validates_tracked_file_size() -> None:
    """A matching cursor should not hide a truncated tracked local file."""

    payload = b"complete-image-bytes"
    asset = DummyAsset(
        "asset-sized",
        "photo.jpg",
        payloads={"original": payload},
        resources={
            "original": PhotoResource(
                key="original",
                filename="photo.jpg",
                url="https://example.com/sized/original",
                size=len(payload),
                type="public.jpeg",
            )
        },
    )
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-sized")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-sized-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        first = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )
        (output_dir / "photo.jpg").write_bytes(b"bad")
        second = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )

        assert first.downloaded_count == 1
        assert second.short_circuited is False
        assert second.downloaded_count == 1
        assert (output_dir / "photo.jpg").read_bytes() == payload
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_align_raw_prefers_requested_representation() -> None:
    """RAW alignment should swap original and alternative resources when requested."""

    raw_asset = DummyAsset(
        "asset-raw",
        "IMG_0001.JPG",
        resources={
            "original": PhotoResource(
                key="original",
                filename="IMG_0001.JPG",
                url="https://example.com/raw/jpeg",
                size=10,
                type="public.jpeg",
            ),
            "alternative": PhotoResource(
                key="alternative",
                filename="IMG_0001.CR2",
                url="https://example.com/raw/cr2",
                size=11,
                type="com.canon.cr2-raw-image",
            ),
        },
    )
    service = DummyService(DummyAlbum("All Photos", [raw_asset]), cursor="cursor-raw")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-raw-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        default_result = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir),
        )
        raw_result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=temp_dir / "raw-output",
                state_dir=temp_dir / "raw-state",
                align_raw="original",
            ),
        )

        assert default_result.downloaded_count == 1
        assert raw_result.downloaded_count == 1
        assert (output_dir / "IMG_0001.JPG").exists()
        assert (temp_dir / "raw-output" / "IMG_0001.CR2").exists()
        assert raw_asset.downloaded_versions == ["original", "alternative"]
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_writes_xmp_sidecar() -> None:
    """XMP sidecar export should write metadata next to downloaded photo files."""

    asset = DummyAsset(
        "asset-xmp",
        "photo.jpg",
        asset_record={
            "fields": {
                "captionEnc": {"value": "VGl0bGUgSGVyZQ=="},
                "assetDate": {"value": 1711929600000},
                "isFavorite": {"value": 1},
            }
        },
    )
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-xmp")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-xmp-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                xmp_sidecar=True,
            ),
        )

        sidecar_path = output_dir / "photo.jpg.xmp"
        assert sidecar_path.exists()
        xml_text = sidecar_path.read_text(encoding="utf-8")
        assert "Title Here" in xml_text
        assert "pyicloud photos-cloudkit" in xml_text
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_sets_exif_datetime_for_jpegs_without_exif() -> None:
    """EXIF datetime export should populate empty JPEG timestamps."""

    asset = DummyAsset(
        "asset-exif",
        "photo.jpg",
        asset_date=datetime(2026, 4, 21, tzinfo=timezone.utc),
        payloads={"original": MINIMAL_JPEG},
    )
    service = DummyService(DummyAlbum("All Photos", [asset]), cursor="cursor-exif")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-exif-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                set_exif_datetime=True,
            ),
        )

        downloaded = output_dir / "photo.jpg"
        contents = downloaded.read_bytes()
        assert b"Exif\x00\x00" in contents
        assert b"DateTimeOriginal" not in contents
        assert b"2026:" in contents
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_set_exif_datetime_preserves_asset_wall_clock_timezone() -> None:
    """EXIF insertion should not convert asset timestamps to the local timezone."""

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-exif-tz-", dir=TEST_BASE))
    try:
        path = temp_dir / "photo.jpg"
        path.write_bytes(MINIMAL_JPEG)
        taken_at = datetime(
            2026,
            1,
            1,
            23,
            30,
            tzinfo=timezone(timedelta(hours=-5)),
        )

        materialize_module.set_exif_datetime_if_missing(path, taken_at)

        assert b"2026:01:01 23:30:00" in path.read_bytes()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_jpeg_has_exif_datetime_reads_big_endian_ifd_offsets() -> None:
    """Big-endian EXIF payloads should be inspected without inserting duplicates."""

    timestamp = b"2026:04:21 12:34:56\x00"
    tiff = (
        b"MM"
        + struct.pack(">H", 42)
        + struct.pack(">I", 8)
        + struct.pack(">H", 1)
        + struct.pack(">HHII", 0x0132, 2, len(timestamp), 26)
        + struct.pack(">I", 0)
        + timestamp
    )
    payload = b"Exif\x00\x00" + tiff
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    jpeg_bytes = b"\xff\xd8" + segment + b"\xff\xd9"

    assert materialize_module._jpeg_has_exif_datetime(jpeg_bytes) is True


def test_apply_local_metadata_skips_mutations_for_preview_modes() -> None:
    """Dry-run and filename-only previews must not mutate existing local files."""

    asset = DummyAsset("asset-preview", "photo.jpg")
    for options in (
        PhotoSyncOptions(
            directory=Path("/tmp"),
            dry_run=True,
            set_exif_datetime=True,
            xmp_sidecar=True,
        ),
        PhotoSyncOptions(
            directory=Path("/tmp"),
            only_print_filenames=True,
            set_exif_datetime=True,
            xmp_sidecar=True,
        ),
    ):
        with (
            patch(
                "pyicloud.services.photos_cloudkit.sync.set_exif_datetime_if_missing"
            ) as set_exif,
            patch("pyicloud.services.photos_cloudkit.sync.write_xmp_sidecar") as xmp,
        ):
            sync_module._apply_local_metadata(
                asset=asset,
                resource=asset.resources["original"],
                resource_key="original",
                target_path=Path("/tmp/photo.jpg"),
                options=options,
            )

        set_exif.assert_not_called()
        xmp.assert_not_called()


def test_run_photo_sync_keep_icloud_recent_days_deletes_old_remote_assets() -> None:
    """Old assets should be deleted remotely once they are confirmed locally."""

    old_asset = DummyAsset("asset-old", "old.jpg", added_days_ago=10)
    service = DummyService(DummyAlbum("All Photos", [old_asset]), cursor="cursor-keep")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-keep-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                keep_icloud_recent_days=0,
            ),
        )

        assert old_asset.deleted is True
        assert result.deleted_count == 1
        assert any(item.reason == "keep-icloud-recent-days" for item in result.items)
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_recent_uses_asset_date_when_added_date_missing() -> None:
    """Recent filtering should fall back to asset_date when added_date is missing."""

    recent_asset = DummyAsset(
        "asset-recent",
        "recent.jpg",
        asset_date=datetime.now(timezone.utc) - timedelta(hours=1),
        added_date=None,
    )
    old_asset = DummyAsset(
        "asset-old",
        "old.jpg",
        asset_date=datetime.now(timezone.utc) - timedelta(days=10),
        added_date=None,
    )
    recent_asset.added_date = None
    old_asset.added_date = None
    service = DummyService(
        DummyAlbum("All Photos", [recent_asset, old_asset]),
        cursor="cursor-recent-fallback",
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-recent-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(directory=output_dir, state_dir=state_dir, recent=1),
        )

        assert result.downloaded_count == 1
        assert (output_dir / "recent.jpg").exists()
        assert not (output_dir / "old.jpg").exists()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


def test_run_photo_sync_keep_icloud_recent_days_skips_assets_without_asset_date() -> (
    None
):
    """Remote deletion should not run when asset_date is missing."""

    undated_asset = DummyAsset("asset-undated", "undated.jpg")
    undated_asset.asset_date = None
    service = DummyService(DummyAlbum("All Photos", [undated_asset]), cursor="cursor")

    temp_dir = Path(tempfile.mkdtemp(prefix="photos-sync-undated-", dir=TEST_BASE))
    try:
        output_dir = temp_dir / "output"
        state_dir = temp_dir / "state"
        result = run_photo_sync(
            service,
            PhotoSyncOptions(
                directory=output_dir,
                state_dir=state_dir,
                keep_icloud_recent_days=0,
            ),
        )

        assert undated_asset.deleted is False
        assert result.deleted_count == 0
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()
