"""Missing server numeric fields remain unknown; caller defaults stay compatible."""

# pylint: disable=protected-access,super-init-not-called

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKQueryResponse, CKRecord
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._writes import RemindersWriteAPI
from pyicloud.services.reminders.models import (
    LocationTrigger,
    RecurrenceFrequency,
    RecurrenceRule,
    Reminder,
)
from pyicloud.services.reminders.service import RemindersService


def mapper() -> RemindersRecordMapper:
    """Mapper."""
    return RemindersRecordMapper(SimpleNamespace, logging.getLogger("test"))


def record(record_type: str, name: str, fields: dict[str, Any]) -> CKRecord:
    """Record."""
    return CKRecord.model_validate({
        "recordName": name,
        "recordType": record_type,
        "fields": fields,
    })


def text(value: str) -> dict[str, Any]:
    """Text."""
    return {"type": "STRING", "value": value}


def int64(value: int) -> dict[str, Any]:
    """Int64."""
    return {"type": "INT64", "value": value}


ALARM_REF = {"type": "REFERENCE", "value": {"recordName": "Alarm/1"}}
REMINDER_REF = {"type": "REFERENCE", "value": {"recordName": "Reminder/1"}}


def bare_rule(**fields: Any) -> RecurrenceRule:
    """Bare rule."""
    return mapper().record_to_recurrence_rule(
        record(
            "RecurrenceRule",
            "RecurrenceRule/1",
            {"Reminder": REMINDER_REF, "Frequency": int64(2), **fields},
        )
    )


# --- reads ---------------------------------------------------------------


def test_a_location_trigger_without_a_radius_has_none() -> None:
    """A location trigger without a radius has none."""
    found = mapper().record_to_alarm_trigger(
        record(
            "AlarmTrigger",
            "AlarmTrigger/1",
            {"Alarm": ALARM_REF, "Type": text("Location")},
        )
    )

    assert isinstance(found, LocationTrigger)
    assert found.radius is None


def test_a_radius_apple_sent_as_zero_is_kept() -> None:
    """A radius apple sent as zero is kept."""
    found = mapper().record_to_alarm_trigger(
        record(
            "AlarmTrigger",
            "AlarmTrigger/1",
            {
                "Alarm": ALARM_REF,
                "Type": text("Location"),
                "Radius": {"type": "DOUBLE", "value": 0.0},
            },
        )
    )

    assert isinstance(found, LocationTrigger)
    assert found.radius == 0.0


def test_a_rule_without_its_numeric_fields_reports_each_as_none() -> None:
    """A rule without its numeric fields reports each as none."""
    found = bare_rule()

    assert found.interval is None
    assert found.occurrence_count is None
    assert found.first_day_of_week is None


def test_numeric_rule_fields_apple_sent_are_kept_including_zero() -> None:
    """Numeric rule fields apple sent are kept including zero."""
    found = bare_rule(
        Interval=int64(3),
        OccurrenceCount=int64(0),
        FirstDayOfTheWeek=int64(0),
    )

    assert found.interval == 3
    assert found.occurrence_count == 0
    assert found.first_day_of_week == 0


