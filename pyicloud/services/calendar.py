"""Calendar service."""

from calendar import monthrange
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from secrets import randbelow
import time
from typing import Any, Literal, TypeVar, cast, get_args, overload
from uuid import uuid4

from requests import Response
from tzlocal import get_localzone_name

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession
from pyicloud.utils import camelcase_to_underscore

T = TypeVar("T")


# Calendar service constants
class DateFormats:
    """Date format constants."""

    API_DATE = "%Y-%m-%d"
    APPLE_DATE = "%Y%m%d"


class CalendarDefaults:
    """Default values for calendars."""

    TITLE = "Untitled"
    SYMBOLIC_COLOR = "__custom__"
    SUPPORTED_TYPE = "Event"
    OBJECT_TYPE = "personal"
    ORDER = 7
    SHARE_TITLE = ""
    SHARED_URL = ""
    COLOR = ""


class InviteeDefaults:
    """Default values for event invitees."""

    ROLE = "REQ-PARTICIPANT"
    STATUS = "NEEDS-ACTION"


class AlarmDefaults:
    """Default values for event alarms."""

    MESSAGE_TYPE = "message"
    IS_LOCATION_BASED = False


@dataclass
class AlarmMeasurement:
    """
    Represents the timing measurement for an alarm.
    """

    before: bool = True
    weeks: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0


@dataclass
class AppleAlarm:
    """
    Represents an alarm in Apple's Calendar API format.

    This is used in the main payload's Alarm array.
    """

    # pylint: disable=invalid-name

    guid: str
    pGuid: str  # noqa: S116
    messageType: str = AlarmDefaults.MESSAGE_TYPE  # noqa: S116
    isLocationBased: bool = AlarmDefaults.IS_LOCATION_BASED  # noqa: S116
    measurement: AlarmMeasurement = field(default_factory=AlarmMeasurement)


@dataclass
class AppleDateFormat:
    """
    Apple's 7-element date array format.

    Format: [YYYYMMDD, YYYY, MM, DD, HH, MM, minutes_from_midnight]
    The date_string uses YYYYMMDD format.
    """

    date_string: str
    year: int
    month: int
    day: int
    hour: int
    minute: int
    minutes_from_midnight: int

    @classmethod
    def from_datetime(cls, dt: datetime, is_start: bool = True) -> "AppleDateFormat":
        """Convert Python datetime to Apple's date format."""
        if is_start:
            minutes_calc = dt.hour * 60 + dt.minute
        else:
            minutes_calc = (24 - dt.hour) * 60 + (60 - dt.minute)

        return cls(
            date_string=dt.strftime(DateFormats.APPLE_DATE),
            year=dt.year,
            month=dt.month,
            day=dt.day,
            hour=dt.hour,
            minute=dt.minute,
            minutes_from_midnight=minutes_calc,
        )

    @classmethod
    def from_list(cls, value: Sequence[int | str]) -> "AppleDateFormat":
        """Build from Apple's 7-element date array."""
        date_string, year, month, day, hour, minute = value[:6]
        minutes_from_midnight = (
            value[6] if len(value) > 6 else int(hour) * 60 + int(minute)
        )
        return cls(
            date_string=str(date_string),
            year=int(year),
            month=int(month),
            day=int(day),
            hour=int(hour),
            minute=int(minute),
            minutes_from_midnight=int(minutes_from_midnight),
        )

    def to_datetime(self) -> datetime:
        """Convert to a naive Python datetime.

        Apple sends wall-clock time with the zone carried separately on the
        event, so the result is intentionally naive.
        """
        return datetime(  # noqa: DTZ001
            self.year, self.month, self.day, self.hour, self.minute
        )

    def to_list(self) -> list[int | str]:
        """Convert to Apple's expected list format."""
        return [
            self.date_string,
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.minutes_from_midnight,
        ]


def _expects_datetime(annotation: Any) -> bool:
    """Return whether a dataclass field is declared as holding a datetime."""
    if annotation is datetime:
        return True
    return datetime in get_args(annotation)


