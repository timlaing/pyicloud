"""CloudKit record-type and field-name enums for the Invites service."""

from __future__ import annotations

from enum import Enum


class InvitesRecordType(str, Enum):
    """CK record types observed in the Invites container."""

    EventDetails = "EventDetails"
    Rsvp = "RSVP"
    HostMessage = "HostMessage"
    EventLimits = "EventLimits"
    OneTimeLinkGuestInfo = "OneTimeLinkGuestInfo"
    Share = "cloudkit.share"


class EventDetailsField(str, Enum):
    """Field names on the ``EventDetails`` record."""

    TITLE = "title"
    NOTES = "notes"
    HOST_DISPLAY_NAME = "hostDisplayName"
    IS_PUBLISHED = "isPublished"
    IS_PRIVATE = "isPrivate"
    IS_CANCELLED = "isCancelled"
    BLOCK_NEW_RSVPS = "blockNewRSVPs"
    MAX_ATTENDEES = "maxAttendees"
    MAX_ADDITIONAL_GUESTS_PER_RSVP = "maxAdditionalGuestsPerRSVP"
    MINIMUM_SUPPORTED_VERSION = "minimumSupportedVersion"
    CLIENT_RECORD_CHANGE_TAG = "clientRecordChangeTag"
    TIME = "time"
    PLACE = "place"
    BACKGROUND = "background"
    STYLE = "style"
    INTEGRATIONS = "integrations"
    HINT = "hint"


class RsvpField(str, Enum):
    """Field names on the ``RSVP`` record."""

    STATUS = "status"
    NAME = "name"
    MESSAGE = "message"
    NUM_ADDITIONAL_GUESTS = "numAdditionalGuests"
    NUM_ADDITIONAL_ADULTS = "numAdditionalAdults"
    NUM_ADDITIONAL_KIDS = "numAdditionalKids"
    MONOGRAM_BACKGROUND_COLOR = "monogramBackgroundColor"
    IMAGE = "image"
    HINT = "hint"


class OneTimeLinkField(str, Enum):
    """Field names on the ``OneTimeLinkGuestInfo`` record."""

    NAME = "name"
    EMAILS = "emails"
    PHONE_NUMBERS = "phoneNumbers"
