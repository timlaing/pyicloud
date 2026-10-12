"""Every public error hierarchy derives from the documented root.

``docs/api/exceptions.md`` promises that a single ``except PyiCloudException``
block catches everything the library raises. Each service used to root its own
error hierarchy at the builtin ``Exception`` instead, so the promise did not
hold for the Invites, Notes, Reminders, or CloudKit errors (#415).
"""

from __future__ import annotations

import pytest

from pyicloud.common.cloudkit.client import (
    CloudKitApiError,
    CloudKitAuthError,
    CloudKitRateLimited,
)
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.invites.client import (
    InvitesApiError,
    InvitesAuthError,
    InvitesEntitlementError,
    InvitesError,
    InvitesRateLimited,
)
from pyicloud.services.invites.service import EventNotFound
from pyicloud.services.notes.client import (
    NotesApiError,
    NotesAuthError,
    NotesError,
    NotesRateLimited,
)
from pyicloud.services.notes.service import NoteLockedError, NoteNotFound
from pyicloud.services.reminders.client import RemindersApiError, RemindersAuthError

#: Every error a caller can catch, across all four hierarchies. The base of
#: each is listed alongside its subclasses so a regression on either the root
#: or an individual subclass is caught.
PUBLIC_ERRORS: list[type[PyiCloudException]] = [
    CloudKitAuthError,
    CloudKitRateLimited,
    CloudKitApiError,
    InvitesError,
    InvitesAuthError,
    InvitesRateLimited,
    InvitesEntitlementError,
    InvitesApiError,
    EventNotFound,
    NotesError,
    NotesAuthError,
    NotesRateLimited,
    NotesApiError,
    NoteNotFound,
    NoteLockedError,
    RemindersAuthError,
    RemindersApiError,
]


@pytest.mark.parametrize("error", PUBLIC_ERRORS, ids=lambda cls: cls.__name__)
def test_public_errors_derive_from_the_documented_root(
    error: type[PyiCloudException],
) -> None:
    """Each documented error is a ``PyiCloudException`` subclass."""
    assert issubclass(error, PyiCloudException)


@pytest.mark.parametrize("error", PUBLIC_ERRORS, ids=lambda cls: cls.__name__)
def test_public_errors_are_caught_as_pyicloud_exception(
    error: type[PyiCloudException],
) -> None:
    """A single ``except PyiCloudException`` block catches every error."""
    exc = error("boom")

    with pytest.raises(PyiCloudException):
        raise exc
