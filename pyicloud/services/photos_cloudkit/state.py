"""Persistent sync state for the modern Photos service."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Iterator, Protocol, runtime_checkable


@dataclass(slots=True)
class SyncedPhotoResource:
    """One locally materialized resource tracked by the sync engine."""

    asset_id: str
    resource_key: str
    relative_path: str
    size: int | None = None
    checksum: str | None = None
    downloaded_at: str | None = None


@runtime_checkable
class PhotoSyncState(Protocol):
    """Backend interface for persisted or ephemeral photo sync state."""

    def __enter__(self) -> "PhotoSyncState":
        """Open the backend and return the active state object."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the backend if needed."""

    def get_sync_cursor(self) -> str | None:
        """Return the last successful sync cursor for this target."""

    def set_sync_cursor(self, value: str | None) -> None:
        """Persist the last successful sync cursor for this target."""

    def get_resource(
        self, asset_id: str, resource_key: str
    ) -> SyncedPhotoResource | None:
        """Look up one tracked resource."""

    def upsert_resource(self, resource: SyncedPhotoResource) -> None:
        """Insert or replace one tracked resource."""

    def delete_resource(self, asset_id: str, resource_key: str) -> None:
        """Delete one tracked resource from the manifest."""

    def iter_resources(self) -> Iterator[SyncedPhotoResource]:
        """Iterate all tracked resources."""

    def resource_count(self) -> int:
        """Return the number of tracked resources."""


class SQLitePhotoSyncState:
    """SQLite-backed manifest and sync-token store for a photo sync target."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "SQLitePhotoSyncState":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        """Open the SQLite database and initialize the schema."""

        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS synced_resources (
                asset_id TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size INTEGER,
                checksum TEXT,
                downloaded_at TEXT,
                PRIMARY KEY (asset_id, resource_key)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection if it is open."""

        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the active SQLite connection."""

        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def get_sync_cursor(self) -> str | None:
        """Return the last successful sync cursor for this target."""

        row = self.conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?",
            ("sync_cursor",),
        ).fetchone()
        return None if row is None else row["value"]

    def set_sync_cursor(self, value: str | None) -> None:
        """Persist the last successful sync cursor for this target."""

        if value is None:
            self.conn.execute("DELETE FROM sync_meta WHERE key = ?", ("sync_cursor",))
        else:
            self.conn.execute(
                """
                INSERT INTO sync_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("sync_cursor", value),
            )
        self.conn.commit()

    def get_resource(
        self, asset_id: str, resource_key: str
    ) -> SyncedPhotoResource | None:
        """Look up a previously synced resource by asset/version key."""

        row = self.conn.execute(
            """
            SELECT asset_id, resource_key, relative_path, size, checksum, downloaded_at
            FROM synced_resources
            WHERE asset_id = ? AND resource_key = ?
            """,
            (asset_id, resource_key),
        ).fetchone()
        if row is None:
            return None
        return SyncedPhotoResource(
            asset_id=row["asset_id"],
            resource_key=row["resource_key"],
            relative_path=row["relative_path"],
            size=row["size"],
            checksum=row["checksum"],
            downloaded_at=row["downloaded_at"],
        )

    def upsert_resource(self, resource: SyncedPhotoResource) -> None:
        """Persist the latest known local state for one synced resource."""

        self.conn.execute(
            """
            INSERT INTO synced_resources(
                asset_id, resource_key, relative_path, size, checksum, downloaded_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, resource_key) DO UPDATE SET
                relative_path = excluded.relative_path,
                size = excluded.size,
                checksum = excluded.checksum,
                downloaded_at = excluded.downloaded_at
            """,
            (
                resource.asset_id,
                resource.resource_key,
                resource.relative_path,
                resource.size,
                resource.checksum,
                resource.downloaded_at,
            ),
        )
        self.conn.commit()

    def delete_resource(self, asset_id: str, resource_key: str) -> None:
        """Forget one synced resource from the manifest."""

        self.conn.execute(
            """
            DELETE FROM synced_resources
            WHERE asset_id = ? AND resource_key = ?
            """,
            (asset_id, resource_key),
        )
        self.conn.commit()

    def iter_resources(self) -> Iterator[SyncedPhotoResource]:
        """Iterate all tracked resources for this sync target."""

        rows = self.conn.execute(
            """
            SELECT asset_id, resource_key, relative_path, size, checksum, downloaded_at
            FROM synced_resources
            ORDER BY relative_path
            """
        )
        for row in rows:
            yield SyncedPhotoResource(
                asset_id=row["asset_id"],
                resource_key=row["resource_key"],
                relative_path=row["relative_path"],
                size=row["size"],
                checksum=row["checksum"],
                downloaded_at=row["downloaded_at"],
            )

    def resource_count(self) -> int:
        """Return the number of tracked resources in the manifest."""

        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM synced_resources"
        ).fetchone()
        return 0 if row is None else int(row["count"])


class MemoryPhotoSyncState:
    """Ephemeral manifest used for preview-only sync runs."""

    def __init__(self) -> None:
        self._cursor: str | None = None
        self._resources: dict[tuple[str, str], SyncedPhotoResource] = {}

    def __enter__(self) -> "MemoryPhotoSyncState":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_sync_cursor(self) -> str | None:
        """Return the stored preview cursor, if any."""

        return self._cursor

    def set_sync_cursor(self, value: str | None) -> None:
        """Store a preview cursor in memory."""

        self._cursor = value

    def get_resource(
        self, asset_id: str, resource_key: str
    ) -> SyncedPhotoResource | None:
        """Look up a preview resource row."""

        return self._resources.get((asset_id, resource_key))

    def upsert_resource(self, resource: SyncedPhotoResource) -> None:
        """Store a preview resource row."""

        self._resources[(resource.asset_id, resource.resource_key)] = resource

    def delete_resource(self, asset_id: str, resource_key: str) -> None:
        """Delete a preview resource row."""

        self._resources.pop((asset_id, resource_key), None)

    def iter_resources(self) -> Iterator[SyncedPhotoResource]:
        """Iterate preview resource rows."""

        yield from self._resources.values()

    def resource_count(self) -> int:
        """Return the number of preview manifest rows."""

        return len(self._resources)


def create_photo_sync_state(
    db_path: Path,
    *,
    ephemeral: bool = False,
) -> PhotoSyncState:
    """Return the appropriate sync-state backend for one sync target."""

    if ephemeral:
        return MemoryPhotoSyncState()
    return SQLitePhotoSyncState(db_path)
