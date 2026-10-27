"""Offline atomicity, baseline handoff, restart, and competing-writer tests."""

from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory, gettempdir
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError
import pytest

from pyicloud.services.mail._sync import SyncCheckpoint, SyncResetRequired
from pyicloud.services.mail._sync_inventory import establish_inventory
from pyicloud.services.mail._sync_state import (
    InventoryMessage,
    SQLiteMailInventory,
    StateConflict,
)


def checkpoint(**changes: Any) -> SyncCheckpoint:
    """Create a synthetic, independently identified mailbox namespace."""
    return SyncCheckpoint.model_validate({
        "account": "test-account",
        "mailbox_id": "test-mailbox",
        "folder": "INBOX",
        "uid_validity": 123,
        "modseq": 100,
        "threadmodseq": 200,
        **changes,
    })


def message(uid: int, **changes: Any) -> InventoryMessage:
    """No content, capability URLs, or arbitrary headers are inventory fields."""
    return InventoryMessage.model_validate({
        "uid": uid,
        "message_id": f"test-message-{uid}",
        "thread_id": "test-thread",
        "flags": 1,
        **changes,
    })


@pytest.fixture(name="state")
def state_fixture() -> Iterator[SQLiteMailInventory]:
    """Use a real SQLite transaction engine, without external filesystem access."""
    connection = sqlite3.connect(":memory:")
    try:
        yield SQLiteMailInventory(connection)
    finally:
        connection.close()


class Source:
    """Exhaust explicit checkpoint observations and inventory attempts."""

    def __init__(
        self,
        checkpoints: list[SyncCheckpoint],
        inventories: list[Iterable[InventoryMessage]],
    ) -> None:
        self.checkpoints = iter(checkpoints)
        self.inventories = inventories
        self.scan_count = 0

    def current_checkpoint(self) -> SyncCheckpoint:
        """Each observation is independent of the metadata enumeration."""
        return next(self.checkpoints)

    def iter_inventory(self) -> Iterator[InventoryMessage]:
        """An error or incomplete iterator must not publish partial state."""
        inventory = self.inventories[self.scan_count]
        self.scan_count += 1
        yield from inventory


def test_initial_inventory_and_empty_baseline(state: SQLiteMailInventory) -> None:
    """An exhausted stable scan, including an empty one, commits a checkpoint."""
    assert state.read_checkpoint() is None
    source = Source([checkpoint()] * 2, [[message(2, flags=None), message(1)]])
    first = establish_inventory(source, state)
    assert first.revision == 1
    assert state.read_messages() == (message(1), message(2, flags=None))
    second = establish_inventory(Source([checkpoint()] * 2, [[]]), state)
    assert second.revision == 2
    assert second.checkpoint == checkpoint()
    assert state.read_checkpoint() == second
    assert state.read_messages() == ()


def test_arrival_during_scan_discards_attempt(state: SQLiteMailInventory) -> None:
    """A message arriving behind the scan boundary forces a fresh full inventory."""
    newer = checkpoint(modseq=101, threadmodseq=201)
    source = Source(
        [checkpoint(), newer, newer, newer], [[message(1)], [message(1), message(2)]]
    )
    result = establish_inventory(source, state, max_attempts=2)
    assert source.scan_count == 2
    assert result.revision == 1
    assert result.checkpoint == newer
    assert state.read_messages() == (message(1), message(2))


def test_unstable_mailbox_leaves_old_inventory(state: SQLiteMailInventory) -> None:
    """A busy mailbox fails within its budget, never publishing an unstable scan."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    newer = checkpoint(modseq=101)
    source = Source([checkpoint(), newer] * 2, [[message(1)], [message(2)]])
    with pytest.raises(SyncResetRequired, match="every inventory attempt"):
        establish_inventory(source, state, max_attempts=2)
    assert source.scan_count == 2
    assert state.read_checkpoint() == old
    assert state.read_messages() == (message(7),)


def interrupted() -> Iterator[InventoryMessage]:
    """Simulate an incomplete live scan after one valid record."""
    yield message(1)
    raise RuntimeError("synthetic interruption")


def test_interrupted_scan_preserves_state(state: SQLiteMailInventory) -> None:
    """The stable-end observation is never attempted after an incomplete scan."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    prepared_source_1 = Source([checkpoint()], [interrupted()])
    with pytest.raises(RuntimeError, match="interruption"):
        establish_inventory(prepared_source_1, state)
    assert state.read_checkpoint() == old
    assert state.read_messages() == (message(7),)


