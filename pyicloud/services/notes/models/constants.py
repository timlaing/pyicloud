from enum import Enum


class NotesRecordType(str, Enum):
    Note = "Note"
    Folder = "Folder"
    PasswordProtectedNote = "PasswordProtectedNote"


class NotesDesiredKey(str, Enum):
    """
    Enum for common desired keys in CloudKit queries for Notes.
    """

    TITLE_ENCRYPTED = "TitleEncrypted"
    SNIPPET_ENCRYPTED = "SnippetEncrypted"
    FIRST_ATTACHMENT_UTI_ENCRYPTED = "FirstAttachmentUTIEncrypted"
    FIRST_ATTACHMENT_THUMBNAIL = "FirstAttachmentThumbnail"
    FIRST_ATTACHMENT_THUMBNAIL_ORIENTATION = "FirstAttachmentThumbnailOrientation"
    MODIFICATION_DATE = "ModificationDate"
    DELETED = "Deleted"
    FOLDERS = "Folders"
    FOLDER = "Folder"
    ATTACHMENTS = "Attachments"
    PARENT_FOLDER = "ParentFolder"
    NOTE = "Note"
    LAST_VIEWED_MODIFICATION_DATE = "LastViewedModificationDate"
    MINIMUM_SUPPORTED_NOTES_VERSION = "MinimumSupportedNotesVersion"
    IS_PINNED = "IsPinned"
