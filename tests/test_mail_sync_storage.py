"""Offline private storage, restart, locking, and hostile local-path tests."""

from collections.abc import Iterator
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.services.mail import _sync_storage as storage
from pyicloud.services.mail._sync_storage import (
    _database,
    _private_file,
    _trusted_parent,
)
from tests.test_mail_sync_inventory import checkpoint, message

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only private storage")
_TEST_UID = getattr(os, "getuid", lambda: 0)()


@pytest.fixture(name="directory")
def directory_fixture() -> Iterator[Path]:
    """Confine real SQLite and absolute cleanup paths to the test sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as temporary:
        parent = Path(temporary)
        try:
            yield parent / "state"
        finally:
            for child in parent.iterdir():
                if child.is_dir() and not child.is_symlink():
                    for item in child.iterdir():
                        item.unlink()
                    child.rmdir()
                else:
                    child.unlink()


def test_private_storage_restart_and_connection_close(directory: Path) -> None:
    """Persist minimal metadata and close the dedicated connection on exit."""
    with storage.open_inventory(directory, timeout=0.25) as state:
        saved = state.replace_inventory(None, checkpoint(), [message(1)])
        assert state.connection.execute("PRAGMA busy_timeout").fetchone() == (250,)
        assert state.connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
        assert state.connection.execute("PRAGMA temp_store").fetchone() == (2,)
        assert state.connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert directory.stat().st_mode & 0o777 == 0o700
        for item in directory.iterdir():
            assert item.stat().st_mode & 0o777 == 0o600
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        state.read_checkpoint()
    with storage.open_inventory(directory) as resumed:
        assert resumed.read_snapshot() == (saved, (message(1),))


def test_active_journal_private_and_cleanup_after_failure(directory: Path) -> None:
    """Rollback sidecars inherit private modes; caller failures release locks."""
    with (
        pytest.raises(RuntimeError, match="synthetic"),
        storage.open_inventory(directory) as state,
    ):
        state.connection.execute("BEGIN IMMEDIATE")
        state.connection.execute("INSERT INTO mail_sync_messages VALUES (1,'a',NULL,0)")
        assert (
            stat.S_IMODE((directory / "inventory.sqlite3-journal").stat().st_mode)
            == 0o600
        )
        raise RuntimeError("synthetic interruption")
    with storage.open_inventory(directory) as resumed:
        assert resumed.read_snapshot() == (None, ())


def test_competing_worker_fails_immediately(directory: Path) -> None:
    """The full-run lock prevents two reconciliations sharing one store."""
    with (
        storage.open_inventory(directory),
        pytest.raises(storage.SyncStorageBusy),
        storage.open_inventory(directory),
    ):
        pytest.fail("contending worker entered")
    with storage.open_inventory(directory) as resumed:
        assert resumed.read_checkpoint() is None


@pytest.mark.parametrize("value", [0, -1, 61, True, "5", float("inf"), float("nan")])
def test_invalid_timeouts_do_not_create_files(directory: Path, value: Any) -> None:
    """Reject unbounded or coercible lock waits before any I/O."""
    with (
        pytest.raises(ValueError, match="timeout"),
        storage.open_inventory(directory, timeout=value),
    ):
        pytest.fail("invalid timeout accepted")
    assert not directory.exists()


def test_unsupported_platform_fails_before_io(directory: Path) -> None:
    """The POSIX safety boundary is not silently weakened elsewhere."""
    with (
        patch.object(os, "name", "nt"),
        pytest.raises(storage.UnsafeSyncStorage, match="POSIX"),
        storage.open_inventory(directory),
    ):
        pytest.fail("unsupported platform accepted")
    assert not directory.exists()


def test_reject_unnamed_directory() -> None:
    """Never use the current directory as a state store."""
    with (
        pytest.raises(storage.UnsafeSyncStorage, match="named"),
        storage.open_inventory(Path()),
    ):
        pytest.fail("unnamed directory accepted")


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o1700])
def test_directory_permissions_are_not_repaired(directory: Path, mode: int) -> None:
    """Refuse permissive or special modes without chmod on existing paths."""
    directory.mkdir(mode=0o700)
    directory.chmod(mode)
    with (
        pytest.raises(storage.UnsafeSyncStorage, match="private"),
        storage.open_inventory(directory),
    ):
        pytest.fail("unsafe directory accepted")
    assert stat.S_IMODE(directory.stat().st_mode) == mode
    assert not list(directory.iterdir())


def test_final_directory_symlink_is_rejected(directory: Path) -> None:
    """Inspect the final path without following it, even inside the sandbox."""
    target = directory.parent / "target"
    target.mkdir(mode=0o700)
    directory.symlink_to(target, target_is_directory=True)
    with pytest.raises(storage.UnsafeSyncStorage), storage.open_inventory(directory):
        pytest.fail("directory symlink accepted")
    assert not list(target.iterdir())


@pytest.mark.parametrize("filename", ["inventory.lock", "inventory.sqlite3"])
@pytest.mark.parametrize("kind", ["symlink", "hard link", "directory", "permissions"])
def test_unsafe_fixed_files_do_not_modify_target(
    directory: Path, filename: str, kind: str
) -> None:
    """Fixed names cannot redirect locks or SQLite to another local file."""
    directory.mkdir(mode=0o700)
    target = directory.parent / "target"
    target.write_bytes(b"unchanged")
    target.chmod(0o600)
    path = directory / filename
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hard link":
        os.link(target, path)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        path.touch(mode=0o644)
        path.chmod(0o644)
    with (
        pytest.raises((storage.UnsafeSyncStorage, OSError)),
        storage.open_inventory(directory),
    ):
        pytest.fail("unsafe file accepted")
    assert target.read_bytes() == b"unchanged"
    if kind == "directory":
        path.rmdir()


@pytest.mark.parametrize(
    "filename", ["inventory.sqlite3-wal", "other", "inventory.sqlite3-journal"]
)
def test_unexpected_or_unsafe_sidecar(directory: Path, filename: str) -> None:
    """Do not follow stale SQLite sidecars or remove unknown user files."""
    directory.mkdir(mode=0o700)
    path = directory / filename
    path.touch(mode=0o644)
    path.chmod(0o644)
    with pytest.raises(storage.UnsafeSyncStorage), storage.open_inventory(directory):
        pytest.fail("unsafe sidecar accepted")
    assert path.exists()


def test_private_stale_journal_can_be_recovered(directory: Path) -> None:
    """An empty rollback journal does not require manual deletion."""
    with storage.open_inventory(directory) as state:
        saved = state.replace_inventory(None, checkpoint(), [message(1)])
    (directory / "inventory.sqlite3-journal").touch(mode=0o600)
    with storage.open_inventory(directory) as state:
        assert state.read_snapshot() == (saved, (message(1),))


@pytest.mark.parametrize("application", [0, 123])
def test_foreign_database_is_not_claimed(directory: Path, application: int) -> None:
    """Reject another SQLite application without initializing Mail tables."""
    directory.mkdir(mode=0o700)
    path = directory / "inventory.sqlite3"
    path.touch(mode=0o600)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute(f"PRAGMA application_id={application}")
    with (
        pytest.raises(storage.UnsafeSyncStorage, match="recognized"),
        storage.open_inventory(directory),
    ):
        pytest.fail("foreign database accepted")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [
            ("unrelated",)
        ]
        assert connection.execute("PRAGMA application_id").fetchone() == (application,)


def test_corrupt_database_preserved_and_lock_released(directory: Path) -> None:
    """Corruption must not replace old bytes with an empty inventory."""
    directory.mkdir(mode=0o700)
    path = directory / "inventory.sqlite3"
    path.touch(mode=0o600)
    path.write_bytes(b"not a database")
    for _ in range(2):
        with pytest.raises(sqlite3.DatabaseError), storage.open_inventory(directory):
            pytest.fail("corrupt database accepted")
        assert path.read_bytes() == b"not a database"


def test_connection_closes_when_backend_init_fails(directory: Path) -> None:
    """Initialization failures also release connections and locks."""
    with (
        patch.object(storage, "SQLiteMailInventory", side_effect=RuntimeError("init")),
        pytest.raises(RuntimeError, match="init"),
        storage.open_inventory(directory),
    ):
        pytest.fail("initialization succeeded")
    with storage.open_inventory(directory) as resumed:
        assert resumed.read_checkpoint() is None


def test_journal_mode_failure_closes_connection(directory: Path) -> None:
    """Fail when SQLite cannot use the expected rollback journal."""
    connection = MagicMock(spec=sqlite3.Connection)
    connection.execute.return_value.fetchone.side_effect = [(0,), ("wal",)]
    connection.execute.return_value.fetchall.return_value = []
    with (
        patch.object(sqlite3, "connect", return_value=connection),
        pytest.raises(storage.UnsafeSyncStorage, match="journal"),
    ):
        _database(directory, 1)
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "mode,owner",
    [
        (stat.S_IFDIR | 0o777, 0),
        (stat.S_IFDIR | 0o775, _TEST_UID),
        (stat.S_IFDIR | 0o700, _TEST_UID + 1),
        (stat.S_IFLNK | 0o700, _TEST_UID),
    ],
)
def test_untrusted_ancestor_rejected(directory: Path, mode: int, owner: int) -> None:
    """A writable parent can replace an otherwise private store."""
    info = os.stat_result((mode, 0, 0, 1, owner, 0, 0, 0, 0, 0))
    with (
        patch.object(Path, "lstat", return_value=info),
        pytest.raises(storage.UnsafeSyncStorage, match="trusted parent"),
    ):
        _trusted_parent(directory)


@pytest.mark.parametrize("mode", [0o755, 0o1777])
def test_root_owned_safe_ancestors(directory: Path, mode: int) -> None:
    """Root-owned sticky temporary parents and non-writable ancestors are allowed."""
    info = os.stat_result((stat.S_IFDIR | mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    with patch.object(Path, "lstat", return_value=info):
        _trusted_parent(directory)


def test_wrong_owner_file_rejected(directory: Path) -> None:
    """An owner mismatch closes the descriptor without changing permissions."""
    directory.mkdir(mode=0o700)
    info = os.stat_result((
        stat.S_IFREG | 0o600,
        0,
        0,
        1,
        os.getuid() + 1,
        0,
        0,
        0,
        0,
        0,
    ))
    with (
        patch.object(os, "fstat", return_value=info),
        pytest.raises(storage.UnsafeSyncStorage),
    ):
        _private_file(directory / "inventory.lock")
    with storage.open_inventory(directory) as state:
        assert state.read_checkpoint() is None


def test_restart_recovers_hot_journal_after_process_exit(directory: Path) -> None:
    """A worker crash must not publish half an inventory or require file deletion."""
    records = tuple(message(uid) for uid in range(1, 501))
    with storage.open_inventory(directory) as state:
        saved = state.replace_inventory(None, checkpoint(), records)
    script = (
        "import os,sqlite3,sys; "
        "c=sqlite3.connect(sys.argv[1]); "
        "c.execute('PRAGMA cache_size=1'); "
        "c.execute('BEGIN IMMEDIATE'); "
        "c.execute('UPDATE mail_sync_messages SET flags=2'); "
        "os._exit(0)"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(directory / "inventory.sqlite3")],
        check=True,
        timeout=1,
        capture_output=True,
    )
    journal = directory / "inventory.sqlite3-journal"
    assert journal.stat().st_size > 0
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    with storage.open_inventory(directory) as resumed:
        assert resumed.read_snapshot() == (saved, records)


def test_fifo_lock_is_rejected_without_blocking(directory: Path) -> None:
    """Opening an unexpected pipe must neither hang nor start SQLite."""
    directory.mkdir(mode=0o700)
    os.mkfifo(directory / "inventory.lock", mode=0o600)
    with pytest.raises(storage.UnsafeSyncStorage), storage.open_inventory(directory):
        pytest.fail("FIFO accepted")
    assert not (directory / "inventory.sqlite3").exists()
