"""Public API for the Notes service."""

from .domain import AttachmentId
from .models import Attachment, ChangeEvent, Note, NoteFolder, NoteSummary
from .service import NotesService

__all__ = [
    "NotesService",
    "Note",
    "NoteSummary",
    "NoteFolder",
    "ChangeEvent",
    "Attachment",
    "AttachmentId",
]