@pytest.mark.parametrize(
    "rows",
    [
        [message(1), message(1, message_id="other")],
        [message(1), message(2, message_id="test-message-1")],
    ],
)
def test_duplicate_scan_identity_requires_reset(
    state: SQLiteMailInventory, rows: list[InventoryMessage]
) -> None:
    """Do not turn inconsistent pages into a seemingly complete deduplicated list."""
    prepared_source_1 = Source([checkpoint()], [rows])
    with pytest.raises(SyncResetRequired, match="duplicate"):
        establish_inventory(prepared_source_1, state)
    assert state.read_checkpoint() is None


def test_message_budget_is_not_silent_truncation(state: SQLiteMailInventory) -> None:
    """An exact-size scan succeeds; one extra message invalidates the whole scan."""
    prepared_source_1 = Source([checkpoint()], [[message(1), message(2)]])
    with pytest.raises(SyncResetRequired, match="message budget"):
        establish_inventory(prepared_source_1, state, max_messages=1)
    assert state.read_checkpoint() is None
    establish_inventory(
        Source([checkpoint()] * 2, [[message(1)]]), state, max_messages=1
    )


@pytest.mark.parametrize("option", ["max_attempts", "max_messages"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_invalid_budget_precedes_reads(
    state: SQLiteMailInventory, option: str, value: Any
) -> None:
    """Explicit resource limits cannot accept boolean or coercible inputs."""
    prepared_source_1 = Source([], [])
    with pytest.raises(ValueError, match="positive integers"):
        establish_inventory(prepared_source_1, state, **{option: value})


@pytest.mark.parametrize("field", ["account", "mailbox_id"])
def test_wrong_existing_source_rejected_before_scan(
    state: SQLiteMailInventory, field: str
) -> None:
    """An existing target cannot accidentally ingest another account or mailbox."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    source = Source([checkpoint(**{field: "other"})], [])
    with pytest.raises(SyncResetRequired, match="another account/mailbox"):
        establish_inventory(source, state)
    assert source.scan_count == 0
    assert state.read_checkpoint() == old


def test_source_switch_during_first_scan_rejected(state: SQLiteMailInventory) -> None:
    """Even a previously empty target pins its first account and mailbox identity."""
    prepared_source_1 = Source(
        [checkpoint(), checkpoint(account="other")], [[message(1)]]
    )
    with pytest.raises(SyncResetRequired, match="during scan"):
        establish_inventory(prepared_source_1, state)
    assert state.read_checkpoint() is None


def test_source_switch_between_attempts_rejected(state: SQLiteMailInventory) -> None:
    """A retry is not authorization to switch accounts after an unstable attempt."""
    prepared_source_1 = Source(
        [checkpoint(), checkpoint(modseq=101), checkpoint(account="other")],
        [[message(1)]],
    )
    with pytest.raises(SyncResetRequired, match="another account/mailbox"):
        establish_inventory(prepared_source_1, state)


def test_namespace_reset_and_rename_require_fresh_stable_scan(
    state: SQLiteMailInventory,
) -> None:
    """A deliberate new baseline replaces, not merges, recreated-folder UIDs."""
    state.replace_inventory(None, checkpoint(), [message(7)])
    newer = checkpoint(uid_validity=124, folder="Renamed", modseq=1, threadmodseq=1)
    source = Source([checkpoint(), newer, newer, newer], [[message(7)], [message(1)]])
    result = establish_inventory(source, state, max_attempts=2)
    assert result.revision == 2
    assert result.checkpoint == newer
    assert state.read_messages() == (message(1),)


def test_atomic_rollback_after_row_insert(state: SQLiteMailInventory) -> None:
    """Failure after DELETE and INSERT restores both old data and old checkpoint."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    prepared_checkpoint_1 = checkpoint(modseq=101)
    prepared_argument_2 = interrupted()
    with pytest.raises(RuntimeError):
        state.replace_inventory(old, prepared_checkpoint_1, prepared_argument_2)
    assert not state.connection.in_transaction
    assert state.read_checkpoint() == old
    assert state.read_messages() == (message(7),)


def test_snapshot_read_is_transactional(state: SQLiteMailInventory) -> None:
    """A consumer can resume from a coherent cursor/inventory pair."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    original = state.read_messages

    def read_inside_transaction() -> tuple[InventoryMessage, ...]:
        assert state.connection.in_transaction
        return original()

    with patch.object(state, "read_messages", side_effect=read_inside_transaction):
        assert state.read_snapshot() == (old, (message(7),))
    assert not state.connection.in_transaction


def test_duplicate_storage_rows_rollback_without_echoing_ids(
    state: SQLiteMailInventory,
) -> None:
    """Storage also enforces UID and message-ID uniqueness inside its transaction."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    secret = message(1, message_id="private-identifier")
    prepared_checkpoint_1 = checkpoint()
    with pytest.raises(SyncResetRequired) as error:
        state.replace_inventory(old, prepared_checkpoint_1, [secret, secret])
    assert "private-identifier" not in str(error.value)
    assert state.read_checkpoint() == old
    assert state.read_messages() == (message(7),)


def test_local_revision_rejects_equal_counter_aba(state: SQLiteMailInventory) -> None:
    """Two writers cannot overwrite each other just because remote counters agree."""
    stale = state.replace_inventory(None, checkpoint(), [message(1)])
    current = state.replace_inventory(stale, checkpoint(), [message(2)])
    prepared_checkpoint_1 = checkpoint(modseq=101)
    prepared_argument_2 = [message(3)]
    with pytest.raises(StateConflict):
        state.replace_inventory(stale, prepared_checkpoint_1, prepared_argument_2)
    assert current.revision == 2
    assert state.read_checkpoint() == current
    assert state.read_messages() == (message(2),)


def test_competing_writer_during_baseline_wins_without_lost_update(
    state: SQLiteMailInventory,
) -> None:
    """A long scan cannot overwrite a writer that committed while it was reading."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])

    def inventory_with_competing_writer() -> Iterator[InventoryMessage]:
        state.replace_inventory(old, checkpoint(modseq=101), [message(9)])
        yield message(1)

    source = Source([checkpoint()] * 2, [inventory_with_competing_writer()])
    with pytest.raises(StateConflict):
        establish_inventory(source, state)
    saved, records = state.read_snapshot()
    assert saved is not None
    assert saved.revision == 2
    assert saved.checkpoint.modseq == 101
    assert records == (message(9),)


def test_later_remote_change_is_not_saved_as_part_of_baseline(
    state: SQLiteMailInventory,
) -> None:
    """The stored baseline cursor does not jump forward to consume a later poll."""
    later = checkpoint(modseq=101, threadmodseq=201)
    source = Source([checkpoint(), checkpoint(), later], [[message(1)]])
    saved = establish_inventory(source, state)
    assert saved.checkpoint == checkpoint()
    assert source.current_checkpoint() == later
    assert state.read_messages() == (message(1),)


@pytest.mark.parametrize(
    "changes",
    [
        {"account": "other"},
        {"mailbox_id": "other"},
        {"modseq": 99},
        {"threadmodseq": 199},
    ],
)
def test_storage_rejects_scope_switch_and_counter_regression(
    state: SQLiteMailInventory, changes: dict[str, Any]
) -> None:
    """A stable but stale/mis-scoped source cannot overwrite an existing target."""
    old = state.replace_inventory(None, checkpoint(), [message(7)])
    prepared_checkpoint_1 = checkpoint(**changes)
    prepared_argument_2 = [message(1)]
    with pytest.raises(SyncResetRequired):
        state.replace_inventory(old, prepared_checkpoint_1, prepared_argument_2)
    assert state.read_checkpoint() == old
    assert state.read_messages() == (message(7),)


def test_dedicated_connection_rejects_existing_transaction() -> None:
    """The backend never commits or rolls back somebody else's transaction."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE unrelated (value INTEGER)")
        connection.execute("INSERT INTO unrelated VALUES (1)")
        with pytest.raises(ValueError, match="idle dedicated"):
            SQLiteMailInventory(connection)
        assert connection.in_transaction
        connection.rollback()
        assert connection.execute("SELECT * FROM unrelated").fetchall() == []


class FailingConnection(sqlite3.Connection):
    """Inject a commit failure without mocking out SQLite transaction behavior."""

    fail_commit = False

    def commit(self) -> None:
        """Fail only when explicitly requested by the test."""
        if self.fail_commit:
            raise sqlite3.OperationalError("synthetic disk failure")
        super().commit()


@pytest.mark.parametrize("failure", [GeneratorExit, KeyboardInterrupt, SystemExit])
def test_interrupted_transaction_rolls_back(
    state: SQLiteMailInventory, failure: type[BaseException]
) -> None:
    """Generator closure and process interruption also release partial writes."""
    original = message(7)
    old = state.replace_inventory(None, checkpoint(), [original])
    newer = checkpoint(modseq=101)

    def rows() -> Iterator[InventoryMessage]:
        yield message(1)
        raise failure

    interrupted_rows = rows()
    with pytest.raises(failure):
        state.replace_inventory(old, newer, interrupted_rows)
    assert not state.connection.in_transaction
    assert state.read_checkpoint() == old
    assert state.read_messages() == (original,)


def test_commit_failure_rolls_back_inventory() -> None:
    """Even failure at the final commit cannot leave a newer saved cursor."""
    connection = sqlite3.connect(":memory:", factory=FailingConnection)
    try:
        state = SQLiteMailInventory(connection)
        old = state.replace_inventory(None, checkpoint(), [message(7)])
        connection.fail_commit = True
        prepared_checkpoint_1 = checkpoint(modseq=101)
        prepared_argument_2 = [message(1)]
        with pytest.raises(sqlite3.OperationalError, match="disk failure"):
            state.replace_inventory(old, prepared_checkpoint_1, prepared_argument_2)
        assert not connection.in_transaction
        assert state.read_checkpoint() == old
        assert state.read_messages() == (message(7),)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "version,revision,data",
    [
        (2, 1, "{}"),
        (1, 0, "{}"),
        (1, 1, "invalid-json"),
        (1, 1, "{}"),
    ],
)
def test_invalid_saved_checkpoint_fails_closed(
    state: SQLiteMailInventory, version: int, revision: int, data: str
) -> None:
    """Unsupported formats and malformed state are never treated as a fresh sync."""
    state.connection.execute(
        "INSERT INTO mail_sync_checkpoint VALUES (1, ?, ?, ?)",
        (version, revision, data),
    )
    state.connection.commit()
    with pytest.raises(SyncResetRequired):
        state.read_checkpoint()


def test_invalid_saved_message_fails_closed(state: SQLiteMailInventory) -> None:
    """Unvalidated database values do not become apparently valid inventory rows."""
    state.connection.execute(
        "INSERT INTO mail_sync_messages VALUES (0, 'fake', NULL, -1)"
    )
    state.connection.commit()
    with pytest.raises(SyncResetRequired):
        state.read_messages()


def test_metadata_model_rejects_content_fields() -> None:
    """The minimal persistence model cannot accidentally store bodies or URLs."""
    with pytest.raises(ValidationError):
        InventoryMessage.model_validate({
            "uid": 1,
            "message_id": "test",
            "body": "private",
        })


@pytest.fixture(name="db_path")
def database_path_fixture() -> Iterator[Path]:
    """Use private sandbox storage and absolute cleanup paths for the test guard."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        path = Path(directory) / "inventory.sqlite3"
        path.touch(mode=0o600)
        try:
            yield path
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def test_durable_restart_and_two_connections(db_path: Path) -> None:
    """A reopened private database retains an atomic revision and rejects stale CAS."""
    path = db_path
    first = sqlite3.connect(path)
    second = sqlite3.connect(path)
    try:
        one, two = SQLiteMailInventory(first), SQLiteMailInventory(second)
        initial = one.replace_inventory(None, checkpoint(), [message(1)])
        stale = two.read_checkpoint()
        current = one.replace_inventory(initial, checkpoint(modseq=101), [message(2)])
        prepared_checkpoint_1 = checkpoint(modseq=102)
        prepared_argument_2 = [message(3)]
        with pytest.raises(StateConflict):
            two.replace_inventory(stale, prepared_checkpoint_1, prepared_argument_2)
    finally:
        first.close()
        second.close()
    connection = sqlite3.connect(path)
    try:
        resumed = SQLiteMailInventory(connection)
        assert resumed.read_checkpoint() == current
        assert resumed.read_messages() == (message(2),)
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        connection.close()
