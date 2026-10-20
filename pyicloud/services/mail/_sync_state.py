"""Private transactional metadata inventory; no file-opening or Mail writes.

The caller owns the SQLite connection, its lifetime, and private backing-file
permissions. Only identifiers, numeric flags, and checkpoints are stored here;
message bodies, headers, signed links, and credentials are not accepted.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from contextlib import contextmanager
import sqlite3

from pydantic import ValidationError

from pyicloud.services.mail._sync import (
    UID,
    Identity,
    Number,
    PositiveNumber,
    SyncCheckpoint,
    SyncModel,
    SyncResetRequired,
)


class InventoryMessage(SyncModel):
    """Minimal folder-local metadata, not a full message or archival backup."""

    uid: UID
    message_id: Identity
    thread_id: Identity | None = None
    flags: Number | None = None


class StoredCheckpoint(SyncModel):
    """Local revision plus remote counters, used for optimistic concurrency."""

    revision: PositiveNumber
    checkpoint: SyncCheckpoint


class StateConflict(SyncResetRequired):
    """Another writer changed the inventory after the caller read its revision."""


class SQLiteMailInventory:
    """One mailbox's private inventory on a dedicated, caller-owned connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        with self._transaction():
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS mail_sync_checkpoint ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                "version INTEGER NOT NULL, revision INTEGER NOT NULL, "
                "data TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS mail_sync_messages ("
                "uid INTEGER PRIMARY KEY, message_id TEXT UNIQUE NOT NULL, "
                "thread_id TEXT, flags INTEGER)"
            )

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        if self.connection.in_transaction:
            raise ValueError("Mail inventory requires an idle dedicated connection")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def read_checkpoint(self) -> StoredCheckpoint | None:
        """Read a validated local revision without changing or advancing it."""
        row = self.connection.execute(
            "SELECT version, revision, data FROM mail_sync_checkpoint "
            "WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return None
        if row[0] != 1:
            raise SyncResetRequired("Unsupported Mail inventory schema version")
        try:
            return StoredCheckpoint(
                revision=row[1], checkpoint=SyncCheckpoint.model_validate_json(row[2])
            )
        except (ValueError, TypeError):
            raise SyncResetRequired("Invalid stored Mail checkpoint") from None

    def read_messages(self) -> tuple[InventoryMessage, ...]:
        """Return minimal records in UID order; no bodies or capabilities exist."""
        rows = self.connection.execute(
            "SELECT uid, message_id, thread_id, flags FROM mail_sync_messages "
            "ORDER BY uid"
        )
        try:
            return tuple(
                InventoryMessage(
                    uid=row[0], message_id=row[1], thread_id=row[2], flags=row[3]
                )
                for row in rows
            )
        except ValidationError:
            raise SyncResetRequired("Invalid stored Mail inventory record") from None

    def read_snapshot(
        self,
    ) -> tuple[StoredCheckpoint | None, tuple[InventoryMessage, ...]]:
        """Read cursor and records in one transaction, not two racing snapshots."""
        with self._transaction():
            return self.read_checkpoint(), self.read_messages()

    def _expect(self, expected: StoredCheckpoint | None) -> StoredCheckpoint | None:
        actual = self.read_checkpoint()
        if actual != expected:
            raise StateConflict("Mail inventory changed during reconciliation")
        return actual

    def confirm_checkpoint(self, expected: StoredCheckpoint) -> StoredCheckpoint:
        """Confirm a no-op poll without rewriting records or advancing revision."""
        with self._transaction():
            self._expect(expected)
        return expected

    def replace_inventory(
        self,
        expected: StoredCheckpoint | None,
        checkpoint: SyncCheckpoint,
        messages: Iterable[InventoryMessage],
    ) -> StoredCheckpoint:
        """Commit a fully reconciled inventory and cursor atomically, or neither.

        A deliberate baseline replacement may refresh the path or UID validity,
        but cannot silently switch the database to a different account/mailbox.
        The revision prevents stale writers and equal-counter ABA overwrites.
        """
        with self._transaction():
            actual = self._expect(expected)
            if actual is not None and (
                actual.checkpoint.account != checkpoint.account
                or actual.checkpoint.mailbox_id != checkpoint.mailbox_id
            ):
                raise SyncResetRequired(
                    "Mail inventory belongs to another account/mailbox"
                )
            if actual is not None and (
                actual.checkpoint.uid_validity == checkpoint.uid_validity
                and (
                    checkpoint.modseq < actual.checkpoint.modseq
                    or checkpoint.threadmodseq < actual.checkpoint.threadmodseq
                )
            ):
                raise SyncResetRequired(
                    "Mail baseline counters regressed in the same namespace"
                )
            result = StoredCheckpoint(
                revision=1 if actual is None else actual.revision + 1,
                checkpoint=checkpoint,
            )
            self.connection.execute("DELETE FROM mail_sync_messages")
            try:
                for message in messages:
                    self.connection.execute(
                        "INSERT INTO mail_sync_messages"
                        "(uid, message_id, thread_id, flags) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            message.uid,
                            message.message_id,
                            message.thread_id,
                            message.flags,
                        ),
                    )
            except sqlite3.IntegrityError:
                raise SyncResetRequired(
                    "Mail inventory contains conflicting identities"
                ) from None
            self.connection.execute(
                "INSERT INTO mail_sync_checkpoint(singleton, version, revision, data) "
                "VALUES (1, 1, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
                "version=excluded.version, revision=excluded.revision, "
                "data=excluded.data",
                (result.revision, checkpoint.model_dump_json()),
            )
        return result