def _coerce(annotation: Any, value: Any) -> Any:
    """Convert Apple's date arrays into datetimes for datetime fields.

    The API returns dates as ``[YYYYMMDD, YYYY, MM, DD, HH, MM, minutes]``.
    Fields declared as holding a ``datetime`` were previously populated with
    that raw list, so the declared type did not match what callers received.
    """
    if not _expects_datetime(annotation) or isinstance(value, datetime):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 6:
        try:
            return AppleDateFormat.from_list(value).to_datetime()
        except (TypeError, ValueError, OverflowError):
            return value
    return value


@dataclass
class AppleEventInvitee:
    """
    Represents an invitee within the Event object in Apple's Calendar API.

    This is the simpler invitee structure used inside the Event.invitees array.
    """

    # pylint: disable=invalid-name

    email: str
    role: str = InviteeDefaults.ROLE
    inviteeStatus: str = InviteeDefaults.STATUS  # noqa: S116


@dataclass
class ApplePayloadInvitee:
    """
    Represents an invitee in the main payload's Invitee array in Apple's Calendar API.

    This is the more detailed invitee structure used in the top-level Invitee array.
    """

    # pylint: disable=invalid-name

    guid: str
    pGuid: str  # noqa: S116
    role: str = InviteeDefaults.ROLE
    isOrganizer: bool = False  # noqa: S116
    email: str = ""
    inviteeStatus: str = InviteeDefaults.STATUS  # noqa: S116
    commonName: str = ""  # noqa: S116
    isMe: bool = False  # noqa: S116


@dataclass
class AppleCalendarEvent:
    """
    Represents an event in Apple's Calendar API format.

    This dataclass exactly mirrors the structure expected by Apple's API,
    using the correct camelCase field names and types.
    """

    # pylint: disable=invalid-name

    title: str
    tz: str
    icon: int
    duration: int
    allDay: bool  # noqa: S116
    pGuid: str  # noqa: S116
    guid: str

    startDate: list[int | str]  # noqa: S116
    endDate: list[int | str]  # noqa: S116
    localStartDate: list[int | str]  # noqa: S116
    localEndDate: list[int | str]  # noqa: S116
    createdDate: list[int | str]  # noqa: S116
    lastModifiedDate: list[int | str]  # noqa: S116

    extendedDetailsAreIncluded: bool  # noqa: S116
    recurrenceException: bool  # noqa: S116
    recurrenceMaster: bool  # noqa: S116
    hasAttachments: bool  # noqa: S116
    readOnly: bool = False  # noqa: S116
    transparent: bool = False
    birthdayIsYearlessBday: bool = False  # noqa: S116
    birthdayShowAsCompany: bool = False  # noqa: S116
    shouldShowJunkUIWhenAppropriate: bool = False  # noqa: S116

    location: str = ""
    url: str = ""
    description: str = ""
    etag: str = ""

    alarms: list[str] = field(default_factory=list)
    attachments: list[Any] = field(default_factory=list)
    invitees: list[str] = field(default_factory=list)

    changeRecurring: str | None = None  # noqa: S116


