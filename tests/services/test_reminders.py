"""Smoke tests for the CloudKit-backed Reminders service facade."""

# pylint: disable=protected-access

from unittest.mock import MagicMock

import pytest

from pyicloud.services.reminders import RemindersService
from pyicloud.services.reminders.models import (
    ListRemindersResult,
    Reminder,
    RemindersList,
)


def test_reminders_service_init() -> None:
    """The reminders facade wires the CloudKit client and typed helpers."""
    params: dict[str, str] = {"dsid": "12345"}
    service = RemindersService("https://example.com", MagicMock(), params)

    assert service.service_root == "https://example.com"
    assert service.params == params
    assert callable(service.lists)
    assert callable(service.list_reminders)
    assert callable(service.get)


def test_reminders_service_lists_delegates_to_read_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reminders facade should forward lists to the read API."""
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    expected = [RemindersList(id="List/WORK", title="Work")]
    monkeypatch.setattr(service._reads, "lists", MagicMock(return_value=iter(expected)))

    assert list(service.lists()) == expected


def test_reminders_service_reminders_aggregates_list_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reminders facade should aggregate reminders across every list."""
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    list_id = "List/WORK"
    reminder = Reminder(id="Reminder/1", list_id=list_id, title="Task 1")
    monkeypatch.setattr(
        service,
        "lists",
        MagicMock(return_value=[RemindersList(id=list_id, title="Work")]),
    )
    list_reminders_mock = MagicMock(
        return_value=ListRemindersResult(
            reminders=[reminder],
            alarms={},
            triggers={},
            attachments={},
            hashtags={},
            recurrence_rules={},
        )
    )
    monkeypatch.setattr(service, "list_reminders", list_reminders_mock)

    assert list(service.reminders()) == [reminder]
    list_reminders_mock.assert_called_once_with(
        list_id=list_id,
        include_completed=True,
        results_limit=200,
    )


def test_reminders_service_create_delegates_to_write_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reminders facade should forward creation to the write API."""
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    created = Reminder(id="Reminder/1", list_id="List/WORK", title="New Task")
    create_mock = MagicMock(return_value=created)
    monkeypatch.setattr(service._writes, "create", create_mock)

    result = service.create("List/WORK", "New Task", desc="Description")

    assert result == created
    create_mock.assert_called_once_with(
        list_id="List/WORK",
        title="New Task",
        desc="Description",
        completed=False,
        due_date=None,
        priority=0,
        flagged=False,
        all_day=False,
        time_zone=None,
        parent_reminder_id=None,
    )


def test_reminders_service_delete_delegates_to_write_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reminders facade should forward deletion to the write API."""
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    reminder = Reminder(id="Reminder/1", list_id="List/WORK", title="Delete me")
    delete_mock = MagicMock()
    monkeypatch.setattr(service._writes, "delete", delete_mock)

    service.delete(reminder)

    delete_mock.assert_called_once_with(reminder)
