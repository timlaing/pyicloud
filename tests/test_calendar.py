"""Test calendar service"""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

from requests import Response

from pyicloud.services.calendar import CalendarObject, CalendarService, EventObject
from pyicloud.session import PyiCloudSession


def test_event_object_initialization() -> None:
    """Test EventObject initialization and default values."""
    event = EventObject(pguid="calendar123")
    assert event.pguid == "calendar123"
    assert event.title == "New Event"
    assert event.duration == 60
    assert event.tz == "US/Pacific"
    assert event.guid != ""


def test_event_object_request_data() -> None:
    """Test EventObject request_data property."""
    event = EventObject(pguid="calendar123")
    data: dict[str, Any] = event.request_data
    assert "Event" in data
    assert "ClientState" in data
    assert data["Event"]["title"] == "New Event"
    assert "pguid" in data["Event"]
    assert data["Event"]["pguid"] == "calendar123"
    assert "guid" in data["Event"]
    assert "Collection" in data["ClientState"]


def test_event_object_dt_to_list() -> None:
    """Test EventObject dt_to_list method."""
    event = EventObject(pguid="calendar123")
    dt = datetime(2023, 1, 1, 12, 30)
    result = event.dt_to_list(dt)
    assert result == ["20230101", 2023, 1, 1, 12, 30, 750]


def test_event_object_add_invitees() -> None:
    """Test EventObject add_invitees method."""
    event = EventObject(pguid="calendar123")
    event.add_invitees(["test@example.com", "user@example.com"])
    assert len(event.invitees) == 2
    assert f"{event.guid}:test@example.com" == event.invitees[0]
    assert f"{event.guid}:user@example.com" == event.invitees[1]


def test_calendar_object_initialization() -> None:
    """Test CalendarObject initialization and default values."""
    calendar = CalendarObject(title="My Calendar")
    assert calendar.title == "My Calendar"
    assert calendar.guid != ""
    assert calendar.color.startswith("#")


def test_calendar_object_request_data() -> None:
    """Test CalendarObject request_data property."""
    calendar = CalendarObject(title="My Calendar")
    data: dict[str, Any] = calendar.request_data
    assert "Collection" in data
    assert data["Collection"]["title"] == "My Calendar"
    assert "ClientState" in data
    assert "guid" in data["Collection"]
    assert "color" in data["Collection"]


def test_calendar_service_get_calendars() -> None:
    """Test CalendarService get_calendars method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"Collection": [{"title": "Test Calendar"}]}
    mock_session.get.return_value = mock_response
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        calendars = service.get_calendars()
        assert len(calendars) == 1
        assert calendars[0]["title"] == "Test Calendar"


def test_calendar_service_add_calendar() -> None:
    """Test CalendarService add_calendar method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"status": "success"}
    mock_session.post.return_value = mock_response
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        calendar = CalendarObject(title="New Calendar")
        response = service.add_calendar(calendar)
        assert response["status"] == "success"


def test_calendar_service_remove_calendar() -> None:
    """Test CalendarService remove_calendar method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"status": "success"}
    mock_session.post.return_value = mock_response

    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        response = service.remove_calendar("calendar123")
        assert response["status"] == "success"


def test_calendar_service_get_events() -> None:
    """Test CalendarService get_events method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"Event": [{"title": "Test Event"}]}
    mock_session.get.return_value = mock_response
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        events = service.get_events()
        assert len(events) == 1
        assert events[0]["title"] == "Test Event"


def test_calendar_service_add_event() -> None:
    """Test CalendarService add_event method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"status": "success"}
    mock_session.post.return_value = mock_response
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        service.get_ctag = MagicMock(return_value="etag123")
        event = EventObject(pguid="calendar123", title="New Event")
        response = service.add_event(event)
        assert response["status"] == "success"


def test_calendar_service_remove_event() -> None:
    """Test CalendarService remove_event method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"status": "success"}
    mock_session.post.return_value = mock_response
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )
        service.get_ctag = MagicMock(return_value="etag123")

        event = EventObject(pguid="calendar123", title="New Event")
        response = service.remove_event(event)
        assert response["status"] == "success"