@pytest.mark.parametrize(
    "field,value,attribute",
    [
        ("Interval", 0, "interval"),
        ("Interval", -3, "interval"),
        ("OccurrenceCount", -1, "occurrence_count"),
        ("FirstDayOfTheWeek", 7, "first_day_of_week"),
        ("FirstDayOfTheWeek", -1, "first_day_of_week"),
    ],
)
def test_an_out_of_range_value_apple_sent_is_unknown_not_a_failure(
    field: str, value: int, attribute: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The model's bounds check CALLER input. A value Apple sent outside them
    used to raise out of the mapper -- and `or 1` reported a sent Interval 0
    as 1 before that. Now the record survives with the field unknown."""

    with caplog.at_level(logging.WARNING, logger="test"):
        found = bare_rule(**{field: int64(value)})

    assert found.id == "RecurrenceRule/1"
    assert getattr(found, attribute) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert field in message
    # Neither the record's name nor the value reaches the log.
    assert "RecurrenceRule/1" not in message
    assert str(value) not in message


def test_in_range_edges_are_kept_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In range edges are kept without a warning."""
    with caplog.at_level(logging.WARNING, logger="test"):
        found = bare_rule(
            Interval=int64(1),
            OccurrenceCount=int64(0),
            FirstDayOfTheWeek=int64(6),
        )

    assert (found.interval, found.occurrence_count, found.first_day_of_week) == (
        1,
        0,
        6,
    )
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def _reminder_record(reminder_id: str) -> CKRecord:
    """Reminder record."""
    return record(
        "Reminder",
        reminder_id,
        {
            "List": {
                "type": "REFERENCE",
                "value": {"recordName": "List/A", "action": "VALIDATE"},
            },
            "Completed": int64(0),
            "Deleted": int64(0),
        },
    )


def _rule_record(rule_id: str, reminder_id: str, **fields: Any) -> CKRecord:
    """Rule record."""
    return record(
        "RecurrenceRule",
        rule_id,
        {
            "Reminder": {
                "type": "REFERENCE",
                "value": {"recordName": reminder_id, "action": "VALIDATE"},
            },
            "Frequency": int64(2),
            **fields,
        },
    )


def test_a_list_read_keeps_every_sibling_of_an_out_of_range_rule() -> None:
    """A list read keeps every sibling of an out of range rule."""
    svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
    svc._raw = MagicMock()
    svc._raw.query.return_value = CKQueryResponse(
        records=[
            _reminder_record("Reminder/A"),
            _reminder_record("Reminder/B"),
            _rule_record("RecurrenceRule/BAD", "Reminder/A", Interval=int64(0)),
            _rule_record(
                "RecurrenceRule/ALSO-BAD",
                "Reminder/A",
                OccurrenceCount=int64(-1),
                FirstDayOfTheWeek=int64(7),
            ),
            _rule_record("RecurrenceRule/GOOD", "Reminder/B", Interval=int64(2)),
        ],
        continuationMarker=None,
    )

    result = svc.list_reminders(list_id="List/A", include_completed=True)

    assert {r.id for r in result.reminders} == {"Reminder/A", "Reminder/B"}
    rules = result.recurrence_rules
    assert set(rules) == {
        "RecurrenceRule/BAD",
        "RecurrenceRule/ALSO-BAD",
        "RecurrenceRule/GOOD",
    }
    assert rules["RecurrenceRule/BAD"].interval is None
    assert rules["RecurrenceRule/ALSO-BAD"].occurrence_count is None
    assert rules["RecurrenceRule/ALSO-BAD"].first_day_of_week is None
    assert rules["RecurrenceRule/GOOD"].interval == 2


# --- writes --------------------------------------------------------------


class Writes(RemindersWriteAPI):
    """The write API with its transport calls captured."""

    def __init__(self) -> None:
        """Capture transport operations without initializing a real service."""
        self.submitted: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []

    def _submit_single_record_update(self, **kwargs: Any) -> CKModifyResponse:
        """Submit single record update."""
        self.submitted.append(kwargs)
        return CKModifyResponse(records=[])

    def _create_linked_child(self, **kwargs: Any) -> tuple[str, Any]:
        """Create linked child."""
        self.created.append(kwargs)
        return "RecurrenceRule/NEW-1", SimpleNamespace(records=[])


def test_updating_a_rule_read_without_fields_writes_none_of_them() -> None:
    """The round trip: an absent Interval must not come back as a written 1."""

    rule = bare_rule()
    api = Writes()

    api.update_recurrence_rule(rule, frequency=RecurrenceFrequency.WEEKLY)

    fields = api.submitted[0]["fields"]
    assert set(fields) == {"Frequency", "Reminder"}
    assert rule.interval is None
    assert rule.occurrence_count is None
    assert rule.first_day_of_week is None


def test_a_create_writes_the_callers_arguments() -> None:
    """A create writes the callers arguments."""
    api = Writes()
    reminder = Reminder(id="Reminder/1", list_id="List/1", title="T")

    created = api.create_recurrence_rule(
        reminder, interval=2, occurrence_count=5, first_day_of_week=1
    )

    fields = api.created[0]["child_fields"]
    assert fields["Interval"] == int64(2)
    assert fields["OccurrenceCount"] == int64(5)
    assert fields["FirstDayOfTheWeek"] == int64(1)
    assert (
        created.interval,
        created.occurrence_count,
        created.first_day_of_week,
    ) == (2, 5, 1)


def test_a_create_still_refuses_an_interval_below_one() -> None:
    """A create still refuses an interval below one."""
    api = Writes()
    reminder = Reminder(id="Reminder/1", list_id="List/1", title="T")

    with pytest.raises(ValidationError):
        api.create_recurrence_rule(reminder, interval=0)
    assert not api.created


@pytest.mark.parametrize(
    "argument,value",
    [("interval", 0), ("occurrence_count", -1), ("first_day_of_week", 7)],
)
def test_an_update_still_refuses_out_of_range_caller_input(
    argument: str, value: int
) -> None:
    """An update still refuses out of range caller input."""
    api = Writes()

    with pytest.raises(ValidationError):
        arguments: dict[str, Any] = {argument: value}
        api.update_recurrence_rule(bare_rule(), **arguments)
    assert not api.submitted


def test_caller_constructor_defaults_remain_compatible() -> None:
    """Caller constructor defaults remain compatible."""
    rule = RecurrenceRule(id="RecurrenceRule/1", reminder_id="Reminder/1")
    trigger = LocationTrigger(id="AlarmTrigger/1", alarm_id="Alarm/1")
    assert (rule.interval, rule.occurrence_count, rule.first_day_of_week) == (1, 0, 0)
    assert trigger.radius == 0.0