@dataclass
class EventObject:
    """
    An EventObject represents an event in the Apple Calendar.
    """

    pguid: str
    title: str = "New Event"
    start_date: datetime = field(default_factory=datetime.today)
    end_date: datetime = field(
        default_factory=lambda: datetime.today() + timedelta(minutes=60)
    )
    local_start_date: datetime | None = None
    local_end_date: datetime | None = None
    duration: int = field(init=False)
    icon: int = 0
    change_recurring: str | None = None
    tz: str = ""
    guid: str = ""
    location: str = ""
    extended_details_are_included: bool = True
    recurrence_exception: bool = False
    recurrence_master: bool = False
    has_attachments: bool = False
    all_day: bool = False
    is_junk: bool = False
    etag: str | None = None

    invitees: list[str] = field(init=False, default_factory=list)
    alarms: list[str] = field(init=False, default_factory=list)
    _alarm_metadata: dict[str, AlarmMeasurement] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        # Validation: check required fields and data integrity
        if not self.pguid.strip():
            raise ValueError("pguid cannot be empty")

        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be before "
                f"end_date ({self.end_date})"
            )

        # Initialize optional dates
        if not self.local_start_date:
            self.local_start_date = self.start_date
        if not self.local_end_date:
            self.local_end_date = self.end_date

        # Generate GUID if not provided
        if not self.guid:
            self.guid = str(uuid4()).upper()

        # Set timezone if not provided
        if not self.tz:
            self.tz = get_localzone_name()

        # Calculate duration (should now always be positive due to validation)
        # Dates are naive wall-clock times, so subtract them directly rather
        # than via timestamp(), which would fold in the process timezone/DST.
        self.duration = int((self.end_date - self.start_date).total_seconds() / 60)

    def to_apple_event(self) -> AppleCalendarEvent:
        """
        Convert this EventObject to Apple's API format.

        This method handles the conversion from our internal representation
        to the exact structure expected by Apple's Calendar API.
        """
        # Convert Python datetime to Apple's date format
        start_date_list = self.dt_to_list(self.start_date)
        end_date_list = self.dt_to_list(self.end_date, False)
        local_start_list = (
            self.dt_to_list(self.local_start_date)
            if self.local_start_date
            else start_date_list
        )
        local_end_list = (
            self.dt_to_list(self.local_end_date, False)
            if self.local_end_date
            else end_date_list
        )

        current_timestamp = time.time()
        current_dt = datetime.fromtimestamp(current_timestamp)
        created_date_list = self.dt_to_list(current_dt)
        last_modified_list = self.dt_to_list(current_dt)

        invitees_list: list[str] = []
        if self.invitees:
            invitees_list = self.invitees

        alarms_list: list[str] = []
        if self.alarms:
            alarms_list = self.alarms

        return AppleCalendarEvent(
            title=self.title,
            tz=self.tz,
            icon=self.icon,
            duration=self.duration,
            allDay=self.all_day,
            pGuid=self.pguid,
            guid=self.guid,
            startDate=start_date_list,
            endDate=end_date_list,
            localStartDate=local_start_list,
            localEndDate=local_end_list,
            createdDate=created_date_list,
            lastModifiedDate=last_modified_list,
            extendedDetailsAreIncluded=self.extended_details_are_included,
            recurrenceException=self.recurrence_exception,
            recurrenceMaster=self.recurrence_master,
            hasAttachments=self.has_attachments,
            shouldShowJunkUIWhenAppropriate=self.is_junk,
            location=self.location,
            etag=self.etag or "",
            alarms=alarms_list,
            invitees=invitees_list,
            changeRecurring=self.change_recurring,
        )

    @property
    def request_data(self) -> dict[str, Any]:
        """Returns the event data in the format required by Apple's calendar."""
        apple_event = self.to_apple_event()
        event_dict = asdict(apple_event)

        data: dict[str, Any] = {
            "Event": event_dict,
            "Invitee": [],
            "Alarm": [],
            "ClientState": {
                "Collection": [{"guid": self.pguid, "ctag": None}],
            },
        }

        if self.invitees:
            payload_invitees = [
                ApplePayloadInvitee(
                    guid=email_guid,
                    pGuid=self.guid,
                    role=InviteeDefaults.ROLE,
                    isOrganizer=False,
                    email=email_guid.split(":")[-1],
                    inviteeStatus=InviteeDefaults.STATUS,
                    commonName="",
                    isMe=False,
                )
                for email_guid in self.invitees
            ]
            data["Invitee"] = [asdict(invitee) for invitee in payload_invitees]

        if self.alarms:
            payload_alarms = [
                AppleAlarm(
                    guid=alarm_guid,
                    pGuid=self.guid,
                    messageType=AlarmDefaults.MESSAGE_TYPE,
                    isLocationBased=AlarmDefaults.IS_LOCATION_BASED,
                    measurement=self._alarm_metadata.get(
                        alarm_guid, AlarmMeasurement()
                    ),
                )
                for alarm_guid in self.alarms
            ]
            data["Alarm"] = [asdict(alarm) for alarm in payload_alarms]

        return data

    def dt_to_list(self, dt: datetime, start: bool = True) -> list[int | str]:
        """
        Converts python datetime object into a list format used
        by Apple's calendar.
        """
        apple_date = AppleDateFormat.from_datetime(dt, is_start=start)
        return apple_date.to_list()

    def add_invitees(self, _invitees: list[str] | None = None) -> None:
        """
        Adds a list of emails to invitees in the correct format
        """
        if _invitees:
            self.invitees += [f"{self.guid}:{email}" for email in _invitees]

    def add_alarm_at_time(self) -> str:
        """
        Adds an alarm at the time of the event.
        Returns the alarm GUID for reference.
        """
        alarm_guid = str(uuid4())
        alarm_full_guid = f"{self.guid}:{alarm_guid}"
        self.alarms.append(alarm_full_guid)

        self._alarm_metadata[alarm_full_guid] = AlarmMeasurement(
            before=False, weeks=0, days=0, hours=0, minutes=0, seconds=0
        )
        return alarm_guid

    def add_alarm_before(
        self, minutes: int = 0, hours: int = 0, days: int = 0, weeks: int = 0
    ) -> str:
        """
        Adds an alarm before the event.

        Args:
            minutes: Minutes before event
            hours: Hours before event
            days: Days before event
            weeks: Weeks before event

        Returns:
            The alarm GUID for reference.
        """
        alarm_guid = str(uuid4())
        alarm_full_guid = f"{self.guid}:{alarm_guid}"
        self.alarms.append(alarm_full_guid)

        self._alarm_metadata[alarm_full_guid] = AlarmMeasurement(
            before=True, weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=0
        )
        return alarm_guid

    def get(self, var: str) -> Any:
        """Get a variable"""
        return getattr(self, var, None)


