"""Typed domain models for the modern Photos CloudKit service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field

from pyicloud.common.cloudkit import CKQueryFilterBy, CKRecord, CKZoneIDReq
from pyicloud.common.cloudkit.base import CKModel
from pyicloud.exceptions import PyiCloudException

if TYPE_CHECKING:
    from .constants import DirectionEnum, ListTypeEnum, ObjectTypeEnum
    from .service import BasePhotoAlbum, PhotoAsset


class PhotosServiceException(PyiCloudException):
    """Photo service exception."""

    def __init__(
        self,
        *args: Any,
        photo: PhotoAsset | None = None,
        album: BasePhotoAlbum | None = None,
    ) -> None:
        super().__init__(*args)
        self.photo = photo
        self.album = album


@dataclass(slots=True)
class PhotoResource:
    """A downloadable photo/video resource variant."""

    key: str
    filename: str
    url: str | None
    size: int | None
    type: str | None
    checksum: str | None = None
    width: int | None = None
    height: int | None = None

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
    record_type: str | None
    deleted: bool
    modified: datetime | None


@dataclass(slots=True, frozen=True)
class SmartAlbumSpec:
    """Static configuration for one Photos smart album."""

    obj_type: ObjectTypeEnum
    list_type: ListTypeEnum
    direction: DirectionEnum
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


# Apple reports an already-present file as a 409 on the putAsset result rather
# than with the old endpoint's isDuplicate flag.
DUPLICATE_STATUS = 409

# uploadStatus answers 200 for an unrecognised job id, marking the entry with
# this code rather than omitting it.
UNKNOWN_JOB_ERROR_CODE = 404


class PhotosCreateUploadUrlRequest(CKModel):
    """Request body for ``/photosupload/createUploadUrl``.

    ``assets`` maps a client-generated UUID to the byte size of the file that
    will be sent for it, so Apple can reserve a correctly sized upload slot.
    """

    zoneName: str
    assets: dict[str, int]


class PhotosCreateUploadUrlResponse(CKModel):
    """Reserved upload URLs keyed by the client UUIDs that requested them."""

    uploadUrls: dict[str, str] = Field(default_factory=dict)


class PhotosSingleFileUpload(CKModel):
    """Receipt returned by the content host once the bytes are stored.

    The whole object is echoed back verbatim as ``singleFileUploadRequest``
    when the asset is registered, so it is modelled permissively.
    """

    referenceChecksum: str | None = None
    size: int | None = None
    fileChecksum: str | None = None
    wrappingKey: str | None = None
    receipt: str | None = None


class PhotosSingleFileUploadResponse(CKModel):
    """Envelope wrapping the single-file upload receipt."""

    singleFile: PhotosSingleFileUpload


class PhotosPutAssetFile(CKModel):
    """One file entry in a ``/photosupload/putAsset`` request.

    ``lastModDate`` is epoch milliseconds and ``timeZoneOffset`` is minutes,
    matching what the web client sends.
    """

    fileName: str
    lastModDate: int
    timeZoneOffset: int
    singleFileUploadRequest: PhotosSingleFileUpload


class PhotosPutAssetRequest(CKModel):
    """Request body for ``/photosupload/putAsset``."""

    zoneName: str
    files: list[PhotosPutAssetFile]
    localTimeZoneId: str
    importGroup: str


class PhotosUploadStatusRequest(CKModel):
    """Request body for ``/photosupload/uploadStatus``."""

    uploadJobIds: list[str]


class PhotosUploadStatus(CKModel):
    """Per-job entry returned by ``/photosupload/uploadStatus``.

    A job Apple is still ingesting reports ``progress``; one it does not
    recognise reports ``errorCode`` 404 instead of being omitted from the
    response.
    """

    progress: int | None = None
    errorCode: int | None = None

    @property
    def is_unknown(self) -> bool:
        """Return whether Apple has no record of this upload job."""

        return self.errorCode == UNKNOWN_JOB_ERROR_CODE


class PhotosPutAssetStatus(CKModel):
    """Per-file status block returned inside a ``putAsset`` result.

    ``isRetryable`` is omitted on duplicate (409) responses, which carry an
    ``errorMessage`` instead.
    """

    status: int | None = None
    isRetryable: bool | None = None
    errorMessage: str | None = None


class PhotosPutAssetResult(CKModel):
    """One registered asset returned by ``/photosupload/putAsset``.

    ``cplMaster`` and ``cplAsset`` are the record names the old
    ``uploadimagews`` endpoint only exposed via a follow-up lookup. They are
    supplied for duplicates too, so a re-upload can resolve to the asset that
    already exists. ``uploadJobId`` is absent in that case, since no new
    ingest job is created.
    """

    uploadJobId: str | None = None
    cplMaster: str | None = None
    cplAsset: str | None = None
    response: PhotosPutAssetStatus | None = None

    @property
    def is_duplicate(self) -> bool:
        """Return whether Apple rejected this file as already present."""

        return bool(self.response and self.response.status == DUPLICATE_STATUS)
