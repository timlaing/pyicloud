"""Private change-decoding candidate, not a synchronization or persistence API.

Unknown events invalidate a batch rather than being silently discarded. A caller
must reconcile a complete inventory and apply every change before saving a new
checkpoint. These pure helpers do not fetch mail or advance stored state.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import SyncHistoryExpired, SyncInventoryRequired, SyncResetRequired

__all__ = ["SyncHistoryExpired", "SyncInventoryRequired", "SyncResetRequired"]

Number = Annotated[int, Field(ge=0)]
UID = Annotated[int, Field(ge=1, le=4294967295)]
PositiveNumber = Annotated[int, Field(ge=1)]
Identity = Annotated[str, Field(min_length=1)]


class SyncModel(BaseModel):
    """Strict immutable data shared by the private synchronization candidate."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ExistsEvent(SyncModel):
    """A mailbox count hint, not a UID or an independently identified new message."""

    kind: Literal["exists"] = "exists"
    count: Number


class MessageEvent(SyncModel):
    """A UID-addressed replacement flag set at a message modification sequence."""

    kind: Literal["message"] = "message"
    sequence: PositiveNumber
    uid: UID
    modseq: PositiveNumber
    flags: tuple[str, ...]


class ArrivalEvent(SyncModel):
    """A newly announced message UID, without metadata or an individual counter."""

    kind: Literal["arrival"] = "arrival"
    count: PositiveNumber
    uid: UID


class VanishedEvent(SyncModel):
    """Inclusive UID ranges, kept compact instead of expanding untrusted sets."""

    kind: Literal["vanished"] = "vanished"
    ranges: tuple[tuple[UID, UID], ...]
    earlier: bool = False


class ThreadEvent(SyncModel):
    """A separate thread update; its identifier is never a message UID."""

    kind: Literal["thread"] = "thread"
    thread_id: Identity = Field(repr=False)
    timestamp: Number | None
    modseq: PositiveNumber
    flags: tuple[str, ...]
    is_new: bool


class ThreadInvalidationEvent(SyncModel):
    """A NIL update without flags; refresh membership, do not infer deletion."""

    kind: Literal["thread-invalidation"] = "thread-invalidation"
    thread_id: Identity = Field(repr=False)
    modseq: PositiveNumber


ChangeEvent = (
    ExistsEvent
    | ArrivalEvent
    | MessageEvent
    | VanishedEvent
    | ThreadEvent
    | ThreadInvalidationEvent
)


class SyncCheckpoint(SyncModel):
    """Candidate scope and counters; not evidence of a completed baseline scan."""

    account: Identity = Field(repr=False)
    mailbox_id: Identity = Field(repr=False)
    folder: Identity = Field(repr=False)
    uid_validity: PositiveNumber
    modseq: Number
    threadmodseq: Number


class ChangeBatch(SyncModel):
    """Validated candidate batch; checkpoint persistence is the caller's job."""

    checkpoint: SyncCheckpoint
    events: tuple[ChangeEvent, ...]


_EXISTS = re.compile(r"\* (\d+) EXISTS", re.ASCII)
_ARRIVAL = re.compile(r"\* (\d+) EXISTS \(UID (\d+)\)", re.ASCII)
_MESSAGE = re.compile(
    r"\* (\d+) FETCH \(UID (\d+) MODSEQ \((\d+)\) FLAGS \(([^()]*)\)\)",
    re.ASCII,
)
_VANISHED = re.compile(r"\* VANISHED (\(EARLIER\) )?([\d:,]+)", re.ASCII)
_THREAD = re.compile(
    r"\* THREADUPDATE \(([A-Za-z0-9+/=_-]+) (\d+|NIL) (\d+) "
    r"FLAGS \(([^()]*)\)( NEW)?\)",
    re.ASCII,
)
_FLAGS = re.compile(r"(?:\\?[A-Za-z0-9$_.-]+(?: \\?[A-Za-z0-9$_.-]+)*)?")
_THREAD_INVALIDATION = re.compile(
    r"\* THREADUPDATE \(([A-Za-z0-9+/=_-]+) NIL (\d+)\)", re.ASCII
)


def _flags(value: str) -> tuple[str, ...]:
    if not _FLAGS.fullmatch(value):
        raise SyncResetRequired("Unsupported Mail event flags")
    return tuple(value.split())


