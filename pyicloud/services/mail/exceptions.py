"""Mail errors shared by read and operation-specific write implementations."""

from pyicloud.exceptions import PyiCloudException


class MailError(PyiCloudException):
    """An invalid response, incomplete enumeration, or failed Mail operation."""


class MailWriteRejected(MailError):
    """The server explicitly rejected the one requested mutation."""


class MailWriteUncertain(MailError):
    """A submitted mutation may have happened; reconcile before retrying."""
