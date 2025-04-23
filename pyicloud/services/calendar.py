"""Calendar service."""

import json
from calendar import monthrange
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from random import randint
from typing import Any, List, Literal, Optional, TypeVar, Union, cast, overload
from uuid import uuid4

from requests import Response
from tzlocal import get_localzone_name

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession

T = TypeVar("T")


@dataclass
class EventObject:
    """
    An EventObject represents an event in the Apple Calendar.
    """

    pguid: str
    title: str = "New Event"
    start_date: datetime = datetime.today()
    end_date: datetime = datetime.today() + timedelta(minutes=60)
    local_start_date: Optional[datetime] = None
    local_end_date: Optional[datetime] = None
    duration: int = field(init=False)
    icon: int = 0
    change_recurring: Optional[str] = None
    tz: str = "US/Pacific"
    guid: str = ""  # event identifier
    location: str = ""
    extended_details_are_included: bool = True
    recurrence_exception: bool = False
    recurrence_master: bool = False
    has_attachments: bool = False
    all_day: bool = False
    is_junk: bool = False
    etag: Optional[str] = None

    invitees: List[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if not self.local_start_date:
            self.local_start_date = self.start_date
        if not self.local_end_date:
            self.local_end_date = self.end_date

        if not self.guid:
            self.guid = str(uuid4()).upper()

        self.duration = int(
            (self.end_date.timestamp() - self.start_date.timestamp()) / 60
        )

    @property
    def request_data(self) -> dict[str, Any]:
        """
        Returns the event data in the format required by Apple's calendar.
        """
        event_dict: dict[str, Any] = asdict(self)
        event_dict["startDate"] = self.dt_to_list(self.start_date)
        event_dict["endDate"] = self.dt_to_list(self.end_date, False)
        if self.local_start_date:
            event_dict["localStartDate"] = self.dt_to_list(self.local_start_date)
        if self.local_end_date:
            event_dict["localEndDate"] = self.dt_to_list(self.local_end_date, False)

        event_dict.pop("start_date", None)
        event_dict.pop("end_date", None)
        event_dict.pop("local_start_date", None)
        event_dict.pop("local_end_date", None)

        data: dict[str, Any] = {
            "Event": event_dict,
            "ClientState": {
                "Collection": [{"guid": self.guid, "ctag": None}],
                "fullState": False,
                "userTime": 1234567890,
                "alarmRange": 60,
            },
        }

        if self.invitees:
            data["Invitee"] = [
                {
                    "guid": email_guid,
                    "pGuid": self.pguid,
                    "role": "REQ-PARTICIPANT",
                    "isOrganizer": False,
                    "email": email_guid.split(":")[-1],
                    "inviteeStatus": "NEEDS-ACTION",
                    "commonName": "",
                    "isMyId": False,
                }
                for email_guid in self.invitees
            ]

        return data

    def dt_to_list(self, dt: datetime, start: bool = True) -> list:
        """
        Converts python datetime object into a list format used
        by Apple's calendar.
        """
        if start:
            minutes: int = dt.hour * 60 + dt.minute
        else:
            minutes = (24 - dt.hour) * 60 + (60 - dt.minute)

        return [
            dt.strftime("%Y%m%d"),
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            minutes,
        ]

    def add_invitees(self, _invitees: Optional[list] = None) -> None:
        """
        Adds a list of emails to invitees in the correct format
        """
        if _invitees:
            self.invitees += ["{}:{}".format(self.guid, email) for email in _invitees]

    def get(self, var: str):
        """Get a variable"""
        return getattr(self, var, None)


@dataclass
class CalendarObject:
    """
    A CalendarObject represents a calendar in the Apple Calendar.
    """

    title: str = "Untitled"
    guid: str = ""
    share_type: Optional[str] = (
        None  # can be (None, 'published', 'shared') where 'published' gens a public caldav link in the response.  Shared is not supported here as it is rather complex.
    )
    symbolic_color: str = "__custom__"
    supported_type: str = "Event"
    object_type: str = "personal"
    share_title: str = ""
    shared_url: str = ""
    color: str = ""
    order: int = 7
    extended_details_are_included: bool = True
    read_only: bool = False
    enabled: bool = True
    ignore_event_updates: Optional[str] = None
    email_notification: Optional[str] = None
    last_modified_date: Optional[str] = None
    me_as_participant: Optional[str] = None
    pre_published_url: Optional[str] = None
    participants: Optional[str] = None
    defer_loading: Optional[str] = None
    published_url: Optional[str] = None
    remove_alarms: Optional[str] = None
    ignore_alarms: Optional[str] = None
    description: Optional[str] = None
    remove_todos: Optional[str] = None
    is_default: Optional[bool] = None
    is_family: Optional[bool] = None
    etag: Optional[str] = None
    ctag: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = str(uuid4()).upper()

        if not self.color:
            self.color = self.gen_random_color()

    def gen_random_color(self) -> str:
        """
        Creates a random rgbhex color.
        """
        return "#%02x%02x%02x" % tuple([randint(0, 255) for _ in range(3)])

    @property
    def request_data(self) -> dict[str, Any]:
        """Returns the calendar data in the format required by Apple's calendar."""
        data: dict[str, Any] = {
            "Collection": asdict(self),
            "ClientState": {
                "Collection": [],
                "fullState": False,
                "userTime": 1234567890,
                "alarmRange": 60,
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
        first_day, last_day = monthrange(today.year, today.month)
        from_dt = datetime(today.year, today.month, first_day)
        to_dt = datetime(today.year, today.month, last_day)
        params = dict(self.params)
        params.update(
            {
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "startDate": from_dt.strftime("%Y-%m-%d"),
                "endDate": to_dt.strftime("%Y-%m-%d"),
            }
        )

        return params

    def obj_from_dict(self, obj: T, _dict) -> T:
        """Creates an object from a dictionary"""
        for key, value in _dict.items():
            setattr(obj, key, value)

        return obj

    def get_ctag(self, guid: str) -> str:
        """Returns the ctag for a given calendar guid"""
        ctag: Optional[str] = None
        for cal in self.get_calendars(as_objs=False):
            if isinstance(cal, CalendarObject) and cal.guid == guid:
                ctag = cal.ctag
            elif isinstance(cal, dict) and cal.get("guid") == guid:
                ctag = cal.get("ctag")

            if ctag:
                return ctag
        raise ValueError("ctag not found.")

    def refresh_client(self, from_dt=None, to_dt=None) -> dict[str, Any]:
        """
        Refreshes the CalendarService endpoint, ensuring that the
        event data is up-to-date. If no 'from_dt' or 'to_dt' datetimes
        have been given, the range becomes this month.
        """
        today: datetime = datetime.today()
        first_day, last_day = monthrange(today.year, today.month)
        if not from_dt:
            from_dt = datetime(today.year, today.month, first_day)
        if not to_dt:
            to_dt = datetime(today.year, today.month, last_day)
        params = dict(self.params)
        params.update(
            {
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "startDate": from_dt.strftime("%Y-%m-%d"),
                "endDate": to_dt.strftime("%Y-%m-%d"),
                "dsid": self.session.service.data["dsInfo"]["dsid"],
            }
        )
        req: Response = self.session.get(self._calendar_refresh_url, params=params)
        return req.json()

    @overload
    def get_calendars(self) -> list[dict[str, Any]]: ...

    @overload
    def get_calendars(self, as_objs: Literal[False]) -> list[dict[str, Any]]: ...

    @overload
    def get_calendars(self, as_objs: Literal[True]) -> list[CalendarObject]: ...

    def get_calendars(
        self, as_objs: Union[Literal[True], Literal[False]] = False
    ) -> Union[list[dict[str, Any]], list[CalendarObject]]:
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
            self._calendar_collections_url + f"/{calendar.guid}",
            params=params,
            data=json.dumps(data),
        )
        return req.json()

    def remove_calendar(self, cal_guid: str) -> dict[str, Any]:
        """
        Removes a Calendar from the apple calendar.
        """
        params: dict[str, Any] = self.default_params
        params["methodOverride"] = "DELETE"

        req: Response = self.session.post(
            self._calendar_collections_url + f"/{cal_guid}",
            params=params,
            data=json.dumps({}),
        )
        return req.json()

    def get_events(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        period: str = "month",
        as_objs: bool = False,
    ) -> list:
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
        events: list = response.get("Event", [])

        if as_objs and events:
            for idx, event in enumerate(events):
                events[idx] = self.obj_from_dict(EventObject(""), event)

        return events

    def get_event_detail(self, pguid, guid, as_obj: bool = False) -> EventObject:
        """
        Fetches a single event's details by specifying a pguid
        (a calendar) and a guid (an event's ID).
        """
        params = dict(self.params)
        params.update(
            {
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "dsid": self.session.service.data["dsInfo"]["dsid"],
            }
        )
        url: str = f"{self._calendar_event_detail_url}/{pguid}/{guid}"
        req: Response = self.session.get(url, params=params)
        response = req.json()
        event = response["Event"][0]

        if as_obj and event:
            event: EventObject = cast(
                EventObject,
                self.obj_from_dict(EventObject(pguid=pguid), event),
            )

        return event

    def add_event(self, event: EventObject) -> dict[str, Any]:
        """
        Adds an Event to a calendar.
        """
        data = event.request_data
        data["ClientState"]["Collection"][0]["ctag"] = self.get_ctag(event.guid)
        params = self.default_params

        req: Response = self.session.post(
            self._calendar_refresh_url + f"/{event.pguid}/{event.guid}",
            params=params,
            data=json.dumps(data),
        )
        return req.json()

    def remove_event(self, event: EventObject) -> dict[str, Any]:
        """
        Removes an Event from a calendar. The calendar's guid corresponds to the EventObject's pGuid
        """
        data = event.request_data
        data["ClientState"]["Collection"][0]["ctag"] = self.get_ctag(event.guid)
        data["Event"] = {}

        params: dict[str, Any] = self.default_params
        params["methodOverride"] = "DELETE"
        if not getattr(event, "etag", None):
            event.etag = self.get_event_detail(
                event.pguid, event.guid, as_obj=False
            ).get("etag")
        params["ifMatch"] = event.etag

        req: Response = self.session.post(
            self._calendar_refresh_url + f"/{event.pguid}/{event.guid}",
            params=params,
            data=json.dumps(data),
        )
        return req.json()
