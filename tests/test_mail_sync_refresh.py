"""Offline explicit history recovery without silent resets or remote writes."""

from collections.abc import Iterator
from contextlib import closing
import sqlite3
from typing import Any
from unittest.mock import Mock, patch

import pytest

from pyicloud.services.mail._sync import (
    SyncHistoryExpired,
    SyncResetRequired,
    validate_batch,
)
from pyicloud.services.mail._sync_refresh import refresh_inventory
from pyicloud.services.mail._sync_state import SQLiteMailInventory, StateConflict
from tests.test_mail_sync_inventory import checkpoint, interrupted, message


@pytest.fixture(name="state")
def state_fixture() -> Iterator[SQLiteMailInventory]:
    """Observe real transaction rollback using in-memory SQLite."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield SQLiteMailInventory(connection)


def source_fixture() -> Mock:
    """Expose only the verified read operations on the synthetic source."""
    source = Mock(spec=["poll_changes", "current_checkpoint", "iter_inventory"])
    source.current_checkpoint.return_value = checkpoint(modseq=110)
    source.iter_inventory.return_value = iter([message(2)])
    source.poll_changes.side_effect = SyncHistoryExpired("expired")
    return source


def test_new_inventory_needs_no_recovery_permission(state: SQLiteMailInventory) -> None:
    """Build a stable baseline rather than polling nonexistent counters."""
    source = source_fixture()
    saved = refresh_inventory(source, state)
    assert saved.revision == 1
    assert state.read_messages() == (message(2),)
    source.poll_changes.assert_not_called()


def test_normal_poll_does_not_rebuild(state: SQLiteMailInventory) -> None:
    """Opting into expiry recovery does not rebuild healthy history."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    source.poll_changes.side_effect = None
    source.poll_changes.return_value = validate_batch(checkpoint(), checkpoint(), [])
    assert refresh_inventory(source, state, rebuild_expired=True) == saved
    source.poll_changes.assert_called_once_with(checkpoint())
    source.iter_inventory.assert_not_called()
    source.current_checkpoint.assert_not_called()


def test_writer_before_first_scan_cannot_be_overwritten(
    state: SQLiteMailInventory,
) -> None:
    """An empty-store decision cannot silently reset a newly established baseline."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    with (
        patch.object(state, "read_checkpoint", side_effect=[None, saved]),
        pytest.raises(StateConflict),
    ):
        refresh_inventory(source, state)
    assert not source.mock_calls
    assert state.read_snapshot() == (saved, (message(1),))


def test_expiry_default_preserves_old_state(state: SQLiteMailInventory) -> None:
    """Report expiry without issuing a null-counter baseline read."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    with pytest.raises(SyncHistoryExpired):
        refresh_inventory(source, state)
    assert state.read_snapshot() == (saved, (message(1),))
    source.iter_inventory.assert_not_called()
    source.current_checkpoint.assert_not_called()


def test_opted_in_expiry_replaces_inventory_atomically(
    state: SQLiteMailInventory,
) -> None:
    """Fully replace stale records only after a complete stable baseline."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    result = refresh_inventory(source, state, rebuild_expired=True)
    assert result.revision == saved.revision + 1
    assert result.checkpoint == checkpoint(modseq=110)
    assert state.read_snapshot() == (result, (message(2),))
    source.poll_changes.assert_called_once_with(saved.checkpoint)
    assert source.current_checkpoint.call_count == 2


@pytest.mark.parametrize(
    "failure", [SyncResetRequired("unknown"), RuntimeError("network")]
)
def test_other_failures_never_recover(
    state: SQLiteMailInventory, failure: Exception
) -> None:
    """Expiry opt-in does not authorize resets for other failures."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    source.poll_changes.side_effect = failure
    with pytest.raises(type(failure)) as error:
        refresh_inventory(source, state, rebuild_expired=True)
    assert error.value is failure
    source.current_checkpoint.assert_not_called()
    assert state.read_snapshot() == (saved, (message(1),))


@pytest.mark.parametrize(
    "kind", ["interrupt", "unstable", "scope", "regression", "budget"]
)
def test_failed_recovery_preserves_prior_snapshot(
    state: SQLiteMailInventory, kind: str
) -> None:
    """Never clear or advance state before all bounded reads have passed."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()
    if kind == "interrupt":
        source.iter_inventory.return_value = interrupted()
    elif kind == "unstable":
        source.current_checkpoint.side_effect = [
            checkpoint(modseq=110),
            checkpoint(modseq=111),
        ]
    elif kind == "scope":
        source.current_checkpoint.return_value = checkpoint(account="other")
    elif kind == "regression":
        source.current_checkpoint.return_value = checkpoint(modseq=99)
    else:
        source.iter_inventory.return_value = iter([message(2), message(3)])
    with pytest.raises((SyncResetRequired, RuntimeError)):
        refresh_inventory(
            source, state, rebuild_expired=True, max_attempts=1, max_messages=1
        )
    assert state.read_snapshot() == (saved, (message(1),))


def test_writer_during_expiry_cannot_be_overwritten(state: SQLiteMailInventory) -> None:
    """Pin the baseline to the revision from before the failed poll."""
    saved = state.replace_inventory(None, checkpoint(), [message(1)])
    source = source_fixture()

    def expire_with_competing_writer(_previous: object) -> None:
        state.replace_inventory(saved, checkpoint(modseq=105), [message(3)])
        raise SyncHistoryExpired("expired")

    source.poll_changes.side_effect = expire_with_competing_writer
    with pytest.raises(StateConflict):
        refresh_inventory(source, state, rebuild_expired=True)
    source.current_checkpoint.assert_not_called()
    assert state.read_messages() == (message(3),)


@pytest.mark.parametrize(
    "option",
    [
        "rebuild_expired",
        "allow_inventory_fallback",
        "max_attempts",
        "max_messages",
        "max_lookups",
    ],
)
def test_invalid_controls_precede_io(state: SQLiteMailInventory, option: str) -> None:
    """Reject truthy recovery switches or invalid budgets before reads."""
    source = source_fixture()
    options: dict[str, Any] = {option: "yes"}
    with pytest.raises(ValueError):
        refresh_inventory(source, state, **options)
    assert not source.mock_calls
