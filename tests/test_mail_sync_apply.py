"""Offline incremental reconciliation, racing metadata, and atomic failure tests."""

from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest

from pyicloud.services.mail._sync import (
    ChangeBatch,
    SyncCheckpoint,
    SyncResetRequired,
    validate_batch,
)
from pyicloud.services.mail._sync_apply import apply_incremental
from pyicloud.services.mail._sync_state import (
    InventoryMessage,
    SQLiteMailInventory,
    StateConflict,
    StoredCheckpoint,
)
from tests.test_mail_sync_inventory import checkpoint, message


@pytest.fixture(name="state")
def state_fixture() -> Iterator[SQLiteMailInventory]:
    """Use real transactions, never network or out-of-sandbox disk access."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield SQLiteMailInventory(connection)


def seed(
    state: SQLiteMailInventory, rows: list[InventoryMessage] | None = None
) -> StoredCheckpoint:
    """Two independent threads are the default stable baseline."""
    return state.replace_inventory(
        None,
        checkpoint(),
        rows
        if rows is not None
        else [
            message(1, thread_id="thread-1"),
            message(2, thread_id="thread-2"),
        ],
    )


class Source:
    """Explicit checked remote results, with hooks to simulate independent races."""

    def __init__(
        self,
        events: list[str],
        *,
        end: SyncCheckpoint | None = None,
        records: dict[int, InventoryMessage | None] | None = None,
        threads: dict[str, Iterable[InventoryMessage]] | None = None,
    ) -> None:
        self.events = events
        self.end = end if end is not None else checkpoint(modseq=101)
        self.observed = self.end
        self.records = records or {}
        self.threads = threads or {}
        self.uid_calls: list[int] = []
        self.thread_calls: list[str] = []
        self.poll_calls: list[SyncCheckpoint] = []
        self.on_poll: Callable[[], None] | None = None
        self.on_observe: Callable[[], None] | None = None

    def current_checkpoint(self) -> SyncCheckpoint:
        """The final observation can advance independently of metadata reads."""
        if self.on_observe is not None:
            self.on_observe()
        return self.observed

    def iter_inventory(self) -> Iterator[InventoryMessage]:
        """An incremental run never silently resets to a full inventory."""
        raise AssertionError("unexpected full inventory")

    def poll_changes(self, previous: SyncCheckpoint) -> ChangeBatch:
        """Retain the exact cursor used by the caller for the request."""
        self.poll_calls.append(previous)
        if self.on_poll is not None:
            self.on_poll()
        return validate_batch(previous, self.end, self.events)

    def iter_thread_inventory(self, thread_id: str) -> Iterator[InventoryMessage]:
        """Return complete synthetic membership or fail partway through it."""
        self.thread_calls.append(thread_id)
        yield from self.threads.get(thread_id, [])

    def message_by_uid(self, uid: int) -> InventoryMessage | None:
        """No newest-N fallback or inferred flag-bit mapping is permitted."""
        self.uid_calls.append(uid)
        return self.records.get(uid)


def test_baseline_required_before_network(state: SQLiteMailInventory) -> None:
    """An uninitialized database is not a complete empty mailbox."""
    source = Source([])
    with pytest.raises(SyncResetRequired, match="complete Mail inventory"):
        apply_incremental(source, state)
    assert not source.poll_calls


def test_count_hint_fallback_requires_explicit_opt_in(
    state: SQLiteMailInventory,
) -> None:
    """A caller may choose a full stable inventory, never a guessed UID scan."""
    seed(state)
    source = Source(["* 3 EXISTS"])
    with patch.object(source, "iter_inventory", return_value=iter([message(3)])):
        result = apply_incremental(source, state, allow_inventory_fallback=True)
    assert result.checkpoint == source.end
    assert state.read_messages() == (message(3),)


def test_fallback_keeps_original_writer_revision(state: SQLiteMailInventory) -> None:
    """Opting into a rescan is not permission to supersede a competing writer."""
    old = seed(state)
    source = Source(["* 3 EXISTS"])

    def other_writer() -> None:
        state.replace_inventory(old, checkpoint(modseq=102), [message(9)])

    source.on_poll = other_writer
    with pytest.raises(StateConflict, match="before baseline fallback"):
        apply_incremental(source, state, allow_inventory_fallback=True)
    assert state.read_messages() == (message(9),)


@pytest.mark.parametrize(
    "changes",
    [
        {"modseq": 100},
        {"threadmodseq": 199},
        {"folder": "Renamed"},
        {"uid_validity": 124},
        {"account": "another"},
        {"mailbox_id": "another"},
    ],
)
def test_fallback_cannot_regress_from_observed_batch(
    state: SQLiteMailInventory, changes: dict[str, Any]
) -> None:
    """A stable rescan is insufficient if it is older than the poll it replaces."""
    old = seed(state)
    source = Source(["* 3 EXISTS"])
    source.observed = source.end.model_copy(update=changes)
    with pytest.raises(SyncResetRequired, match="observed change batch"):
        apply_incremental(source, state, allow_inventory_fallback=True)
    assert state.read_checkpoint() == old


def test_fallback_stability_budget_is_one_scan(state: SQLiteMailInventory) -> None:
    """Busy fallback inventories fail without advancing the original checkpoint."""
    old = seed(state)
    source = Source(["* 3 EXISTS"])
    with (
        patch.object(source, "iter_inventory", return_value=iter([message(3)])),
        patch.object(
            source,
            "current_checkpoint",
            side_effect=[source.end, checkpoint(modseq=102)],
        ),
        pytest.raises(SyncResetRequired),
    ):
        apply_incremental(source, state, allow_inventory_fallback=True)
    assert state.read_checkpoint() == old


@pytest.mark.parametrize("value", [1, "yes", None])
def test_fallback_policy_is_not_coerced(state: SQLiteMailInventory, value: Any) -> None:
    """A rescan cannot be accidentally enabled by a truthy configuration value."""
    with pytest.raises(ValueError, match="explicitly boolean"):
        apply_incremental(Source([]), state, allow_inventory_fallback=value)


def test_noop_does_not_rewrite_inventory_or_revision(
    state: SQLiteMailInventory,
) -> None:
    """An empty, unchanged poll confirms state without generating database churn."""
    old = seed(state)
    before = state.connection.total_changes
    source = Source([], end=checkpoint())
    assert apply_incremental(source, state) == old
    assert state.connection.total_changes == before
    assert source.poll_calls == [old.checkpoint]


def test_numeric_flags_are_refreshed_not_inferred(state: SQLiteMailInventory) -> None:
    """Do not guess the server's integer flag bits from the event's IMAP strings."""
    old = seed(state)
    source = Source(
        ["* 1 FETCH (UID 1 MODSEQ (101) FLAGS (\\Flagged))"],
        records={1: message(1, thread_id="thread-1", flags=8192)},
    )
    result = apply_incremental(source, state)
    assert result.revision == old.revision + 1
    assert state.read_messages()[0].flags == 8192
    assert source.uid_calls == [1] and not source.thread_calls


def test_arrival_fetches_exact_uid_and_preserves_other_messages(
    state: SQLiteMailInventory,
) -> None:
    """A UID-bearing arrival adds fresh checked metadata to the saved inventory."""
    seed(state)
    source = Source(["* 3 EXISTS (UID 3)"], records={3: message(3)})
    apply_incremental(source, state)
    assert [row.uid for row in state.read_messages()] == [1, 2, 3]
    assert source.uid_calls == [3]


def test_arrival_and_thread_are_reconciled_once(state: SQLiteMailInventory) -> None:
    """A complete changed thread can contain more members than the arrival alone."""
    seed(state)
    source = Source(
        [
            "* 3 EXISTS (UID 3)",
            "* THREADUPDATE (thread-new 1700000000 201 FLAGS () NEW)",
        ],
        end=checkpoint(modseq=101, threadmodseq=201),
        threads={
            "thread-new": [
                message(3, thread_id="thread-new"),
                message(4, thread_id="thread-new"),
            ]
        },
    )
    apply_incremental(source, state)
    assert [row.uid for row in state.read_messages()] == [1, 2, 3, 4]
    assert not source.uid_calls and source.thread_calls == ["thread-new"]


def test_thread_membership_loss_is_checked_by_uid(state: SQLiteMailInventory) -> None:
    """Disappearing from a thread is not automatically disappearing from the mailbox."""
    seed(state)
    source = Source(
        ["* THREADUPDATE (thread-1 NIL 201 FLAGS ())"],
        end=checkpoint(modseq=101, threadmodseq=201),
        records={1: message(1, thread_id="thread-other")},
    )
    apply_incremental(source, state)
    assert state.read_messages()[0].thread_id == "thread-other"
    assert source.uid_calls == [1]


def test_missing_thread_member_can_be_authoritatively_absent(
    state: SQLiteMailInventory,
) -> None:
    """Exact absence can remove an old member without inventing a global deletion."""
    seed(state, [message(1, thread_id="thread-1"), message(2, thread_id="thread-1")])
    source = Source(
        ["* THREADUPDATE (thread-1 1700000000 201 FLAGS ())"],
        end=checkpoint(modseq=101, threadmodseq=201),
        threads={"thread-1": [message(1, thread_id="thread-1", flags=9)]},
    )
    apply_incremental(source, state)
    assert state.read_messages() == (message(1, thread_id="thread-1", flags=9),)
    assert source.uid_calls == [2]


def test_full_thread_and_exact_lookup_cannot_disagree(
    state: SQLiteMailInventory,
) -> None:
    """An allegedly complete empty thread cannot omit a still-present member."""
    old = seed(state)
    source = Source(
        ["* THREADUPDATE (thread-1 NIL 201 FLAGS ())"],
        end=checkpoint(modseq=101, threadmodseq=201),
        records={1: message(1, thread_id="thread-1")},
    )
    with pytest.raises(SyncResetRequired, match="disagree"):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old


def test_compact_removal_ranges_do_not_expand(state: SQLiteMailInventory) -> None:
    """A billion-scale range removes only known matching local records."""
    seed(state)
    apply_incremental(Source(["* VANISHED 1:4294967295"]), state)
    assert state.read_messages() == ()


@pytest.mark.parametrize("reassigned", [False, True])
def test_flag_free_thread_invalidation_checks_current_membership(
    state: SQLiteMailInventory, reassigned: bool
) -> None:
    """A NIL update refreshes the old thread and checks absent members by UID."""
    seed(state)
    moved = message(1, thread_id="another-thread")
    source = Source(
        ["* THREADUPDATE (thread-1 NIL 201)"],
        end=checkpoint(modseq=101, threadmodseq=201),
        threads={"thread-1": []},
        records={1: moved if reassigned else None},
    )
    apply_incremental(source, state)
    assert source.thread_calls == ["thread-1"]
    assert source.uid_calls == [1]
    expected = (
        (moved, message(2, thread_id="thread-2"))
        if reassigned
        else (message(2, thread_id="thread-2"),)
    )
    assert state.read_messages() == expected


def test_removal_ranges_merge_and_keep_gaps(state: SQLiteMailInventory) -> None:
    """Overlapping, adjacent, and disjoint ranges preserve unaffected UIDs."""
    seed(state, [message(uid) for uid in [1, 2, 4, 6, 11, 20, 21]])
    apply_incremental(Source(["* VANISHED 10:12,2:3,3:5,20"]), state)
    assert [row.uid for row in state.read_messages()] == [1, 6, 21]


@pytest.mark.parametrize("present", [True, False])
def test_flag_update_then_removal_uses_authoritative_absence(
    state: SQLiteMailInventory, present: bool
) -> None:
    """A removed UID must not be resurrected by stale metadata or prior flags."""
    old = seed(state)
    source = Source(
        ["* 1 FETCH (UID 1 MODSEQ (101) FLAGS ())", "* VANISHED 1"],
        records={1: message(1) if present else None},
    )
    if present:
        with pytest.raises(SyncResetRequired, match="removals"):
            apply_incremental(source, state)
        assert state.read_checkpoint() == old
    else:
        apply_incremental(source, state)
        assert [row.uid for row in state.read_messages()] == [2]


@pytest.mark.parametrize("kind", ["wrong-thread", "duplicate-uid", "removed-member"])
def test_inconsistent_thread_metadata_never_commits(
    state: SQLiteMailInventory, kind: str
) -> None:
    """A complete thread read still needs membership and tombstone validation."""
    old = seed(state)
    events = ["* THREADUPDATE (thread-1 1700000000 201 FLAGS ())"]
    records = [message(1, thread_id="thread-1")]
    if kind == "wrong-thread":
        records = [message(1, thread_id="wrong")]
    elif kind == "duplicate-uid":
        records *= 2
    else:
        events.append("* VANISHED 1")
    source = Source(
        events,
        end=checkpoint(modseq=101, threadmodseq=201),
        threads={"thread-1": records},
    )
    with pytest.raises(SyncResetRequired):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old


@pytest.mark.parametrize("kind", ["uid-reused", "wrong-uid", "duplicate-message-id"])
def test_inconsistent_uid_metadata_never_commits(
    state: SQLiteMailInventory, kind: str
) -> None:
    """A reused UID or aliased identity cannot overwrite an unrelated local row."""
    old = seed(state)
    if kind == "uid-reused":
        source = Source(
            ["* 1 FETCH (UID 1 MODSEQ (101) FLAGS ())"],
            records={1: message(1, message_id="wrong")},
        )
    elif kind == "wrong-uid":
        source = Source(
            ["* 1 FETCH (UID 1 MODSEQ (101) FLAGS ())"], records={1: message(2)}
        )
    else:
        source = Source(
            ["* 3 EXISTS (UID 3)"], records={3: message(3, message_id="test-message-2")}
        )
    with pytest.raises(SyncResetRequired):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old


def test_counter_race_during_metadata_read_keeps_old_state(
    state: SQLiteMailInventory,
) -> None:
    """Do not bind metadata from a later change to an earlier ending cursor."""
    old = seed(state)
    source = Source(["* 3 EXISTS (UID 3)"], records={3: message(3)})
    source.observed = checkpoint(modseq=102)
    with pytest.raises(SyncResetRequired, match="during reconciliation"):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old
    assert [row.uid for row in state.read_messages()] == [1, 2]


@pytest.mark.parametrize(
    "events,end",
    [
        (["* 3 EXISTS"], checkpoint(modseq=101)),
        (["* 1 EXPUNGE"], checkpoint(modseq=101)),
        (["* VANISHED 1"], checkpoint()),
        ([], checkpoint(modseq=101)),
        ([], checkpoint(modseq=99)),
        ([], checkpoint(uid_validity=124)),
    ],
)
def test_incomplete_or_stale_batch_is_not_an_implicit_reset(
    state: SQLiteMailInventory, events: list[str], end: SyncCheckpoint
) -> None:
    """Unsupported history, count-only hints, and scope drift require recovery."""
    old = seed(state)
    with pytest.raises(SyncResetRequired):
        apply_incremental(Source(events, end=end), state)
    assert state.read_checkpoint() == old


def test_engine_revalidates_an_adapter_batch(state: SQLiteMailInventory) -> None:
    """A reader implementation cannot bypass the engine's stored scope validation."""
    old = seed(state)
    source = Source([])
    bad = ChangeBatch(checkpoint=checkpoint(account="wrong"), events=())
    with (
        patch.object(source, "poll_changes", return_value=bad),
        pytest.raises(SyncResetRequired),
    ):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old


