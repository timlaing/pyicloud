"""Private POSIX-only state opening; no default path or public sync API.

Use a dedicated owner-only directory beneath trusted canonical ancestors.
Other users cannot replace the database or SQLite journal. Same-user malicious
processes, ACL overrides, network filesystems, and encryption at rest are outside
this boundary. Never repair permissions or delete suspicious existing files.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import sqlite3
import stat

from pyicloud.services.mail._sync_state import SQLiteMailInventory
from pyicloud.services.mail.exceptions import SyncStorageBusy, UnsafeSyncStorage

__all__ = ["SyncStorageBusy", "UnsafeSyncStorage"]

_APPLICATION_ID = 0x504D5349
INVENTORY_FILENAME = "inventory.sqlite3"
_FILES = {INVENTORY_FILENAME, f"{INVENTORY_FILENAME}-journal", "inventory.lock"}


def _trusted_parent(parent: Path) -> None:
    for ancestor in (parent, *parent.parents):
        info = ancestor.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, os.getuid())
            or (info.st_mode & 0o022 and not sticky_root)
        ):
            raise UnsafeSyncStorage("Mail state requires trusted parent directories")


def _private(info: os.stat_result, *, directory: bool = False) -> None:
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = 0o700 if directory else 0o600
    if (
        not kind
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != mode
        or (not directory and info.st_nlink != 1)
    ):
        raise UnsafeSyncStorage("Mail state requires private, owner-only storage")


def _private_file(path: Path) -> int:
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        _private(os.fstat(descriptor))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _database(path: Path, timeout: float) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=timeout)
    try:
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        tables = connection.execute("SELECT name FROM sqlite_master").fetchall()
        if application != _APPLICATION_ID and (application != 0 or tables):
            raise UnsafeSyncStorage("Mail state is not a recognized inventory database")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise UnsafeSyncStorage("Mail state requires rollback-journal storage")
        connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def open_inventory(
    directory: Path, *, timeout: float = 5.0
) -> Generator[SQLiteMailInventory, None, None]:
    """Open one private mailbox store, or fail without resetting existing state.

    The parent must already exist; its canonical path is used deliberately.
    The final directory and fixed filenames must not be symlinks. All workers
    using this entry point hold a non-blocking advisory lock for the full run.
    SQLite lock waits are finite; DELETE journaling keeps recovery files inside
    the private directory. No process-wide umask or permissions are changed.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise UnsafeSyncStorage("Private Mail state currently requires POSIX storage")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 60
    ):
        raise ValueError("Mail state timeout must be greater than zero and at most 60")
    # Import only after the platform check so other platforms can import Mail.
    import fcntl  # pylint: disable=import-outside-toplevel

    if directory.name in ("", ".", ".."):
        raise UnsafeSyncStorage("Mail state requires a dedicated named directory")
    parent = directory.parent.resolve(strict=True)
    _trusted_parent(parent)
    directory = parent / directory.name
    with suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    _private(directory.lstat(), directory=True)
    lock = _private_file(directory / "inventory.lock")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncStorageBusy("Mail state is in use; retry later") from None
        for child in directory.iterdir():
            if child.name not in _FILES:
                raise UnsafeSyncStorage(
                    "Mail state directory contains unexpected files"
                )
            _private(child.lstat())
        descriptor = _private_file(directory / INVENTORY_FILENAME)
        os.close(descriptor)
        connection = _database(directory / INVENTORY_FILENAME, timeout)
        try:
            yield SQLiteMailInventory(connection)
        finally:
            connection.close()
    finally:
        os.close(lock)
