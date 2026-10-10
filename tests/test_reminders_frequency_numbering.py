"""Apple numbers a recurrence rule's `Frequency` from 0; the enum numbers from 1.

On the wire 0 is daily, 1 weekly, 2 monthly and 3 yearly. Apple's reminders2
client maps it that way, and on 2026-09-24 a rule this library created with
`RecurrenceFrequency.DAILY` -- written as wire 1, upstream's number -- opened
in Apple's Reminders app as Weekly: a live write, the strongest evidence this
project ranks. Upstream reads and writes the enum's value 1:1, so every rule it
reads is labelled one cadence too frequent and every rule it writes is one
cadence too coarse.

The members keep upstream's names and values (drop-in); only the wire changes,
through `from_wire()` on read and `wire_value` on write. A number Apple sends
outside 0..3 is `UNKNOWN`, keeps Apple's number as its `wire_value`, and is
sent back unchanged.
"""

# pylint: disable=protected-access,super-init-not-called

from __future__ import annotations

import logging
import pickle
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.reminders import RemindersService
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._writes import RemindersWriteAPI
from pyicloud.services.reminders.models import (
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
)

WIRE = [
    (0, RecurrenceFrequency.DAILY),
    (1, RecurrenceFrequency.WEEKLY),
    (2, RecurrenceFrequency.MONTHLY),
    (3, RecurrenceFrequency.YEARLY),
]


def mapper() -> RemindersRecordMapper:
    """Mapper."""
    return RemindersRecordMapper(lambda: None, logging.getLogger("test"))


def rule_record(frequency: int, interval: int = 1) -> CKRecord:
    """Rule record."""
    return CKRecord.model_validate({
        "recordName": "RecurrenceRule/RR-1",
        "recordType": "RecurrenceRule",
        "recordChangeTag": "ctag-rr",
        "fields": {
            "Reminder": {
                "type": "REFERENCE",
                "value": {"recordName": "Reminder/R-1"},
            },
            "Frequency": {"type": "INT64", "value": frequency},
            "Interval": {"type": "INT64", "value": interval},
        },
    })


def read(frequency: int, interval: int = 1) -> RecurrenceRule:
    """Read."""
    return mapper().record_to_recurrence_rule(rule_record(frequency, interval))


def service() -> RemindersService:
    """Service."""
    svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
    svc._raw = MagicMock()
    svc._raw.modify.return_value = CKModifyResponse(records=[], syncToken="s")
    return svc


def created_frequency(svc: RemindersService) -> Any:
    """Created frequency."""
    operations = cast(MagicMock, svc._raw).modify.call_args.kwargs["operations"]
    (op,) = [o for o in operations if o.record.recordType == "RecurrenceRule"]
    return op.record.fields["Frequency"].value


class Updates(RemindersWriteAPI):
    """Updates."""

    def __init__(self) -> None:
        """Capture transport operations without initializing a real service."""
        self.submitted: list[dict[str, Any]] = []

    def _submit_single_record_update(self, **kwargs: Any) -> CKModifyResponse:
        """Submit single record update."""
        self.submitted.append(kwargs)
        return CKModifyResponse(records=[])


def reminder() -> Reminder:
    """Reminder."""
    return Reminder(
        id="Reminder/R-1",
        list_id="List/L-1",
        title="t",
        record_change_tag="c",
        alarm_ids=[],
        hashtag_ids=[],
        attachment_ids=[],
        recurrence_rule_ids=[],
    )


# --- reading ---------------------------------------------------------------


@pytest.mark.parametrize("wire,member", WIRE)
def test_apples_number_reads_as_the_cadence_apple_shows(
    wire: int, member: RecurrenceFrequency
) -> None:
    """Apples number reads as the cadence apple shows."""
    assert read(wire).frequency is member


@pytest.mark.parametrize("wire", [4, -1, 9])
def test_a_number_outside_apples_four_is_unknown_and_kept(wire: int) -> None:
    """A number outside apples four is unknown and kept."""
    frequency = read(wire).frequency

    assert frequency is not None
    assert frequency.name == "UNKNOWN"
    assert frequency.wire_value == wire
    # Unsupported wire values remain distinct from the named members.
    assert frequency not in list(RecurrenceFrequency)
    assert all(frequency != member for member in RecurrenceFrequency)


