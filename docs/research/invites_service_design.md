# Invites Service — Design Plan

## Status

Design draft based on two Proxyman HAR captures of the iCloud Invites web UI
(2026-05-15 owner browsing + invite/accept; 2026-05-16 RSVP status variations
and full event lifecycle including one-time link). Not yet implemented.

## Summary

Add `InvitesService` to pyicloud, exposing Apple's invite/event sharing system
(branded as "iCloud Invites" since 2024, internally still named "Events"). The
service consumes the `com.apple.icloud.events` CloudKit container via the
existing `pyicloud.common.cloudkit` client, in the same shape as the existing
Notes / Reminders / Photos services.

The defining design challenge is a recurring **atomic dual-write pattern**: a
handful of event state flags live on _both_ the private `EventDetails` record
and the public-facing `cloudkit.share` record, and must be updated together in
one CloudKit operation. Most other complexity (encryption, zone topology,
share resolution) follows standard CloudKit conventions.

## Container & topology

| Property    | Value                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------ |
| Container   | `com.apple.icloud.events`                                                                        |
| Environment | `production`                                                                                     |
| Host (web)  | `p51-ckdatabasews.icloud.com` (region-routed; we reuse the existing pyicloud endpoint discovery) |
| Auth        | Existing pyicloud iCloud session (cookies + X-Apple-\* headers)                                  |

**Zone-per-event**: each invite lives in its own custom zone. `zoneName` is the
event UUID. `zoneType: REGULAR_CUSTOM_ZONE`. The owner sees zones in the
`PRIVATE` database; guests who have accepted see them in `SHARED`. The `PUBLIC`
database is used only for the share-resolution endpoints (`resolve` / `accept`).

A `query` on `recordType: EventDetails` with `zoneWide: true` returns one record
per event without enumerating zones individually — the natural list endpoint.

## Record types

### `EventDetails` (singleton per zone)

`recordName`: `EventDetails:<event-UUID>` (UUID equals the zoneName).

| Field                        | CK type      | Encrypted | Notes                                                             |
| ---------------------------- | ------------ | --------- | ----------------------------------------------------------------- |
| `title`                      | STRING       | yes       | display name                                                      |
| `notes`                      | STRING       | yes       | host's "Message to guests" — long-form description                |
| `hostDisplayName`            | STRING       | yes       | usually empty                                                     |
| `isPublished`                | NUMBER_INT64 | yes       | 1 once owner publishes; guests can't see until set                |
| `isPrivate`                  | NUMBER_INT64 | yes       | 1 = no public link                                                |
| `isCancelled`                | NUMBER_INT64 | yes       | soft-cancellation (mirrors on share)                              |
| `blockNewRSVPs`              | NUMBER_INT64 | (no)      | seen unencrypted on a record                                      |
| `maxAttendees`               | NUMBER_INT64 | yes       | per-event cap                                                     |
| `maxAdditionalGuestsPerRSVP` | NUMBER_INT64 | yes       | plus-one allowance                                                |
| `minimumSupportedVersion`    | NUMBER_INT64 | yes       | always 1 in current captures                                      |
| `time`                       | BYTES        | yes       | base64 JSON, see codec below                                      |
| `place`                      | BYTES        | yes       | base64 JSON, see codec below                                      |
| `background`                 | BYTES        | yes       | base64 JSON, see codec below                                      |
| `style`                      | BYTES        | yes       | base64 JSON, see codec below                                      |
| `integrations`               | BYTES        | yes       | base64 JSON, see codec below                                      |
| `clientRecordChangeTag`      | STRING       | yes       | client-supplied; rarely needed                                    |
| `hint`                       | STRING       | no        | JSON sidecar (subscription token + UI metadata); see "hint field" |

### `cloudkit.share` (singleton per zone)

`recordName`: `cloudkit.zoneshare`. Standard CloudKit share record with a few
Apple-specific extras.

