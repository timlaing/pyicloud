"""Public API for the Notes service."""

from .domain import AttachmentId
from .models import Attachment, ChangeEvent, Note, NoteFolder, NoteSummary
from .service import NotesService

__all__ = [
    "Attachment",
    "AttachmentId",
    "ChangeEvent",
    "Note",
    "NoteFolder",
    "NoteSummary",
    "NotesService",
]
