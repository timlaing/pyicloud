"""Test calendar service"""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

from requests import Response

from pyicloud.services.calendar import (
    AlarmDefaults,
    AlarmMeasurement,
    AppleAlarm,
    AppleDateFormat,
    CalendarDefaults,
    CalendarObject,
    CalendarService,
    DateFormats,
    EventObject,
)
from pyicloud.session import PyiCloudSession


def test_event_object_initialization() -> None:
    """Test EventObject initialization and default values."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="calendar123")
        assert event.pguid == "calendar123"
        assert event.title == "New Event"
        assert event.duration == 60
        assert event.tz == "UTC"  # Now tests dynamic timezone detection
        assert event.guid != ""


def test_event_object_request_data() -> None:
    """Test EventObject request_data property."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="calendar123")
        data: dict[str, Any] = event.request_data
        assert "Event" in data
        assert "ClientState" in data
        assert data["Event"]["title"] == "New Event"
        assert "pGuid" in data["Event"]  # Note: camelCase in output
        assert data["Event"]["pGuid"] == "calendar123"
        assert "guid" in data["Event"]
        assert "Collection" in data["ClientState"]


def test_event_object_dt_to_list() -> None:
    """Test EventObject dt_to_list method."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="calendar123")
        dt = datetime(2023, 1, 1, 12, 30)
        result = event.dt_to_list(dt)
        assert result == ["20230101", 2023, 1, 1, 12, 30, 750]


def test_event_object_add_invitees() -> None:
    """Test EventObject add_invitees method."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="calendar123")
        event.add_invitees(["test@example.com", "user@example.com"])
        assert len(event.invitees) == 2
        assert f"{event.guid}:test@example.com" == event.invitees[0]
        assert f"{event.guid}:user@example.com" == event.invitees[1]


def test_event_object_dynamic_timezone() -> None:
    """Test that EventObject uses dynamic timezone detection based on user's locale."""
    # Test with different timezones to ensure dynamic behavior
    with patch(
        "pyicloud.services.calendar.get_localzone_name", return_value="Europe/London"
    ):
        event = EventObject(pguid="calendar123")
        assert event.tz == "Europe/London"

    with patch(
        "pyicloud.services.calendar.get_localzone_name", return_value="Asia/Tokyo"
    ):
        event = EventObject(pguid="calendar123")
        assert event.tz == "Asia/Tokyo"

    with patch(
        "pyicloud.services.calendar.get_localzone_name", return_value="America/New_York"
    ):
        event = EventObject(pguid="calendar123")
        assert event.tz == "America/New_York"


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


# =====================================
# Tests for NEW Features Added in PR
# =====================================


def test_constants_and_defaults() -> None:
    """Test all constant classes have expected values."""
    # Test DateFormats
    assert DateFormats.API_DATE == "%Y-%m-%d"
    assert DateFormats.APPLE_DATE == "%Y%m%d"

    # Test CalendarDefaults
    assert CalendarDefaults.TITLE == "Untitled"
    assert CalendarDefaults.SYMBOLIC_COLOR == "__custom__"
    assert CalendarDefaults.SUPPORTED_TYPE == "Event"
    assert CalendarDefaults.OBJECT_TYPE == "personal"
    assert CalendarDefaults.ORDER == 7
    assert CalendarDefaults.SHARE_TITLE == ""
    assert CalendarDefaults.SHARED_URL == ""
    assert CalendarDefaults.COLOR == ""

    # Test AlarmDefaults
    assert AlarmDefaults.MESSAGE_TYPE == "message"
    assert not AlarmDefaults.IS_LOCATION_BASED


