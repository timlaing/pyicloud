"""Offline guards for a private candidate; these tests do not establish sync."""

from typing import Any

from pydantic import ValidationError
import pytest

from pyicloud.services.mail._sync import (
    ArrivalEvent,
    ExistsEvent,
    MessageEvent,
    SyncCheckpoint,
    SyncResetRequired,
    ThreadEvent,
    ThreadInvalidationEvent,
    VanishedEvent,
    decode_events,
    validate_batch,
)
from tests.fixture_loader import load_fixture


def checkpoint(**changes: Any) -> SyncCheckpoint:
    """Use a fully synthetic mailbox namespace and request checkpoint."""
    return SyncCheckpoint.model_validate({
        "account": "synthetic-account",
        "mailbox_id": "synthetic-mailbox",
        "folder": "INBOX",
        "uid_validity": 123,
        "modseq": 100,
        "threadmodseq": 200,
        **changes,
    })


def test_synthetic_capture_shapes() -> None:
    """Counts, UID flags, tombstones, and threads keep distinct typed meanings."""
    fixture = load_fixture("mail", "sync_events_response.json")
    previous = checkpoint()
    current = checkpoint(modseq=105, threadmodseq=205)
    batch = validate_batch(previous, current, fixture["events"])
    assert batch.checkpoint == current
    assert batch.events == (
        ExistsEvent(count=4),
        MessageEvent(sequence=2, uid=17, modseq=103, flags=("\\Seen", "$Synthetic")),
        VanishedEvent(ranges=((3, 8), (12, 12))),
        ThreadEvent(
            thread_id="synthetic-thread",
            timestamp=1700000000000,
            modseq=204,
            flags=("\\Seen",),
            is_new=True,
        ),
    )
    assert previous == checkpoint()
    with pytest.raises(ValidationError):
        batch.checkpoint.modseq = 500


def test_empty_poll_and_empty_flags() -> None:
    """No-op polls and empty replacement flag sets are valid, distinct cases."""
    assert validate_batch(checkpoint(), checkpoint(), []).events == ()
    events = decode_events([
        "* 0 EXISTS",
        "* 1 FETCH (UID 1 MODSEQ (1) FLAGS ())",
        "* THREADUPDATE (thread-1 NIL 1 FLAGS ())",
    ])
    assert events[0] == ExistsEvent(count=0)
    assert isinstance(events[1], MessageEvent) and events[1].flags == ()
    assert isinstance(events[2], ThreadEvent) and events[2].timestamp is None
    assert not events[2].is_new


def test_thread_invalidation_is_not_an_empty_flag_update() -> None:
    """Observed flag-less NIL updates have their own narrowly typed meaning."""
    fixture = load_fixture("mail", "sync_thread_invalidation_response.json")
    batch = validate_batch(
        checkpoint(), checkpoint(modseq=101, threadmodseq=201), fixture["events"]
    )
    assert batch.events[-1] == ThreadInvalidationEvent(
        thread_id="synthetic-thread", modseq=201
    )
    assert not isinstance(batch.events[-1], ThreadEvent)


def test_ranges_are_compact_and_reversed_bounds_are_normalized() -> None:
    """An enormous UID interval cannot allocate billions of entries."""
    assert decode_events(["* VANISHED (EARLIER) 4294967295:1,9"]) == (
        VanishedEvent(ranges=((1, 4294967295), (9, 9)), earlier=True),
    )


def test_arrival_push_keeps_uid_separate_from_count_and_thread_id() -> None:
    """The observed UID-bearing EXISTS format is not a count-only notification."""
    fixture = load_fixture("mail", "sync_arrival_push.json")["mboxEvents"]["INBOX"]
    previous = checkpoint(
        modseq=fixture["prevModseq"], threadmodseq=fixture["prevThreadModseq"]
    )
    current = checkpoint(modseq=fixture["modseq"], threadmodseq=fixture["threadModseq"])
    batch = validate_batch(previous, current, fixture["events"])
    assert batch.events[0] == ArrivalEvent(count=5, uid=18)
    assert isinstance(batch.events[1], ThreadEvent)
    assert batch.events[1].is_new
    assert batch.checkpoint == current
    assert previous == checkpoint()