| Field                                           | Notes                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| `cloudkit.title`                                | STRING — duplicates `EventDetails.title` for share-preview UIs                        |
| `time`, `place`, `style`                        | BYTES — same codecs as on EventDetails; duplicated so guests see a preview pre-accept |
| `startDate`, `endDate`                          | TIMESTAMP (ms) — extracted from `time` for guest-side calendaring                     |
| `isPublished`, `isPrivate`, `isCancelled`       | NUMBER_INT64 — mirror of EventDetails flags                                           |
| `participants`                                  | array of participant objects (see below)                                              |
| `owner`, `currentUserParticipant`               | participant pointers                                                                  |
| `publicPermission`                              | `NONE` / `READ_WRITE`                                                                 |
| `denyAccessRequests`                            | bool                                                                                  |
| `allowAnyoneToResolve`, `publicAnonymousAccess` | flags governing share-link visibility                                                 |
| `stableUrl.shortGUID`                           | the public token (`008S4-kMf8_DSNuoHZIfm26nA`)                                        |
| `stableUrl.displayedHostname`                   | always `www.icloud.com` in our captures                                               |
| `oneTimeStableUrlInfo.oneTimeLinks`             | array of `{participantId: [...]}` for OTL invites                                     |
| `invitedPCS`, `selfAddedPCS`                    | Protection Class System blobs (E2E key wrapping)                                      |

**Participant shape:**

```jsonc
{
  "participantId": "c32e4f61-754d-397e-9044-e763354be277",
  "userIdentity": {
    "userRecordName": "_7c587f3e..." or "++ZmaNURj...=" or absent,
    "nameComponents": {"givenName": "...", "familyName": "..."},
    "lookupInfo": {"emailAddress": "..."}
  },
  "type": "OWNER" | "PUBLIC_USER" | "USER",
  "acceptanceStatus": "ACCEPTED" | "INVITED" | "REMOVED",
  "permission": "READ_WRITE" | "READ_ONLY",
  "publicKeyVersion": 1 | 2,
  "protectionInfo": {"bytes": "...base64...", "pcsChangeTag": "..."}
}
```

Participant `type` distinguishes:

- `OWNER` — the event creator
- `PUBLIC_USER` — a guest who joined via the shortGUID link with their iCloud account
- `USER` — a guest invited via OneTimeLink (email/phone, may or may not yet have accepted)

Guest `userRecordName` values may use the privacy-encoded `++...=` form rather
than the `_<hex>` form owners see for themselves.

### `RSVP` (one per participant)

`recordName`: `<participantId>_rsvp`.

| Field                     | CK type      | Encrypted            | Notes                                                                       |
| ------------------------- | ------------ | -------------------- | --------------------------------------------------------------------------- |
| `status`                  | NUMBER_INT64 | yes                  | enum (see RsvpStatus below)                                                 |
| `name`                    | STRING       | yes                  | display name guest entered                                                  |
| `message`                 | STRING       | yes                  | optional note to host                                                       |
| `numAdditionalGuests`     | NUMBER_INT64 | yes                  | total plus-ones (= adults + kids)                                           |
| `numAdditionalAdults`     | NUMBER_INT64 | yes                  |                                                                             |
| `numAdditionalKids`       | NUMBER_INT64 | yes                  |                                                                             |
| `monogramBackgroundColor` | NUMBER_INT64 | yes                  | UI avatar tint index                                                        |
| `image`                   | ASSETID      | (asset-level crypto) | optional avatar with `downloadURL` to `cvws.icloud-content.com`             |
| `hint`                    | STRING       | no                   | `{"updatedFields":["STATUS"],"timeZone":"...","bundleId":"com.apple.rsvp"}` |

`RsvpStatus` enum (confirmed by three RSVPs — Not Going / Maybe / Going):

```python
class RsvpStatus(IntEnum):
    NO_RESPONSE = 0   # presumed default before first response
    NOT_GOING = 1
    MAYBE = 2
    GOING = 3
```

### `OneTimeLinkGuestInfo` (one per invited email/phone)

`recordName`: `<participantId>_otl` where `participantId` matches the share
participant added in the same atomic write.

| Field          | CK type     | Encrypted | Notes                                         |
| -------------- | ----------- | --------- | --------------------------------------------- |
| `name`         | STRING      | yes       | guest's display name (may be empty initially) |
| `emails`       | STRING_LIST | yes       | list of email addresses                       |
| `phoneNumbers` | STRING_LIST | yes       | list of phone numbers                         |

### Deferred record types (v1 out-of-scope)

- **`HostMessage`** — queried in every capture, but **never written**. The
  "Message to guests" the user enters in the web UI flows through
  `EventDetails.notes`. `HostMessage` is likely an iOS-only or legacy feature.
- **`EventLimits`** — same pattern: queried, never written. Probably server-set
  for quota tracking.

Both record types should be queryable in v1 (cheap to expose, returns empty
record sets in our captures) but the domain model doesn't need first-class
support.

## Encoded field codecs

