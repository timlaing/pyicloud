"""Timed reminder due dates carry a time zone so devices show the right time."""

# pylint: disable=protected-access

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from pyicloud.common.cloudkit import CKModifyResponse, CKRecord
from pyicloud.services.reminders._writes import _default_time_zone
from pyicloud.services.reminders.models import Reminder
from pyicloud.services.reminders.service import RemindersService

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@pytest.mark.parametrize(
    ("due_date", "all_day", "expected"),
    [
        (datetime(2026, 10, 20, 9, 0, tzinfo=AMSTERDAM), False, "Europe/Amsterdam"),
        (datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), False, "UTC"),
        (datetime(2026, 10, 20, 9, 0), False, "UTC"),
        (
            datetime(2026, 10, 20, 9, 0, tzinfo=timezone(timedelta(hours=2))),
            False,
            None,
        ),
        (datetime(2026, 10, 20, 9, 0, tzinfo=AMSTERDAM), True, None),
        (None, False, None),
    ],
)
def test_default_time_zone(
    due_date: datetime | None, all_day: bool, expected: str | None
) -> None:
    """ZoneInfo names and UTC are stored; fixed offsets and all-day are not."""
    assert _default_time_zone(due_date, all_day) == expected


def service() -> RemindersService:
    """Create a service with a mocked CloudKit client."""
    svc = RemindersService("https://ckdatabasews.icloud.com", MagicMock(), {})
    raw = MagicMock()
    # update() reads the current server copy first; an empty record list makes
    # it fall back to a fresh document, which keeps these tests focused on the
    # time-zone field rather than on CRDT text handling.
    raw.lookup.return_value = MagicMock(records=[])
    raw.modify.return_value = CKModifyResponse(
        records=[
            CKRecord.model_validate({
                "recordName": "Reminder/REM-TZ",
                "recordType": "Reminder",
                "recordChangeTag": "new-tag",
                "fields": {},
            })
        ],
        syncToken="mock-sync",
    )
    svc._raw = raw
    return svc


def written_fields(svc: RemindersService) -> Any:
    """Return the fields of the record written by the last modify call."""
    raw: Any = svc._raw
    return raw.modify.call_args.kwargs["operations"][0].record.fields


def create(
    svc: RemindersService, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> Any:
    """Create a reminder and return the written fields."""
    monkeypatch.setattr(
        svc._writes,
        "_lookup_created_reminder",
        MagicMock(return_value=Reminder(id="Reminder/X", list_id="List/L", title="T")),
    )
    svc.create(list_id="List/L", title="T", **kwargs)
    return written_fields(svc)


def test_create_stores_utc_for_a_utc_due_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UTC due date without time_zone is stored with TimeZone UTC."""
    fields = create(
        service(),
        monkeypatch,
        due_date=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert fields["TimeZone"].value == "UTC"


def test_create_stores_the_zoneinfo_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ZoneInfo due date stores its IANA name."""
    fields = create(
        service(), monkeypatch, due_date=datetime(2026, 10, 20, 9, 0, tzinfo=AMSTERDAM)
    )

    assert fields["TimeZone"].value == "Europe/Amsterdam"


def test_create_keeps_an_explicit_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit time_zone wins over the due date's zone."""
    fields = create(
        service(),
        monkeypatch,
        due_date=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
        time_zone="Europe/Paris",
    )

    assert fields["TimeZone"].value == "Europe/Paris"


def test_create_leaves_all_day_reminders_floating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-day reminders get no TimeZone."""
    fields = create(
        service(),
        monkeypatch,
        due_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
        all_day=True,
    )

    assert "TimeZone" not in fields


def reminder(due_date: datetime, *, all_day: bool = False) -> Reminder:
    """Build a reminder to update."""
    return Reminder(
        id="Reminder/REM-TZ",
        list_id="List/L",
        title="T",
        due_date=due_date,
        all_day=all_day,
        record_change_tag="old-tag",
    )


def test_update_fills_in_the_zoneinfo_name() -> None:
    """update() stores the IANA name of a ZoneInfo due date."""
    svc = service()
    item = reminder(datetime(2026, 10, 20, 9, 0, tzinfo=AMSTERDAM))

    svc.update(item)

    assert written_fields(svc)["TimeZone"].value == "Europe/Amsterdam"
    assert item.time_zone == "Europe/Amsterdam"


def test_update_does_not_store_the_zone_when_the_write_fails() -> None:
    """A failed write leaves reminder.time_zone unset; nothing was stored."""
    svc = service()
    item = reminder(datetime(2026, 10, 20, 9, 0, tzinfo=AMSTERDAM))
    raw: Any = svc._raw
    raw.modify.side_effect = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        svc.update(item)

    assert item.time_zone is None


def test_update_does_not_guess_a_zone_for_utc_due_dates() -> None:
    """A UTC due date may be floating time read back; update() keeps it as is."""
    svc = service()

    svc.update(reminder(datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)))

    assert written_fields(svc)["TimeZone"].value is None


def test_update_leaves_all_day_reminders_floating() -> None:
    """All-day reminders keep an empty TimeZone on update."""
    svc = service()

    svc.update(reminder(datetime(2026, 10, 20, tzinfo=AMSTERDAM), all_day=True))

    assert written_fields(svc)["TimeZone"].value is None
