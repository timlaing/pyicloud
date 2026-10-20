"""Private incremental reconciliation of minimal, folder-scoped Mail metadata.

Read the saved snapshot, poll its exact counters, refresh affected metadata, and
publish only if a final observation still equals the batch's ending checkpoint.
Failures do not publish this worker's partial state. No remote mutations occur.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator
from typing import Protocol

from pyicloud.services.mail._sync import (
    ArrivalEvent,
    ChangeBatch,
    ExistsEvent,
    MessageEvent,
    SyncCheckpoint,
    SyncInventoryRequired,
    SyncResetRequired,
    ThreadEvent,
    ThreadInvalidationEvent,
    VanishedEvent,
    validate_decoded_batch,
)
from pyicloud.services.mail._sync_inventory import InventorySource, establish_inventory
from pyicloud.services.mail._sync_state import (
    InventoryMessage,
    SQLiteMailInventory,
    StoredCheckpoint,
)


class IncrementalSource(InventorySource, Protocol):
    """Checked read-only operations required to reconcile one checkpoint stride."""

    def poll_changes(self, previous: SyncCheckpoint) -> ChangeBatch:
        """Return a complete validated batch for exactly these request counters."""

    def iter_thread_inventory(self, thread_id: str) -> Iterator[InventoryMessage]:
        """Exhaust one changed thread's current, non-deleted folder membership."""

    def message_by_uid(self, uid: int) -> InventoryMessage | None:
        """Read an exact UID, or prove its absence in the pinned namespace."""


def _removed(batch: ChangeBatch) -> Callable[[int], bool]:
    ranges = sorted(
        pair
        for event in batch.events
        if isinstance(event, VanishedEvent)
        for pair in event.ranges
    )
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    starts = [pair[0] for pair in merged]

    def contains(uid: int) -> bool:
        index = bisect_right(starts, uid) - 1
        return index >= 0 and uid <= merged[index][1]

    return contains


def apply_incremental(
    source: IncrementalSource,
    state: SQLiteMailInventory,
    *,
    max_messages: int = 100000,
    max_lookups: int = 1000,
    allow_inventory_fallback: bool = False,
) -> StoredCheckpoint:
    """Reconcile one bounded batch and atomically save metadata plus its cursor.

    Count-only hints require an explicitly enabled full inventory. Unknown events,
    conflicting metadata, concurrent changes, and exhausted budgets never reset
    history or advance state automatically. Expiry never triggers this fallback.
    This is not a cross-folder move API.
    """
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (max_messages, max_lookups)
    ):
        raise ValueError("Mail reconciliation budgets must be positive integers")
    if not isinstance(allow_inventory_fallback, bool):
        raise ValueError("Mail inventory fallback must be explicitly boolean")
    expected, stored = state.read_snapshot()
    if expected is None:
        raise SyncResetRequired("Establish a complete Mail inventory before polling")
    if len(stored) > max_messages:
        raise SyncResetRequired("Stored Mail inventory exceeds the message budget")
    batch = validate_decoded_batch(
        expected.checkpoint, source.poll_changes(expected.checkpoint)
    )
    if not batch.events:
        return state.confirm_checkpoint(expected)
    if any(isinstance(event, ExistsEvent) for event in batch.events):
        if allow_inventory_fallback:
            return establish_inventory(
                source,
                state,
                max_attempts=1,
                max_messages=max_messages,
                expected_revision=expected,
                minimum_checkpoint=batch.checkpoint,
            )
        raise SyncInventoryRequired(
            "Count-only Mail change requires an explicit inventory"
        )
    original = {record.uid: record for record in stored}
    threads = {
        event.thread_id
        for event in batch.events
        if isinstance(event, (ThreadEvent, ThreadInvalidationEvent))
    }
    targets = {
        event.uid
        for event in batch.events
        if isinstance(event, (ArrivalEvent, MessageEvent))
    }
    targets.update(record.uid for record in stored if record.thread_id in threads)
    removed = _removed(batch)
    records = {
        record.uid: record
        for record in stored
        if record.thread_id not in threads and not removed(record.uid)
    }
    refreshed: set[int] = set()
    lookups = 0
    fetched = 0

    def accept(record: InventoryMessage) -> None:
        nonlocal fetched
        fetched += 1
        if (
            fetched > max_messages
            or len(records) + (record.uid not in records) > max_messages
        ):
            raise SyncResetRequired("Mail metadata exceeds the reconciliation budget")
        if record.uid in refreshed or removed(record.uid):
            raise SyncResetRequired(
                "Mail metadata conflicts with thread membership or removals"
            )
        old = original.get(record.uid)
        if old is not None and old.message_id != record.message_id:
            raise SyncResetRequired("Mail reused a UID in the same namespace")
        records[record.uid] = record
        refreshed.add(record.uid)

    for thread_id in sorted(threads):
        lookups += 1
        if lookups > max_lookups:
            raise SyncResetRequired("Mail reconciliation exhausted its lookup budget")
        for record in source.iter_thread_inventory(thread_id):
            if record.thread_id != thread_id:
                raise SyncResetRequired("Mail returned another thread's metadata")
            accept(record)
    for uid in sorted(targets - refreshed):
        lookups += 1
        if lookups > max_lookups:
            raise SyncResetRequired("Mail reconciliation exhausted its lookup budget")
        found = source.message_by_uid(uid)
        if found is None:
            records.pop(uid, None)
            continue
        if found.uid != uid or found.thread_id in threads:
            raise SyncResetRequired("Mail UID and complete-thread lookups disagree")
        accept(found)
    if len({record.message_id for record in records.values()}) != len(records):
        raise SyncResetRequired("Mail metadata contains duplicate message identifiers")
    if source.current_checkpoint() != batch.checkpoint:
        raise SyncResetRequired(
            "Mail changed during reconciliation; checkpoint retained"
        )
    return state.replace_inventory(expected, batch.checkpoint, records.values())
