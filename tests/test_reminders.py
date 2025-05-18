"""Unit tests for the RemindersService class."""

import datetime
import json
from unittest.mock import MagicMock, patch

from requests import Response

from pyicloud.services.reminders import RemindersService
from pyicloud.session import PyiCloudSession


def test_reminders_service_init(mock_session: MagicMock) -> None:
    """Test RemindersService initialization."""
    mock_session.get.return_value = MagicMock(
        spec=Response, json=lambda: {"Collections": [], "Reminders": []}
    )
    params: dict[str, str] = {"dsid": "12345"}

    with patch("pyicloud.services.reminders.get_localzone_name", return_value="UTC"):
        service = RemindersService("https://example.com", mock_session, params)

        assert service.service_root == "https://example.com"
        assert service.params == params
        assert not service.lists
        assert not service.collections


def test_reminders_service_refresh() -> None:
    """Test the refresh method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {
        "Collections": [
            {"title": "Work", "guid": "guid1", "ctag": "ctag1"},
            {"title": "Personal", "guid": "guid2", "ctag": "ctag2"},
        ],
        "Reminders": [
            {"title": "Task 1", "pGuid": "guid1", "dueDate": [2023, 10, 1, 12, 0, 0]},
            {"title": "Task 2", "pGuid": "guid2", "dueDate": None},
        ],
    }
    mock_session.get.return_value = mock_response
    with patch("pyicloud.services.reminders.get_localzone_name", return_value="UTC"):
        service = RemindersService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        service.refresh()

        assert "Work" in service.lists
        assert "Personal" in service.lists
        assert len(service.lists["Work"]) == 1
        assert len(service.lists["Personal"]) == 1

        work_task = service.lists["Work"][0]
        assert work_task["title"] == "Task 1"
        assert work_task["due"] == datetime.datetime(2023, 10, 1, 12, 0, 0)

        personal_task = service.lists["Personal"][0]
        assert personal_task["title"] == "Task 2"
        assert personal_task["due"] is None


def test_reminders_service_post() -> None:
    """Test the post method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.ok = True
    mock_session.post.return_value = mock_response

    with patch("pyicloud.services.reminders.get_localzone_name", return_value="UTC"):
        service = RemindersService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        service.collections = {"Work": {"guid": "guid1"}}

        # Test posting a reminder with a due date
        due_date = datetime.datetime(2023, 10, 1, 12, 0, 0)
        result: bool = service.post("New Task", "Description", "Work", due_date)

        assert result is True
        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        assert kwargs["data"]
        data = json.loads(kwargs["data"])
        assert data["Reminders"]["title"] == "New Task"
        assert data["Reminders"]["description"] == "Description"
        assert data["Reminders"]["pGuid"] == "guid1"
        assert data["Reminders"]["dueDate"] == [20231001, 2023, 10, 1, 12, 0]

        # Test posting a reminder without a due date
        mock_session.post.reset_mock()
        result = service.post("Task Without Due Date", collection="Work")

        assert result is True
        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        data = json.loads(kwargs["data"])
        assert data["Reminders"]["title"] == "Task Without Due Date"
        assert data["Reminders"]["dueDate"] is None


def test_reminders_service_post_invalid_collection() -> None:
    """Test the post method with an invalid collection."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.ok = True
    mock_session.post.return_value = mock_response
    with patch("pyicloud.services.reminders.get_localzone_name", return_value="UTC"):
        service = RemindersService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )

        # Post to a non-existent collection
        result = service.post("Task", collection="NonExistent")
        assert result is True
        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        data = json.loads(kwargs["data"])
        assert data["Reminders"]["pGuid"] == "tasks"  # Default collection


def test_reminders_service_refresh_empty_response() -> None:
    """Test the refresh method with an empty response."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"Collections": [], "Reminders": []}
    mock_session.get.return_value = mock_response
    with patch("pyicloud.services.reminders.get_localzone_name", return_value="UTC"):
        service = RemindersService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        service.refresh()

        assert not service.lists
        assert not service.collections
