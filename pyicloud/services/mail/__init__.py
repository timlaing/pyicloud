"""Experimental read-only iCloud Mail service."""

from .models import (
    MailBody,
    MailFolder,
    MailMessage,
    MailMessagePage,
    MailPart,
    MailSearchHit,
    MailSearchPage,
    MailThreadPage,
)
from .service import MailError, MailService

__all__ = [
    "MailBody",
    "MailError",
    "MailFolder",
    "MailMessage",
    "MailMessagePage",
    "MailPart",
    "MailSearchHit",
    "MailSearchPage",
    "MailService",
    "MailThreadPage",
]
