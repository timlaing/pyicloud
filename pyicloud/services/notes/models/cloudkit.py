"""
CloudKit “wire” models for /records/query requests & responses (Notes container).
- Response models (records) + refined request models (query payloads).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import (
    Base64Bytes,
    BeforeValidator,
    Field,
    JsonValue,
    PlainSerializer,
    RootModel,
    TypeAdapter,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from ._ck_base import CKModel

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Year-zero/ancient timestamp handling: normalize to None for Optional[datetime] fields.
# Python datetime supports years 1..9999 (no year 0). Some APIs use ancient ms values
# as "not set" sentinels; we treat those as None during parsing.
CANONICAL_MIN_MS = -62135596800000  # 0001-01-01T00:00:00Z
SENTINEL_ZERO_MS: set[int] = {
    CANONICAL_MIN_MS,
    -62135769600000,  # observed in captures (approx 2 days earlier)
}


def _from_millis_or_none(v):
    # Accept int/float or digit-only str; be strict about being milliseconds.
    if isinstance(v, (int, float)):
        iv = int(v)
    elif isinstance(v, str) and v.isdigit():
        iv = int(v)
    elif isinstance(v, str) and v.startswith("0001-01-01"):
        # ISO-like sentinel for year 1 -> treat as None
        return None
    else:
        raise TypeError("Expected milliseconds since epoch as int or digit string")
    # Coerce sentinels and anything older than canonical MIN to None
    if iv in SENTINEL_ZERO_MS or iv <= CANONICAL_MIN_MS:
        return None
    return datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc)


def _to_millis(dt: datetime) -> int:
    if dt.tzinfo is None:
        # If you prefer, raise instead of coercing.
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


MillisDateTime = Annotated[
    datetime,
    BeforeValidator(_from_millis_or_none),
    PlainSerializer(_to_millis, return_type=int, when_used="json"),
    WithJsonSchema({"type": "integer", "description": "milliseconds since Unix epoch"}),
]

# Nullable variant used for wrappers that can legitimately carry "no timestamp"
MillisDateTimeOrNone = Annotated[
    Optional[datetime],
    BeforeValidator(lambda v: None if v is None else _from_millis_or_none(v)),
    PlainSerializer(
        lambda v: None if v is None else _to_millis(v),
        return_type=int,
        when_used="json",
    ),
    WithJsonSchema(
        {
            "type": ["integer", "null"],
            "description": "milliseconds since Unix epoch or null sentinel",
        }
    ),
]


# Some top-level properties (e.g., CKRecord.expirationTime) arrive as seconds-since-epoch
# in this API. Be tolerant and also accept millisecond values if Apple changes shape.
def _from_secs_or_millis(v):
    if isinstance(v, (int, float)):
        iv = int(v)
    elif isinstance(v, str) and v.isdigit():
        iv = int(v)
    else:
        raise TypeError("Expected seconds or milliseconds since epoch as int/str")
    # Heuristic: values < 1e11 are seconds (covers dates up to ~5138 CE)
    if abs(iv) < 100_000_000_000:
        return datetime.fromtimestamp(iv, tz=timezone.utc)
    # Otherwise treat as milliseconds
    return datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc)


def _to_secs(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


SecsOrMillisDateTime = Annotated[
    datetime,
    BeforeValidator(_from_secs_or_millis),
    PlainSerializer(_to_secs, return_type=int, when_used="json"),
    WithJsonSchema({"type": "integer", "description": "seconds since Unix epoch"}),
]


# ---------------------------------------------------------------------------
# CloudKit primitives shared by request & response
# ---------------------------------------------------------------------------


class CKZoneID(CKModel):
    zoneName: str
    ownerRecordName: Optional[str] = None
    zoneType: Optional[str] = None


class CKAuditInfo(CKModel):
    """
    Appears as `created` / `modified` at the record level (response).
    """

    timestamp: MillisDateTime
    userRecordName: Optional[str] = None
    deviceID: Optional[str] = None


class CKParent(CKModel):
    recordName: str


class CKStableUrl(CKModel):
    routingKey: Optional[str] = None
    shortTokenHash: Optional[str] = None
    protectedFullToken: Optional[str] = None
    encryptedPublicSharingKey: Optional[str] = None
    displayedHostname: Optional[str] = None


class CKChainProtectionInfo(CKModel):
    bytes: Optional[Base64Bytes] = None  # base64 string as seen on wire
    pcsChangeTag: Optional[str] = None


# ---------------------------------------------------------------------------
# Share-surface models (coarse first)
# ---------------------------------------------------------------------------


class CKShare(CKModel):
    """
    Minimal share reference as seen embedded under a record's top-level `share` key.
    Your audit samples only surfaced `recordName` and `zoneID` inside this object.
    Keep this coarse for now; we can expand with shortGUID/shortTokenHash, etc.,
    if they appear nested here in future captures.
    """

    recordName: Optional[str] = None
    zoneID: Optional[CKZoneID] = None


class CKReference(CKModel):
    """
    Value inside REFERENCE / REFERENCE_LIST typed fields (both request & response).
    """

    recordName: str
    action: Optional[str] = None  # e.g., "VALIDATE"
    zoneID: Optional[CKZoneID] = None


# ---------------------------------------------------------------------------
# Response-side: typed field wrappers under record.fields
# ---------------------------------------------------------------------------


class _CKFieldBase(CKModel):
    # Every field wrapper has a 'type' discriminator and a 'value'
    type: str


class CKTimestampField(_CKFieldBase):
    type: Literal["TIMESTAMP"]
    value: (
        MillisDateTimeOrNone  # Apple sometimes sends a "zero" ms sentinel; map to None
    )


class CKInt64Field(_CKFieldBase):
    type: Literal["INT64"]
    value: int


class CKEncryptedBytesField(_CKFieldBase):
    type: Literal["ENCRYPTED_BYTES"]
    value: Base64Bytes


class CKReferenceField(_CKFieldBase):
    type: Literal["REFERENCE"]
    value: CKReference


class CKReferenceListField(_CKFieldBase):
    type: Literal["REFERENCE_LIST"]
    value: List[CKReference]


# Occasionally CloudKit also uses STRING-typed wrappers; not present in your
# three responses at the 'fields' level, but kept for completeness.
class CKStringField(_CKFieldBase):
    type: Literal["STRING"]
    value: str
    isEncrypted: Optional[bool] = None  # seen on some STRING wrappers (lookup)


# Asset thumbnails / tokens (e.g. FirstAttachmentThumbnail)
class CKAssetToken(CKModel):
    # Keep as str to preserve exact wire representation.
    fileChecksum: Optional[str] = None
    referenceChecksum: Optional[str] = None
    wrappingKey: Optional[str] = None
    downloadURL: Optional[str] = None
    size: Optional[int] = None


class CKAssetIDField(_CKFieldBase):
    type: Literal["ASSETID"]
    value: CKAssetToken


# Optional but seen in other CK APIs
class CKAssetField(_CKFieldBase):
    type: Literal["ASSET"]
    value: CKAssetToken


class CKDoubleField(_CKFieldBase):
    type: Literal["DOUBLE"]
    value: float


class CKBytesField(_CKFieldBase):
    # Raw bytes seen on wire (e.g., LastViewedTimestamp, CryptoPassphraseVerifier)
    type: Literal["BYTES"]
    value: Base64Bytes


class CKDoubleListField(_CKFieldBase):
    type: Literal["DOUBLE_LIST"]
    value: List[float]


class CKInt64ListField(_CKFieldBase):
    type: Literal["INT64_LIST"]
    value: List[int]


class CKAssetIDListField(_CKFieldBase):
    # e.g., PreviewImages, PaperAssets (most cases)
    type: Literal["ASSETID_LIST"]
    value: List[CKAssetToken]


class CKUnknownListField(_CKFieldBase):
    # extremely rare: seen on PaperAssets as UNKNOWN_LIST in your samples
    type: Literal["UNKNOWN_LIST"]
    value: List[JsonValue]  # keep generic to be future-proof


class CKPassthroughField(_CKFieldBase):
    type: str
    value: JsonValue


# One source of truth for known CloudKit field 'type' tags.
KNOWN_TAGS: frozenset[str] = frozenset(
    {
        "TIMESTAMP",
        "INT64",
        "ENCRYPTED_BYTES",
        "REFERENCE",
        "REFERENCE_LIST",
        "STRING",
        "ASSETID",
        "ASSET",
        "DOUBLE",
        "BYTES",
        "DOUBLE_LIST",
        "INT64_LIST",
        "ASSETID_LIST",
        "UNKNOWN_LIST",
    }
)


# Discriminated union over all known field wrapper types we saw/anticipate.
# Split into (a) a known, literal-tagged union and (b) an open wrapper that
# gracefully falls back to CKPassthroughField for unknown tags.
KnownCKField = Annotated[
    Union[
        CKTimestampField,
        CKInt64Field,
        CKEncryptedBytesField,
        CKReferenceField,
        CKReferenceListField,
        CKStringField,
        CKAssetIDField,
        CKAssetField,
        CKDoubleField,
        CKBytesField,
        CKDoubleListField,
        CKInt64ListField,
        CKAssetIDListField,
        CKUnknownListField,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Record and response
# ---------------------------------------------------------------------------


class CKFieldOpen(RootModel[Union[KnownCKField, CKPassthroughField]]):
    """
    Public API for field wrappers:
      - `.value` (preferred): the decoded inner `value` of the CK wrapper
      - `.type_tag`: the CloudKit `type` string (e.g., "TIMESTAMP", "INT64_LIST")
      - `.unwrap()`: return the inner typed wrapper instance (e.g., `CKTimestampField`)

    Implementation detail: `.root` is internal — avoid relying on it outside this class.
    """

    # v2 root models name the inner value "root"
    root: Union[KnownCKField, CKPassthroughField]

    @property
    def value(self):
        # unified way to read the inner 'value' without touching .root
        return getattr(self.root, "value", None)

    @property
    def type_tag(self) -> Optional[str]:
        # useful when inspecting unknown/passthrough fields
        return getattr(self.root, "type", None)

    def unwrap(self):
        """Return the inner typed wrapper (e.g., CKTimestampField).
        Public escape hatch; prefer `.value` for most use-cases.
        """
        return self.root

    @model_validator(mode="before")
    @classmethod
    def _dispatch_before(cls, obj):
        """
        Ensure nested contexts (e.g., values inside Dict[str, CKFieldOpen]) use the same
        discriminator-based dispatch as our explicit model_validate(...) call.

        IMPORTANT: For RootModel, 'before' must return the *underlying* value, not
        a dict like {'root': ...}; returning a dict breaks the discriminated union
        when this model is nested.
        """
        t = obj.get("type") if isinstance(obj, dict) else None

        # Known wrappers: coerce to the exact discriminated member instance
        if t in KNOWN_TAGS:
            return TypeAdapter(KnownCKField).validate_python(obj)

        # Already an instance of one of our wrapper models -> keep as-is
        # (covers CKEncryptedBytesField, CKTimestampField, CKPassthroughField, etc.)
        if isinstance(obj, _CKFieldBase):
            return obj

        # Explicit but unknown wrapper -> passthrough instance
        if isinstance(obj, dict) and "type" in obj and "value" in obj:
            return CKPassthroughField(**obj)

        # Fallback: wrap whatever came in as passthrough
        return CKPassthroughField(type=str(t) if t else "UNKNOWN", value=obj)


# Insert CKFields class here (after CKFieldOpen, before record/response section)
class CKFields(dict[str, CKFieldOpen]):
    """
    Dict-like container that also allows attribute access, e.g.:
        rec.fields.ModificationDate.value
    Falls back to normal dict behavior for [] access.

    Public surface:
      - Attribute access: `rec.fields.<FieldName>.value`
      - Mapping access:   `rec.fields["FieldName"].value`
      - Helpers:          `get_field()`, `get_value()`

    Implementation detail: `.root` is **internal**; client code should not use it.
    Use `.unwrap()` if you need the inner typed wrapper for `isinstance` checks.
    """

    def __getattr__(self, name: str) -> CKFieldOpen:
        try:
            return dict.__getitem__(self, name)
        except KeyError as e:
            raise AttributeError(name) from e

    def __dir__(self):
        base = set(super().__dir__())
        return sorted(base | set(self.keys()))

    def get_field(self, key: str):
        f = self.get(key)
        if f is None:
            return None
        # Use public API; avoid touching `.root` here.
        return f.unwrap() if hasattr(f, "unwrap") else f

    def get_value(self, key: str):
        f = self.get_field(key)
        return None if f is None else getattr(f, "value", None)


# ---------------------------------------------------------------------------
# Record and response
# ---------------------------------------------------------------------------


class CKRecordType(str, Enum):
    Note = "Note"
    Folder = "Folder"
    PasswordProtectedNote = "PasswordProtectedNote"


class CKRecord(CKModel):
    """
    A CloudKit record as returned in /records/query for Notes.

    The 'fields' map contains app-level fields by **PascalCase** names
    (e.g., TitleEncrypted, ModificationDate, Deleted, Folder, Folders, ...),
    each wrapped in a CKField type above.
    """

    recordName: str
    recordType: Union[CKRecordType, str]  # allow unknown record types

    # App-level fields (typed wrappers)
    fields: CKFields = Field(default_factory=CKFields)

    @field_validator("fields", mode="before")
    @classmethod
    def _coerce_fields(cls, v):
        """
        Ensure the mapping is validated item-by-item to CKFieldOpen
        and wrapped in CKFields to enable attribute access DX.
        """
        if isinstance(v, CKFields):
            return v
        if isinstance(v, dict):
            adapter = TypeAdapter(CKFieldOpen)
            return CKFields({k: adapter.validate_python(val) for k, val in v.items()})
        return v

    @field_validator("fields")
    @classmethod
    def _validate_encrypted_bytes(cls, v: CKFields) -> CKFields:
        """Enforce a strict invariant: any field ending with 'Encrypted' must be
        represented as ENCRYPTED_BYTES. This guarantees downstream code can
        assume a single decoding path (bytes) for encrypted payloads.

        If a server variant ever sends a different wrapper (e.g., STRING), this
        validator will fail fast with a clear error during model validation.
        """
        try:
            for key, wrapper in v.items():
                if not isinstance(key, str) or not key.endswith("Encrypted"):
                    continue
                # CKFieldOpen unwrap -> typed wrapper (e.g., CKEncryptedBytesField)
                inner = wrapper.unwrap() if hasattr(wrapper, "unwrap") else wrapper
                tag = getattr(inner, "type", None)
                if tag != "ENCRYPTED_BYTES":
                    # Keep the message explicit to aid debugging if the server flips shape
                    raise TypeError(
                        f"Field '{key}' must be ENCRYPTED_BYTES, got {tag!r}"
                    )
        except Exception as e:
            # Re-raise to integrate with Pydantic's error surfacing
            raise e
        return v

    # Often present, often empty object
    pluginFields: Dict[str, JsonValue] = Field(default_factory=dict)

    # Record metadata
    recordChangeTag: Optional[str] = None
    created: Optional[CKAuditInfo] = None
    modified: Optional[CKAuditInfo] = None
    deleted: Optional[bool] = None

    zoneID: Optional[CKZoneID] = None
    parent: Optional[CKParent] = None

    # Sharing/identity/exposure
    displayedHostname: Optional[str] = None
    stableUrl: Optional[CKStableUrl] = None
    shortGUID: Optional[str] = None

    # Share-surface (top-level, coarse types)
    share: Optional[CKShare] = None
    publicPermission: Optional[str] = None
    participants: Optional[List[Dict[str, JsonValue]]] = None
    requesters: Optional[List[Dict[str, JsonValue]]] = None
    blocked: Optional[List[Dict[str, JsonValue]]] = None
    denyAccessRequests: Optional[bool] = None
    owner: Optional[Dict[str, JsonValue]] = None
    currentUserParticipant: Optional[Dict[str, JsonValue]] = None
    invitedPCS: Optional[Dict[str, JsonValue]] = None
    selfAddedPCS: Optional[Dict[str, JsonValue]] = None
    shortTokenHash: Optional[str] = None

    # End-to-end encryption metadata (optional)
    chainProtectionInfo: Optional[CKChainProtectionInfo] = None
    chainParentKey: Optional[str] = None
    chainPrivateKey: Optional[str] = None

    # Observed on InlineAttachment records as numeric seconds since epoch
    expirationTime: Optional[SecsOrMillisDateTime] = None


# ---------------------------------------------------------------------------
# Error items mixed into records[] on failure
# ---------------------------------------------------------------------------
class CKErrorItem(CKModel):
    """
    Error item possibly present inside `records[]` when a per-record operation fails.
    Strict during modeling: unknown keys will raise (inherits extra="forbid").
    """

    serverErrorCode: str
    reason: Optional[str] = None
    recordName: Optional[str] = None


# ---------------------------------------------------------------------------
# Tombstone record for deleted entries
# ---------------------------------------------------------------------------
class CKTombstoneRecord(CKModel):
    """
    A 'tombstone' entry returned by CloudKit to indicate a deleted record.
    Tombstones intentionally omit `recordType` and `fields` — they only assert
    that a record with `recordName` existed but has since been deleted.
    Additional server-provided properties will be preserved via CKModel(extra="allow").
    """

    recordName: str
    deleted: Literal[True]
    zoneID: Optional[CKZoneID] = None


class CKQueryResponse(CKModel):
    """
    Top-level response from /records/query:
    - records: list of CKRecord
    - continuationMarker: optional paging token (present if more results exist)
    """

    records: List[Union[CKRecord, CKTombstoneRecord, CKErrorItem]] = Field(
        default_factory=list
    )
    continuationMarker: Optional[str] = None
    # When getCurrentSyncToken=true is passed, server also returns a top-level syncToken
    # Include it for strict validation; clients can ignore if not needed.
    syncToken: Optional[str] = None


# ---------------------------------------------------------------------------
# Request-side: /records/query payloads (refined)
# ---------------------------------------------------------------------------


# Comparators seen on the wire. Keep Union[str, Enum] to be forward-compatible.
class CKComparator(str, Enum):
    EQUALS = "EQUALS"
    IN_ = "IN"  # 'IN' is a reserved word in Python, keep name distinct
    CONTAINS_ANY = "CONTAINS_ANY"
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUALS = "LESS_THAN_OR_EQUALS"
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUALS = "GREATER_THAN_OR_EQUALS"
    BEGINS_WITH = "BEGINS_WITH"
    # Add more as you encounter them


# FieldValue typed wrappers (request side) — discriminated by 'type'
class _CKFilterValueBase(CKModel):
    type: str


class CKFVString(_CKFilterValueBase):
    type: Literal["STRING"]
    value: str


class CKFVInt64(_CKFilterValueBase):
    type: Literal["INT64"]
    value: int


class CKFVStringList(_CKFilterValueBase):
    type: Literal["STRING_LIST"]
    value: List[str]


class CKFVReference(_CKFilterValueBase):
    type: Literal["REFERENCE"]
    value: CKReference  # zoneID optional in your samples


class CKFVReferenceList(_CKFilterValueBase):
    type: Literal["REFERENCE_LIST"]
    value: List[CKReference]


CKFilterValue = Annotated[
    Union[
        CKFVString,
        CKFVInt64,
        CKFVStringList,
        CKFVReference,
        CKFVReferenceList,
    ],
    Field(discriminator="type"),
]


class CKQuerySortBy(CKModel):
    """
    Sort directive. Example:
      {"fieldName": "modTime", "ascending": false}
    """

    fieldName: str
    ascending: Optional[bool] = None


class CKQueryFilterBy(CKModel):
    """
    Filter clause. Examples:

    STRING equality:
      {"comparator": "EQUALS",
       "fieldName": "indexName",
       "fieldValue": {"value": "recents", "type": "STRING"}}

    REFERENCE equality:
      {"comparator": "EQUALS",
       "fieldName": "reference",
       "fieldValue": {"value": {"recordName": "...", "action": "VALIDATE"},
                      "type": "REFERENCE"}}
    """

    comparator: Union[CKComparator, str]
    fieldName: str
    fieldValue: CKFilterValue


class CKQueryObject(CKModel):
    """
    The 'query' object inside the request.

    recordType can be an app-defined pseudo type like "SearchIndexes" or "pinned"
    (your samples), or a real record type.
    """

    recordType: str
    filterBy: Optional[List[CKQueryFilterBy]] = None
    sortBy: Optional[List[CKQuerySortBy]] = None


class CKDesiredKey(str, Enum):
    """
    Enum for common desired keys in CloudKit queries.
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