Five `EventDetails` fields and three `cloudkit.share` fields are typed as
`BYTES` (or `ENCRYPTED_BYTES`) but their value is **base64-encoded JSON**. They
appear "encrypted" only at the CloudKit protocol layer — authenticated sessions
receive them with the inner JSON decryptable client-side. Schemas (all keys
optional unless noted):

**`time`**

```python
{
    "startSince1970": int,    # ms since epoch (required)
    "endSince1970": int,      # ms since epoch
    "isAllDay": bool,
    "isOpenEnded": bool,
}
```

**`place`**

```python
{
    "latitude": float, "longitude": float,
    "title": str, "subtitle": str, "city": str,
    "timeZoneIdentifier": str,    # IANA tz, e.g. "Europe/London"
    "url": str,                   # https://maps.apple.com/...
}
```

A "timezone-only" form (just `city` + `timeZoneIdentifier`) is valid for events
without a physical location.

**`background`**

```python
{
    "kind": str,                  # "image" observed
    "visibility": int,            # 1 observed
    "image": {"cropRect": [x, y, w, h]},  # if kind == "image"
}
```

**`style`**

```python
{"titleFont": int}
```

**`integrations`**

```python
{
    "version": "1",
    "data": [{"type": str}, ...],
}
```

Observed widget types: `com.apple.widget.weather`, `.location`, `.photos`,
`.music`, `.link.placeholder`.

## Lifecycle states & the dual-write pattern

Three boolean flags govern visibility/state. Each lives on **both**
`EventDetails` (encrypted, owner-private) and `cloudkit.share` (plain,
guest-visible). They must be updated **atomically in one `records/modify`
request** containing both operations:

| Flag          | Effect                                                              |
| ------------- | ------------------------------------------------------------------- |
| `isPublished` | 1 = event is publishable; guests resolving the shortGUID can see it |
| `isPrivate`   | 1 = no public shortGUID resolution                                  |
| `isCancelled` | 1 = event soft-cancelled (still readable, marked as such)           |

Wire shape (example: `event.cancel()`):

```jsonc
{
  "atomic": true,
  "zoneID": {"ownerRecordName": "_<owner>", "zoneName": "<uuid>",
             "zoneType": "REGULAR_CUSTOM_ZONE"},
  "operations": [
    {"operationType": "update", "record": {
      "recordType": "EventDetails", "recordName": "EventDetails:<uuid>",
      "recordChangeTag": "<latest>",
      "fields": {"isCancelled": {"value": 1, "type": "NUMBER_INT64"}, ...}
    }},
    {"operationType": "update", "record": {
      "recordType": "cloudkit.share", "recordName": "cloudkit.zoneshare",
      "recordChangeTag": "<latest>",
      "fields": {"isCancelled": {"value": 1, "type": "NUMBER_INT64"}}
    }}
  ]
}
```

`blockNewRSVPs` lives only on `EventDetails` and is a single-write flag (no
mirror on share).

## Share URL format

```
https://www.icloud.com/invites/<shortGUID>
```

The `shortGUID` is the value at `cloudkit.share.stableUrl.shortGUID` (also
returned at top level of `records/resolve` and `records/accept` responses).
The `routingKey` ("008" in our captures) is metadata, not part of the
displayed URL.

## Operations reference (wire-level)

All paths are relative to
`https://p51-ckdatabasews.icloud.com/database/1/com.apple.icloud.events/production`.
The scope segment (`private`/`shared`/`public`) is per request.

| Operation                                                     | Method | Path                                         | Database    | Triggered by                               |
| ------------------------------------------------------------- | ------ | -------------------------------------------- | ----------- | ------------------------------------------ |
| List zones                                                    | GET    | `/private/zones/list`                        | private     | owner browsing                             |
| List shared zones                                             | GET    | `/shared/zones/list`                         | shared      | guest browsing                             |
| Create event zone                                             | POST   | `/private/zones/modify`                      | private     | `create_event` step 1                      |
| Query EventDetails (all events)                               | POST   | `/private/records/query` (`zoneWide: true`)  | private     | `events()`                                 |
| Lookup EventDetails by zone                                   | POST   | `/private/records/lookup`                    | private     | `event(id)` refresh                        |
| Modify EventDetails                                           | POST   | `/private/records/modify`                    | private     | `update`, `publish`, `cancel` (dual-write) |
| Modify cloudkit.share                                         | POST   | `/private/records/modify`                    | private     | share/participant changes                  |
| Query RSVP / HostMessage / EventLimits / OneTimeLinkGuestInfo | POST   | `/private/records/query`                     | private     | event detail load                          |
| Modify RSVP                                                   | POST   | `/private/records/modify` (or `/shared/...`) | guest scope | `rsvp()`                                   |
| Modify OneTimeLinkGuestInfo + share                           | POST   | `/private/records/modify`                    | private     | `invite_via_link`                          |
| Resolve share                                                 | POST   | `/public/records/resolve`                    | public      | guest preview                              |
| Accept share                                                  | POST   | `/public/records/accept`                     | public      | guest join                                 |
| Changes/zone                                                  | POST   | `/private/changes/zone`                      | private     | sync deltas                                |

