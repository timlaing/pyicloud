"""Private baseline algorithm; requires a verified, caller-supplied read source.

Counter stability is a necessary handoff condition, not a claim that Apple's
counter/history semantics have been fully verified. The adapter is private; no
public sync API or CLI is exposed. Only minimal metadata is stored locally.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from typing import Protocol

from pyicloud.services.mail._sync import SyncCheckpoint, SyncResetRequired
from pyicloud.services.mail._sync_state import (
    InventoryMessage,
    SQLiteMailInventory,
    StateConflict,
    StoredCheckpoint,
)


class InventorySource(Protocol):
    """Read-only source with verified account, mailbox, and UID namespace context."""

    def current_checkpoint(self) -> SyncCheckpoint:
        """Read current counters without silently resetting an expired session."""

    def iter_inventory(self) -> Iterator[InventoryMessage]:
        """Exhaust unfiltered metadata, raising on incomplete or inconsistent scans."""


class _RevisionPolicy(Enum):
    """Distinguish an omitted revision from an explicitly pinned empty store."""

    ANY = "any"


def establish_inventory(
    source: InventorySource,
    state: SQLiteMailInventory,
    *,
    max_attempts: int = 3,
    max_messages: int = 100000,
    expected_revision: StoredCheckpoint | None | _RevisionPolicy = _RevisionPolicy.ANY,
    minimum_checkpoint: SyncCheckpoint | None = None,
) -> StoredCheckpoint:
    """Publish only a bounded inventory bracketed by identical checkpoints.

    Changed counters/namespace discard the whole attempt. Partial reads, exhausted
    budgets, or local writer conflicts retain the previously committed inventory.
    A later remote change belongs to the next poll, not this stored checkpoint.
    An explicitly supplied None pins an empty store; omission reads its revision.
    A fallback's minimum checkpoint forbids regression from an observed batch.
    """
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (max_attempts, max_messages)
    ):
        raise ValueError("Mail inventory budgets must be positive integers")
    expected = state.read_checkpoint()
    if expected_revision is not _RevisionPolicy.ANY and expected != expected_revision:
        raise StateConflict("Mail inventory changed before baseline fallback")
    scope = (
        None
        if expected is None
        else (expected.checkpoint.account, expected.checkpoint.mailbox_id)
    )
    for _ in range(max_attempts):
        before = source.current_checkpoint()
        if minimum_checkpoint is not None and (
            any(
                getattr(before, field) != getattr(minimum_checkpoint, field)
                for field in ("account", "mailbox_id", "folder", "uid_validity")
            )
            or before.modseq < minimum_checkpoint.modseq
            or before.threadmodseq < minimum_checkpoint.threadmodseq
        ):
            raise SyncResetRequired(
                "Mail inventory regressed from the observed change batch"
            )
        if scope is None:
            scope = (before.account, before.mailbox_id)
        if scope != (before.account, before.mailbox_id):
            raise SyncResetRequired(
                "Mail inventory source belongs to another account/mailbox"
            )
        messages: list[InventoryMessage] = []
        uids: set[int] = set()
        identifiers: set[str] = set()
        for message in source.iter_inventory():
            if len(messages) >= max_messages:
                raise SyncResetRequired("Mail inventory exceeds the message budget")
            if message.uid in uids or message.message_id in identifiers:
                raise SyncResetRequired("Mail inventory returned duplicate identities")
            messages.append(message)
            uids.add(message.uid)
            identifiers.add(message.message_id)
        after = source.current_checkpoint()
        if scope != (after.account, after.mailbox_id):
            raise SyncResetRequired(
                "Mail inventory source changed account/mailbox during scan"
            )
        if after != before:
            continue
        return state.replace_inventory(expected, after, messages)
    raise SyncResetRequired("Mail changed during every inventory attempt; retry later")