# Request side (only what you actually send on the wire)
class CKZoneIDReq(CKModel):
    zoneName: Literal["Notes"]


class CKQueryRequest(CKModel):
    """
    Top-level /records/query request payload.
    """

    query: CKQueryObject
    zoneID: CKZoneIDReq
    desiredKeys: Optional[List[Union[CKDesiredKey, str]]] = (
        None  # can include duplicates; keep order
    )
    resultsLimit: Optional[int] = None
    # Observed as a base64-like string on the wire; keep as str for strictness
    continuationMarker: Optional[str] = None


class CKLookupDescriptor(CKModel):
    recordName: str


# ---------------------------------------------------------------------------
# Request-side: /records/lookup payloads
# ---------------------------------------------------------------------------


class CKLookupRequest(CKModel):
    records: List[CKLookupDescriptor]
    zoneID: CKZoneIDReq


class CKLookupResponse(CKModel):
    records: List[Union[CKRecord, CKTombstoneRecord, CKErrorItem]]
    # Server returns a top-level syncToken when getCurrentSyncToken=true
    syncToken: Optional[str] = None


# ---------------------------------------------------------------------------
# Response-side: /changes/zone responses (delta sync)
# ---------------------------------------------------------------------------


class CKZoneChangesZone(CKModel):
    """
    One zone entry inside the /changes/zone response.

    Based on your corpus:
      - Always has: records[], zoneID, syncToken
      - moreComing is present but sometimes null (treat as Optional[bool])
    """

    records: List[Union[CKRecord, CKTombstoneRecord, CKErrorItem]] = Field(
        default_factory=list
    )
    moreComing: Optional[bool] = None
    syncToken: str
    zoneID: CKZoneID


class CKZoneChangesResponse(CKModel):
    """
    Top-level envelope for /private/changes/zone (and /shared/changes/zone) responses.
    """

    zones: List[CKZoneChangesZone] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request-side: /changes/zone payloads
# ---------------------------------------------------------------------------


class CKZoneChangesZoneReq(CKModel):
    """
    One zone request entry for /changes/zone.

    Observed keys in corpus:
      - zoneID: includes zoneName (always "Notes" here), sometimes zoneType and ownerRecordName (for shared)
      - desiredKeys: list of field names to project (duplicates allowed, order preserved)
      - desiredRecordTypes: list of record types to include
      - syncToken: optional paging token (base64-like string)
      - reverse: optional bool
    """

    zoneID: CKZoneID  # allow ownerRecordName/zoneType when present
    desiredKeys: Optional[List[Union[CKDesiredKey, str]]] = None
    desiredRecordTypes: Optional[List[str]] = None
    # Observed as a base64-like string on the wire; keep as str for strictness
    syncToken: Optional[str] = None
    reverse: Optional[bool] = None


class CKZoneChangesRequest(CKModel):
    zones: List[CKZoneChangesZoneReq]
