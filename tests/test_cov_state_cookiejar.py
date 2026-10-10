"""Branch-coverage tests for photo sync state and the cookie jar (issue #394)."""

# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.services.photos_cloudkit.state import (
    MemoryPhotoSyncState,
    SQLitePhotoSyncState,
    SyncedPhotoResource,
)


@pytest.fixture(scope="module")
def state_base() -> Path:
    """A writable base directory permitted by the filesystem guard."""
    base = Path(tempfile.gettempdir()) / "python-test-results"
    base.mkdir(parents=True, exist_ok=True)
    return base


def test_sqlite_state_open_is_idempotent_and_conn_lazy(state_base: Path) -> None:
    """open() is idempotent, conn auto-opens, and close() is safe when unopened."""

    db_path = state_base / "cov-state-lazy.sqlite3"
    state = SQLitePhotoSyncState(db_path)
    try:
        # conn property opens lazily (covers the None branch at line 126).
        assert state.conn is not None
        state.open()  # Already open -> early return (line 87).
        assert state.conn is not None

        state.close()
        assert state._conn is None
        state.close()  # Already closed -> early return (line 117).
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            candidate.unlink(missing_ok=True)


def test_sqlite_state_clears_cursor_with_none(state_base: Path) -> None:
    """Setting the sync cursor to None deletes the stored row."""

    db_path = state_base / "cov-state-cursor.sqlite3"
    state = SQLitePhotoSyncState(db_path)
    try:
        with state:
            state.set_sync_cursor("cursor-1")
            assert state.get_sync_cursor() == "cursor-1"
            state.set_sync_cursor(None)
            assert state.get_sync_cursor() is None
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            candidate.unlink(missing_ok=True)


def test_memory_state_full_manifest_round_trip() -> None:
    """The ephemeral state backend stores resources, cursors, and counts."""

    resource = SyncedPhotoResource(
        asset_id="asset-1",
        resource_key="original",
        relative_path="2026/04/photo.jpg",
        size=3,
        checksum="checksum-1",
        downloaded_at="2026-04-01T00:00:00+00:00",
    )
    state = MemoryPhotoSyncState()
    with state as active:
        assert active is state
        assert state.get_sync_cursor() is None
        assert state.get_resource("asset-1", "original") is None

        state.set_sync_cursor("cursor-1")
        assert state.get_sync_cursor() == "cursor-1"

        state.upsert_resource(resource)
        assert state.get_resource("asset-1", "original") is resource
        assert list(state.iter_resources()) == [resource]
        assert state.resource_count() == 1

        state.delete_resource("asset-1", "original")
        assert state.get_resource("asset-1", "original") is None
        assert state.resource_count() == 0

        # Deleting a missing row is a no-op.
        state.delete_resource("missing", "original")


def test_cookie_jar_save_without_filename_is_noop() -> None:
    """save() returns immediately when no filename is bound."""

    jar = PyiCloudCookieJar()
    jar.save()


def test_cookie_jar_save_swallows_runtime_error() -> None:
    """A RuntimeError while copying the jar is swallowed during save."""

    jar = PyiCloudCookieJar(filename="unused-cookies.txt")
    with patch.object(jar, "copy", side_effect=RuntimeError("race")):
        jar.save()