@dataclass
class CalendarObject:
    """
    A CalendarObject represents a calendar in the Apple Calendar.
    """

    title: str = CalendarDefaults.TITLE
    guid: str = ""
    share_type: str | None = None
    symbolic_color: str = CalendarDefaults.SYMBOLIC_COLOR
    supported_type: str = CalendarDefaults.SUPPORTED_TYPE
    object_type: str = CalendarDefaults.OBJECT_TYPE
    share_title: str = CalendarDefaults.SHARE_TITLE
    shared_url: str = CalendarDefaults.SHARED_URL
    color: str = CalendarDefaults.COLOR
    order: int = CalendarDefaults.ORDER
    extended_details_are_included: bool = True
    read_only: bool = False
    enabled: bool = True
    ignore_event_updates: str | None = None
    email_notification: str | None = None
    last_modified_date: str | None = None
    me_as_participant: str | None = None
    pre_published_url: str | None = None
    participants: str | None = None
    defer_loading: str | None = None
    published_url: str | None = None
    remove_alarms: str | None = None
    ignore_alarms: str | None = None
    description: str | None = None
    remove_todos: str | None = None
    is_default: bool | None = None
    is_family: bool | None = None
    etag: str | None = None
    ctag: str | None = None

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = str(uuid4()).upper()

        if not self.color:
            self.color = self.gen_random_color()

    def gen_random_color(self) -> str:
        """
        Creates a random rgbhex color.
        """
        return f"#{randbelow(256):02x}{randbelow(256):02x}{randbelow(256):02x}"

    @property
    def request_data(self) -> dict[str, Any]:
        """Returns the calendar data in the format required by Apple's calendar."""
        data: dict[str, Any] = {
            "Collection": asdict(self),
            "ClientState": {
                "Collection": [],
                "fullState": False,
            },
        }
        return data


