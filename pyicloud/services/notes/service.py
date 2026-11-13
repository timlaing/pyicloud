"""
High-level Apple Notes service built on top of a preconfigured CloudKit client.

Public API:
  - NotesService.recents(limit=50) -> Iterable[NoteSummary]
  - NotesService.recents_in_folder(folder_id, limit=20) -> Iterable[NoteSummary]
  - NotesService.iter_all(since=None) -> Iterable[NoteSummary]
  - NotesService.folders() -> Iterable[NoteFolder]
  - NotesService.in_folder(folder_id, limit=None) -> Iterable[NoteSummary]
  - NotesService.get(note_id, with_attachments=False) -> Note
  - NotesService.create_note(title, text, folder_id=...) -> NoteWriteResult
  - NotesService.update_note(note_id, title=..., text=..., expected_change_tag=...)
  - NotesService.trash_note(note_id, expected_change_tag=...)
  - NotesService.restore_note(note_id, folder_id=..., expected_change_tag=...)
  - NotesService.sync_cursor() -> str
  - NotesService.export_note(note_id, output_dir, **config_kwargs) -> str
  - NotesService.render_note(note_id, **config_kwargs) -> str
  - NotesService.iter_changes(since=None) -> Iterable[ChangeEvent]
  - NotesService.raw -> CloudKitNotesClient

Prefer the typed service methods for normal use. ``raw`` is an escape hatch for
advanced or unsupported CloudKit workflows.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any, Literal
from uuid import UUID, uuid4

from pyicloud.common.cloudkit import (
    CKErrorItem,
    CKFVString,
    CKLookupResponse,
    CKQueryFilterBy,
    CKQueryObject,
    CKQueryResponse,
    CKQuerySortBy,
    CKRecord,
    CKReference,
    CKTombstoneRecord,
    CKZoneChangesZoneReq,
    CKZoneIDReq,
    CloudKitExtraMode,
)
from pyicloud.common.cloudkit.models import (
    CKAssetToken,
    CKReferenceField,
    CKReferenceListField,
    CKWriteParent,
)
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.base import BaseService
from pyicloud.services.notes.decoding import BodyDecoder
from pyicloud.session import PyiCloudSession

from ._constants import NOTES_ZONE, NOTES_ZONE_NAME, NOTES_ZONE_REQ
from ._locked_asset import download_locked_asset
from ._locked_note import decrypt_locked_note_asset, decrypt_locked_note_body
from .client import CloudKitNotesClient
from .domain import AttachmentId, NoteBody
from .encoding import (
    TABLE_UTI,
    TAG_UTI,
    InlineObject,
    append_inline_note_object,
    encode_checklist_state,
    encode_new_note,
    encode_note_edit,
    existing_image_id,
    inline_note_objects,
    remove_inline_note_object,
    replace_inline_note_text,
)
from .exceptions import (
    NoteLockedError,
    NoteNotFound,
    NoteRevisionConflict,
    NotesApiError,
    NotesAuthError,
    NotesError,
    NotesLinkedWriteIncomplete,
    NotesRateLimited,
    NotesWriteRejected,
    NotesWriteUncertain,
)
from .models import Attachment, Note, NoteSummary
from .models.constants import NotesDesiredKey, NotesRecordType
from .models.dto import ChangeEvent, NoteFolder
from .models.rich import RichNoteBody
from .models.write import (
    NotePinResult,
    NotesFolderParent,
    NotesFolderReference,
    NotesFolderWriteRecord,
    NotesModifyOperation,
    NotesObjectWriteRecord,
    NotesPreviewLocator,
    NotesPreviewReceipt,
    NotesUserSpecificWriteRecord,
    NotesWriteField,
    NotesWriteRecord,
    NoteTable,
    NoteTableWriteResult,
    NoteTagResult,
    NoteWriteResult,
)
from .rendering.exporter import NoteExporter, build_datasource, decode_and_parse_note
from .rendering.options import ExportConfig
from .rendering.renderer import NoteRenderer
from .table_encoding import (
    TableAxis,
    TableResizeAction,
    encode_new_table,
    encode_table_cell_edit,
    encode_table_resize,
    table_cells,
)

__all__ = ["NoteLockedError", "NoteNotFound"]

LOGGER = logging.getLogger(__name__)
_HAS_SUBFOLDER_FIELD = "HasSubfolder"
_TRASH_FOLDER_ID = "TrashFolder-CloudKit"
_INLINE_FIELDS = ("TitleEncrypted", "SnippetEncrypted", "TextDataEncrypted")
_EMPTY_ASSET_FIELDS = (
    "FirstAttachmentThumbnail",
    "FirstAttachmentUTIEncrypted",
    "TextDataAsset",
)
_PLAIN_NOTE_FIELDS = frozenset((
    *_INLINE_FIELDS,
    *_EMPTY_ASSET_FIELDS,
    "CreationDate",
    "ModificationDate",
    "FoldersModificationDate",
    "Folder",
    "Folders",
    "Deleted",
))
_UNSUPPORTED_WRITE = (
    "Only supported private inline notes without attachments can be changed"
)
_IMAGE_METADATA = (
    "AttachmentViewType",
    "FirstAttachmentThumbnailOrientation",
    "FirstAttachmentUTIEncrypted",
    "MinimumSupportedNotesVersion",
    "PaperStyleType",
    "ReplicaIDToNotesVersionDataEncrypted",
    "ReplicaIDToUserIDEncrypted",
)
_IMAGE_NOTE_FIELDS = _PLAIN_NOTE_FIELDS.union(_IMAGE_METADATA)
_UNVERIFIED_IMAGE = (
    "Could not verify the note's existing image preview; no note write submitted"
)
_UNVERIFIED_PIN = "Could not verify the private note's per-user pin state"
_UNVERIFIED_TAGS = "Could not verify existing Notes tags"
_PIN_FIELDS = frozenset({"IsPinned", "Folder", "Note", "LastViewedModificationDate"})
_TABLE_ASSET_FIELDS = ("MergeableDataAsset", "EncryptedValues", "EncryptedValuesAsset")
_UNVERIFIED_TABLE = "Could not verify the table linked to this private note"
_UNVERIFIED_EMPTY_FOLDER = "Could not verify that the Notes folder is empty"
_RESTORE_BEFORE_EDIT = "Restore the note before editing it"


# ----------------------------- NotesService ----------------------------------


class NotesService(BaseService):
    """
    Typed, developer-friendly Notes API.

    The service exposes lightweight listing helpers, full-note retrieval,
    attachment download helpers, and HTML render/export utilities while hiding
    most CloudKit details behind ``NoteSummary``, ``Note``, and ``Attachment``
    models.
    """

    _CONTAINER = "com.apple.notes"
    _ENV = "production"
    _SCOPE = "private"

    def __init__(
        self,
        service_root: str,
        session: PyiCloudSession,
        params: dict[str, str],
        *,
        cloudkit_validation_extra: CloudKitExtraMode | None = None,
    ) -> None:
        super().__init__(service_root=service_root, session=session, params=params)
        endpoint = (
            f"{self.service_root}/database/1/{self._CONTAINER}/{self._ENV}/"
            f"{self._SCOPE}"
        )
        # Sensible defaults; lower-case booleans are applied in the raw client
        base_params = {
            "remapEnums": True,
            "getCurrentSyncToken": True,
            **(params or {}),
        }
        self._raw = CloudKitNotesClient(
            base_url=endpoint,
            session=session,
            base_params=base_params,
            validation_extra=cloudkit_validation_extra,
        )
        # In-memory caches
        self._folder_name_cache: dict[str, str | None] = {}
        self._attachment_meta_cache: dict[str, Attachment] = {}

    # -------------------------- Public API methods ---------------------------

    def recents(self, *, limit: int = 50) -> Iterable[NoteSummary]:
        """
        Yield the newest note summaries, ordered most-recent first.

        Args:
            limit: Maximum number of notes to yield.

        Yields:
            ``NoteSummary`` instances with lightweight metadata suitable for
            feeds, selectors, or navigation UIs.
        """
        if limit <= 0:
            return

        desired_keys = [
            NotesDesiredKey.TITLE_ENCRYPTED,
            NotesDesiredKey.SNIPPET_ENCRYPTED,
            NotesDesiredKey.MODIFICATION_DATE,
            NotesDesiredKey.DELETED,
            NotesDesiredKey.FOLDER,
            NotesDesiredKey.FIRST_ATTACHMENT_UTI_ENCRYPTED,
            NotesDesiredKey.FIRST_ATTACHMENT_THUMBNAIL,
            NotesDesiredKey.FIRST_ATTACHMENT_THUMBNAIL_ORIENTATION,
            NotesDesiredKey.ATTACHMENTS,
        ]
        query = CKQueryObject(
            recordType="SearchIndexes",
            filterBy=[
                CKQueryFilterBy(
                    comparator="EQUALS",
                    fieldName="indexName",
                    fieldValue=CKFVString(type="STRING", value="recents"),
                )
            ],
            sortBy=[CKQuerySortBy(fieldName="modTime", ascending=False)],
        )
        yielded = 0
        cont: str | None = None
        LOGGER.debug("Fetching recents: limit=%d", limit)
        while True:
            remaining = limit - yielded
            if remaining <= 0:
                return
            resp: CKQueryResponse = self._raw.query(
                query=query,
                zone_id=NOTES_ZONE_REQ,
                desired_keys=self._coerce_keys(desired_keys),
                results_limit=min(200, remaining),
                continuation=cont,
            )
            for rec in resp.records:
                if isinstance(rec, CKRecord):
                    summary = self._summary_from_record(rec)
                    yielded += 1
                    yield summary
                    if yielded >= limit:
                        LOGGER.debug(
                            "Recents: yielded %d notes (limit reached)", yielded
                        )
                        return
            cont = getattr(resp, "continuationMarker", None)
            if not cont:
                LOGGER.debug("Recents: no more continuation marker, done.")
                return

    def recents_in_folder(
        self, folder_id: str, *, limit: int = 20
    ) -> Iterable[NoteSummary]:
        """
        Yield recent note summaries that belong to ``folder_id``.

        This is a convenience helper that filters the global recents feed rather
        than scanning the full Notes history.
        """
        if limit <= 0:
            return
        emitted = 0
        # Pull a larger window than requested to increase the chance of finding
        # matches fast.
        window = max(200, limit * 5)
        LOGGER.debug(
            "Fetching recents in folder: folder_id=%s limit=%d", folder_id, limit
        )
        for n in self.recents(limit=window):
            if n.folder_id == folder_id and not n.is_deleted:
                yield n
                emitted += 1
                if emitted >= limit:
                    LOGGER.debug(
                        "Recents in folder: yielded %d notes (limit reached)", emitted
                    )
                    return

    def iter_all(self, *, since: str | None = None) -> Iterable[NoteSummary]:
        """
        Yield note summaries from the Notes changes feed.

        Args:
            since: Optional sync token from ``sync_cursor()``. When provided, the
                iterator yields only note records changed since that cursor.

        Yields:
            ``NoteSummary`` instances for full exports, indexing jobs, or local
            cache refreshes.
        """
        if self._matches_current_sync_cursor(since):
            LOGGER.debug("Skipping Notes full scan because sync token is current")
            return

        LOGGER.debug("Iterating all notes%s", f" since={since}" if since else "")
        for zone in self._raw.changes(
            zone_req=CKZoneChangesZoneReq(
                zoneID=NOTES_ZONE,
                desiredRecordTypes=[NotesRecordType.Note],
                desiredKeys=self._coerce_keys([
                    NotesDesiredKey.TITLE_ENCRYPTED,
                    NotesDesiredKey.SNIPPET_ENCRYPTED,
                    NotesDesiredKey.MODIFICATION_DATE,
                    NotesDesiredKey.DELETED,
                    NotesDesiredKey.FOLDER,
                    NotesDesiredKey.ATTACHMENTS,
                ]),
                syncToken=since,
                reverse=False,
            ),
            # CloudKit page sizing is implicit; we cap our own yield volume by iterating
        ):
            for rec in zone.records:
                if isinstance(rec, CKRecord):
                    yield self._summary_from_record(rec)

    def folders(self) -> Iterable[NoteFolder]:
        """Yield all Notes folders, including nested folders and their parents."""
        # A root-only search index omits descendants, and HasSubfolder is not
        # supplied for every folder. Enumerate Folder records and derive the
        # relationship from ParentFolder rather than trusting an absent flag.
        records: list[CKRecord] = []
        for zone in self._raw.changes(
            zone_req=CKZoneChangesZoneReq(
                zoneID=NOTES_ZONE,
                desiredRecordTypes=[NotesRecordType.Folder],
                desiredKeys=self._coerce_keys([
                    NotesDesiredKey.TITLE_ENCRYPTED,
                    "ParentFolder",
                ]),
                reverse=False,
            )
        ):
            records.extend(
                record
                for record in zone.records
                if isinstance(record, CKRecord) and record.recordType == "Folder"
            )
        parents: dict[str, str | None] = {}
        for record in records:
            parent = record.fields.get_value("ParentFolder")
            name = getattr(parent, "recordName", None)
            parents[record.recordName] = (
                name if isinstance(name, str) and name else None
            )
        with_children = {parent for parent in parents.values() if parent}
        for record in records:
            name = self._decode_encrypted(record.fields.get_value("TitleEncrypted"))
            self._folder_name_cache.setdefault(record.recordName, name)
            yield NoteFolder(
                id=record.recordName,
                name=name,
                parent_id=parents[record.recordName],
                has_subfolders=record.recordName in with_children,
                count=None,
                record_change_tag=record.recordChangeTag,
            )

    def in_folder(
        self, folder_id: str, *, limit: int | None = None
    ) -> Iterable[NoteSummary]:
        """
        Yield non-deleted notes in ``folder_id``, ordered newest first.

        Args:
            folder_id: Folder identifier from ``folders()`` or note metadata.
            limit: Optional maximum number of notes to yield.
        """
        emitted = 0
        LOGGER.debug(
            "Fetching notes in folder: folder_id=%s limit=%s", folder_id, limit
        )
        for zone in self._raw.changes(
            zone_req=CKZoneChangesZoneReq(
                zoneID=NOTES_ZONE,
                desiredRecordTypes=[NotesRecordType.Note],
                desiredKeys=self._coerce_keys([
                    NotesDesiredKey.TITLE_ENCRYPTED,
                    NotesDesiredKey.SNIPPET_ENCRYPTED,
                    NotesDesiredKey.MODIFICATION_DATE,
                    NotesDesiredKey.DELETED,
                    NotesDesiredKey.FOLDER,
                    NotesDesiredKey.FIRST_ATTACHMENT_UTI_ENCRYPTED,
                    NotesDesiredKey.FIRST_ATTACHMENT_THUMBNAIL,
                    NotesDesiredKey.FIRST_ATTACHMENT_THUMBNAIL_ORIENTATION,
                    NotesDesiredKey.ATTACHMENTS,
                ]),
                reverse=True,  # newest first
            )
        ):
            for rec in zone.records:
                if not isinstance(rec, CKRecord):
                    continue
                rec_folder_id = self._extract_folder_id(rec)
                if self._is_deleted(rec) or rec_folder_id != folder_id:
                    continue
                yield self._summary_from_record(rec)
                emitted += 1
                if limit and emitted >= limit:
                    LOGGER.debug(
                        "Notes in folder: yielded %d notes (limit reached)", emitted
                    )
                    return

    def get(
        self,
        note_id: str,
        *,
        with_attachments: bool = False,
        password: str | None = None,
    ) -> Note:
        """
        Return a single note with decoded text and optional attachment metadata.

        Args:
            note_id: The CloudKit note record identifier.
            with_attachments: When ``True``, resolve ``Attachment`` metadata and
                include it on the returned ``Note``. Not supported for locked notes.
            password: Optional custom Notes password for local, read-only decryption.

        Raises:
            NoteNotFound: If the note does not exist.
            NoteLockedError: If the note is passphrase-locked and its content
                cannot be read.
        """
        LOGGER.debug(
            "Fetching note: note_id=%s with_attachments=%s", note_id, with_attachments
        )
        target = self._lookup_note_record(
            note_id,
            desired_keys=[
                NotesDesiredKey.TITLE_ENCRYPTED,
                NotesDesiredKey.SNIPPET_ENCRYPTED,
                NotesDesiredKey.MODIFICATION_DATE,
                NotesDesiredKey.DELETED,
                NotesDesiredKey.FOLDER,
                NotesDesiredKey.ATTACHMENTS,
                "TextDataEncrypted",  # may or may not be present
            ],
        )

        summary = self._summary_from_record(target)
        note_body = (
            self._read_locked_body(target, summary, password, with_attachments)
            if summary.is_locked
            else self._decode_note_body(target)
        )
        # Minimal breadcrumbs for body decode outcome
        try:
            if note_body and note_body.text:
                LOGGER.info(
                    "notes.body.decoded ok id=%s len=%d",
                    summary.id,
                    len(note_body.text),
                )
            else:
                LOGGER.info("notes.body.decoded empty id=%s", summary.id)
        except Exception:
            LOGGER.info(
                "notes.body.decoded %s",
                "ok" if note_body and note_body.text else "empty",
            )
        attachments: list[Attachment] | None = None
        html: str | None = None

        text = note_body.text if note_body else None
        attachment_ids: list[AttachmentId] = []
        if note_body and note_body.attachment_ids:
            attachment_ids = note_body.attachment_ids

        if with_attachments:
            attachments = self._resolve_attachments_for_record(
                target, attachment_ids=attachment_ids
            )

        return Note(
            id=summary.id,
            title=summary.title,
            snippet=summary.snippet,
            modified_at=summary.modified_at,
            folder_id=summary.folder_id,
            folder_name=summary.folder_name,
            is_deleted=summary.is_deleted,
            is_locked=summary.is_locked,
            text=text,
            html=html,
            attachments=attachments,
            record_change_tag=target.recordChangeTag,
        )

    def _read_locked_body(
        self,
        record: CKRecord,
        summary: NoteSummary,
        password: str | None,
        with_attachments: bool,
    ) -> NoteBody:
        if password is None:
            LOGGER.warning("Note is locked and cannot be read: %s", summary.id)
            raise NoteLockedError(
                f"Note '{summary.title or summary.id}' is locked and cannot be read."
            )
        if with_attachments:
            raise NoteLockedError(
                "Attachments are not supported for locked Notes reads."
            )
        raw = record.fields.get_value("TextDataEncrypted")
        error = "Could not read this custom-password locked note."
        if not isinstance(raw, bytes) or not self._is_private_record(record):
            raise NoteLockedError(error)
        try:
            plaintext = decrypt_locked_note_body(raw, password)
        except ValueError:
            raise NoteLockedError(error) from None
        body = BodyDecoder().decode(plaintext, max_document_bytes=2 * 1024 * 1024)
        if body is None or not body.text:
            raise NoteLockedError(error)
        return body

    def locked_attachment_ids(
        self, note_id: str, *, password: str
    ) -> tuple[AttachmentId, ...]:
        """Discover supported image IDs from the authenticated locked note body."""
        record = self._lookup_note_record(note_id, require_single=True)
        summary = self._summary_from_record(record)
        if not summary.is_locked or summary.is_deleted:
            raise NoteLockedError("Expected an active custom-password locked note.")
        body = self._read_locked_body(record, summary, password, False)
        return tuple(
            item
            for item in body.attachment_ids
            if item.identifier and item.type_uti in {"public.jpeg", "public.png"}
        )

    def download_locked_attachment(
        self, note_id: str, attachment_id: str, *, password: str
    ) -> bytes:
        """Return one authenticated JPEG or PNG without writing a local file."""
        error = "Could not verify the locked Notes attachment."
        matches = [
            item
            for item in self.locked_attachment_ids(note_id, password=password)
            if item.identifier == attachment_id
        ]
        if len(matches) != 1:
            raise NotesError(error)
        attachment = self._lookup_locked_child(note_id, attachment_id, "Attachment")
        media_ref = attachment.fields.get_value("Media")
        note_ref = attachment.fields.get_value("Note")
        if (
            not isinstance(media_ref, CKReference)
            or not media_ref.recordName.strip()
            or not isinstance(note_ref, CKReference)
            or note_ref.recordName != note_id
            or any(
                ref.zoneID is not None and ref.zoneID.zoneName != NOTES_ZONE_NAME
                for ref in (media_ref, note_ref)
            )
            or attachment.fields.get_value("UTI") != matches[0].type_uti
        ):
            raise NotesError(error)
        media = self._lookup_locked_child(note_id, media_ref.recordName, "Media")
        asset = media.fields.get_value("Asset")
        if not isinstance(asset, CKAssetToken):
            raise NotesError(error)
        data = download_locked_asset(asset)
        try:
            image = decrypt_locked_note_asset(data, password)
        except ValueError:
            raise NotesError(
                "Could not authenticate the locked Notes attachment."
            ) from None
        magic = (
            b"\xff\xd8\xff"
            if matches[0].type_uti == "public.jpeg"
            else b"\x89PNG\r\n\x1a\n"
        )
        if not image.startswith(magic):
            raise NotesError(error)
        return image

    def _lookup_locked_child(self, note_id: str, record_id: str, kind: str) -> CKRecord:
        records = self._raw.lookup([record_id]).records
        record = records[0] if len(records) == 1 else None
        if (
            not isinstance(record, CKRecord)
            or record.recordName != record_id
            or record.recordType != kind
            or record.deleted
            or record.fields.get_value("Deleted")
            or not self._is_private_record(record)
            or record.parent is None
            or record.parent.recordName != note_id
        ):
            raise NotesError("Could not verify the locked Notes attachment.")
        return record

    def create_note(
        self, title: str, text: str | RichNoteBody = "", *, folder_id: str
    ) -> NoteWriteResult:
        """Create a plain- or rich-text note in an explicit private folder."""
        if not folder_id.strip():
            raise ValueError("A Notes folder ID is required")
        content = encode_new_note(title, text)
        self._verify_private_folder_chain(folder_id)
        folder = NotesFolderReference(recordName=folder_id, zoneID=NOTES_ZONE_REQ)
        now = time.time_ns() // 1_000_000
        fields = {
            "CreationDate": NotesWriteField(value=now),
            "ModificationDate": NotesWriteField(value=now),
            "TitleEncrypted": NotesWriteField(value=content.title),
            "SnippetEncrypted": NotesWriteField(value=content.snippet),
            "TextDataEncrypted": NotesWriteField(value=content.body),
            "Folder": NotesWriteField(value=folder),
            "Folders": NotesWriteField(value=[folder]),
            "FirstAttachmentThumbnail": NotesWriteField(),
            "FirstAttachmentUTIEncrypted": NotesWriteField(),
            "TextDataAsset": NotesWriteField(),
        }
        operation = NotesModifyOperation(
            operationType="create",
            record=NotesWriteRecord(
                recordName=str(uuid4()),
                createShortGUID=True,
                parent=CKWriteParent(recordName=folder_id),
                fields=fields,
            ),
        )
        return self._raw.modify_note(operation)

    def _require_write_folder(
        self, folder_id: str, *, allow_trash: bool = False
    ) -> CKRecord:
        """Look up the exact active private folder required for a write."""
        if not folder_id.strip() or (folder_id == _TRASH_FOLDER_ID and not allow_trash):
            raise ValueError(
                "Select a private destination folder outside Recently Deleted"
            )
        response = self._raw.lookup([folder_id])
        if len(response.records) == 1:
            record = response.records[0]
            if (
                isinstance(record, CKRecord)
                and record.recordName == folder_id
                and record.recordType == "Folder"
                and not record.deleted
                and not record.fields.get_value("Deleted")
                and self._is_private_record(record)
            ):
                return record
        raise NotesError("The requested Notes folder is not available for writing")

    def create_folder(
        self, name: str, *, parent_id: str | None = None
    ) -> NoteWriteResult:
        """Create a private top-level folder or subfolder."""
        title = self._encode_folder_title(name)
        self._verify_private_folder_chain(parent_id)
        return self._submit_folder_write(str(uuid4()), title, parent_id)

    def rename_folder(
        self, folder_id: str, name: str, *, expected_change_tag: str
    ) -> NoteWriteResult:
        """Rename a private folder without changing its parent."""
        title = self._encode_folder_title(name)
        record = self._require_mutable_folder(folder_id, expected_change_tag)
        return self._submit_folder_write(
            folder_id, title, self._folder_parent(record), tag=expected_change_tag
        )

    def move_folder(
        self, folder_id: str, *, parent_id: str | None, expected_change_tag: str
    ) -> NoteWriteResult:
        """Move a private folder without creating an ancestry cycle."""
        record = self._require_mutable_folder(folder_id, expected_change_tag)
        if self._folder_parent(record) == parent_id:
            raise NotesError("The folder is already at the requested location")
        title = record.fields.get_value("TitleEncrypted")
        if not isinstance(title, bytes) or not title:
            raise NotesError("Could not verify the existing Notes folder name")
        self._verify_private_folder_chain(parent_id, excluded=folder_id)
        return self._submit_folder_write(
            folder_id,
            base64.b64encode(title).decode("ascii"),
            parent_id,
            tag=expected_change_tag,
            clear_parent=parent_id is None,
        )

    def delete_folder(
        self, folder_id: str, *, expected_change_tag: str, confirm_empty: bool = False
    ) -> bool:
        """Permanently delete an empty private folder after a contents check.

        The server does not provide an atomic emptiness check. Do not move
        items into the folder concurrently with deletion.
        """
        if not confirm_empty:
            raise ValueError("Folder deletion requires explicit confirmation")
        self._require_mutable_folder(folder_id, expected_change_tag)
        self._verify_empty_folder(folder_id)
        self._require_mutable_folder(folder_id, expected_change_tag)
        deleted = self._raw.delete_empty_folder(folder_id)
        self._folder_name_cache.pop(folder_id, None)
        return deleted

    @staticmethod
    def _encode_folder_title(name: str) -> str:
        if (
            not name.strip()
            or name.splitlines() != [name]
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError("A Notes folder name must be a non-empty single line")
        try:
            return base64.b64encode(name.encode("utf-8")).decode("ascii")
        except UnicodeEncodeError:
            raise ValueError("A Notes folder name must be valid Unicode") from None

    @staticmethod
    def _folder_parent(record: CKRecord) -> str | None:
        ref = record.fields.get_value("ParentFolder")
        if ref is None and record.parent is None:
            return None
        if (
            isinstance(ref, CKReference)
            and ref.recordName
            and (ref.zoneID is None or ref.zoneID.zoneName == NOTES_ZONE_NAME)
            and (record.parent is None or record.parent.recordName == ref.recordName)
        ):
            return ref.recordName
        raise NotesError("Could not verify the existing Notes folder parent")

    def _require_mutable_folder(self, folder_id: str, tag: str) -> CKRecord:
        try:
            valid_id = str(UUID(folder_id)) == folder_id.lower()
        except ValueError:
            valid_id = False
        if not valid_id or not tag.strip():
            raise ValueError(
                "A user-created folder ID and its current revision are required"
            )
        record = self._require_write_folder(folder_id)
        if record.recordChangeTag != tag:
            raise NoteRevisionConflict(
                "The folder revision changed; list the folders again"
            )
        self._verify_private_folder_chain(
            self._folder_parent(record), excluded=folder_id
        )
        return record

    def _verify_private_folder_chain(
        self, ancestor: str | None, *, excluded: str | None = None
    ) -> None:
        """Check each ancestor for sharing and reject cyclic folder chains."""
        visited = {excluded} if excluded is not None else set()
        while ancestor is not None:
            if ancestor in visited or len(visited) >= 64:
                raise NotesError("Could not verify a safe Notes folder ancestry")
            visited.add(ancestor)
            ancestor = self._folder_parent(self._require_write_folder(ancestor))

    def _submit_folder_write(
        self,
        folder_id: str,
        title: str,
        parent_id: str | None,
        *,
        tag: str | None = None,
        clear_parent: bool = False,
    ) -> NoteWriteResult:
        fields = {"TitleEncrypted": NotesWriteField(value=title)}
        parent = None
        if parent_id is not None:
            fields["ParentFolder"] = NotesWriteField(
                value=NotesFolderReference(recordName=parent_id, zoneID=NOTES_ZONE_REQ)
            )
            parent = NotesFolderParent(recordName=parent_id)
        elif tag is not None:
            fields["ParentFolder"] = NotesWriteField()
            if clear_parent:
                parent = NotesFolderParent(recordName=None)
        result = self._raw.modify_note(
            NotesModifyOperation(
                operationType="create" if tag is None else "update",
                record=NotesFolderWriteRecord(
                    recordName=folder_id,
                    fields=fields,
                    parent=parent,
                    recordChangeTag=tag,
                ),
            )
        )
        self._folder_name_cache.pop(folder_id, None)
        return result

    def _verify_empty_folder(self, folder_id: str) -> None:
        """Require a complete zone scan without any live children."""
        tokens: set[str] = set()
        for zone in self._raw.changes(
            zone_req=CKZoneChangesZoneReq(
                zoneID=NOTES_ZONE,
                desiredRecordTypes=["Folder", "Note", "PasswordProtectedNote"],
                desiredKeys=["ParentFolder", "Folder", "Folders", "Deleted"],
                reverse=False,
            )
        ):
            if (
                zone.zoneID.zoneName != NOTES_ZONE_NAME
                or zone.syncToken in tokens
                or len(tokens) >= 256
                or "records" not in zone.model_fields_set
            ):
                break
            tokens.add(zone.syncToken)
            for record in zone.records:
                if isinstance(record, CKErrorItem):
                    raise NotesError(_UNVERIFIED_EMPTY_FOLDER)
                if isinstance(record, CKRecord) and self._is_live_child(
                    record, folder_id
                ):
                    raise NotesError("The Notes folder is not empty")
            if zone.moreComing is False:
                return
        raise NotesError(_UNVERIFIED_EMPTY_FOLDER)

    @staticmethod
    def _is_live_child(record: CKRecord, folder_id: str) -> bool:
        """Check all supported parent references on a non-deleted record."""
        if record.deleted or record.fields.get_value("Deleted"):
            return False
        parents = [
            record.parent,
            record.fields.get_value("ParentFolder"),
            record.fields.get_value("Folder"),
        ]
        folders = record.fields.get_value("Folders")
        if isinstance(folders, list):
            parents.extend(folders)
        return any(
            getattr(parent, "recordName", None) == folder_id for parent in parents
        )

    def update_note(
        self,
        note_id: str,
        *,
        title: str,
        text: str | RichNoteBody,
        expected_change_tag: str,
    ) -> NoteWriteResult:
        """Replace the title and body of a supported private note."""
        record, folder_id = self._require_write_note(note_id, expected_change_tag)
        if folder_id == _TRASH_FOLDER_ID:
            raise NotesError(_RESTORE_BEFORE_EDIT)
        fields = self._existing_write_fields(record, folder_id)
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes):
            raise NotesError(_UNSUPPORTED_WRITE)
        encoded = encode_note_edit(compressed, title, text)
        fields.update(
            TitleEncrypted=NotesWriteField(value=encoded.title),
            SnippetEncrypted=NotesWriteField(value=encoded.snippet),
            TextDataEncrypted=NotesWriteField(value=encoded.body),
            ModificationDate=NotesWriteField(value=time.time_ns() // 1_000_000),
        )
        return self._submit_note_update(record, folder_id, fields)

    def trash_note(self, note_id: str, *, expected_change_tag: str) -> NoteWriteResult:
        """Move a supported private note to Recently Deleted, not a tombstone."""
        record, folder_id = self._require_write_note(
            note_id, expected_change_tag, existing_image=None
        )
        if folder_id == _TRASH_FOLDER_ID:
            raise NotesError("The note is already in Recently Deleted")
        return self._relocate_note(record, _TRASH_FOLDER_ID)

    def delete_note_forever(
        self, note_id: str, *, expected_change_tag: str, confirm: bool = False
    ) -> NoteWriteResult:
        """Permanently delete one supported private note from Recently Deleted."""
        if confirm is not True:
            raise ValueError("Confirm permanent deletion explicitly")
        record, folder_id = self._require_write_note(note_id, expected_change_tag)
        if folder_id != _TRASH_FOLDER_ID:
            raise NotesError(
                "Only notes in Recently Deleted can be permanently deleted"
            )
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes) or inline_note_objects(compressed):
            raise NotesError(_UNSUPPORTED_WRITE)
        fields = self._existing_write_fields(record, folder_id)
        fields["Deleted"] = NotesWriteField(value=1)
        return self._submit_note_update(record, folder_id, fields)

    def set_checklist_item(
        self, note_id: str, *, item: int, checked: bool, expected_change_tag: str
    ) -> NoteWriteResult:
        """Check or uncheck a one-based item without replacing the note body."""
        record, folder_id = self._require_write_note(note_id, expected_change_tag)
        if folder_id == _TRASH_FOLDER_ID:
            raise NotesError(_RESTORE_BEFORE_EDIT)
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes):
            raise NotesError(_UNSUPPORTED_WRITE)
        body = encode_checklist_state(compressed, item, checked=checked)
        if body is None:
            return NoteWriteResult(id=note_id, record_change_tag=expected_change_tag)
        fields = self._existing_write_fields(record, folder_id)
        fields.update(
            TextDataEncrypted=NotesWriteField(value=body),
            ModificationDate=NotesWriteField(value=time.time_ns() // 1_000_000),
        )
        return self._submit_note_update(record, folder_id, fields)

    def set_note_pinned(
        self, note_id: str, *, pinned: bool, expected_change_tag: str
    ) -> NotePinResult:
        """Pin or unpin a private note without changing its content revision."""
        record = self._lookup_note_record(note_id, require_single=True)
        if (
            not expected_change_tag.strip()
            or record.recordChangeTag != expected_change_tag
        ):
            raise NoteRevisionConflict(
                "The note revision changed; fetch the note again"
            )
        folder = record.fields.get_value("Folder")
        if (
            record.recordType != "Note"
            or record.deleted
            or self._is_deleted(record)
            or not self._is_private_record(record)
            or not isinstance(folder, CKReference)
            or not folder.recordName
            or folder.zoneID is None
            or folder.zoneID.zoneName != NOTES_ZONE_NAME
            or folder.zoneID.zoneType != "REGULAR_CUSTOM_ZONE"
        ):
            raise NotesError(_UNVERIFIED_PIN)
        owner = folder.zoneID.ownerRecordName
        if not owner or not re.fullmatch(r"_[0-9a-fA-F]{32}", owner) or "::" in note_id:
            raise NotesError(_UNVERIFIED_PIN)
        self._verify_note_source_folder(folder.recordName)
        pin_id = (
            f"Note_UserSpecific::{owner}/Private::{NOTES_ZONE_NAME}::{owner}::{note_id}"
        )
        existing = self._lookup_pin_record(pin_id, note_id, owner)
        previous = (
            existing.fields.get_value("IsPinned") if existing is not None else None
        )
        if previous not in (None, 0, 1):
            raise NotesError(_UNVERIFIED_PIN)
        if bool(previous) == pinned:
            return NotePinResult(id=note_id, pinned=pinned, changed=False)
        viewed = (
            existing.fields.get_value("LastViewedModificationDate")
            if existing is not None
            else None
        )
        if viewed is not None and not isinstance(viewed, datetime):
            raise NotesError(_UNVERIFIED_PIN)
        fields = {
            "IsPinned": NotesWriteField(value=int(pinned)),
            "Note": NotesWriteField(
                value=NotesFolderReference(
                    recordName=note_id, action="NONE", zoneID=NOTES_ZONE_REQ
                )
            ),
            "Folder": NotesWriteField(
                value=NotesFolderReference(
                    recordName=folder.recordName,
                    action="NONE",
                    zoneID=CKZoneIDReq(
                        zoneName=NOTES_ZONE_NAME,
                        ownerRecordName=owner,
                        zoneType=folder.zoneID.zoneType,
                    ),
                )
            ),
            "LastViewedModificationDate": NotesWriteField(
                value=0 if viewed is None else self._date_millis(viewed)
            ),
        }
        self._raw.modify_note(
            NotesModifyOperation(
                operationType="create" if existing is None else "update",
                record=NotesUserSpecificWriteRecord(
                    recordName=pin_id,
                    fields=fields,
                    recordChangeTag=existing.recordChangeTag
                    if existing is not None
                    else None,
                ),
            ),
            public_id=note_id,
        )
        return NotePinResult(id=note_id, pinned=pinned, changed=True)

    def _lookup_pin_record(
        self, pin_id: str, note_id: str, owner: str
    ) -> CKRecord | None:
        records = self._raw.lookup([pin_id]).records
        if len(records) != 1 or records[0].recordName != pin_id:
            raise NotesError(_UNVERIFIED_PIN)
        record = records[0]
        if isinstance(record, CKErrorItem) and record.serverErrorCode == "NOT_FOUND":
            return None
        if (
            not isinstance(record, CKRecord)
            or record.recordType != "Note_UserSpecific"
            or record.deleted
            or not self._is_private_record(record)
            or not record.recordChangeTag
            or not set(record.fields) <= _PIN_FIELDS
            or "LastViewedModificationDate" not in record.fields
        ):
            raise NotesError(_UNVERIFIED_PIN)
        note = record.fields.get_value("Note")
        folder = record.fields.get_value("Folder")
        if (
            not isinstance(note, CKReference)
            or note.recordName != note_id
            or not isinstance(folder, CKReference)
            or folder.zoneID is None
            or folder.zoneID.zoneName != NOTES_ZONE_NAME
            or folder.zoneID.ownerRecordName != owner
        ):
            raise NotesError(_UNVERIFIED_PIN)
        return record

    def restore_note(
        self, note_id: str, *, folder_id: str, expected_change_tag: str
    ) -> NoteWriteResult:
        """Restore a supported note to an explicit private folder."""
        record, source = self._require_write_note(
            note_id, expected_change_tag, existing_image=None
        )
        if source != _TRASH_FOLDER_ID:
            raise NotesError("Only notes in Recently Deleted can be restored")
        self._verify_private_folder_chain(folder_id)
        return self._relocate_note(record, folder_id, restoring=True)

    def move_note(
        self, note_id: str, *, folder_id: str, expected_change_tag: str
    ) -> NoteWriteResult:
        """Move an active private note to another private folder."""
        record, source = self._require_write_note(
            note_id, expected_change_tag, existing_image=None
        )
        if source == _TRASH_FOLDER_ID:
            raise NotesError("Use restore_note for a note in Recently Deleted")
        if source == folder_id:
            raise NotesError("The note is already in the destination folder")
        self._verify_private_folder_chain(folder_id)
        return self._relocate_note(record, folder_id)

    def replace_note_text(
        self, note_id: str, *, old: str, new: str, expected_change_tag: str
    ) -> NoteWriteResult:
        """Replace one text fragment while retaining supported linked objects."""
        record, folder_id = self._require_write_note(
            note_id, expected_change_tag, existing_image=None
        )
        if folder_id == _TRASH_FOLDER_ID:
            raise NotesError(_RESTORE_BEFORE_EDIT)
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes):
            raise NotesError(_UNVERIFIED_IMAGE)
        encoded = replace_inline_note_text(compressed, old, new)
        fields = self._linked_parent_fields(record, folder_id, compressed)
        if (
            encoded.attachment_id is not None
            and "FirstAttachmentThumbnail" not in fields
        ):
            fields["FirstAttachmentThumbnail"] = NotesWriteField(
                value=self._image_preview_receipt(record, encoded.attachment_id)
            )
        fields.update(
            SnippetEncrypted=NotesWriteField(value=encoded.snippet),
            TextDataEncrypted=NotesWriteField(value=encoded.body),
            ModificationDate=NotesWriteField(value=time.time_ns() // 1_000_000),
        )
        return self._submit_note_update(record, folder_id, fields)

    def add_note_tag(
        self, note_id: str, *, tag: str, expected_change_tag: str
    ) -> NoteTagResult:
        """Append a tag to an existing private note, preserving its other content."""
        label = self._tag_label(tag)
        record, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        token = label.upper().encode("ascii")
        tag_ids = [
            obj.id for obj in inline_note_objects(compressed) if obj.uti == TAG_UTI
        ]
        for tag_record in self._lookup_note_tags(note_id, tag_ids):
            if tag_record.fields.get_value("TokenContentIdentifierEncrypted") == token:
                return NoteTagResult(
                    id=note_id,
                    record_change_tag=expected_change_tag,
                    tag_id=tag_record.recordName,
                    changed=False,
                )
        tag_id = str(uuid4())
        body = append_inline_note_object(compressed, InlineObject(tag_id, TAG_UTI))
        fields = self._linked_parent_fields(record, folder_id, compressed)
        now = time.time_ns() // 1_000_000
        fields.update(
            TextDataEncrypted=NotesWriteField(value=body),
            ModificationDate=NotesWriteField(value=now),
        )
        child = NotesObjectWriteRecord(
            recordName=tag_id,
            recordType="InlineAttachment",
            parent=CKWriteParent(recordName=note_id),
            fields={
                "CreationDate": NotesWriteField(value=now),
                "Note": NotesWriteField(
                    value=NotesFolderReference(
                        recordName=note_id, zoneID=NOTES_ZONE_REQ
                    )
                ),
                "Deleted": NotesWriteField(value=0),
                "MinimumSupportedNotesVersion": NotesWriteField(value=7),
                "UTIEncrypted": NotesWriteField(value=self._b64_text(TAG_UTI)),
                "AltTextEncrypted": NotesWriteField(value=self._b64_text(f"#{label}")),
                "TokenContentIdentifierEncrypted": NotesWriteField(
                    value=base64.b64encode(token).decode("ascii")
                ),
            },
        )
        saved, _ = self._submit_linked_note_create(record, folder_id, fields, child)
        return NoteTagResult(
            id=saved.id,
            record_change_tag=saved.record_change_tag,
            tag_id=tag_id,
            changed=True,
        )

    @staticmethod
    def _tag_label(tag: str) -> str:
        label = tag.removeprefix("#")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", label):
            raise ValueError(
                "Use 1 to 64 ASCII letters, digits, hyphens, or underscores for a tag"
            )
        return label

    def remove_note_tag(
        self, note_id: str, *, tag: str, expected_change_tag: str
    ) -> NoteTagResult:
        """Remove one tag from this note, leaving the global hashtag alone."""
        token = self._tag_label(tag).upper().encode("ascii")
        record, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        tag_ids = [
            obj.id for obj in inline_note_objects(compressed) if obj.uti == TAG_UTI
        ]
        matches = [
            child
            for child in self._lookup_note_tags(note_id, tag_ids)
            if child.fields.get_value("TokenContentIdentifierEncrypted") == token
        ]
        if len(matches) != 1:
            raise NotesError("Expected exactly one matching tag on the note")
        child = self._tag_removal_record(note_id, matches[0])
        return self._submit_tag_removal(record, folder_id, compressed, child)

    def _tag_removal_record(
        self, note_id: str, record: CKRecord
    ) -> NotesObjectWriteRecord:
        fields = {
            "Deleted": NotesWriteField(value=1),
            "Note": NotesWriteField(
                value=NotesFolderReference(recordName=note_id, zoneID=NOTES_ZONE_REQ)
            ),
        }
        for name in (
            "AltTextEncrypted",
            "TokenContentIdentifierEncrypted",
            "UTIEncrypted",
        ):
            value = record.fields.get_value(name)
            if not isinstance(value, bytes):
                raise NotesError(_UNVERIFIED_TAGS)
            fields[name] = NotesWriteField(
                value=base64.b64encode(value).decode("ascii")
            )
        created = record.fields.get_value("CreationDate")
        version = record.fields.get_value("MinimumSupportedNotesVersion")
        if (
            not record.recordChangeTag
            or not isinstance(created, datetime)
            or not isinstance(version, int)
            or isinstance(version, bool)
        ):
            raise NotesError(_UNVERIFIED_TAGS)
        fields["CreationDate"] = NotesWriteField(value=self._date_millis(created))
        fields["MinimumSupportedNotesVersion"] = NotesWriteField(value=version)
        return NotesObjectWriteRecord(
            recordName=record.recordName,
            recordChangeTag=record.recordChangeTag,
            recordType="InlineAttachment",
            parent=CKWriteParent(recordName=note_id),
            fields=fields,
        )

    def _submit_tag_removal(
        self,
        record: CKRecord,
        folder_id: str,
        compressed: bytes,
        child: NotesObjectWriteRecord,
    ) -> NoteTagResult:
        note_id = record.recordName
        try:
            saved = self._submit_inline_object_removal(
                record, folder_id, compressed, InlineObject(child.recordName, TAG_UTI)
            )
        except NotesWriteUncertain:
            raise NotesLinkedWriteIncomplete(
                note_id,
                child.recordName,
                note_state="uncertain",
                object_state="not_submitted",
            ) from None
        try:
            self._raw.modify_note(
                NotesModifyOperation(operationType="update", record=child),
                public_id=note_id,
            )
        except (NotesWriteRejected, NotesWriteUncertain) as exc:
            raise NotesLinkedWriteIncomplete(
                note_id,
                child.recordName,
                note_state="confirmed",
                object_state="rejected"
                if isinstance(exc, NotesWriteRejected)
                else "uncertain",
                error_code=exc.error_code
                if isinstance(exc, NotesWriteRejected)
                else None,
            ) from None
        return NoteTagResult(
            id=saved.id,
            record_change_tag=saved.record_change_tag,
            tag_id=child.recordName,
            changed=True,
        )

    def remove_note_table(
        self,
        note_id: str,
        *,
        table_id: str,
        expected_change_tag: str,
        expected_table_change_tag: str,
        confirm: bool = False,
    ) -> NoteWriteResult:
        """Remove one table from the note without deleting its CloudKit record."""
        if confirm is not True:
            raise ValueError("Confirm whole-table removal explicitly")
        record, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        obj = InlineObject(table_id, TABLE_UTI)
        if obj not in inline_note_objects(compressed):
            raise NotesError(_UNVERIFIED_TABLE)
        _, _, revision = self._lookup_note_table(note_id, table_id)
        if revision != expected_table_change_tag:
            raise NoteRevisionConflict(
                "The table revision changed; fetch the table again"
            )
        return self._submit_inline_object_removal(record, folder_id, compressed, obj)

    def _submit_inline_object_removal(
        self, record: CKRecord, folder_id: str, compressed: bytes, obj: InlineObject
    ) -> NoteWriteResult:
        encoded = remove_inline_note_object(compressed, obj)
        fields = self._linked_parent_fields(record, folder_id, compressed)
        fields.update(
            TextDataEncrypted=NotesWriteField(value=encoded.body),
            SnippetEncrypted=NotesWriteField(value=encoded.snippet),
            ModificationDate=NotesWriteField(value=time.time_ns() // 1_000_000),
        )
        return self._submit_note_update(record, folder_id, fields)

    def add_note_table(
        self, note_id: str, *, rows: Sequence[Sequence[str]], expected_change_tag: str
    ) -> NoteTableWriteResult:
        """Append an inline plain-text table without replacing existing content."""
        encoded = encode_new_table(rows)
        record, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        table_id = str(uuid4())
        body = append_inline_note_object(compressed, InlineObject(table_id, TABLE_UTI))
        fields = self._linked_parent_fields(record, folder_id, compressed)
        now = time.time_ns() // 1_000_000
        fields.update(
            TextDataEncrypted=NotesWriteField(value=body),
            ModificationDate=NotesWriteField(value=now),
        )
        child_fields = self._table_body_fields(encoded)
        child_fields.update(
            CreationDate=NotesWriteField(value=now),
            Note=NotesWriteField(
                value=NotesFolderReference(recordName=note_id, zoneID=NOTES_ZONE_REQ)
            ),
            UTI=NotesWriteField(value=TABLE_UTI),
            MinimumSupportedNotesVersion=NotesWriteField(value=2),
            TitleEncrypted=NotesWriteField(value=self._b64_text("Table")),
        )
        child = NotesObjectWriteRecord(
            recordName=table_id,
            recordType="Attachment",
            parent=CKWriteParent(recordName=note_id),
            fields=child_fields,
        )
        saved, table = self._submit_linked_note_create(record, folder_id, fields, child)
        return NoteTableWriteResult(
            id=note_id,
            record_change_tag=saved.record_change_tag,
            table_id=table_id,
            table_change_tag=table.record_change_tag,
            changed=True,
        )

    def get_note_table(self, note_id: str, *, table: int = 1) -> NoteTable:
        """Read a one-based table and the note and table revisions for editing."""
        note = self._lookup_note_record(note_id, require_single=True)
        body = note.fields.get_value("TextDataEncrypted")
        if (
            note.recordType != "Note"
            or note.deleted
            or not self._is_private_record(note)
            or not note.recordChangeTag
            or not isinstance(body, bytes)
        ):
            raise NotesError(_UNVERIFIED_TABLE)
        objects = [obj for obj in inline_note_objects(body) if obj.uti == TABLE_UTI]
        if (
            not isinstance(table, int)
            or isinstance(table, bool)
            or not 1 <= table <= len(objects)
        ):
            raise ValueError("Notes table number does not exist")
        table_id = objects[table - 1].id
        record, raw, revision = self._lookup_note_table(note_id, table_id)
        return NoteTable(
            id=note_id,
            record_change_tag=note.recordChangeTag,
            table_id=record.recordName,
            table_change_tag=revision,
            rows=table_cells(raw),
        )

    def update_note_table_cell(
        self,
        note_id: str,
        *,
        table_id: str,
        row: int,
        column: int,
        text: str,
        expected_change_tag: str,
        expected_table_change_tag: str,
    ) -> NoteTableWriteResult:
        """Change one plain-text table cell using both current revisions."""
        _, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        raw = self._require_table_revision(
            note_id, table_id, compressed, expected_table_change_tag
        )
        encoded = encode_table_cell_edit(raw, row, column, text)
        if encoded is None:
            return NoteTableWriteResult(
                id=note_id,
                record_change_tag=expected_change_tag,
                table_id=table_id,
                table_change_tag=expected_table_change_tag,
                changed=False,
            )
        try:
            saved = self._submit_table_update(
                note_id, table_id, encoded, expected_table_change_tag
            )
        except NotesWriteUncertain:
            raise NotesLinkedWriteIncomplete(
                note_id, table_id, note_state="not_submitted", object_state="uncertain"
            ) from None
        note = self._touch_table_parent(note_id, folder_id, compressed, table_id)
        return NoteTableWriteResult(
            id=note_id,
            record_change_tag=note.record_change_tag,
            table_id=table_id,
            table_change_tag=saved.record_change_tag,
            changed=True,
        )

    def resize_note_table(
        self,
        note_id: str,
        *,
        table_id: str,
        axis: TableAxis,
        action: TableResizeAction,
        index: int,
        expected_change_tag: str,
        expected_table_change_tag: str,
        confirm_nonempty: bool = False,
    ) -> NoteTableWriteResult:
        """Insert before a one-based row or column, or delete it with confirmation."""
        record, folder_id, compressed = self._require_linked_note(
            note_id, expected_change_tag
        )
        raw = self._require_table_revision(
            note_id, table_id, compressed, expected_table_change_tag
        )
        encoded = encode_table_resize(
            raw,
            axis=axis,
            action=action,
            index=index,
            confirm_nonempty=confirm_nonempty,
        )
        fields = self._linked_parent_fields(record, folder_id, compressed)
        fields["ModificationDate"] = NotesWriteField(value=time.time_ns() // 1_000_000)
        try:
            note = self._submit_note_update(record, folder_id, fields)
        except NotesWriteUncertain:
            raise NotesLinkedWriteIncomplete(
                note_id, table_id, note_state="uncertain", object_state="not_submitted"
            ) from None
        try:
            saved = self._submit_table_update(
                note_id, table_id, encoded, expected_table_change_tag
            )
        except (NotesWriteRejected, NotesWriteUncertain) as exc:
            raise NotesLinkedWriteIncomplete(
                note_id,
                table_id,
                note_state="confirmed",
                object_state="rejected"
                if isinstance(exc, NotesWriteRejected)
                else "uncertain",
                error_code=exc.error_code
                if isinstance(exc, NotesWriteRejected)
                else None,
            ) from None
        return NoteTableWriteResult(
            id=note_id,
            record_change_tag=note.record_change_tag,
            table_id=table_id,
            table_change_tag=saved.record_change_tag,
            changed=True,
        )

    def _require_table_revision(
        self, note_id: str, table_id: str, compressed: bytes, revision: str
    ) -> bytes:
        """Read the current body only if the note links to this table revision."""
        if InlineObject(table_id, TABLE_UTI) not in inline_note_objects(compressed):
            raise NotesError(_UNVERIFIED_TABLE)
        _, raw, current = self._lookup_note_table(note_id, table_id)
        if current != revision:
            raise NoteRevisionConflict(
                "The table revision changed; fetch the table again"
            )
        return raw

    def _submit_table_update(
        self, note_id: str, table_id: str, encoded: str, revision: str
    ) -> NoteWriteResult:
        """Submit the revision-guarded update through the Notes mutation client."""
        return self._raw.modify_note(
            NotesModifyOperation(
                operationType="update",
                record=NotesObjectWriteRecord(
                    recordName=table_id,
                    recordType="Attachment",
                    parent=CKWriteParent(recordName=note_id),
                    fields=self._table_body_fields(encoded),
                    recordChangeTag=revision,
                ),
            ),
            public_id=note_id,
        )

    def _touch_table_parent(
        self, note_id: str, folder_id: str, compressed: bytes, table_id: str
    ) -> NoteWriteResult:
        """Advance the note timestamp after a confirmed table mutation."""
        try:
            current = self._lookup_note_record(note_id, require_single=True)
            if (
                current.recordType != "Note"
                or current.deleted
                or self._is_deleted(current)
                or not self._is_private_record(current)
                or self._extract_folder_id(current) != folder_id
                or current.fields.get_value("TextDataEncrypted") != compressed
                or not current.recordChangeTag
                or not set(current.fields) <= _IMAGE_NOTE_FIELDS
                or current.fields.get_value("TextDataAsset") is not None
            ):
                raise NotesError(_UNVERIFIED_TABLE)
            self._verify_note_source_folder(folder_id)
            fields = self._linked_parent_fields(current, folder_id, compressed)
            fields["ModificationDate"] = NotesWriteField(
                value=time.time_ns() // 1_000_000
            )
        except (PyiCloudException, OSError, ValueError):
            raise NotesLinkedWriteIncomplete(
                note_id, table_id, note_state="not_submitted", object_state="confirmed"
            ) from None
        try:
            return self._submit_note_update(current, folder_id, fields)
        except (NotesWriteRejected, NotesWriteUncertain) as exc:
            raise NotesLinkedWriteIncomplete(
                note_id,
                table_id,
                note_state="rejected"
                if isinstance(exc, NotesWriteRejected)
                else "uncertain",
                object_state="confirmed",
                error_code=exc.error_code
                if isinstance(exc, NotesWriteRejected)
                else None,
            ) from None

    def _lookup_note_table(
        self, note_id: str, table_id: str
    ) -> tuple[CKRecord, bytes, str]:
        """Read the exact private table linked to the note."""
        records = self._raw.lookup([table_id]).records
        if len(records) != 1 or not isinstance(records[0], CKRecord):
            raise NotesError(_UNVERIFIED_TABLE)
        record = records[0]
        raw = record.fields.get_value("MergeableDataEncrypted")
        if (
            record.recordName != table_id
            or record.recordType != "Attachment"
            or record.deleted
            or record.fields.get_value("Deleted")
            or not self._is_private_record(record)
            or record.parent is None
            or record.parent.recordName != note_id
            or getattr(record.fields.get_value("Note"), "recordName", None) != note_id
            or record.fields.get_value("UTIEncrypted") != TABLE_UTI.encode("ascii")
            or any(
                record.fields.get_value(field) is not None
                for field in _TABLE_ASSET_FIELDS
            )
            or not isinstance(raw, bytes)
            or not record.recordChangeTag
        ):
            raise NotesError(_UNVERIFIED_TABLE)
        return record, raw, record.recordChangeTag

    def _table_body_fields(self, encoded: str) -> dict[str, NotesWriteField]:
        """Build the inline table fields without external asset references."""
        return {
            **{field: NotesWriteField() for field in _TABLE_ASSET_FIELDS},
            "UTIEncrypted": NotesWriteField(value=self._b64_text(TABLE_UTI)),
            "MergeableDataEncrypted": NotesWriteField(value=encoded),
        }

    @staticmethod
    def _b64_text(text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    def _require_linked_note(
        self, note_id: str, tag: str
    ) -> tuple[CKRecord, str, bytes]:
        record, folder_id = self._require_write_note(note_id, tag, existing_image=None)
        if folder_id == _TRASH_FOLDER_ID:
            raise NotesError(_RESTORE_BEFORE_EDIT)
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes):
            raise NotesError(_UNSUPPORTED_WRITE)
        return record, folder_id, compressed

    def _lookup_note_tags(self, note_id: str, tag_ids: list[str]) -> list[CKRecord]:
        if not tag_ids:
            return []
        records = self._raw.lookup(tag_ids).records
        tags = [record for record in records if isinstance(record, CKRecord)]
        if len(tags) != len(tag_ids) or {tag.recordName for tag in tags} != set(
            tag_ids
        ):
            raise NotesError(_UNVERIFIED_TAGS)
        for tag in tags:
            if (
                tag.recordType != "InlineAttachment"
                or tag.deleted
                or tag.fields.get_value("Deleted")
                or not self._is_private_record(tag)
                or tag.parent is None
                or tag.parent.recordName != note_id
                or getattr(tag.fields.get_value("Note"), "recordName", None) != note_id
                or tag.fields.get_value("UTIEncrypted") != TAG_UTI.encode("ascii")
                or not isinstance(
                    tag.fields.get_value("TokenContentIdentifierEncrypted"), bytes
                )
            ):
                raise NotesError(_UNVERIFIED_TAGS)
        return tags

    def _submit_linked_note_create(
        self,
        record: CKRecord,
        folder_id: str,
        fields: dict[str, NotesWriteField],
        child: NotesObjectWriteRecord,
    ) -> tuple[NoteWriteResult, NoteWriteResult]:
        """Write the parent link, then create and verify the linked record."""
        try:
            self._submit_note_update(record, folder_id, fields)
        except NotesWriteUncertain:
            raise NotesLinkedWriteIncomplete(
                record.recordName,
                child.recordName,
                note_state="uncertain",
                object_state="not_submitted",
            ) from None
        try:
            linked = self._raw.modify_note(
                NotesModifyOperation(operationType="create", record=child),
                public_id=record.recordName,
            )
        except (NotesWriteRejected, NotesWriteUncertain) as exc:
            raise NotesLinkedWriteIncomplete(
                record.recordName,
                child.recordName,
                note_state="confirmed",
                object_state="rejected"
                if isinstance(exc, NotesWriteRejected)
                else "uncertain",
                error_code=exc.error_code
                if isinstance(exc, NotesWriteRejected)
                else None,
            ) from None
        failure = NotesLinkedWriteIncomplete(
            record.recordName,
            child.recordName,
            note_state="uncertain",
            object_state="confirmed",
        )
        try:
            current = self._lookup_note_record(record.recordName, require_single=True)
            body = current.fields.get_value("TextDataEncrypted")
            if (
                current.recordType == "Note"
                and not current.deleted
                and not self._is_deleted(current)
                and self._is_private_record(current)
                and self._extract_folder_id(current) == folder_id
                and isinstance(body, bytes)
                and base64.b64encode(body).decode("ascii")
                == fields["TextDataEncrypted"].value
                and current.recordChangeTag
                and current.recordChangeTag != record.recordChangeTag
            ):
                return NoteWriteResult(
                    id=current.recordName, record_change_tag=current.recordChangeTag
                ), linked
        except (OSError, PyiCloudException):
            raise failure from None
        raise failure

    def _linked_parent_fields(
        self, record: CKRecord, folder_id: str, compressed: bytes
    ) -> dict[str, NotesWriteField]:
        fields = self._preserved_write_fields(record, folder_id)
        if record.fields.get_value("FirstAttachmentThumbnail") is not None:
            fields["FirstAttachmentThumbnail"] = NotesWriteField(
                value=self._image_preview_receipt(record, existing_image_id(compressed))
            )
        return fields

    def _preserved_write_fields(
        self, record: CKRecord, folder_id: str
    ) -> dict[str, NotesWriteField]:
        fields = self._existing_write_fields(record, folder_id)
        for name in (*_IMAGE_METADATA, "Deleted"):
            value = record.fields.get_value(name)
            if isinstance(value, bytes):
                fields[name] = NotesWriteField(
                    value=base64.b64encode(value).decode("ascii")
                )
            elif isinstance(value, int) and not isinstance(value, bool):
                fields[name] = NotesWriteField(value=value)
            elif value is not None:
                raise NotesError(_UNVERIFIED_IMAGE)
        return fields

    def _image_preview_receipt(
        self, note: CKRecord, attachment_id: str
    ) -> NotesPreviewReceipt:
        target = note.fields.get_value("FirstAttachmentThumbnail")
        if (
            not isinstance(target, CKAssetToken)
            or not target.fileChecksum
            or not target.referenceChecksum
            or not target.size
        ):
            raise NotesError(_UNVERIFIED_IMAGE)
        records = self._raw.lookup([attachment_id]).records
        if len(records) != 1 or not isinstance(records[0], CKRecord):
            raise NotesError(_UNVERIFIED_IMAGE)
        attachment = records[0]
        owner = attachment.fields.get_value("Note")
        previews = attachment.fields.get_value("PreviewImages")
        if (
            attachment.recordName != attachment_id
            or attachment.recordType != "Attachment"
            or attachment.deleted
            or attachment.fields.get_value("Deleted")
            or not self._is_private_record(attachment)
            or not isinstance(owner, CKReference)
            or owner.recordName != note.recordName
            or attachment.parent is None
            or attachment.parent.recordName != note.recordName
            or not isinstance(previews, list)
        ):
            raise NotesError(_UNVERIFIED_IMAGE)
        # The note and attachment references have different reference checksums
        # and wrapping keys even when they point to the same file.
        expected = (target.fileChecksum, target.size)
        matches = [
            i
            for i, asset in enumerate(previews)
            if isinstance(asset, CKAssetToken)
            and (asset.fileChecksum, asset.size) == expected
        ]
        if len(matches) != 1:
            raise NotesError(_UNVERIFIED_IMAGE)
        receipt = self._raw.rereference_preview(
            NotesPreviewLocator(recordName=attachment_id, listIndex=matches[0])
        )
        if (receipt.fileChecksum, receipt.size) != expected:
            raise NotesError(_UNVERIFIED_IMAGE)
        return receipt

    @staticmethod
    def _is_private_record(record: CKRecord) -> bool:
        """Reject records outside Notes or with explicit sharing metadata."""
        return (
            (record.zoneID is None or record.zoneID.zoneName == NOTES_ZONE_NAME)
            and record.share is None
            and record.currentUserParticipant is None
            and not record.participants
            and not record.publicPermission
        )

    def _require_write_note(
        self, note_id: str, tag: str, *, existing_image: bool | None = False
    ) -> tuple[CKRecord, str]:
        """Verify the note revision, supported data shape, and folder privacy."""
        if not note_id.strip() or not tag.strip():
            raise ValueError("A note ID and its current revision are required")
        record = self._lookup_note_record(note_id, require_single=True)
        if record.recordChangeTag != tag:
            raise NoteRevisionConflict(
                "The note revision changed; fetch the note again"
            )
        folder = record.fields.get_value("Folder")
        folders = record.fields.get_value("Folders")
        allowed = (
            _IMAGE_NOTE_FIELDS if existing_image is not False else _PLAIN_NOTE_FIELDS
        )
        if existing_image is None:
            existing_image = (
                record.fields.get_value("FirstAttachmentThumbnail") is not None
            )
        empty_assets = ("TextDataAsset",) if existing_image else _EMPTY_ASSET_FIELDS
        if (
            record.recordType != "Note"
            or record.deleted
            or record.fields.get_value("Deleted")
            or not self._is_private_record(record)
            or not record.shortGUID
            or not set(record.fields.keys()) <= allowed
            or any(record.fields.get_value(name) is not None for name in empty_assets)
            or not isinstance(folder, CKReference)
            or not folder.recordName.strip()
            or (folder.zoneID is not None and folder.zoneID.zoneName != NOTES_ZONE_NAME)
            or folders != [folder]
            or (
                record.parent is not None
                and record.parent.recordName != folder.recordName
            )
        ):
            raise NotesError(_UNSUPPORTED_WRITE)
        self._verify_note_source_folder(folder.recordName)
        return record, folder.recordName

    def _verify_note_source_folder(self, folder_id: str) -> None:
        """Verify privacy of the source, including the special Trash folder."""
        if folder_id == _TRASH_FOLDER_ID:
            trash = self._require_write_folder(folder_id, allow_trash=True)
            self._verify_private_folder_chain(
                self._folder_parent(trash), excluded=folder_id
            )
            return
        self._verify_private_folder_chain(folder_id)

    @staticmethod
    def _date_millis(date: datetime) -> int:
        delta = date - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (
            delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
        )

    @staticmethod
    def _existing_write_fields(
        record: CKRecord, folder_id: str
    ) -> dict[str, NotesWriteField]:
        """Keep the unchanged fields Apple echoes for plain-note updates."""
        fields = {name: NotesWriteField() for name in _EMPTY_ASSET_FIELDS}
        for name in _INLINE_FIELDS:
            value = record.fields.get_value(name)
            if not isinstance(value, bytes):
                raise NotesError(_UNSUPPORTED_WRITE)
            fields[name] = NotesWriteField(
                value=base64.b64encode(value).decode("ascii")
            )
        for name in ("CreationDate", "ModificationDate", "FoldersModificationDate"):
            date = record.fields.get_value(name)
            if not isinstance(date, datetime):
                raise NotesError(_UNSUPPORTED_WRITE)
            fields[name] = NotesWriteField(value=NotesService._date_millis(date))
        folder = NotesFolderReference(recordName=folder_id, zoneID=NOTES_ZONE_REQ)
        fields["Folder"] = NotesWriteField(value=folder)
        fields["Folders"] = NotesWriteField(value=[folder])
        return fields

    def _relocate_note(
        self, record: CKRecord, folder_id: str, *, restoring: bool = False
    ) -> NoteWriteResult:
        compressed = record.fields.get_value("TextDataEncrypted")
        if not isinstance(compressed, bytes):
            raise NotesError(_UNSUPPORTED_WRITE)
        inline_note_objects(compressed)
        fields = self._linked_parent_fields(record, folder_id, compressed)
        fields["FoldersModificationDate"] = NotesWriteField(
            value=time.time_ns() // 1_000_000
        )
        if restoring:
            fields["ModificationDate"] = NotesWriteField(
                value=time.time_ns() // 1_000_000
            )
        return self._submit_note_update(record, folder_id, fields)

    def _submit_note_update(
        self, record: CKRecord, folder_id: str, fields: dict[str, NotesWriteField]
    ) -> NoteWriteResult:
        return self._raw.modify_note(
            NotesModifyOperation(
                operationType="update",
                record=NotesWriteRecord(
                    recordName=record.recordName,
                    recordChangeTag=record.recordChangeTag,
                    shortGUID=record.shortGUID,
                    parent=CKWriteParent(recordName=folder_id),
                    fields=fields,
                ),
            )
        )

    def sync_cursor(self) -> str:
        """
        Return the current Notes sync token.

        Persist this token and pass it back to ``iter_all(since=...)`` or
        ``iter_changes(since=...)`` on a later run to perform incremental syncs.
        """
        LOGGER.debug("Fetching sync cursor for Notes zone")
        return self._raw.current_sync_token(zone_name=NOTES_ZONE_NAME)

    def export_note(self, note_id: str, output_dir: str, **config_kwargs: Any) -> str:
        """
        Export a note to HTML on disk and return the generated file path.

        Args:
            note_id: The UUID of the note to export.
            output_dir: Directory where the HTML file will be written.
            **config_kwargs: Keyword arguments forwarded to ``ExportConfig``,
                including ``export_mode``, ``assets_dir``, ``full_page``,
                ``preview_appearance``, ``pdf_object_height``, and link behavior
                settings.

        Returns:
            The path to the generated HTML file.

        Notes:
            By default, this produces archival output: a full HTML page with
            local asset downloads.
        """
        target = self._lookup_note_record(note_id)

        config = ExportConfig(**config_kwargs)
        exporter = NoteExporter(self._raw, config=config)
        path = exporter.export(target, output_dir=output_dir)
        if not path:
            raise NotesError(f"Failed to export note: {note_id}")
        return path

    def render_note(self, note_id: str, **config_kwargs: Any) -> str:
        """
        Render a note to an HTML fragment string.

        Args:
            note_id: The UUID of the note to render.
            **config_kwargs: Keyword arguments forwarded to ``ExportConfig`` to
                tune preview selection or link behavior for the rendered HTML.

        Returns:
            An HTML fragment string. This method does not download assets or
            write files to disk.
        """
        target = self._lookup_note_record(note_id)

        config = ExportConfig(**config_kwargs)
        note = decode_and_parse_note(target)
        if not note:
            return ""

        ds, _ = build_datasource(self._raw, target, note, config)
        renderer = NoteRenderer(config)
        return renderer.render(note, ds)

    def iter_changes(  # noqa: S3776
        self, *, since: str | None = None
    ) -> Iterable[ChangeEvent]:
        """
        Yield ``ChangeEvent`` items from the Notes changes feed.

        Pass a sync token from ``sync_cursor()`` to process only new changes
        since a previous run.
        """
        if self._matches_current_sync_cursor(since):
            LOGGER.debug("Skipping Notes change scan because sync token is current")
            return

        LOGGER.debug("Iterating changes%s", f" since={since}" if since else "")
        for zone in self._raw.changes(
            zone_req=CKZoneChangesZoneReq(
                zoneID=NOTES_ZONE,
                desiredRecordTypes=[NotesRecordType.Note],
                desiredKeys=self._coerce_keys([
                    NotesDesiredKey.TITLE_ENCRYPTED,
                    NotesDesiredKey.SNIPPET_ENCRYPTED,
                    NotesDesiredKey.MODIFICATION_DATE,
                    NotesDesiredKey.DELETED,
                    NotesDesiredKey.FOLDER,
                    NotesDesiredKey.ATTACHMENTS,
                ]),
                syncToken=since,
                reverse=False,
            )
        ):
            for rec in zone.records:
                if isinstance(rec, CKRecord):
                    deleted_flag = self._is_deleted(rec)
                    evt_type: Literal["updated", "deleted"] = (
                        "deleted" if deleted_flag else "updated"
                    )
                    yield ChangeEvent(
                        type=evt_type,
                        note=self._summary_from_record(rec),
                    )
                    continue

                if isinstance(rec, CKTombstoneRecord):
                    record_name = getattr(rec, "recordName", None)
                    if record_name:
                        yield ChangeEvent(
                            type="deleted",
                            note=NoteSummary(
                                id=record_name,
                                title=None,
                                snippet=None,
                                modified_at=None,
                                folder_id=None,
                                folder_name=None,
                                is_deleted=True,
                                is_locked=False,
                            ),
                        )
                    continue

                if isinstance(rec, CKErrorItem):
                    details = {
                        "serverErrorCode": rec.serverErrorCode,
                        "reason": rec.reason,
                        "recordName": rec.recordName,
                    }
                    LOGGER.error(
                        "CloudKit error during change enumeration: %s (%s) record=%s",
                        rec.serverErrorCode or "UNKNOWN",
                        rec.reason,
                        rec.recordName,
                    )
                    raise NotesApiError(
                        (
                            "CloudKit error during change enumeration: "
                            f"{rec.serverErrorCode or 'UNKNOWN'}"
                        ),
                        payload=details,
                    )

                LOGGER.error("Unexpected record type in changes feed: %r", rec)
                raise NotesApiError(
                    "Unexpected record type in changes feed",
                    payload={"record_repr": repr(rec)},
                )

    @property
    def raw(self) -> CloudKitNotesClient:
        """
        Return the authenticated low-level Notes CloudKit client.

        This is an escape hatch for advanced or unsupported operations; prefer
        the typed service methods above for normal use.
        """
        return self._raw

    # -------------------------- Internal helpers -----------------------------

    def _matches_current_sync_cursor(self, since: str | None) -> bool:
        """Return whether an incremental Notes cursor is already current."""
        if not since:
            return False

        try:
            return self._raw.current_sync_token(zone_name=NOTES_ZONE_NAME) == since
        except NotesApiError as exc:
            LOGGER.warning("Failed to preflight Notes sync token: %s", exc)
            return False

    def _lookup_note_record(
        self,
        note_id: str,
        *,
        desired_keys: Iterable[object] | None = None,
        require_single: bool = False,
    ) -> CKRecord:
        """Return one Notes record or raise ``NoteNotFound``."""
        resp = self._raw.lookup(
            record_names=[note_id],
            desired_keys=self._coerce_keys(desired_keys),
        )
        if require_single and len(resp.records) != 1:
            raise NoteNotFound(f"Could not verify one note record: {note_id}")
        for rec in resp.records:
            if isinstance(rec, CKRecord) and rec.recordName == note_id:
                return rec

        LOGGER.warning("Note not found: %s", note_id)
        raise NoteNotFound(f"Note not found: {note_id}")

    @staticmethod
    def _coerce_keys(keys: Iterable[object] | None) -> list[str] | None:
        if keys is None:
            return None
        out: list[str] = []
        for k in keys:
            if isinstance(k, NotesDesiredKey):
                out.append(k.value)
            else:
                out.append(str(k))
        return out

    @staticmethod
    def _decode_encrypted(b: bytes | str | None) -> str | None:
        if b is None:
            return None
        if isinstance(b, str):
            return b
        try:
            return b.decode("utf-8", "replace")
        except Exception:
            return None

    def _extract_folder_id(self, rec: CKRecord) -> str | None:
        f = rec.fields.get_field("Folder")
        if isinstance(f, CKReferenceField) and f.value:
            return f.value.recordName
        fl = rec.fields.get_field("Folders")
        if isinstance(fl, CKReferenceListField) and fl.value:
            return fl.value[0].recordName
        return None

    def _is_deleted(self, record: CKRecord) -> bool:
        return bool(record.fields.get_value("Deleted")) or (
            self._extract_folder_id(record) == _TRASH_FOLDER_ID
        )

    def _folder_name(self, folder_id: str | None) -> str | None:
        if not folder_id:
            return None
        if folder_id in self._folder_name_cache:
            return self._folder_name_cache[folder_id]
        try:
            resp = self._raw.lookup([folder_id], desired_keys=["TitleEncrypted"])
            name: str | None = None
            for rec in resp.records:
                if isinstance(rec, CKRecord) and rec.recordName == folder_id:
                    name = self._decode_encrypted(
                        rec.fields.get_value("TitleEncrypted")
                    )
                    break
            self._folder_name_cache[folder_id] = name
            LOGGER.debug("Folder name resolved: folder_id=%s name=%s", folder_id, name)
            return name
        except (NotesApiError, NotesAuthError, NotesRateLimited):
            self._folder_name_cache[folder_id] = None
            LOGGER.warning("Failed to resolve folder name: folder_id=%s", folder_id)
            return None

    def _summary_from_record(self, rec: CKRecord) -> NoteSummary:
        title = self._decode_encrypted(rec.fields.get_value("TitleEncrypted"))
        snippet = self._decode_encrypted(rec.fields.get_value("SnippetEncrypted"))
        modified = rec.fields.get_value(
            "ModificationDate"
        )  # already tz-aware datetime or None
        deleted = self._is_deleted(rec)
        folder_id = self._extract_folder_id(rec)
        folder_name = self._folder_name(folder_id)
        is_locked = (
            str(getattr(rec, "recordType", "")).lower()
            == NotesRecordType.PasswordProtectedNote.lower()
        )
        return NoteSummary(
            id=rec.recordName,
            title=title,
            snippet=snippet,
            modified_at=modified,
            folder_id=folder_id,
            folder_name=folder_name,
            is_deleted=deleted,
            is_locked=is_locked,
        )

    def _decode_note_body(self, rec: CKRecord) -> NoteBody | None:
        """Decode TextDataEncrypted into a NoteBody (text + attachment IDs)."""

        raw = rec.fields.get_value("TextDataEncrypted")
        if not raw:
            LOGGER.debug("notes.body.missing TextDataEncrypted id=%s", rec.recordName)
            return None
        try:
            nb = BodyDecoder().decode(raw)
            if nb and isinstance(nb, NoteBody):
                return nb
            LOGGER.debug(
                "notes.body.no_text id=%s bytes=%s",
                rec.recordName,
                (
                    len(getattr(nb, "bytes", b""))
                    if nb and getattr(nb, "bytes", None)
                    else "0"
                ),
            )
            return None
        except Exception as e:
            LOGGER.warning("notes.body.decode_failed id=%s err=%s", rec.recordName, e)
            return None

    def _resolve_attachments_for_record(  # noqa: S3776
        self,
        rec: CKRecord,
        *,
        attachment_ids: list[AttachmentId] | None = None,
    ) -> list[Attachment]:
        """Hydrate attachment metadata for a note.

        Combines attachment identifiers from CloudKit references and the decoded
        protobuf body. Missing records are skipped gracefully.
        """

        out: list[Attachment] = []

        alias_ids: list[str] = []
        lookup_candidates: list[str] = []

        if attachment_ids:
            for aid in attachment_ids:
                ident = getattr(aid, "identifier", None)
                if ident:
                    alias_ids.append(ident)

        fld = rec.fields.get_field("Attachments")
        if fld and hasattr(fld, "value"):
            refs: list[CKReference] = getattr(fld, "value", []) or []
            for ref in refs:
                rn = getattr(ref, "recordName", None)
                if rn:
                    lookup_candidates.append(rn)
                    alias_ids.append(rn)

        # Deduplicate alias list while preserving order
        seen_alias: set[str] = set()
        ids: list[str] = []
        for cid in alias_ids:
            if cid not in seen_alias:
                seen_alias.add(cid)
                ids.append(cid)

        LOGGER.debug(
            "notes.attachments.candidates alias=%s lookup=%s",
            ids,
            lookup_candidates,
        )

        if not ids and not lookup_candidates:
            return out

        # Fetch metadata for uncached attachments
        seen_lookup: set[str] = set()
        lookup_ids: list[str] = []
        lookup_source_ids = lookup_candidates if lookup_candidates else ids
        for cid in lookup_source_ids:
            if cid not in seen_lookup:
                seen_lookup.add(cid)
                lookup_ids.append(cid)

        missing = [aid for aid in lookup_ids if aid not in self._attachment_meta_cache]
        if missing:
            desired_keys = [
                "Filename",
                "AttachmentUTI",
                "UTI",
                "Size",
                "Thumbnail",
                "FirstAttachmentThumbnail",
                "PrimaryAsset",
                "PreviewImages",
                "PreviewAppearances",
                "FallbackImage",
                "FallbackPDF",
                "PaperAssets",
                "AttachmentIdentifier",
                "attachmentIdentifier",
                "Identifier",
            ]
            try:
                resp = self._raw.lookup(missing, desired_keys=desired_keys)
            except NotesApiError as err:
                LOGGER.debug(
                    "notes.attachments.lookup_failed ids=%s err=%s",
                    missing,
                    err,
                )
                resp = CKLookupResponse(records=[])

            for rec_a in getattr(resp, "records", []):
                if not isinstance(rec_a, CKRecord):
                    continue

                attachment = self._build_attachment_from_record(rec_a)
                if not attachment:
                    LOGGER.debug(
                        "notes.attachments.unhandled record=%s fields=%s",
                        getattr(rec_a, "recordName", None),
                        list(rec_a.fields.keys())
                        if getattr(rec_a, "fields", None)
                        else None,
                    )
                    continue

                base_id = attachment.id
                aliases = self._attachment_aliases(rec_a, base_id)
                for alias in aliases:
                    self._attachment_meta_cache[alias] = attachment
                    LOGGER.debug(
                        "notes.attachments.cached base=%s alias=%s",
                        base_id,
                        alias,
                    )

        for cid in ids or lookup_ids:
            att = self._attachment_meta_cache.get(cid)
            if att and att not in out:
                out.append(att)

        LOGGER.debug(
            "Resolved %d attachments for note %s",
            len(out),
            getattr(rec, "recordName", None),
        )
        return out

    @staticmethod
    def _coerce_string(rec: CKRecord, names: list[str]) -> str | None:
        for n in names:
            v = rec.fields.get_value(n)
            if v is None:
                continue
            if isinstance(v, bytes):
                try:
                    return v.decode("utf-8", "replace")
                except Exception:
                    continue
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def _attachment_aliases(rec: CKRecord, record_name: str) -> list[str]:
        aliases = [record_name]
        identifier = NotesService._coerce_string(
            rec, ["AttachmentIdentifier", "attachmentIdentifier", "Identifier"]
        )
        if identifier and identifier not in aliases:
            aliases.append(identifier)
        return aliases

    def _build_attachment_from_record(self, rec: CKRecord) -> Attachment | None:
        aid = getattr(rec, "recordName", None)
        if not aid:
            return None

        filename = self._coerce_string(rec, ["Filename", "Name", "FileName"])
        uti = self._coerce_string(rec, ["AttachmentUTI", "UTI"])
        size = self._coerce_int(rec, ["Size", "FileSize"])

        download_url = None
        preview_url = None
        thumbnail_url = None

        download_url = download_url or self._coerce_asset_url(rec, ["PrimaryAsset"])
        download_url = download_url or self._coerce_asset_url(rec, ["FallbackPDF"])
        download_url = download_url or self._coerce_asset_url_from_list(
            rec, "FallbackPDF"
        )
        download_url = download_url or self._coerce_asset_url_from_list(
            rec, "PaperAssets"
        )

        preview_url = preview_url or self._coerce_asset_url_from_list(
            rec, "PreviewImages"
        )
        preview_url = preview_url or self._coerce_asset_url(rec, ["FallbackImage"])

        thumbnail_url = thumbnail_url or self._coerce_asset_url(
            rec, ["Thumbnail", "FirstAttachmentThumbnail"]
        )
        thumbnail_url = thumbnail_url or preview_url

        return Attachment(
            id=aid,
            filename=filename,
            uti=uti,
            size=size,
            download_url=download_url,
            preview_url=preview_url,
            thumbnail_url=thumbnail_url,
        )

    @staticmethod
    def _coerce_int(rec: CKRecord, names: list[str]) -> int | None:
        for n in names:
            v = rec.fields.get_value(n)
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
            if isinstance(v, str) and v.isdigit():
                return int(v)
        return None

    @staticmethod
    def _coerce_asset_url(rec: CKRecord, names: list[str]) -> str | None:
        for n in names:
            fld = rec.fields.get_field(n)
            if not fld:
                continue
            # ASSET/ASSETID wrapper -> value.downloadURL if present
            val = getattr(fld, "value", None)
            url = getattr(val, "downloadURL", None)
            if isinstance(url, str):
                return url
        return None

    @staticmethod
    def _coerce_asset_url_from_list(rec: CKRecord, name: str) -> str | None:
        fld = rec.fields.get_field(name)
        if not fld:
            return None
        val = getattr(fld, "value", None)
        if isinstance(val, (list, tuple)):
            for token in val:
                url = None
                if isinstance(token, dict):
                    url = token.get("downloadURL")
                else:
                    url = getattr(token, "downloadURL", None)
                if isinstance(url, str) and url:
                    return url
        return None

    def download_attachment_to(self, att: Attachment, directory: str) -> str:
        """Download an attachment to a directory and return the local path."""

        url = att.download_url or att.preview_url or att.thumbnail_url
        if not url:
            raise NotesApiError("Attachment does not expose a download URL.")
        LOGGER.debug("Downloading attachment %s to %s", att.id, directory)
        return self._raw.download_asset_to(url, directory)

    def stream_attachment(
        self, att: Attachment, *, chunk_size: int = 65536
    ) -> Iterator[bytes]:
        """Yield the attachment bytes in chunks."""

        url = att.download_url or att.preview_url or att.thumbnail_url
        if not url:
            raise NotesApiError("Attachment does not expose a download URL.")
        LOGGER.debug("Streaming attachment %s chunk_size=%d", att.id, chunk_size)
        yield from self._raw.download_asset_stream(url, chunk_size=chunk_size)
