"""User-facing Invites data transfer objects.

These types form the public API surface; users of :class:`InvitesService`
will receive and pass these. All are :class:`FrozenServiceModel` subclasses
(immutable, strictly validated).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, IntEnum
from typing import Optional

from pydantic import Field

from pyicloud.common.models import FrozenServiceModel
from pyicloud.services.invites._constants import SHARE_URL_PREFIX


class RsvpStatus(IntEnum):
    """Guest's RSVP response state (encrypted ``status`` int on the wire)."""

    NO_RESPONSE = 0  # Presumed default before first explicit response.
    NOT_GOING = 1
    MAYBE = 2
    GOING = 3


class ParticipantType(str, Enum):
    """Type of participant on a ``cloudkit.share`` record."""

    OWNER = "OWNER"
    PUBLIC_USER = "PUBLIC_USER"  # Joined via shortGUID share link.
    USER = "USER"  # Invited via OneTimeLink (email/phone).


class AcceptanceStatus(str, Enum):
    """Guest's acceptance status for an invitation."""

    ACCEPTED = "ACCEPTED"
    INVITED = "INVITED"
    REMOVED = "REMOVED"


class EventScope(str, Enum):
    """Which CK database scope an event lives in for the current user."""

    PRIVATE = "private"  # current user is the owner
    SHARED = "shared"  # current user is an accepted guest


class EventTime(FrozenServiceModel):
    """Decoded ``EventDetails.time`` payload."""

    start: datetime
    end: Optional[datetime] = None
    is_all_day: bool = False
    is_open_ended: bool = False


class EventPlace(FrozenServiceModel):
    """Decoded ``EventDetails.place`` payload.

    All fields are optional. Events without a physical location may carry
    only ``city`` and ``time_zone_identifier``.
    """

    title: Optional[str] = None
    subtitle: Optional[str] = None
    city: Optional[str] = None
    time_zone_identifier: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    url: Optional[str] = None


class Participant(FrozenServiceModel):
    """A participant on the event's ``cloudkit.share`` record."""

    participant_id: str
    user_record_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    email: Optional[str] = None
    type: ParticipantType
    acceptance_status: AcceptanceStatus
    permission: str


class OneTimeLinkGuest(FrozenServiceModel):
    """Guest invited via the email/phone one-time-link flow."""

    record_name: str  # "<participant_id>_otl"
    participant_id: str
    name: str = ""
    emails: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()


class EventShare(FrozenServiceModel):
    """View of an event's ``cloudkit.share`` record."""

    short_guid: str
    public_permission: str
    participants: tuple[Participant, ...] = ()
    one_time_links: tuple[OneTimeLinkGuest, ...] = ()
    # ``participantId`` of the current authenticated user on this share — used
    # to locate or create the user's own ``RSVP`` record. Set from
    # ``cloudkit.share.currentUserParticipant.participantId`` at parse time.
    current_user_participant_id: Optional[str] = None

    @property
    def url(self) -> str:
        """Public invite URL: ``https://www.icloud.com/invites/<shortGUID>``."""
        return f"{SHARE_URL_PREFIX}{self.short_guid}"


class Rsvp(FrozenServiceModel):
    """A guest's RSVP response."""

    record_name: str  # "<participant_id>_rsvp"
    participant_id: str
    name: str = ""
    status: RsvpStatus
    message: Optional[str] = None
    num_additional_adults: int = 0
    num_additional_kids: int = 0
    image_download_url: Optional[str] = None
    record_change_tag: Optional[str] = None


class Event(FrozenServiceModel):
    """An Invites event from the current user's perspective."""

    event_id: str  # zoneName / UUID
    scope: EventScope
    record_change_tag: Optional[str] = None
    title: str = ""
    notes: str = ""
    host_display_name: str = ""
    is_published: bool = False
    is_private: bool = False
    is_cancelled: bool = False
    block_new_rsvps: bool = False
    max_attendees: Optional[int] = None
    max_additional_guests_per_rsvp: int = 0
    time: Optional[EventTime] = None
    place: Optional[EventPlace] = None
    background: dict = Field(default_factory=dict)
    style: dict = Field(default_factory=dict)
    integrations: tuple[str, ...] = ()
    created_timestamp: Optional[datetime] = None
    modified_timestamp: Optional[datetime] = None
    share: Optional[EventShare] = None
    rsvps: tuple[Rsvp, ...] = ()


class ResolvedShare(FrozenServiceModel):
    """Preview returned by ``records/resolve`` before accepting a share."""

    short_guid: str
    event_id: str  # zoneName from the resolved share
    owner_record_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_given_name: Optional[str] = None
    owner_family_name: Optional[str] = None
    participant_status: str  # ACCEPTED, INVITED, etc. for the requester
    participant_type: str
    participant_permission: str
    share: EventShare
