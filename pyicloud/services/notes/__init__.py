"""Public API for the Notes service."""

from .domain import AttachmentId
from .exceptions import (
    NoteRevisionConflict,
    NotesLinkedWriteIncomplete,
    NotesWriteRejected,
    NotesWriteUncertain,
)
from .models import Attachment, ChangeEvent, Note, NoteFolder, NoteSummary
from .models.rich import NoteParagraph, NoteTextRun, RichNoteBody
from .models.write import (
    NotePinResult,
    NoteTable,
    NoteTableWriteResult,
    NoteTagResult,
    NoteWriteResult,
)
from .service import NotesService

__all__ = [
    "NotesService",
    "Note",
    "NoteSummary",
    "NoteFolder",
    "ChangeEvent",
    "Attachment",
    "AttachmentId",
    "NoteWriteResult",
    "NotePinResult",
    "NoteTagResult",
    "NoteTable",
    "NoteTableWriteResult",
    "NotesLinkedWriteIncomplete",
    "NoteParagraph",
    "NoteTextRun",
    "RichNoteBody",
    "NoteRevisionConflict",
    "NotesWriteRejected",
    "NotesWriteUncertain",
]