def test_apple_date_format_dataclass() -> None:
    """Test AppleDateFormat dataclass functionality."""
    # Test from_datetime for start time
    dt = datetime(2023, 6, 15, 14, 30)
    apple_format = AppleDateFormat.from_datetime(dt, is_start=True)

    assert apple_format.date_string == "20230615"
    assert apple_format.year == 2023
    assert apple_format.month == 6
    assert apple_format.day == 15
    assert apple_format.hour == 14
    assert apple_format.minute == 30
    assert apple_format.minutes_from_midnight == 870  # 14*60 + 30

    # Test to_list conversion
    result_list = apple_format.to_list()
    expected = ["20230615", 2023, 6, 15, 14, 30, 870]
    assert result_list == expected

    # Test from_datetime for end time (different calculation)
    apple_format_end = AppleDateFormat.from_datetime(dt, is_start=False)
    assert (
        apple_format_end.minutes_from_midnight == 630
    )  # (24-14)*60 + (60-30) = 10*60 + 30


def test_calendar_object_uses_defaults() -> None:
    """Test CalendarObject uses constant defaults correctly."""
    calendar = CalendarObject()

    assert calendar.title == CalendarDefaults.TITLE
    assert calendar.symbolic_color == CalendarDefaults.SYMBOLIC_COLOR
    assert calendar.supported_type == CalendarDefaults.SUPPORTED_TYPE
    assert calendar.object_type == CalendarDefaults.OBJECT_TYPE
    assert calendar.order == CalendarDefaults.ORDER
    assert calendar.share_title == CalendarDefaults.SHARE_TITLE
    assert calendar.shared_url == CalendarDefaults.SHARED_URL
    # Color gets generated, so just check it's not empty
    assert calendar.color.startswith("#")


def test_event_object_validation() -> None:
    """Test EventObject validation logic."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        # Test empty pguid validation
        try:
            EventObject(pguid="")
            assert False, "Should have raised ValueError for empty pguid"
        except ValueError as e:
            assert "pguid cannot be empty" in str(e)

        # Test empty pguid with whitespace
        try:
            EventObject(pguid="   ")
            assert False, "Should have raised ValueError for whitespace-only pguid"
        except ValueError as e:
            assert "pguid cannot be empty" in str(e)

        # Test invalid date range (start after end)
        try:
            EventObject(
                pguid="test-calendar",
                start_date=datetime(2023, 6, 15, 15, 0),
                end_date=datetime(2023, 6, 15, 14, 0),  # Earlier than start
            )
            assert False, "Should have raised ValueError for invalid date range"
        except ValueError as e:
            assert "start_date" in str(e) and "must be before end_date" in str(e)

        # Test valid event creation
        event = EventObject(
            pguid="test-calendar",
            title="Valid Event",
            start_date=datetime(2023, 6, 15, 14, 0),
            end_date=datetime(2023, 6, 15, 15, 0),
        )
        assert event.pguid == "test-calendar"
        assert event.title == "Valid Event"
        assert event.duration == 60  # 1 hour in minutes


def test_event_object_alarm_functionality() -> None:
    """Test alarm creation and management."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="test-calendar", title="Alarm Test Event")

        # Test add_alarm_at_time
        alarm_guid = event.add_alarm_at_time()
        assert alarm_guid in event.alarms[0]  # Format: "eventGuid:alarmGuid"
        assert len(event.alarms) == 1
        assert event.alarms[0].startswith(event.guid)

        # Verify alarm metadata for "at time" alarm
        alarm_full_guid = event.alarms[0]
        assert alarm_full_guid in event._alarm_metadata
        metadata = event._alarm_metadata[alarm_full_guid]
        assert not metadata.before
        assert metadata.minutes == 0
        assert metadata.hours == 0

        # Test add_alarm_before with different time periods
        alarm_guid_5min = event.add_alarm_before(minutes=5)
        event.add_alarm_before(hours=1)  # Don't need to store this one
        alarm_guid_complex = event.add_alarm_before(days=1, hours=2, minutes=30)

        assert len(event.alarms) == 4  # 1 at-time + 3 before alarms

        # Verify "5 minutes before" alarm metadata
        alarm_5min_full = f"{event.guid}:{alarm_guid_5min}"
        metadata_5min = event._alarm_metadata[alarm_5min_full]
        assert metadata_5min.before
        assert metadata_5min.minutes == 5
        assert metadata_5min.hours == 0
        assert metadata_5min.days == 0

        # Verify "complex before" alarm metadata
        alarm_complex_full = f"{event.guid}:{alarm_guid_complex}"
        metadata_complex = event._alarm_metadata[alarm_complex_full]
        assert metadata_complex.before
        assert metadata_complex.days == 1
        assert metadata_complex.hours == 2
        assert metadata_complex.minutes == 30


