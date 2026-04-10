"""Typed domain models for the modern Photos CloudKit service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from pyicloud.common.cloudkit import CKQueryFilterBy, CKRecord, CKZoneIDReq
from pyicloud.common.cloudkit.base import CKModel
from pyicloud.exceptions import PyiCloudException


class PhotosServiceException(PyiCloudException):
    """Photo service exception."""

    def __init__(
        self,
        *args,
        photo: "PhotoAsset | None" = None,
        album: "BasePhotoAlbum | None" = None,
    ) -> None:
        super().__init__(*args)
        self.photo = photo
        self.album = album


@dataclass(slots=True)
class PhotoResource:
    """A downloadable photo/video resource variant."""

    key: str
    filename: str
    url: Optional[str]
    size: Optional[int]
    type: Optional[str]
    checksum: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        """Return a compatibility dict for legacy callers/tests."""

        return {
            "filename": self.filename,
            "url": self.url,
            "size": self.size,
            "type": self.type,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True)
class PhotoChangeEvent:
    """A zone change event surfaced by ``icloud photos changes``."""

    kind: str
    record_name: str
    record_type: Optional[str]
    deleted: bool
    modified: Optional[datetime]


@dataclass(slots=True, frozen=True)
class SmartAlbumSpec:
    """Static configuration for one Photos smart album."""

    obj_type: "ObjectTypeEnum"
    list_type: "ListTypeEnum"
    direction: "DirectionEnum"
    query_filters: tuple[CKQueryFilterBy, ...] = ()


class PhotosBatchCountFieldValue(CKModel):
    """Minimal wrapper for the Hyperion item count value."""

    value: int


class PhotosBatchCountFields(CKModel):
    """Fields envelope returned by the Hyperion count query."""

    itemCount: PhotosBatchCountFieldValue


class PhotosBatchCountRecord(CKModel):
    """One record inside a Hyperion count batch response."""

    fields: PhotosBatchCountFields


class PhotosBatchCountResponseBatch(CKModel):
    """One batch entry returned by the Hyperion count endpoint."""

    records: list[PhotosBatchCountRecord] = Field(default_factory=list)


class PhotosBatchCountResponse(CKModel):
    """Response payload for Photos' internal batch count endpoint."""

    batch: list[PhotosBatchCountResponseBatch] = Field(default_factory=list)


class PhotosBatchCountStringListValue(CKModel):
    """STRING_LIST filter value used by the Hyperion count request."""

    type: str = "STRING_LIST"
    value: list[str]


class PhotosBatchCountFilter(CKModel):
    """Single filter envelope for the Hyperion count request."""

    fieldName: str
    comparator: str
    fieldValue: PhotosBatchCountStringListValue


class PhotosBatchCountQuery(CKModel):
    """Internal Photos query object for album/member counts."""

    recordType: str
    filterBy: PhotosBatchCountFilter


class PhotosBatchCountRequestBatch(CKModel):
    """One batch entry posted to the Hyperion count endpoint."""

    resultsLimit: int
    query: PhotosBatchCountQuery
    zoneWide: bool
    zoneID: CKZoneIDReq


class PhotosBatchCountRequest(CKModel):
    """Request payload for Photos' internal batch count endpoint."""

    batch: list[PhotosBatchCountRequestBatch]


class PhotosUploadError(CKModel):
    """One upload-image-ws error item."""

    code: str | None = None
    message: str | None = None


class PhotosUploadResponse(CKModel):
    """Upload-image-ws response payload."""

    records: list[CKRecord] = Field(default_factory=list)
    errors: list[PhotosUploadError] = Field(default_factory=list)
    isDuplicate: bool | None = None


# Import-only type hints to avoid circular imports at runtime.
if False:  # pragma: no cover
    from .constants import DirectionEnum, ListTypeEnum, ObjectTypeEnum
    from .service import BasePhotoAlbum, PhotoAsset