def _event(value: str) -> ChangeEvent:
    if match := _EXISTS.fullmatch(value):
        return ExistsEvent(count=int(match[1]))
    if match := _ARRIVAL.fullmatch(value):
        return ArrivalEvent(count=int(match[1]), uid=int(match[2]))
    if match := _MESSAGE.fullmatch(value):
        return MessageEvent(
            sequence=int(match[1]),
            uid=int(match[2]),
            modseq=int(match[3]),
            flags=_flags(match[4]),
        )
    if match := _VANISHED.fullmatch(value):
        ranges = []
        for item in match[2].split(","):
            bounds = item.split(":")
            if len(bounds) not in (1, 2):
                raise SyncResetRequired("Unsupported Mail UID range")
            first, last = int(bounds[0]), int(bounds[-1])
            ranges.append((min(first, last), max(first, last)))
        return VanishedEvent(ranges=tuple(ranges), earlier=bool(match[1]))
    if match := _THREAD.fullmatch(value):
        return ThreadEvent(
            thread_id=match[1],
            timestamp=None if match[2] == "NIL" else int(match[2]),
            modseq=int(match[3]),
            flags=_flags(match[4]),
            is_new=bool(match[5]),
        )
    if match := _THREAD_INVALIDATION.fullmatch(value):
        return ThreadInvalidationEvent(thread_id=match[1], modseq=int(match[2]))
    raise SyncResetRequired("Unsupported Mail change event; inventory reset required")


def decode_events(values: list[str]) -> tuple[ChangeEvent, ...]:
    """Decode bounded event strings without dropping unknown or malformed input."""
    if not isinstance(values, list) or len(values) > 1024:
        raise SyncResetRequired("Invalid or oversized Mail event batch")
    total = 0
    events = []
    for value in values:
        if (
            not isinstance(value, str)
            or len(value) > 8192
            or not value.isascii()
            or any(ord(char) < 32 for char in value)
            or "\x7f" in value
        ):
            raise SyncResetRequired("Invalid or oversized Mail event string")
        total += len(value)
        if total > 1048576:
            raise SyncResetRequired("Mail event batch exceeds decoding budget")
        try:
            events.append(_event(value))
        except ValueError:
            raise SyncResetRequired(
                "Mail change event requires inventory reset"
            ) from None
    return tuple(events)


def validate_batch(
    previous: SyncCheckpoint, current: SyncCheckpoint, raw_events: list[str]
) -> ChangeBatch:
    """Decode a wire batch, then validate its exact request scope and stride."""
    return validate_decoded_batch(
        previous, ChangeBatch(checkpoint=current, events=decode_events(raw_events))
    )


def validate_decoded_batch(previous: SyncCheckpoint, batch: ChangeBatch) -> ChangeBatch:
    """Reject mismatched scopes, counter regression, and unaccounted advancement.

    Each response must be paired with its exact request checkpoint. There is no
    push-event merging, persisted-state mutation, or implicit baseline creation.
    """
    current, events = batch.checkpoint, batch.events
    identity = ("account", "mailbox_id", "folder", "uid_validity")
    if any(getattr(previous, key) != getattr(current, key) for key in identity):
        raise SyncResetRequired(
            "Mail checkpoint scope changed; inventory reset required"
        )
    if current.modseq < previous.modseq or current.threadmodseq < previous.threadmodseq:
        raise SyncResetRequired(
            "Mail change counters regressed; inventory reset required"
        )
    if not events and current != previous:
        raise SyncResetRequired(
            "Mail counters advanced without a supported change batch"
        )
    for event in events:
        _validate_event_stride(previous, current, event)
    return batch


def _validate_event_stride(
    previous: SyncCheckpoint, current: SyncCheckpoint, event: ChangeEvent
) -> None:
    """Check one event against the exact message or thread counter interval."""
    if isinstance(event, VanishedEvent) and current.modseq == previous.modseq:
        raise SyncResetRequired("Mail removals have no advancing message counter")
    if isinstance(event, ArrivalEvent) and current.modseq == previous.modseq:
        raise SyncResetRequired("Mail arrival has no advancing message counter")
    if isinstance(event, MessageEvent) and not (
        previous.modseq < event.modseq <= current.modseq
    ):
        raise SyncResetRequired("Mail message event is outside the requested stride")
    if isinstance(event, (ThreadEvent, ThreadInvalidationEvent)) and not (
        previous.threadmodseq < event.modseq <= current.threadmodseq
    ):
        raise SyncResetRequired("Mail thread event is outside the requested stride")