class CalendarService(BaseService):
    """
    The 'Calendar' iCloud service, connects to iCloud and returns events.
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        super().__init__(service_root, session, params)
        self._calendar_endpoint: str = f"{self.service_root}/ca"
        self._calendar_refresh_url: str = f"{self._calendar_endpoint}/events"
        self._calendar_event_detail_url: str = f"{self._calendar_endpoint}/eventdetail"
        self._calendar_collections_url: str = f"{self._calendar_endpoint}/collections"
        self._calendars_url: str = f"{self._calendar_endpoint}/allcollections"

    @property
    def default_params(self) -> dict[str, Any]:
        """Returns the default parameters for the calendar service."""
        today: datetime = datetime.today()
        _, days_in_month = monthrange(today.year, today.month)
        # monthrange returns: weekday of the first day of the month (0 -> Mon,
        # 6 -> Sun) and number of days in the month (Jan -> 31, Feb -> 28/29,
        # etc.)
        from_dt = datetime(
            today.year, today.month, 1
        )  # Hardcoded to 1 so that startDate is always the first (1st) day of the month
        to_dt = datetime(today.year, today.month, days_in_month)
        params = dict(self.params)
        params.update({
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "startDate": from_dt.strftime(DateFormats.API_DATE),
            "endDate": to_dt.strftime(DateFormats.API_DATE),
        })

        return params

    def _refresh_duration(self, obj: Any) -> None:
        """Recompute an object's duration from its resolved start/end dates.

        The object is constructed with default dates, so ``duration`` reflects
        the defaults rather than the dates populated from the API payload.
        """
        start_date = getattr(obj, "start_date", None)
        end_date = getattr(obj, "end_date", None)
        if not hasattr(obj, "duration"):
            return
        if not isinstance(start_date, datetime) or not isinstance(end_date, datetime):
            return
        obj.duration = int((end_date - start_date).total_seconds() / 60)

    def obj_from_dict(self, obj: T, _dict: dict[str, Any]) -> T:
        """Creates an object from a dictionary with proper field validation."""
        if hasattr(obj, "__dataclass_fields__") and not isinstance(obj, type):
            valid_fields = {f.name for f in fields(cast(Any, obj))}
            field_types = {f.name: f.type for f in fields(cast(Any, obj))}

            special_mappings = {
                "pGuid": "pguid",
                "shouldShowJunkUIWhenAppropriate": "is_junk",
            }

            for api_key, value in _dict.items():
                field_name: str = special_mappings.get(
                    api_key, camelcase_to_underscore(api_key)
                )

                if field_name in valid_fields:
                    setattr(
                        obj, field_name, _coerce(field_types.get(field_name), value)
                    )

            self._refresh_duration(obj)
        else:
            for key, value in _dict.items():
                setattr(obj, key, value)

        return obj

    def get_ctag(self, guid: str) -> str:
        """Returns the ctag for a given calendar guid"""
        ctag: str | None = None
        for cal in self.get_calendars(as_objs=False):
            if isinstance(cal, CalendarObject) and cal.guid == guid:
                ctag = cal.ctag
            elif isinstance(cal, dict) and cal.get("guid") == guid:
                ctag = cal.get("ctag")

            if ctag:
                return ctag
        raise ValueError("ctag not found.")

    def refresh_client(
        self, from_dt: datetime | None = None, to_dt: datetime | None = None
    ) -> dict[str, Any]:
        """
        Refresh the Calendar service and return a fresh event payload.

        Date range semantics:
        - If both 'from_dt' and 'to_dt' are provided, they are respected as-is.
        - If exactly one bound is provided, the missing bound is anchored to the
          same month as the provided bound and expanded to the full month
          (1st..last day of that month).
        - If neither is provided, defaults to the current month.

        Notes:
        - Apple's Calendar API treats 'endDate' as inclusive; events occurring on
          the last day of the month (including all-day and 23:00->00:00 boundary
          events) are returned. See tests in tests/test_calendar.py.
        """
        today: datetime = datetime.today()
        # Anchor missing bound(s) to whichever bound is provided, else to 'today'
        anchor: datetime = from_dt or to_dt or today
        year, month = anchor.year, anchor.month
        _, days_in_month = monthrange(
            year, month
        )  # (weekday_of_first_day, days_in_month)
        # If either bound is missing, normalize to the full month of the anchor.
        # When both bounds are provided (e.g., day/week queries), respect them as-is.
        if from_dt is None or to_dt is None:
            from_dt = anchor.replace(day=1)
            to_dt = anchor.replace(day=days_in_month)
        params = dict(self.params)
        params.update({
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "startDate": from_dt.strftime(DateFormats.API_DATE),
            "endDate": to_dt.strftime(DateFormats.API_DATE),
            "dsid": self.session.service.data["dsInfo"]["dsid"],
        })
        req: Response = self.session.get(self._calendar_refresh_url, params=params)
        return cast(dict[str, Any], req.json())

    @overload
    def get_calendars(self) -> list[dict[str, Any]]: ...

    @overload
    def get_calendars(self, as_objs: Literal[False]) -> list[dict[str, Any]]: ...

    @overload
    def get_calendars(self, as_objs: Literal[True]) -> list[CalendarObject]: ...

    def get_calendars(
        self, as_objs: Literal[True] | Literal[False] = False
    ) -> list[dict[str, Any]] | list[CalendarObject]:
        """
        Retrieves calendars of this month.
        """
        params: dict[str, Any] = self.default_params
        req: Response = self.session.get(self._calendars_url, params=params)
        response = req.json()
        calendars: list[dict[str, Any]] = response["Collection"]

        if not as_objs and calendars:
            return calendars

        return [self.obj_from_dict(CalendarObject(), cal) for cal in calendars]

    def add_calendar(self, calendar: CalendarObject) -> dict[str, Any]:
        """
        Adds a Calendar to the apple calendar.
        """
        data: dict[str, Any] = calendar.request_data
        params: dict[str, Any] = self.default_params

        req: Response = self.session.post(
            f"{self._calendar_collections_url}/{calendar.guid}",
            params=params,
            json=data,
        )
        return cast(dict[str, Any], req.json())

    def remove_calendar(self, cal_guid: str) -> dict[str, Any]:
        """
        Removes a Calendar from the apple calendar.
        """
        params: dict[str, Any] = self.default_params
        params["methodOverride"] = "DELETE"

        req: Response = self.session.post(
            f"{self._calendar_collections_url}/{cal_guid}", params=params, json={}
        )
        return cast(dict[str, Any], req.json())

    @overload
    def get_events(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        period: str = "month",
    ) -> list[dict[str, Any]]: ...

    @overload
    def get_events(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        period: str = "month",
        as_objs: Literal[False] = False,
    ) -> list[dict[str, Any]]: ...

    @overload
    def get_events(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        period: str = "month",
        as_objs: Literal[True] = True,
    ) -> list[EventObject]: ...

    def get_events(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        period: str = "month",
        as_objs: bool = False,
    ) -> list[dict[str, Any]] | list[EventObject]:
        """
        Retrieves events for a given date range, by default, this month.
        """
        today: datetime = datetime.today()
        if period != "month" and from_dt:
            today = datetime(from_dt.year, from_dt.month, from_dt.day)

        if period == "day":
            if not from_dt:
                from_dt = datetime(today.year, today.month, today.day)
            to_dt = from_dt + timedelta(days=1)
        elif period == "week":
            if not from_dt:
                from_dt = datetime(today.year, today.month, today.day) - timedelta(
                    days=today.weekday() + 1
                )
            to_dt = from_dt + timedelta(days=6)

        response: dict[str, Any] = self.refresh_client(from_dt, to_dt)
        events: list[Any] = response.get("Event", [])

        if as_objs and events:
            for idx, event in enumerate(events):
                pguid = event.get("pGuid", "")
                if not pguid:
                    raise ValueError(f"Event missing required pGuid field: {event}")
                events[idx] = self.obj_from_dict(EventObject(pguid), event)

        return events

    def get_event_detail(
        self, pguid: str, guid: str, as_obj: bool = False
    ) -> EventObject | dict[str, Any]:
        """
        Fetches a single event's details by specifying a pguid
        (a calendar) and a guid (an event's ID).
        """
        params = dict(self.params)
        params.update({
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "dsid": self.session.service.data["dsInfo"]["dsid"],
        })
        url: str = f"{self._calendar_event_detail_url}/{pguid}/{guid}"
        req: Response = self.session.get(url, params=params)
        response = req.json()
        event: dict[str, Any] = response["Event"][0]

        if as_obj and event:
            return self.obj_from_dict(EventObject(pguid=pguid), event)

        return event

    def add_event(self, event: EventObject) -> dict[str, Any]:
        """
        Adds an Event to a calendar.
        """
        data = event.request_data
        data["ClientState"]["Collection"][0]["ctag"] = self.get_ctag(event.pguid)
        params = self.default_params

        req: Response = self.session.post(
            f"{self._calendar_refresh_url}/{event.pguid}/{event.guid}",
            params=params,
            json=data,
        )
        return cast(dict[str, Any], req.json())

    def remove_event(self, event: EventObject) -> dict[str, Any]:
        """
        Removes an Event from a calendar. The calendar's guid corresponds to the
        EventObject's pGuid
        """
        data = event.request_data
        data["ClientState"]["Collection"][0]["ctag"] = self.get_ctag(event.pguid)
        data["Event"] = {}

        params: dict[str, Any] = self.default_params
        params["methodOverride"] = "DELETE"
        if not getattr(event, "etag", None):
            event.etag = self.get_event_detail(
                event.pguid, event.guid, as_obj=False
            ).get("etag")
        params["ifMatch"] = event.etag

        req: Response = self.session.post(
            f"{self._calendar_refresh_url}/{event.pguid}/{event.guid}",
            params=params,
            json=data,
        )
        return cast(dict[str, Any], req.json())
