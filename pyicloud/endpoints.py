"""Inventory of the Apple webservices this library depends on.

Apple advertises a ``webservices`` map at login and changes it over time: hosts
move between partitions, keys are added, and endpoints are withdrawn. Until now
the set of keys pyicloud relies on could only be recovered by grepping for
``get_webservice_url`` calls scattered across the service properties, which made
two ordinary questions harder to answer than they should be -- what is this
library exposed to, and what breaks if Apple stops advertising a given key.

This module answers both in one place. It is deliberately data, not behaviour:
``get_webservice_url()`` does not consult it, because callers may legitimately
resolve keys for services pyicloud does not wrap. What keeps it honest is a test
that scans the source for resolved keys and fails if the two ever disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Webservice:
    """One key in Apple's ``webservices`` map that pyicloud resolves.

    ``powers`` names the ``PyiCloudService`` properties that depend on the key,
    so the blast radius of a missing host is readable without tracing calls.
    """

    key: str
    powers: tuple[str, ...]
    description: str


WEBSERVICES: tuple[Webservice, ...] = (
    Webservice(
        key="account",
        powers=("account",),
        description="Account details, storage usage, and family members.",
    ),
    Webservice(
        key="calendar",
        powers=("calendar",),
        description="Calendar collections and events.",
    ),
    Webservice(
        key="ckdatabasews",
        powers=("photos", "reminders", "notes", "invites"),
        description=(
            "CloudKit database. The most heavily shared host in the library: "
            "four services are unavailable without it."
        ),
    ),
    Webservice(
        key="contacts",
        powers=("contacts",),
        description="Contact records and the account's own card.",
    ),
    Webservice(
        key="docws",
        powers=("drive",),
        description="iCloud Drive document uploads and downloads.",
    ),
    Webservice(
        key="drivews",
        powers=("drive",),
        description="iCloud Drive folder structure and file metadata.",
    ),
    Webservice(
        key="findme",
        powers=("devices",),
        description="Find My iPhone device list and location.",
    ),
    Webservice(
        key="photosupload",
        powers=("photos",),
        description=(
            "CloudKit-backed Photos upload host used by the replacement flow: "
            "createUploadUrl, putAsset, and uploadStatus."
        ),
    ),
    Webservice(
        key="premiummailsettings",
        powers=("hidemyemail",),
        description="Hide My Email address generation and management.",
    ),
    Webservice(
        key="sharedstreams",
        powers=("photos",),
        description=(
            "Legacy shared photo streams. Backs one Photos feature rather than "
            "the service as a whole."
        ),
    ),
    Webservice(
        key="ubiquity",
        powers=("files",),
        description="Legacy iCloud Documents, superseded by iCloud Drive.",
    ),
    Webservice(
        key="uploadimagews",
        powers=("photos",),
        description=(
            "Original single-POST Photos upload host. Apple withdrew the "
            "endpoint it serves, which returns HTTP 410 Gone."
        ),
    ),
)

WEBSERVICE_KEYS: frozenset[str] = frozenset(entry.key for entry in WEBSERVICES)


def webservice(key: str) -> Webservice | None:
    """Return the inventory entry for ``key``, or None if it is not one we use."""

    return next((entry for entry in WEBSERVICES if entry.key == key), None)


def webservices_for(service: str) -> tuple[str, ...]:
    """Return the webservice keys the named ``PyiCloudService`` property needs."""

    return tuple(entry.key for entry in WEBSERVICES if service in entry.powers)


def services_using(key: str) -> tuple[str, ...]:
    """Return the service properties that stop working without ``key``."""

    entry = webservice(key)
    return entry.powers if entry else ()
