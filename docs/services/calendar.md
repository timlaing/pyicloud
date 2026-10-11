# Calendar

[← Back to the documentation index](../README.md)

The calendar webservice supports fetching, creating, and removing calendars and events, with support for alarms, and invitees.

## Calendars

The calendar functionality is based around the `CalendarObject` dataclass. Every variable has a default value named according to the http payload parameters from the icloud API. The `guid` is a uuid4 identifier unique to each calendar. The class will create one automatically if it is left blank when the `CalendarObject` is instanced. the `guid` parameter should only be set when you know the guid of an existing calendar. The color is an rgb hex value and will be a random color if not set.

### Functions

**get_calendars(as_objs:bool=False) -> list**<br>
_returns a list of the user's calendars_<br>
if `as_objs` is set to `True`, the returned list will be of CalendarObjects; else it will be of dictionaries.

**add_calendar(calendar:CalendarObject) -> None:**<br>
_adds a calendar to the users apple calendar_

**remove_calendar(cal_guid:str) -> None**<br>
_Removes a Calendar from the apple calendar given the provided guid_

### Examples

_Create and add a new calendar:_

```python
from pyicloud import PyiCloudService
from pyicloud.services.calendar import CalendarObject

api = PyiCloudService("username", "password")
calendar_service = api.calendar
cal = CalendarObject(title="My Calendar", share_type="published")
cal.color = "#FF0000"
calendar_service.add_calendar(cal)
```

_Remove an existing calendar:_

```python
cal = calendar_service.get_calendars(as_objs=True)[1]
calendar_service.remove_calendar(cal.guid)
```

## Events

The events functionality is based around the `EventObject` dataclass with support for alarms and invitees. `guid` is the unique identifier of each event, while `pguid` is the identifier of the calendar to which this event belongs. `pguid` is the only required parameter. The `EventObject` includes automatic validation, dynamic timezone detection, and multiple methods for event management.

### Key Features

- **Automatic Validation**: Events validate required fields, date ranges, and calendar GUIDs
- **Dynamic Timezone Detection**: Automatically detects and uses the user's local timezone
- **Alarm Support**: Add alarms at event time or before the event with flexible timing
- **Invitee Management**: Add multiple invitees who will receive email notifications

### Functions

**get_events(from_dt:datetime=None, to_dt:datetime=None, period:str="month", as_objs:bool=False)**<br>
_Returns a list of events from `from_dt` to `to_dt`. If `period` is provided, it will return the events in that period referencing `from_dt` if it was provided; else using today's date. IE if `period` is "month", the events for the entire month that `from_dt` falls within will be returned._

**get_event_detail(pguid, guid, as_obj:bool=False)**<br>
_Returns a specific event given that event's `guid` and `pguid`_

**add_event(event:EventObject) -> None**<br>
_Adds an Event to a calendar specified by the event's `pguid`._

**remove_event(event:EventObject) -> None**<br>
_Removes an Event from a calendar specified by the event's `pguid`._

### EventObject Methods

**add_invitees(emails: list) -> None**<br>
_Adds a list of email addresses as invitees to the event. They will receive email notifications when the event is created._

**add_alarm_at_time() -> str**<br>
_Adds an alarm that triggers at the exact time of the event. Returns the alarm GUID for reference._

**add_alarm_before(minutes=0, hours=0, days=0, weeks=0) -> str**<br>
_Adds an alarm that triggers before the event starts. You can specify any combination of time units. Returns the alarm GUID for reference._

### Examples

_Create an event with invitees and alarms:_

```python
from datetime import datetime, timedelta
from pyicloud import PyiCloudService
from pyicloud.services.calendar import EventObject

api = PyiCloudService("username", "password")
calendar_service = api.calendar

# Get a calendar to use
calendars = calendar_service.get_calendars(as_objs=True)
calendar_guid = calendars[0].guid

# Create an event with proper validation
event = EventObject(
    pguid=calendar_guid,
    title="Team Meeting",
    start_date=datetime.now() + timedelta(hours=2),
    end_date=datetime.now() + timedelta(hours=3),
    location="Conference Room A",
    all_day=False,
)

# Add invitees (they'll receive email notifications)
event.add_invitees(["colleague1@company.com", "colleague2@company.com"])

# Add alarms
event.add_alarm_before(minutes=15)  # 15 minutes before
event.add_alarm_before(days=1)  # 1 day before

# Add the event to the calendar
calendar_service.add_event(event)
```

_Create a simple event:_

```python
# Basic event creation
event = EventObject(
    pguid=calendar_guid,
    title="Doctor Appointment",
    start_date=datetime(2024, 1, 15, 14, 0),
    end_date=datetime(2024, 1, 15, 15, 0),
)

# Add a 30-minute warning alarm
event.add_alarm_before(minutes=30)

calendar_service.add_event(event)
```

_Get events in a specific date range:_

```python
from_dt = datetime(2024, 1, 1)
to_dt = datetime(2024, 1, 31)
events = calendar_service.get_events(from_dt, to_dt, as_objs=True)

for event in events:
    print(f"Event: {event.title} at {event.start_date}")
```

_Get next week's events:_

```python
next_week_events = calendar_service.get_events(
    from_dt=datetime.today() + timedelta(days=7), period="week", as_objs=True
)
```

_Remove an event:_

```python
calendar_service.remove_event(event)
```