def test_every_two_weeks_reads_as_weekly_every_two() -> None:
    """This account's 40 "every 2 weeks on three days" rules carry Frequency 1
    and Interval 2; upstream reads them as every other DAY."""

    rule = read(1, interval=2)

    assert rule.frequency is RecurrenceFrequency.WEEKLY
    assert rule.interval == 2


# --- writing ---------------------------------------------------------------


@pytest.mark.parametrize("wire,member", WIRE)
def test_a_created_rule_sends_apples_number(
    wire: int, member: RecurrenceFrequency
) -> None:
    """A created rule sends apples number."""
    svc = service()

    created = svc.create_recurrence_rule(reminder(), frequency=member)

    assert created_frequency(svc) == wire
    assert created.frequency is member


def test_a_created_rule_from_a_plain_int_sends_apples_number() -> None:
    """Upstream callers may pass the enum's number; 2 is WEEKLY, Apple's 1."""

    svc = service()
    svc.create_recurrence_rule(reminder(), frequency=2)  # type: ignore[arg-type]

    assert created_frequency(svc) == 1


def test_a_rule_without_a_frequency_is_refused_before_any_request() -> None:
    """A new rule has no cadence to send; upstream's `int(None)` raised TypeError."""

    svc = service()

    with pytest.raises(ValueError, match="needs a frequency"):
        svc.create_recurrence_rule(reminder(), frequency=None)  # type: ignore[arg-type]

    cast(MagicMock, svc._raw).modify.assert_not_called()


@pytest.mark.parametrize("wire,member", WIRE)
def test_an_update_sends_apples_number(wire: int, member: RecurrenceFrequency) -> None:
    """An update sends apples number."""
    writes = Updates()
    rule = read(3)

    writes.update_recurrence_rule(rule, frequency=member)

    assert writes.submitted[0]["fields"]["Frequency"] == {
        "type": "INT64",
        "value": wire,
    }
    assert rule.frequency is member


@pytest.mark.parametrize("wire,member", WIRE)
def test_a_rule_read_and_written_back_is_unchanged(
    wire: int, member: RecurrenceFrequency
) -> None:
    """A rule read and written back is unchanged."""
    writes = Updates()
    rule = read(wire)
    assert rule.frequency is member

    writes.update_recurrence_rule(rule, frequency=rule.frequency)

    assert writes.submitted[0]["fields"]["Frequency"]["value"] == wire


@pytest.mark.parametrize("wire", [4, -1, 9])
def test_an_unknown_is_written_back_as_apple_sent_it(wire: int) -> None:
    """An unknown is written back as apple sent it."""
    writes = Updates()
    rule = read(wire)
    assert rule.frequency is not None
    assert rule.frequency.name == "UNKNOWN"

    writes.update_recurrence_rule(rule, frequency=rule.frequency)
    svc = service()
    svc.create_recurrence_rule(reminder(), frequency=rule.frequency)

    assert writes.submitted[0]["fields"]["Frequency"]["value"] == wire
    assert created_frequency(svc) == wire


# --- what did not change for a caller ---------------------------------------


def test_the_members_keep_upstreams_names_and_values() -> None:
    """The members keep upstreams names and values."""
    assert [(m.name, m.value) for m in RecurrenceFrequency] == [
        ("DAILY", 1),
        ("WEEKLY", 2),
        ("MONTHLY", 3),
        ("YEARLY", 4),
    ]
    assert RecurrenceFrequency(2) is RecurrenceFrequency.WEEKLY
    assert [m.wire_value for m in RecurrenceFrequency] == [0, 1, 2, 3]


def test_json_carries_the_enums_number_not_apples() -> None:
    """A stored dump means what it meant: 2 is WEEKLY, which Apple sends as 1."""

    rule = read(1)
    dumped = rule.model_dump(mode="json")

    assert dumped["frequency"] == 2
    assert RecurrenceRule.model_validate(dumped).frequency is RecurrenceFrequency.WEEKLY


def test_absent_frequency_is_unknown() -> None:
    """Absent frequency is unknown."""
    source = rule_record(0)
    del source.fields["Frequency"]
    assert mapper().record_to_recurrence_rule(source).frequency is None


@pytest.mark.parametrize("wire", [0, 1, 2, 3, 4, -1, 9])
def test_frequency_survives_pickle(wire: int) -> None:
    """Frequency survives pickle."""
    frequency = RecurrenceFrequency.from_wire(wire)
    restored = pickle.loads(pickle.dumps(frequency))
    assert restored == frequency
    assert restored.name == frequency.name
    assert restored.wire_value == wire
