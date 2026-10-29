"""Exceptions raised by the Mail service, shared by reads, sync, and writes."""

from pyicloud.exceptions import PyiCloudException


class MailError(PyiCloudException):
    """An invalid response, incomplete enumeration, or failed Mail operation."""


class MailWriteRejected(MailError):
    """The server explicitly rejected the one requested mutation."""


class MailWriteUncertain(MailError):
    """A submitted mutation may have happened; reconcile before retrying."""


class SyncResetRequired(ValueError):
    """The candidate cannot establish a safe, complete change batch."""


class SyncHistoryExpired(SyncResetRequired):
    """Apple explicitly rejected saved counters as older than retained history."""


class SyncInventoryRequired(SyncResetRequired):
    """A count-only change hint requires an explicitly authorized full scan."""


class StateConflict(SyncResetRequired):
    """Another writer changed the inventory after the caller read its revision."""


class UnsafeSyncStorage(ValueError):
    """The local state location cannot be opened without weakening its safety."""


class SyncStorageBusy(ValueError):
    """Another process holds this mailbox's local state lock; retry later."""