Resolve/accept request shape (identical):

```jsonc
{ "shortGUIDs": [{ "value": "<shortGUID>" }] }
```

## Service shape (mirrors codebase conventions)

### `InvitesService` constructor

Same pattern as [`NotesService`](../../pyicloud/services/notes/service.py) and
[`RemindersService`](../../pyicloud/services/reminders/service.py): extend
[`BaseService`](../../pyicloud/services/base.py), declare container/env/scope
class constants, build endpoint URL, set baseline params, instantiate the
wire client.

```python
class InvitesService(BaseService):
    _CONTAINER = "com.apple.icloud.events"
    _ENV = "production"

    def __init__(
        self,
        service_root: str,
        session,
        params: Dict[str, str],
        *,
        cloudkit_validation_extra: CloudKitExtraMode | None = None,
    ):
        super().__init__(service_root=service_root, session=session, params=params)
        base = f"{self.service_root}/database/1/{self._CONTAINER}/{self._ENV}"
        base_params = {
            "remapEnums": True,
            "getCurrentSyncToken": True,
            **(params or {}),
        }
        # Three sub-clients, one per CK scope — see "Three-scope client" below.
        self._raw = CloudKitInvitesClient(
            base, session, base_params,
            validation_extra=cloudkit_validation_extra,
        )
```

### Multi-scope architecture (Invites-specific)

Existing services use a single CK scope (always `private`), so their wire
client has the scope baked into the endpoint URL. Invites is the first
service that has to talk to multiple scopes — but the three scopes are
**not symmetric**:

| Scope     | Shape                                | Operations                                                                                     |
| --------- | ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `private` | records-in-zones (owner perspective) | query, lookup, modify, changes, zones/modify                                                   |
| `shared`  | records-in-zones (guest perspective) | **same shape as private**, different DB view                                                   |
| `public`  | shortGUID-keyed share resolution     | `records/resolve`, `records/accept` — **structurally different**, takes shortGUIDs not zoneIDs |

So it's really _one_ records-in-zones API (private + shared) plus _one_
share-resolution helper (public). Treating all three symmetrically obscures
that and creates downstream complications.

#### Approach (chosen): scope-per-call in common client + Invites-local helpers for resolve/accept

1. Extend
   [`CloudKitContainerClient`](../../pyicloud/common/cloudkit/client.py)
   methods with an optional `scope: Literal["private","shared","public"] = "private"`
   parameter. The default preserves all existing-service behavior bit-for-bit
   — Notes / Reminders / Photos need zero changes.
2. Promote `zones/modify` to the common client at the same time (it's
   clearly reusable).
