# Invites Service — Design and Implementation Status

## Status

Reviewed against pyicloud **2026.10.1** (`2d3d137`) on 2026-10-16. This is a
maintainer protocol reference, not a promise that every captured browser operation
is available through the library. See the [user guide](../services/invites.md)
for the supported API.

| Capability                                                         | Status                                                    | Reference                                               |
| ------------------------------------------------------------------ | --------------------------------------------------------- | ------------------------------------------------------- |
| Event listing, lookup, RSVP reads, share resolution and acceptance | Implemented                                               | #246, #339, #352                                        |
| Create/update the current user's RSVP                              | Implemented                                               | #247                                                    |
| Read-only CLI (`list`, `show`, `rsvps`, `resolve`)                 | Implemented                                               | #346                                                    |
| Download an RSVP image through `rsvp_image()`                      | Implemented                                               | #349                                                    |
| Cancel/reinstate events with cached entitlement tokens             | Implemented                                               | #355                                                    |
| Create an event                                                    | Not implemented                                           | [#431](https://github.com/timlaing/pyicloud/issues/431) |
| Publish an event                                                   | Not implemented; encrypted-field writes remain unresolved | [#432](https://github.com/timlaing/pyicloud/issues/432) |
| Invite guests through one-time links                               | Not implemented                                           | [#433](https://github.com/timlaing/pyicloud/issues/433) |

The original umbrella [#341](https://github.com/timlaing/pyicloud/issues/341) is
closed. This status reconciliation is tracked in
[#434](https://github.com/timlaing/pyicloud/issues/434). The original phase schedule
is superseded by the shipped capabilities and focused follow-ups above.

## Evidence and limitations

The initial design was derived from four local Proxyman HAR captures of the
iCloud Invites website on 2026-05-15 and 2026-05-16. Those captures describe browser
behavior; they do not establish that pyicloud can construct every encrypted field,
share participant, or PCS blob needed to reproduce it.

Later investigations in #341 and #355 established the entitlement gateway and
verified cancellation/reinstatement. In particular, #355 reports that plain
encodings of `isPublished` were rejected even after token acquisition worked.
Earlier speculation that owner writes were unsupported, or that obtaining a token
alone unblocked every write, must not be used as the current conclusion.

The existing May captures were also re-inspected for this update: both contain
successful browser publication requests and responses (see Publishing limitation).
No new live captures or writes were performed for this documentation update.
Sections labeled historical or proposed below require fresh verification before
implementation.

## Container and scope topology

| Property       | Value                                                                                                                   |
| -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Container      | `com.apple.icloud.events`                                                                                               |
| Environment    | `production`                                                                                                            |
| CloudKit host  | Discovered from the authenticated `ckdatabasews` webservice; the historical captures used `p51-ckdatabasews.icloud.com` |
| Authentication | Existing pyicloud session and Apple headers/cookies                                                                     |

Each event lives in its own custom zone: `zoneName` is the event UUID and
`zoneType` is `REGULAR_CUSTOM_ZONE`. Owners see events in `private`; accepted
guests see them in `shared`. The `public` scope serves `records/resolve` and
`records/accept`, which take short GUIDs rather than zone IDs. Using that scope
does not imply anonymous access.

### Implemented reads

- **Private listing:** `events()` queries `EventDetails` with `zoneWide: true`.
- **Shared listing:** CloudKit rejects zone-wide shared-database queries. The
  service lists shared zones, reads every page of each zone's changes feed, and
  selects `EventDetails` records (#339). Zone owner metadata is preserved for
  shared lookups and changes requests.
- **Combined listing:** results are deduplicated by `(scope, event_id)` and contain
  event details only. Call `event(event_id)` to hydrate the share and guest data.
  Unreadable scopes/zones are handled on a best-effort basis, so a returned list
  need not prove that every remote event was readable.
- **Single event:** `event(event_id)` tries private then shared lookup for
  `EventDetails:<event_id>` and `cloudkit.zoneshare`.
- **RSVPs and one-time-link guests:** read through the zone changes feed (#352).
  Hosted event zones reject the former per-zone `records/query` approach with
  `syncToken operations supported only in SyncZone`. Live records and tombstones
  are reconciled by record name. Full-event guest hydration preserves partial
  results on a feed failure; the explicit `rsvps(event)` call propagates failures.

## Record reference

These are capture-derived field descriptions, with later findings called out.
Read-side decrypted data is not necessarily a valid write-side representation.
Canonical field names live in
[`models/constants.py`](../../pyicloud/services/invites/models/constants.py).

### EventDetails

One record per event zone, named `EventDetails:<event_id>`.

| Fields                                                                  | Observed content and constraints                                              |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `title`, `notes`, `hostDisplayName`                                     | Text; `title` is PCS-encrypted in the owner-write investigation               |
| `isPublished`, `isPrivate`                                              | Integer flags; PCS-encrypted, unlike `isCancelled`                            |
| `isCancelled`                                                           | Unencrypted integer flag; the shipped writer sends `INT64`, `0` or `1`        |
| `blockNewRSVPs`                                                         | Observed unencrypted; no public setter is implemented                         |
| `maxAttendees`, `maxAdditionalGuestsPerRSVP`, `minimumSupportedVersion` | Integer metadata from historical captures; not implemented as owner writes    |
| `time`, `place`, `background`, `style`, `integrations`                  | Byte fields whose decrypted contents are JSON; see codecs below               |
| `clientRecordChangeTag`                                                 | Captured client metadata; not required by the shipped cancellation writer     |
| `hint`                                                                  | Unencrypted STRING containing JSON; owner writes require an entitlement token |

The original capture tables called integer fields `NUMBER_INT64`. The current
writer uses `INT64` for cancellation. These names must not be interchanged as an
assumption about other fields, particularly encrypted publishing fields.

### cloudkit.share

The record name is `cloudkit.zoneshare`. Historical captures contain:

- `cloudkit.title`, `time`, `place`, `style`, `startDate`, and `endDate` for previews.
- Copies of lifecycle flags such as `isPublished`, `isPrivate`, and `isCancelled`.
- `participants`, `owner`, and `currentUserParticipant`, with participant identity,
  permission, acceptance status, and protection metadata.
- `publicPermission`, `denyAccessRequests`, `allowAnyoneToResolve`, and
  `publicAnonymousAccess` for share access.
- `stableUrl.shortGUID` and `stableUrl.displayedHostname` for the public URL.
- `oneTimeStableUrlInfo.oneTimeLinks`, `invitedPCS`, and `selfAddedPCS` for guest
  links and protection metadata.

Participant types include `OWNER`, `PUBLIC_USER` (public-link guest), and `USER`
(one-time-link guest). User record names can use different privacy encodings;
callers must not assume every identity uses an underscore-prefixed record name.

The public URL is `https://www.icloud.com/invites/<shortGUID>`. Routing keys are
separate metadata and are not an extra URL path component.

### RSVP and OneTimeLinkGuestInfo

An RSVP record is named `<participant_id>_rsvp`. It carries `status`, `name`,
`message`, plus-one counts, optional monogram metadata, and an optional `image`
asset. The service locates the current user through the share's participant ID.

`RsvpStatus` maps `NOT_GOING=1`, `MAYBE=2`, and `GOING=3`. `NO_RESPONSE=0` remains
the presumed default; the original captures did not verify that value. The
shipped RSVP writer creates a first response or updates an existing response and
zeros plus-one counts for `NOT_GOING`.

`rsvp_image(rsvp)` returns bytes, or `None` when there is no image. It validates
that the asset URL is absolute HTTPS and delegates the download to the raw client.

A one-time-link guest-info record is named `<participant_id>_otl`, with `name`,
`emails`, and `phoneNumbers`. Those records and share participants are populated
on reads. Constructing new invitations is separate work (#433); readable
protection metadata is not proof that the library can generate it.

`HostMessage` and `EventLimits` appeared in historical browser queries without
observed writes. They have no dedicated public service methods or domain models;
the raw query interface remains available for investigation.

## Encoded field codecs

[`codecs.py`](../../pyicloud/services/invites/codecs.py) handles JSON encoded in
byte fields returned after CloudKit decryption. The historical inner shapes are:

| Field          | JSON keys                                                                         |
| -------------- | --------------------------------------------------------------------------------- |
| `time`         | `startSince1970`, `endSince1970` (milliseconds), `isAllDay`, `isOpenEnded`        |
| `place`        | `latitude`, `longitude`, `title`, `subtitle`, `city`, `timeZoneIdentifier`, `url` |
| `background`   | `kind`, `visibility`, optional `image.cropRect`                                   |
| `style`        | `titleFont`                                                                       |
| `integrations` | `version`, `data` entries with widget `type`                                      |

A place can contain only a city and time zone. Observed integration types include
`com.apple.widget.weather`, `.location`, `.photos`, `.music`, and `.link.placeholder`.
These observations are not a complete schema for future owner writes. Encoding
inner JSON alone does not implement PCS encryption.

## Implemented owner write: cancellation

`cancel(event, cancelled=True)` and `cancel(event, cancelled=False)` cancel and
reinstate an event. #355 reports live verification of both directions against a
disposable event with no guests, including the guest-visible share state.

The caller supplies an `Event` with a current `record_change_tag`, from `event()`
or a previous write. Missing tags are rejected locally; Apple rejects stale tags.
The returned event carries the updated tag, allowing another write to be chained.

### Entitlement gateway and hint

The gateway is outside the CloudKit container and is not advertised in the login
`webservices` map. Its base URL is explicitly defined in
[`entitlement.py`](../../pyicloud/services/invites/entitlement.py):

```text
GET https://gatewayws.icloud.com/acsegateway/v4/accounts/{dsid}/subscriptions/features
x-apple-softwarecapabilityflags: apps.rsvp.create-event
```

The client passes its base query parameters, including the account DSID. The
response is a list of grants matched by `featureKey`, containing `canUse`,
`accessToken`, and `cacheTill`. The service caches the grant until 60 seconds
before expiry; a missing expiry makes it unsuitable for reuse. If the capability
is unavailable or no token is supplied, it raises `InvitesEntitlementError`
before sending the event modify.

Cancellation includes an unencrypted STRING `hint` containing:

```json
{ "subscriptionAccessToken": "<entitlement-token>" }
```

Without the token, the investigated EventDetails writes returned opaque
`502 INTERNAL_ERROR` responses. Historical browser hints also contain `bundleId`,
`updatedFields`, `shortGUIDLink`, or `timeZone`. The shipped cancellation path
uses the token-only form; requirements for other operations must be verified
instead of guessing values such as `IS_CANCELLED` or `IS_PUBLISHED`.

### Event and share consistency

The writer updates EventDetails and, when its current change tag can be read,
the share's `isCancelled` copy in one `atomic=True` modify. Both use unencrypted
`INT64` values. The share does not automatically follow an event-only update.

There is an intentional fallback: when the share cannot be read or has no usable
tag, the implementation sends only the EventDetails update. Atomic dual-write
consistency therefore applies when both operations are available, not as an
unconditional guarantee. A returned write result is not a fully rehydrated event.

### Publishing limitation

The later #355 investigation reports `BAD_REQUEST` with
`expected type ENCRYPTED_NUMBER_INT64` for attempted plain `isPublished` writes.
Writing only the share's plain copy succeeds without updating EventDetails.
The entitlement token solves authorization for supported writes, but the library's
publication path remains unresolved. `isPrivate` and `title` are also identified
as PCS-protected in that investigation.

There is an important qualification in the original browser evidence. The May 15
capture (entry 33) and May 16 lifecycle capture (entry 25) both show successful
atomic publication requests using `server_crypto=true` in the query string,
`NUMBER_INT64` with numeric `value: 1` and `isEncrypted: true` on EventDetails,
and a plain `NUMBER_INT64` copy on the share. Both response records report the
published state without per-record errors. Their hints include the entitlement
token, `shortGUIDLink`, `bundleId`, `reminderTime`, `rsvpReminderTimes`, and
`timeZone`.

These captures do not show a client-generated ciphertext for `isPublished`.
Therefore the failed pyicloud attempts alone do not establish that implementing
client-side encryption is necessary. Compare the full request, serialization,
query parameters, hint, and session setup before deciding the missing primitive
in #432. The historical success does not prove the same request still works
today; a fresh capture may be needed after that comparison.

## Current architecture and API

- [`PyiCloudService.invites`](../../pyicloud/base.py) lazily constructs the service
  using the discovered `ckdatabasews` endpoint.
- [`CloudKitInvitesClient`](../../pyicloud/services/invites/client.py) composes
  private and shared `CloudKitContainerClient` instances. Public resolve/accept
  calls use an Invites-local helper, and entitlement requests use the gateway.
- [`InvitesService`](../../pyicloud/services/invites/service.py) maps shared
  CloudKit wire models into the frozen, typed
  [domain models](../../pyicloud/services/invites/models/dto.py). Listing results
  can omit time, share, timestamps, and other unavailable metadata.
- `InvitesError` derives from `PyiCloudException`. Its service-specific subclasses
  include `InvitesAuthError`, `InvitesRateLimited`, `InvitesApiError`, and
  `InvitesEntitlementError`; `EventNotFound` also derives from `InvitesError`.
- Raw CloudKit validation uses the shared `PYICLOUD_CK_EXTRA` setting (default
  `allow`) or an explicit validation override. Invites-specific `EXTRA` and
  request-dump `DEBUG` environment variables from the original proposal are not
  implemented.

The public service methods are:

| Method                          | Input and result                                             |
| ------------------------------- | ------------------------------------------------------------ |
| `events()`                      | Returns `list[Event]`, with event details only               |
| `event(event_id)`               | Full event lookup by ID, returning `Event`                   |
| `rsvps(event)`                  | Takes an `Event`; returns `list[Rsvp]`                       |
| `rsvp_image(rsvp)`              | Takes an `Rsvp`; returns `bytes` or `None`                   |
| `resolve(short_guid)`           | Returns a `ResolvedShare` preview                            |
| `accept(short_guid)`            | Accepts a share and returns `Event`; this changes membership |
| `rsvp(event, status, ...)`      | Creates/updates the current user's response; returns `Rsvp`  |
| `cancel(event, cancelled=True)` | Cancels or reinstates; returns `Event`                       |
| `raw`                           | Exposes `CloudKitInvitesClient`                              |

The read-only [CLI group](../../pyicloud/cli/commands/invites.py) exposes
`icloud invites list`, `show`, `rsvps`, and `resolve`. CLI `show`/`rsvps` accept
unambiguous event-ID prefixes; the service's `event()` takes the event ID.
There is no public `create_event()`, `publish()`, `update()`,
`set_block_new_rsvps()`, or `invite_via_link()` method. Earlier proposed signatures
must not be used as runnable examples.

## Remaining work and open questions

1. **Event creation (#431):** verify the browser sequence for zone creation,
   EventDetails creation, and share creation, including encrypted fields and
   entitlement hints. The common client has no `zones_modify()` helper today.
2. **Publishing (#432):** establish the encrypted-field write mechanism and atomic
   event/share update. Verify whether unpublishing is supported before designing
   a symmetric API. Broader owner edits have the same need for field-specific
   protocol evidence.
3. **One-time-link invitations (#433):** verify guest-info creation, participant
   and protection metadata, link generation, notification behavior, and the
   required atomic share update. Existing read support is not a writer.
4. **Other owner controls:** `blockNewRSVPs` has no public setter. Historical notes
   disagree about whether it is mirrored on the share; confirm that and its hint
   requirements before implementing a setter.
5. **Guest flow:** unauthenticated acceptance was not verified in the original
   captures. The existing service uses an authenticated session. `NO_RESPONSE=0`
   is still an inferred RSVP state.
6. **Assets:** RSVP avatar and background-image uploads remain unimplemented in
   Invites. The shared client now has `upload_asset()` (used for Reminders Urgent
   alarms in #419), but the Invites field wiring and constraints need verification.
7. **Other deferred work:** HostMessage/EventLimits writes, push subscriptions,
   and construction of PCS/key-wrapping metadata have no supported Invites API.
   A public incremental-sync API and its multi-scope cursor contract are also
   future design work, not an existing `(private_token, shared_token)` API.

## Testing and capture handling

- The existing [service and codec tests](../../tests/test_invites.py),
  [additional coverage tests](../../tests/test_cov_invites.py), and CLI tests use
  synthetic fixtures in [`tests/fixtures/invites/`](../../tests/fixtures/invites/).
- Test fixture reads go through `tests.fixture_loader.load_fixture`; automated
  tests do not access live iCloud. Cancellation tests cover event/share writes,
  the entitlement hint and cache, scope routing, missing tags, and share fallback.
- Live verification of future writes must use disposable events and controlled
  guest identities. Record notification behavior and cleanup; do not exercise
  invitations or cancellation against real guests merely to test a change.
- Keep raw HAR files, account data, signed URLs, cookies, entitlement tokens, and
  binary assets local. Promote only minimized synthetic or sanitized fixtures.
  There is no implemented `PYICLOUD_INVITES_LIVE` test switch; any future live
  harness must be designed explicitly.

## Historical capture references

These local paths document the original evidence, not tracked test dependencies
or a guarantee that the files are available in another checkout:

- `workspace/p51-ckdatabasews.icloud.com_05-15-2026-22-05-56.har`: owner browsing.
- `workspace/ckdatabasews.icloud.com_05-15-2026-22-21-27.har`: invite/accept flow.
- `workspace/cvws.icloud-content.com_05-16-2026-02-06-00.har`: RSVP status variants.
- `workspace/p51-ckdatabasews.icloud.com_05-16-2026-02-15-34.har`: event lifecycle and
  one-time-link behavior.

Current implementation and later validated findings take precedence over the
original capture-era assumptions. See #341 for the investigation history and
[#355](https://github.com/timlaing/pyicloud/pull/355) for the shipped owner-write
scope and its reported live verification.