def test_event_object_alarm_payload_structure() -> None:
    """Test alarm payload structure in request_data."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="test-calendar", title="Alarm Test Event")

        # Add alarms
        event.add_alarm_at_time()
        event.add_alarm_before(minutes=15)

        # Get request data
        request_data = event.request_data

        # Verify Alarm array structure
        assert "Alarm" in request_data
        assert len(request_data["Alarm"]) == 2

        # Check first alarm structure (at time)
        alarm1 = request_data["Alarm"][0]
        assert "guid" in alarm1
        assert "pGuid" in alarm1
        assert "messageType" in alarm1
        assert "isLocationBased" in alarm1
        assert "measurement" in alarm1

        assert alarm1["pGuid"] == event.guid  # Event GUID, not calendar GUID
        assert alarm1["messageType"] == AlarmDefaults.MESSAGE_TYPE
        assert alarm1["isLocationBased"] == AlarmDefaults.IS_LOCATION_BASED

        # Check measurement structure
        measurement1 = alarm1["measurement"]
        assert "before" in measurement1
        assert "minutes" in measurement1
        assert "hours" in measurement1
        assert "days" in measurement1

        # Verify Event.alarms field contains correct string format
        event_data = request_data["Event"]
        assert "alarms" in event_data
        assert len(event_data["alarms"]) == 2
        assert all(
            ":" in alarm for alarm in event_data["alarms"]
        )  # Format: "eventGuid:alarmGuid"


def test_event_object_invitee_payload_structure() -> None:
    """Test invitee payload structure in request_data."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(pguid="test-calendar", title="Invitee Test Event")

        # Add invitees
        event.add_invitees(["test@example.com", "user@example.com"])

        # Get request data
        request_data = event.request_data

        # Verify Invitee array structure
        assert "Invitee" in request_data
        assert len(request_data["Invitee"]) == 2

        # Check first invitee structure
        invitee1 = request_data["Invitee"][0]
        assert "guid" in invitee1
        assert "pGuid" in invitee1
        assert "role" in invitee1
        assert "isOrganizer" in invitee1
        assert "email" in invitee1
        assert "inviteeStatus" in invitee1
        assert "commonName" in invitee1
        assert "isMe" in invitee1  # Should be "isMe", not "isMyId"

        assert invitee1["pGuid"] == event.guid  # Event GUID, not calendar GUID
        assert invitee1["email"] == "test@example.com"
        assert not invitee1["isMe"]

        # Verify Event.invitees field contains correct string format
        event_data = request_data["Event"]
        assert "invitees" in event_data
        assert len(event_data["invitees"]) == 2
        assert event_data["invitees"][0] == f"{event.guid}:test@example.com"
        assert event_data["invitees"][1] == f"{event.guid}:user@example.com"