@pytest.mark.parametrize("option", ["max_messages", "max_lookups"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_invalid_reconciliation_budgets(
    state: SQLiteMailInventory, option: str, value: Any
) -> None:
    """Budgets are checked before reading state or touching the remote service."""
    seed(state)
    source = Source([])
    with (
        patch.object(
            state, "read_snapshot", side_effect=AssertionError("unexpected state read")
        ) as read_snapshot,
        pytest.raises(
            ValueError, match="^Mail reconciliation budgets must be positive integers$"
        ),
    ):
        apply_incremental(source, state, **{option: value})
    read_snapshot.assert_not_called()
    assert not source.poll_calls
    assert not source.uid_calls
    assert not source.thread_calls


def test_existing_inventory_over_budget_precedes_poll(
    state: SQLiteMailInventory,
) -> None:
    """A caller cannot accidentally process an oversized stored inventory."""
    seed(state)
    source = Source([])
    with pytest.raises(SyncResetRequired, match="Stored Mail inventory"):
        apply_incremental(source, state, max_messages=1)
    assert not source.poll_calls


def test_new_metadata_budget_preserves_checkpoint(state: SQLiteMailInventory) -> None:
    """An extra arrival cannot silently exceed or truncate the selected inventory."""
    old = seed(state)
    with pytest.raises(SyncResetRequired, match="metadata exceeds"):
        apply_incremental(
            Source(["* 3 EXISTS (UID 3)"], records={3: message(3)}),
            state,
            max_messages=2,
        )
    assert state.read_checkpoint() == old


@pytest.mark.parametrize("thread_mode", [False, True])
def test_lookup_budget_is_shared_by_threads_and_uids(
    state: SQLiteMailInventory, thread_mode: bool
) -> None:
    """Neither exact refreshes nor thread expansion can bypass the request budget."""
    old = seed(state)
    events = (
        [
            "* THREADUPDATE (thread-a NIL 201 FLAGS ())",
            "* THREADUPDATE (thread-b NIL 201 FLAGS ())",
        ]
        if thread_mode
        else ["* 3 EXISTS (UID 3)", "* 4 EXISTS (UID 4)"]
    )
    source = Source(events, end=checkpoint(modseq=101, threadmodseq=201))
    with pytest.raises(SyncResetRequired, match="lookup budget"):
        apply_incremental(source, state, max_lookups=1)
    assert state.read_checkpoint() == old


@pytest.mark.parametrize("noop", [False, True])
def test_local_writer_race_never_overwrites_newer_state(
    state: SQLiteMailInventory, noop: bool
) -> None:
    """CAS protects both no-op confirmation and a fully reconciled changed batch."""
    old = seed(state)
    source = Source(
        [] if noop else ["* 3 EXISTS (UID 3)"],
        end=checkpoint() if noop else checkpoint(modseq=101),
        records={3: message(3)},
    )

    def other_writer() -> None:
        state.replace_inventory(old, checkpoint(modseq=102), [message(9)])

    if noop:
        source.on_poll = other_writer
    else:
        source.on_observe = other_writer
    with pytest.raises(StateConflict):
        apply_incremental(source, state)
    assert state.read_messages() == (message(9),)
    current = state.read_checkpoint()
    assert current is not None and current.checkpoint.modseq == 102


def test_interrupted_thread_iterator_never_commits(state: SQLiteMailInventory) -> None:
    """A short failed stream is not a complete new thread inventory."""
    old = seed(state)

    def interrupted() -> Iterator[InventoryMessage]:
        yield message(1, thread_id="thread-1")
        raise RuntimeError("synthetic interrupted scan")

    source = Source(
        ["* THREADUPDATE (thread-1 1700000000 201 FLAGS ())"],
        end=checkpoint(modseq=101, threadmodseq=201),
        threads={"thread-1": interrupted()},
    )
    with pytest.raises(RuntimeError):
        apply_incremental(source, state)
    assert state.read_checkpoint() == old
