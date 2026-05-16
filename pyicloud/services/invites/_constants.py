"""Shared constants for the Invites CloudKit service.

Invites uses ``com.apple.icloud.events`` (Apple kept the legacy "Events"
container name internally for what's now branded "iCloud Invites"). Each event
lives in its own custom zone whose ``zoneName`` is the event UUID; there is no
single shared zone like Notes or Reminders.
"""

from __future__ import annotations

CONTAINER: str = "com.apple.icloud.events"
ENV: str = "production"

# Record-name constants
SHARE_RECORD_NAME: str = "cloudkit.zoneshare"
EVENT_DETAILS_RECORD_NAME_PREFIX: str = "EventDetails:"
RSVP_RECORD_NAME_SUFFIX: str = "_rsvp"
ONE_TIME_LINK_RECORD_NAME_SUFFIX: str = "_otl"

# Stable share-URL format. The shortGUID returned in the share record's
# ``stableUrl`` (and at the top level of resolve/accept responses) appends to
# this prefix to form the public invite link.
SHARE_URL_PREFIX: str = "https://www.icloud.com/invites/"