3. `CloudKitInvitesClient` holds **one** `CloudKitContainerClient` instance
   and adds `resolve(short_guids)` / `accept(short_guids)` as service-local
   methods. These hit `/public/records/{resolve,accept}` directly, bypassing
   the records/zones abstraction (which doesn't fit their shape).

```python
class CloudKitInvitesClient:
    def __init__(self, base, session, base_params, *, validation_extra=None):
        # base = .../database/1/com.apple.icloud.events/production
        self._client = CloudKitContainerClient(
            base, session, base_params,
            validation_extra=validation_extra,
            bool_param_style="lower",
            redact_urls=True,
            debug_hook=self._dump_http_debug,
        )

    def query(self, *, scope, query, zone_id, ...):
        return self._client.query(scope=scope, query=query, zone_id=zone_id, ...)

    def modify(self, *, scope, operations, zone_id, atomic=True):
        return self._client.modify(scope=scope, operations=operations, ...)

    def resolve(self, short_guids: Sequence[str]) -> ResolveResponse:
        ...  # POST <base>/public/records/resolve

    def accept(self, short_guids: Sequence[str]) -> AcceptResponse:
        ...  # POST <base>/public/records/accept
```

#### Alternatives considered

**A. Three symmetric sub-clients.** Construct three `CloudKitContainerClient`
instances (one per scope) inside `CloudKitInvitesClient`. Rejected because:

- triplicates configuration state (validation, timeouts, debug hook,
  rate-limit policy) — config-drift bug magnet
- forces resolve/accept into the records/zones abstraction they don't fit
- doesn't generalize: any future multi-scope service would re-invent it

**B. Scope-per-call** (chosen, above).

**C. Hand-write HTTP for non-private calls.** Bypass the typed client for
shared/public. Rejected because it re-implements validation, retry, debug
hook, and URL redaction for half the operations and loses Pydantic-validated
responses.

#### Downstream complications (still apply to chosen approach)

Multi-scope visibility is a domain requirement regardless of client
architecture. These must still be handled:

1. **`Event` carries a `scope` field** (`PRIVATE` or `SHARED`) set at parse
   time from the originating query. Mutation operations dispatch via it.
2. **`events()` queries both scopes and merges.** Sequential in v1; can be
   parallelized later. Deduplicate by `(scope, event_id)` defensively.
3. **Sync cursors are per-scope** — represent as a `(private_token,
shared_token)` tuple, not a single string. Don't collapse them into one
   key.
4. **API takes Event objects, not raw IDs**, for any operation where scope
   matters (mirrors Reminders' `update(reminder)` / `delete(reminder)`
   pattern). String-ID overloads can be added later via a single-event
   lookup helper.
5. **RSVP scope dispatch** — guest mutating own RSVP uses `shared`; owner
   mutating own RSVP uses `private`. The service decides by checking the
   current user's `userRecordName` against the event's owner.

#### Migration path

If "scope-per-call" turns out to be wrong, falling back to three sub-clients
is mechanical: construct three clients, always pass the matching `scope=`.
The reverse (collapsing three sub-clients into one) is harder — config-drift
bugs and call-site rewrites — so the asymmetry favors trying scope-per-call
first.

#### Open questions to resolve in implementation

- **Public endpoints with auth**: confirm `public/records/resolve` requires
  the same X-Apple-\* / cookie auth as private. Almost certainly yes
  ("public DB" ≠ "public access"), but verify before deploying.
- **Rate limits**: per-account or per-scope? If per-scope, the rate-limit
  handler may need scope context when computing back-off.
- **Same event in two scopes**: can a user be both owner and guest of the
  same event? Probably impossible at the protocol level, but the merge in
  `events()` should deduplicate defensively.

### Error hierarchy

Same shape as Notes / Reminders:

```python
class InvitesError(Exception): pass
class InvitesAuthError(InvitesError): pass         # 401/403 (cookies/PCS)
class InvitesRateLimited(InvitesError):            # 429
    retry_after: Optional[float]
class InvitesApiError(InvitesError):               # catch-all
    payload: Optional[object]
```

The wire client catches `CloudKitApiError` / `CloudKitAuthError` /
`CloudKitRateLimited` and re-raises as the Invites variant via a private
`_raise_invites_error` translator. See
[`CloudKitNotesClient._raise_notes_error`](../../pyicloud/services/notes/client.py)
for the precise pattern.

### Validation and debug env vars

Following the per-service env-var convention seen in
[`notes/models/_ck_base.py`](../../pyicloud/services/notes/models/_ck_base.py):

| Variable                                       | Effect                                                                                                |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `PYICLOUD_INVITES_EXTRA=allow\|forbid\|ignore` | Override Pydantic `extra=` strictness on raw CK models. Falls back to `PYICLOUD_EXTRA`, then `forbid` |
| `PYICLOUD_INVITES_DEBUG=1`                     | Dump every HTTP request/response as JSON under `workspace/invites_debug/`                             |
| `PYICLOUD_DEBUG_MAX_BYTES`                     | Truncate large response bodies in the debug dump                                                      |

The debug dump is invaluable when reverse-engineering further Apple-side
behavior; Notes uses the same mechanism.

### `PyiCloudService` integration

Add to [pyicloud/base.py](../../pyicloud/base.py) following the pattern at
lines 1395-1413:

```python
# in __init__
self._invites: Optional[InvitesService] = None

# new property
@property
def invites(self) -> InvitesService:
    """Gets the 'Invites' service."""
    if not self._invites:
        try:
            service_root = self.get_webservice_url("ckdatabasews")
            self._invites = InvitesService(
                service_root=service_root,
                session=self.session,
                params=self.params,
                cloudkit_validation_extra=self._cloudkit_validation_extra,
            )
        except (PyiCloudAPIResponseException, PyiCloudServiceNotActivatedException) as error:
            raise PyiCloudServiceUnavailable("Invites service not available") from error
    return self._invites
```

The `ckdatabasews` webservice URL is shared with Notes / Reminders / Photos —
no new endpoint discovery required.

## Domain model

Domain types use [`pyicloud.common.models.FrozenServiceModel`][fsm] (Pydantic
`BaseModel` with `frozen=True`, `extra="forbid"`), matching the existing
Notes and Reminders services. This gives us validation at construction,
field aliasing from camelCase wire data, discriminated unions, and early
detection of Apple-side schema drift.

[fsm]: ../../pyicloud/common/models.py

```python
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Optional

from pydantic import Field

from pyicloud.common.models import FrozenServiceModel

class RsvpStatus(IntEnum):
    NO_RESPONSE = 0
    NOT_GOING = 1
    MAYBE = 2
    GOING = 3

class ParticipantType(StrEnum):
    OWNER = "OWNER"
    PUBLIC_USER = "PUBLIC_USER"      # joined via shortGUID
    USER = "USER"                    # invited via OneTimeLink

class AcceptanceStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    INVITED = "INVITED"
    REMOVED = "REMOVED"

class EventScope(StrEnum):
    """Which CK database scope the event lives in for the current user."""
    PRIVATE = "private"              # user is the owner
    SHARED = "shared"                # user is an accepted guest

class EventTime(FrozenServiceModel):
    start: datetime                  # required
    end: Optional[datetime] = None
    is_all_day: bool = False
    is_open_ended: bool = False

class EventPlace(FrozenServiceModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    city: Optional[str] = None
    time_zone_identifier: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    url: Optional[str] = None

class Participant(FrozenServiceModel):
    participant_id: str
    user_record_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    email: Optional[str] = None
    type: ParticipantType
    acceptance_status: AcceptanceStatus
    permission: str

class OneTimeLinkGuest(FrozenServiceModel):
    record_name: str                 # "<participantId>_otl"
    participant_id: str
    name: str = ""
    emails: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()

class EventShare(FrozenServiceModel):
    short_guid: str
    public_permission: str
    participants: tuple[Participant, ...] = ()
    one_time_links: tuple[OneTimeLinkGuest, ...] = ()

    @property
    def url(self) -> str:
        return f"https://www.icloud.com/invites/{self.short_guid}"

class Rsvp(FrozenServiceModel):
    record_name: str
    participant_id: str
    name: str
    status: RsvpStatus
    message: Optional[str] = None
    num_additional_adults: int = 0
    num_additional_kids: int = 0
    image_download_url: Optional[str] = None
    record_change_tag: str

class Event(FrozenServiceModel):
    event_id: str                    # zoneName / UUID
    scope: EventScope                # which CK DB this event lives in for current user
    record_change_tag: str           # for optimistic concurrency
    title: str
    notes: str = ""
    is_published: bool = False
    is_private: bool = False
    is_cancelled: bool = False
    block_new_rsvps: bool = False
    max_attendees: Optional[int] = None
    max_additional_guests_per_rsvp: int = 0
    time: EventTime
    place: Optional[EventPlace] = None
    background: dict = Field(default_factory=dict)   # opaque pass-through (v1)
    style: dict = Field(default_factory=dict)        # opaque pass-through (v1)
    integrations: tuple[str, ...] = ()               # widget type strings
    created_timestamp: datetime
    modified_timestamp: datetime
    share: EventShare
    rsvps: tuple[Rsvp, ...] = ()
```

Wire-side parsing models in `models/` use the same Pydantic base classes but
mirror the raw CK record/field shape (`{"value": ..., "type": "STRING", ...}`),
with `Field(alias="camelCaseName")` where Apple uses camelCase. Domain models
above are built from wire models via a thin mapping layer in `client.py`,
keeping parse concerns separate from the public-facing types.

## `InvitesService` API

Service-centric, mirroring the Notes / Reminders convention. Most operations
live on the service rather than on DTO methods, keeping DTOs simple and
frozen. We can add fluent variants (e.g. `event.publish()`) later if usage
patterns call for them, following the `attachment.save_to(service=...)`
pattern from Notes if/when added.

```python
service.invites                                          # InvitesService

# --- Read (owner + accepted guest) ---
service.invites.events() -> Iterable[Event]
service.invites.event(event_id_or_short_guid: str) -> Event
service.invites.rsvps(event_id: str) -> Iterable[Rsvp]

# --- Guest flow ---
service.invites.resolve(short_guid: str) -> ResolvedShare   # preview without joining
service.invites.accept(short_guid: str) -> Event            # join, return joined event

# --- RSVP (works for any participant) ---
service.invites.rsvp(
    event_id: str,
    status: RsvpStatus,
    *,
    name: Optional[str] = None,
    message: Optional[str] = None,
    plus_one_adults: int = 0,
    plus_one_kids: int = 0,
) -> Rsvp

# --- Owner writes ---
service.invites.create_event(
    title: str,
    time: EventTime,
    *,
    place: Optional[EventPlace] = None,
    notes: str = "",
    max_attendees: Optional[int] = None,
    max_additional_guests_per_rsvp: int = 0,
    is_private: bool = False,
) -> Event

service.invites.update(event_id: str, **fields) -> Event    # batched partial update
service.invites.publish(event_id: str) -> Event             # atomic dual-write isPublished=1
service.invites.cancel(event_id: str) -> Event              # atomic dual-write isCancelled=1
service.invites.set_block_new_rsvps(event_id: str, blocked: bool) -> Event
service.invites.invite_via_link(
    event_id: str,
    *,
    emails: Sequence[str] = (),
    phone_numbers: Sequence[str] = (),
    name: str = "",
) -> OneTimeLinkGuest                                       # atomic OTL + share update
```

## Module layout

Mirrors Notes' established layout (see [pyicloud/services/notes/](../../pyicloud/services/notes/)).
The leading underscore on module names marks internal helpers (per
[reminders/](../../pyicloud/services/reminders/) convention).

```
pyicloud/services/invites/
  __init__.py          # public re-exports (InvitesService, Event, Rsvp, EventShare, ...)
  _constants.py        # container ID, env, scope strings; record-type names; share record name
  client.py            # CloudKit wire layer; service-specific error hierarchy; resolve/accept helpers
  service.py           # InvitesService(BaseService) — public entry point
  codecs.py            # base64-JSON codecs for time/place/background/style/integrations
  models/
    __init__.py        # re-exports of dto types
    constants.py       # CK record-type enums, desired-key enums (mirrors notes/models/constants.py)
    dto.py             # user-facing FrozenServiceModel DTOs (Event, Rsvp, Participant, ...)
    _ck_base.py        # optional: env-driven `extra=` mode for raw CK wire models
```

`domain.py` is omitted — all our types are user-facing, with no encoded
intermediates analogous to Notes' `AttachmentId` / `NoteBody`. If codec
intermediates emerge during implementation, add `domain.py` then.

`_reads.py` / `_writes.py` (the Reminders split) is **not** introduced in v1.
Notes runs a 973-line monolithic `service.py` without that split; we'll
follow the same pattern until method count or LOC make a refactor worthwhile.

### Where the wire-model `CKRecord`-style classes live

Apple's CloudKit JSON shape (`{"value": ..., "type": "STRING", ...}`) is
already modeled centrally in [pyicloud/common/cloudkit/models.py](../../pyicloud/common/cloudkit/models.py).
Invites should reuse those base shapes and add only the Invites-specific
record-type names and field-key enums in `models/constants.py` —
matching what [notes/models/constants.py](../../pyicloud/services/notes/models/constants.py)
does for `NotesRecordType` / `NotesDesiredKey`.

## Implementation phases

### Phase 1 — Read-only MVP

- Module scaffold per the layout above (`__init__.py`, `_constants.py`,
  `client.py`, `service.py`, `codecs.py`, `models/` with `dto.py` +
  `constants.py`).
- Extend [`CloudKitContainerClient`](../../pyicloud/common/cloudkit/client.py)
  with `scope=` parameter on query/lookup/modify/changes (default
  `"private"` so existing services need no changes). Add `zones_modify(...)`.
- `CloudKitInvitesClient` wrapping one `CloudKitContainerClient`, plus
  service-local `resolve(short_guids)` / `accept(short_guids)` methods, plus
  the service-specific error hierarchy.
- `InvitesService(BaseService)` constructor + `events()` (merges private +
  shared) + `event(...)` + `rsvps(event)`.
- Codec functions for the five base64-JSON fields (`time`, `place`,
  `background`, `style`, `integrations`).
- DTOs (`Event`, `EventTime`, `EventPlace`, `EventShare`, `Participant`,
  `OneTimeLinkGuest`, `Rsvp`) as `FrozenServiceModel` subclasses.
- `PyiCloudService.invites` lazy property wired in [pyicloud/base.py](../../pyicloud/base.py).
- Synthetic test fixtures under `tests/fixtures/invites/` (sanitized from
  the HAR captures) + a `tests/services/test_invites.py` with mocked-client
  tests covering codecs, query parsing, and the lazy-property wiring.

**Exit criteria**: a live integration smoke (`pyicloud.invites.events()`)
lists existing events with their share URLs, participants, and RSVPs against
a real iCloud account.

### Phase 2 — RSVP write

- `event.rsvp(status, ...)` via `records/modify` (update existing RSVP record).
- Handle `recordChangeTag` correctly.

**Exit criteria**: can change one's own RSVP from a script.

### Phase 3 — Guest flow

- `resolve(short_guid)` and `accept(short_guid)` against the `public/` scope.
- Returns enough to populate an `Event` after accept.

**Exit criteria**: a second iCloud account can use the API to accept a share
link and submit an RSVP.

### Phase 4 — Owner writes

- `create_event` (`zones/modify` + `records/modify` EventDetails + create
  `cloudkit.share`).
- `event.update(**fields)`.
- `event.publish()` / `event.cancel()` (atomic dual-write).
- `event.set_block_new_rsvps()`.

**Exit criteria**: full event lifecycle from Python.

### Phase 5 — OneTimeLink

- `invite_via_link(emails=, phone_numbers=)` (atomic OTL create + share
  participant update).
- Mutability of the OTL `name` field.

## Open questions

1. **`hint` field on writes**: every web-UI update includes a `hint` blob with
   a JWT `subscriptionAccessToken`. Is the server strict about its presence?
   Initial plan is to omit it; if the server rejects writes, fall back to
   fetching the token from a prior read or accepting that pyicloud writes don't
   trigger push notifications (likely fine — pyicloud is server-side).

2. **`recordChangeTag` strategy**: fetch-before-write (simpler, 2 round trips)
   vs cache-and-retry-on-conflict (efficient, more code). Recommend fetch-
   before-write for v1, optimize later if anyone complains.

3. **Anonymous accept**: in the capture, the guest who accepted a one-time link
   was still signed into iCloud. We never observed an unauthenticated guest
   completing an accept. Likely iCloud Invites requires login; if so,
   pyicloud's role is purely owner + authenticated-guest, and the OTL is just a
   distribution-channel convenience.

4. **`status: 0` mapping**: `NO_RESPONSE = 0` is inferred (we never observed
   that wire value). A fresh-accepted RSVP without explicit user input may have
   value 0; needs verification in implementation tests.

5. **Asset upload for RSVP avatars**: out of scope for v1 reads. Writing avatar
   assets later means integrating with `assets/upload` (we have entry samples
   but haven't unpacked the protocol).

## Out of scope (v1)

- `HostMessage` writes (no observed usage).
- `EventLimits` writes (no observed usage).
- Push notification subscriptions (`subscriptions/modify`).
- Asset uploads (RSVP avatars, event background images).
- PCS / E2E key wrapping (CloudKit handles the standard share flow; we
  pass-through PCS blobs but don't construct them).
- Truly anonymous (no iCloud auth) guest accept.
- CLI integration — defer until the service is stable.

## Testing strategy

Mirror the Notes / Reminders pattern:

- **Synthetic CloudKit fixtures** in `tests/fixtures/invites/`, derived from the
  two HAR captures and sanitized (no real `userRecordName` or tokens).
- **Unit tests** for codecs (round-trip every JSON shape we've observed).
- **Mocked-client tests** for `InvitesService` against the fixtures.
- **Manual smoke** for live writes (`create_event`, `publish`, `cancel`,
  `invite_via_link`) — gated behind an environment variable like
  `PYICLOUD_INVITES_LIVE=1`, since live tests mutate the user's iCloud account.

## References

- HAR captures (local, not committed):
  - `workspace/p51-ckdatabasews.icloud.com_05-15-2026-22-05-56.har` — owner browse
  - `workspace/ckdatabasews.icloud.com_05-15-2026-22-21-27.har` — invite + accept
  - `workspace/cvws.icloud-content.com_05-16-2026-02-06-00.har` — RSVP status enum
  - `workspace/p51-ckdatabasews.icloud.com_05-16-2026-02-15-34.har` — full lifecycle
- Existing CloudKit service patterns:
  - `pyicloud/services/notes/` — closest structural analog
  - `pyicloud/services/reminders/`
  - `pyicloud/common/cloudkit/` — shared container client
