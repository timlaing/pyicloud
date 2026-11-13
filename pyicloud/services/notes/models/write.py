"""Notes-specific CloudKit mutation models, using captured value-only fields."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from pyicloud.common.cloudkit import CKZoneIDReq
from pyicloud.common.cloudkit.base import CKModel
from pyicloud.common.cloudkit.models import CKWriteParent
from pyicloud.common.models import FrozenServiceModel


class NoteWriteResult(FrozenServiceModel):
    """Acknowledged note identity and revision returned by a write."""

    id: str
    record_change_tag: str


class NotePinResult(FrozenServiceModel):
    """Result of setting a note's per-user pin state."""

    id: str
    pinned: bool
    changed: bool


class NoteTagResult(NoteWriteResult):
    """Note revision and the inline tag linked to it."""

    tag_id: str
    changed: bool


class NoteTableRevision(NoteWriteResult):
    """Current revisions of a note and its linked table."""

    table_id: str
    table_change_tag: str


class NoteTableWriteResult(NoteTableRevision):
    """An acknowledged change to a linked table."""

    changed: bool


class NoteTable(NoteTableRevision):
    """A read snapshot used to select a table cell and its revision."""

    rows: tuple[tuple[str, ...], ...] = Field(repr=False)


class NotesFolderReference(CKModel):
    """The folder reference shape used in Notes writes."""

    recordName: str
    action: Literal["VALIDATE", "NONE"] = "VALIDATE"
    zoneID: CKZoneIDReq


class NotesPreviewLocator(FrozenServiceModel):
    """Identify the exact preview already stored on an attachment record."""

    recordName: str = Field(min_length=1)
    fieldName: Literal["PreviewImages"] = "PreviewImages"
    listIndex: int = Field(ge=0, strict=True)


class NotesPreviewReceipt(NotesPreviewLocator):
    """One-use receipt for referencing a stored preview without uploading."""

    receipt: str = Field(min_length=1, repr=False)
    fileChecksum: str = Field(min_length=1, repr=False)
    referenceChecksum: str = Field(min_length=1, repr=False)
    wrappingKey: str = Field(min_length=1, repr=False)
    size: int = Field(gt=0, strict=True)


class NotesWriteField(CKModel):
    """A value-only wrapper; an absent value serializes to an empty wrapper."""

    value: (
        int
        | str
        | NotesFolderReference
        | list[NotesFolderReference]
        | NotesPreviewReceipt
        | None
    ) = Field(default=None, repr=False)


class NotesWriteRecord(CKModel):
    """The record fields supported by the Notes modify endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recordName: str
    recordType: Literal["Note", "PasswordProtectedNote"] = "Note"
    fields: dict[str, NotesWriteField] = Field(repr=False)
    parent: CKWriteParent | None = None
    createShortGUID: bool | None = None
    shortGUID: str | None = None
    recordChangeTag: str | None = None


class NotesFolderParent(CKModel):
    """A folder parent; a null name explicitly moves a folder to the root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recordName: str | None


class NotesFolderWriteRecord(CKModel):
    """Write a private folder's name and optional parent reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recordName: str
    recordType: Literal["Folder"] = "Folder"
    fields: dict[str, NotesWriteField] = Field(repr=False)
    parent: NotesFolderParent | None = None
    recordChangeTag: str | None = None


class NotesUserSpecificWriteRecord(CKModel):
    """Write the per-user Notes metadata without changing note content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recordName: str
    recordType: Literal["Note_UserSpecific"] = "Note_UserSpecific"
    fields: dict[str, NotesWriteField] = Field(repr=False)
    parent: None = None
    recordChangeTag: str | None = None


class NotesObjectWriteRecord(CKModel):
    """A table or inline token whose parent is an existing note."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recordName: str
    recordType: Literal["Attachment", "InlineAttachment"]
    fields: dict[str, NotesWriteField] = Field(repr=False)
    parent: CKWriteParent
    recordChangeTag: str | None = None


class NotesModifyOperation(CKModel):
    """One guarded mutation; force and replace operations are not exposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operationType: Literal["create", "update"]
    record: (
        NotesWriteRecord
        | NotesFolderWriteRecord
        | NotesUserSpecificWriteRecord
        | NotesObjectWriteRecord
    )

    @model_validator(mode="after")
    def _validate_revision(self) -> NotesModifyOperation:
        tag = self.record.recordChangeTag
        if (
            self.record.recordType == "PasswordProtectedNote"
            and self.operationType != "update"
        ):
            raise ValueError("Protected Notes records can only be updated")
        if self.operationType == "update" and (tag is None or not tag.strip()):
            raise ValueError("A Notes update requires a record change tag")
        if self.operationType == "create" and tag is not None:
            raise ValueError("A Notes create must not specify a record change tag")
        return self


class NotesModifyRequest(CKModel):
    """Notes zone mutation envelope."""

    operations: list[NotesModifyOperation]
    zoneID: CKZoneIDReq
