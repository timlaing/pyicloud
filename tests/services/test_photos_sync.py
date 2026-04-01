"""Tests for the Photos sync engine and state backend."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from pyicloud.services.photos import PhotoResource, PhotoSyncOptions, run_photo_sync
from pyicloud.services.photos_cloudkit.state import SQLitePhotoSyncState

TEST_BASE = Path(tempfile.gettempdir()) / "python-test-results"
TEST_BASE.mkdir(parents=True, exist_ok=True)


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
        resources: Optional[dict[str, PhotoResource]] = None,
    ) -> None:
        self.id = asset_id
        self.filename = filename
        self.item_type = item_type
        self.is_live_photo = is_live_photo
        self.asset_date = datetime.now(timezone.utc) - timedelta(days=added_days_ago)
        self.added_date = self.asset_date
        self.downloaded_versions: list[str] = []
        self.resources = resources or {
            "original": PhotoResource(
                key="original",
                filename=filename,
                url=f"https://example.com/{asset_id}/original",
                size=32,
                type="public.jpeg",
                checksum=f"checksum-{asset_id}",
            )
        }

    def download(self, version: str = "original", **kwargs) -> bytes:
        _ = kwargs
        self.downloaded_versions.append(version)
        return f"{self.id}:{version}".encode()


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


def test_run_photo_sync_dry_run_does_not_create_state() -> None:
    """Preview-only sync runs should avoid creating a new SQLite state file."""

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
        assert not (output_dir / "preview.jpg").exists()
        assert not Path(result.state_path).exists()
    finally:
        for path in sorted(temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temp_dir.rmdir()


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