def test_arrival_without_message_counter_advancement_requires_reset() -> None:
    """Thread-only advancement cannot make a replayed arrival safe to apply."""
    with pytest.raises(SyncResetRequired, match="advancing message counter"):
        validate_batch(
            checkpoint(), checkpoint(threadmodseq=201), ["* 5 EXISTS (UID 18)"]
        )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "* 1 EXPUNGE",
        "* VANISHED *",
        "* VANISHED 1:",
        "* VANISHED 1:2:3",
        "* VANISHED 1,,2",
        "* VANISHED 0",
        "* VANISHED 4294967296",
        "* -1 EXISTS",
        "* 0 EXISTS (UID 1)",
        "* 1 EXISTS (UID 0)",
        "* 1 EXISTS (UID 4294967296)",
        "* 1 EXISTS (UID 1) trailing",
        "* 1 EXISTS (UID 1 FLAGS ())",
        "* 1 EXISTS trailing",
        "* 1 EXISTS\r\n",
        "* 1 EXIST\x7fS",
        "* １ EXISTS",
        "* 0 FETCH (UID 1 MODSEQ (1) FLAGS ())",
        "* 1 FETCH (UID 0 MODSEQ (1) FLAGS ())",
        "* 1 FETCH (UID 1 MODSEQ (0) FLAGS ())",
        '* 1 FETCH (UID 1 MODSEQ (1) FLAGS ("private-secret"))',
        "* THREADUPDATE (thread NIL 0 FLAGS ())",
        "* THREADUPDATE (thread NIL 0)",
        "* THREADUPDATE (thread 1700000000 201)",
        "* THREADUPDATE (thread NIL 201 NEW)",
        "* THREADUPDATE (thread NIL 201) trailing",
        "x" * 8193,
        None,
        1,
        True,
    ],
)
def test_invalid_event_rejects_entire_batch_without_echoing_data(value: Any) -> None:
    """Unsupported operations and malformed fields never silently disappear."""
    with pytest.raises(SyncResetRequired) as error:
        decode_events(["* 1 EXISTS", value])
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize("events", [None, (), "", ["* 1 EXISTS"] * 1025])
def test_invalid_batch_container_or_count(events: Any) -> None:
    """Bound event count before parsing or allocating a result."""
    with pytest.raises(SyncResetRequired):
        decode_events(events)


def test_total_character_budget() -> None:
    """Individually bounded events cannot bypass the overall parsing budget."""
    value = "* THREADUPDATE (" + "a" * 8000 + " NIL 1 FLAGS ())"
    with pytest.raises(SyncResetRequired, match="budget"):
        decode_events([value] * 132)


@pytest.mark.parametrize(
    "changes",
    [
        {"account": "other"},
        {"mailbox_id": "other"},
        {"folder": "Renamed"},
        {"uid_validity": 124},
        {"modseq": 99},
        {"threadmodseq": 199},
    ],
)
def test_checkpoint_scope_and_regression(changes: dict[str, Any]) -> None:
    """Account switches, moves, recreation, and old responses require recovery."""
    with pytest.raises(SyncResetRequired):
        validate_batch(checkpoint(), checkpoint(**changes), [])


def test_advancement_without_events_is_not_a_complete_change_batch() -> None:
    """Do not save an advanced cursor merely because an HTTP request succeeded."""
    with pytest.raises(SyncResetRequired):
        validate_batch(checkpoint(), checkpoint(modseq=101), [])


@pytest.mark.parametrize(
    "event",
    [
        "* 1 FETCH (UID 1 MODSEQ (100) FLAGS ())",
        "* 1 FETCH (UID 1 MODSEQ (106) FLAGS ())",
        "* THREADUPDATE (thread NIL 200 FLAGS ())",
        "* THREADUPDATE (thread NIL 206 FLAGS ())",
        "* THREADUPDATE (thread NIL 200)",
        "* THREADUPDATE (thread NIL 206)",
    ],
)
def test_event_outside_exact_request_stride_requires_reset(event: str) -> None:
    """Stale, overlapping, and future events cannot advance a candidate cursor."""
    with pytest.raises(SyncResetRequired):
        validate_batch(checkpoint(), checkpoint(modseq=105, threadmodseq=205), [event])


@pytest.mark.parametrize(
    "changes",
    [
        {"uid_validity": 0},
        {"modseq": -1},
        {"threadmodseq": True},
        {"modseq": "100"},
        {"account": ""},
        {"extra": "unexpected"},
    ],
)
def test_checkpoint_validation(changes: dict[str, Any]) -> None:
    """Persisted scope/counters must not accept coercions or invalid identities."""
    with pytest.raises(ValidationError):
        checkpoint(**changes)