def test_calendar_service_guid_bug_fix() -> None:
    """Test that GUID vs Calendar GUID bug is fixed."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"status": "success"}
    mock_session.post.return_value = mock_response

    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        service = CalendarService(
            "https://example.com", mock_session, {"dsid": "12345"}
        )

        # Mock get_ctag to verify it's called with calendar GUID, not event GUID
        def mock_get_ctag(guid):
            # This should be called with the calendar GUID (event.pguid)
            # NOT the event GUID (event.guid)
            if guid == "calendar-guid-123":
                return "test-ctag"
            else:
                raise ValueError(f"get_ctag called with wrong GUID: {guid}")

        service.get_ctag = mock_get_ctag

        # Create event with different event GUID and calendar GUID
        event = EventObject(pguid="calendar-guid-123", title="Test Event")
        event.guid = "event-guid-456"  # Different from pguid

        # This should work - get_ctag should be called with calendar GUID
        response = service.add_event(event)
        assert response["status"] == "success"

        # For remove_event as well
        response = service.remove_event(event)
        assert response["status"] == "success"


def test_complete_payload_structure() -> None:
    """Test that generated payload matches Apple's expected JSON structure."""
    with patch("pyicloud.services.calendar.get_localzone_name", return_value="UTC"):
        event = EventObject(
            pguid="test-calendar-guid",
            title="Complete Test Event",
            start_date=datetime(2023, 6, 15, 14, 0),
            end_date=datetime(2023, 6, 15, 15, 0),
            location="Test Location",
            all_day=False,
        )

        # Add invitees and alarms for complete test
        event.add_invitees(["test@example.com"])
        event.add_alarm_before(minutes=15)

        request_data = event.request_data

        # Verify top-level structure
        expected_keys = ["Event", "Invitee", "Alarm", "ClientState"]
        for key in expected_keys:
            assert key in request_data, f"Missing top-level key: {key}"

        # Verify Event structure has camelCase fields
        event_data = request_data["Event"]
        camelcase_fields = [
            "pGuid",
            "startDate",
            "endDate",
            "localStartDate",
            "localEndDate",
            "createdDate",
            "lastModifiedDate",
            "extendedDetailsAreIncluded",
            "recurrenceException",
            "recurrenceMaster",
            "hasAttachments",
            "shouldShowJunkUIWhenAppropriate",
            "changeRecurring",
            "allDay",
        ]
        for field in camelcase_fields:
            assert field in event_data, f"Missing camelCase field: {field}"

        # Verify date fields are in Apple's 7-element format
        assert isinstance(event_data["startDate"], list)
        assert len(event_data["startDate"]) == 7
        assert event_data["startDate"][0] == "20230615"  # YYYYMMDD string
        assert event_data["startDate"][1] == 2023  # Year
        assert event_data["startDate"][2] == 6  # Month

        # Verify ClientState structure
        client_state = request_data["ClientState"]
        assert "Collection" in client_state
        assert len(client_state["Collection"]) == 1
        collection = client_state["Collection"][0]
        assert collection["guid"] == event.pguid  # Calendar GUID, not event GUID
        assert "ctag" in collection


def test_alarm_measurement_dataclass() -> None:
    """Test AlarmMeasurement dataclass."""
    # Test default values
    measurement = AlarmMeasurement()
    assert measurement.before
    assert measurement.weeks == 0
    assert measurement.days == 0
    assert measurement.hours == 0
    assert measurement.minutes == 0
    assert measurement.seconds == 0

    # Test custom values
    measurement = AlarmMeasurement(before=False, days=1, hours=2, minutes=30)
    assert not measurement.before
    assert measurement.days == 1
    assert measurement.hours == 2
    assert measurement.minutes == 30


def test_apple_alarm_dataclass() -> None:
    """Test AppleAlarm dataclass."""
    measurement = AlarmMeasurement(before=True, minutes=15)
    alarm = AppleAlarm(
        guid="event-guid:alarm-guid", pGuid="event-guid", measurement=measurement
    )

    assert alarm.guid == "event-guid:alarm-guid"
    assert alarm.pGuid == "event-guid"
    assert alarm.messageType == AlarmDefaults.MESSAGE_TYPE
    assert alarm.isLocationBased == AlarmDefaults.IS_LOCATION_BASED
    assert alarm.measurement.minutes == 15
