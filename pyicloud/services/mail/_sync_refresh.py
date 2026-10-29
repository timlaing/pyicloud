"""Private read-only orchestration with explicit, narrowly typed expiry recovery."""

from pyicloud.services.mail._sync_apply import IncrementalSource, apply_incremental
from pyicloud.services.mail._sync_inventory import establish_inventory
from pyicloud.services.mail._sync_state import SQLiteMailInventory, StoredCheckpoint
from pyicloud.services.mail.exceptions import SyncHistoryExpired


def refresh_inventory(
    source: IncrementalSource,
    state: SQLiteMailInventory,
    *,
    rebuild_expired: bool = False,
    allow_inventory_fallback: bool = False,
    max_attempts: int = 3,
    max_messages: int = 100000,
    max_lookups: int = 1000,
) -> StoredCheckpoint:
    """Baseline a new store or apply deltas, retaining old state on any failure.

    Only an explicitly reported history-expiry error can opt into a bounded,
    stable full replacement. Unknown events, authorization failures, corruption,
    and local writer conflicts never authorize a reset. Rebuilds stay pinned to
    the original account/mailbox and local revision; nothing is deleted first.
    """
    if not isinstance(rebuild_expired, bool) or not isinstance(
        allow_inventory_fallback, bool
    ):
        raise ValueError("Mail recovery options must be explicitly boolean")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (max_attempts, max_messages, max_lookups)
    ):
        raise ValueError("Mail recovery budgets must be positive integers")
    expected = state.read_checkpoint()
    if expected is None:
        return establish_inventory(
            source,
            state,
            max_attempts=max_attempts,
            max_messages=max_messages,
            expected_revision=None,
        )
    try:
        return apply_incremental(
            source,
            state,
            max_messages=max_messages,
            max_lookups=max_lookups,
            allow_inventory_fallback=allow_inventory_fallback,
        )
    except SyncHistoryExpired:
        if not rebuild_expired:
            raise
        return establish_inventory(
            source,
            state,
            max_attempts=max_attempts,
            max_messages=max_messages,
            expected_revision=expected,
        )
