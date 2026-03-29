"""Smoke tests for the CloudKit-backed Reminders service facade."""

from unittest.mock import MagicMock

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


def test_reminders_service_lists_delegates_to_read_api() -> None:
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    expected = [RemindersList(id="List/WORK", title="Work")]
    service._reads.lists = MagicMock(return_value=iter(expected))

    assert list(service.lists()) == expected


def test_reminders_service_reminders_aggregates_list_snapshots() -> None:
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    list_id = "List/WORK"
    reminder = Reminder(id="Reminder/1", list_id=list_id, title="Task 1")
    service.lists = MagicMock(return_value=[RemindersList(id=list_id, title="Work")])
    service.list_reminders = MagicMock(
        return_value=ListRemindersResult(
            reminders=[reminder],
            alarms={},
            triggers={},
            attachments={},
            hashtags={},
            recurrence_rules={},
        )
    )

    assert list(service.reminders()) == [reminder]
    service.list_reminders.assert_called_once_with(
        list_id=list_id,
        include_completed=True,
        results_limit=200,
    )


def test_reminders_service_create_delegates_to_write_api() -> None:
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    created = Reminder(id="Reminder/1", list_id="List/WORK", title="New Task")
    service._writes.create = MagicMock(return_value=created)

    result = service.create("List/WORK", "New Task", desc="Description")

    assert result == created
    service._writes.create.assert_called_once_with(
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


def test_reminders_service_delete_delegates_to_write_api() -> None:
    service = RemindersService("https://example.com", MagicMock(), {"dsid": "12345"})
    reminder = Reminder(id="Reminder/1", list_id="List/WORK", title="Delete me")
    service._writes.delete = MagicMock()

    service.delete(reminder)

    service._writes.delete.assert_called_once_with(reminder)
