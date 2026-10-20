"""Experimental read-only iCloud Mail service."""

from .models import (
    MailBody,
    MailFilteredThread,
    MailFolder,
    MailFolderSearchPage,
    MailMessage,
    MailMessageExport,
    MailMessagePage,
    MailMultiSearchPage,
    MailPart,
    MailPreview,
    MailSearchHit,
    MailSearchPage,
    MailThreadDigest,
    MailThreadFilterPage,
    MailThreadPage,
)
from .service import MailError, MailService

__all__ = [
    "MailBody",
    "MailError",
    "MailFilteredThread",
    "MailFolder",
    "MailFolderSearchPage",
    "MailMessage",
    "MailMessageExport",
    "MailMessagePage",
    "MailMultiSearchPage",
    "MailPart",
    "MailPreview",
    "MailSearchHit",
    "MailSearchPage",
    "MailService",
    "MailThreadDigest",
    "MailThreadFilterPage",
    "MailThreadPage",
]
