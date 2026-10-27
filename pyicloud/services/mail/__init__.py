"""Experimental Mail reads and explicit single-message flag updates."""

from .models import (
    MailBody,
    MailFilteredThread,
    MailFlagUpdate,
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
from .service import MailError, MailService, MailWriteRejected, MailWriteUncertain

__all__ = [
    "MailBody",
    "MailError",
    "MailFilteredThread",
    "MailFlagUpdate",
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
    "MailWriteRejected",
    "MailWriteUncertain",
]
