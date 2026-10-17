# Invites

[← Back to the documentation index](../index.md)

Apple Invites events, read and RSVP. Events you created live in your private
library; events other people shared with you arrive in the shared one, and both
are returned together.

_List every event you can see:_

```python
api = PyiCloudService("jappleseed@apple.com", "password")

for event in api.invites.events():
    print(event.scope.value, event.event_id, event.title)
```

`scope` is `EventScope.PRIVATE` for events you own and `EventScope.SHARED` for
events shared with you. The listing is a summary: `share` and `rsvps` are not
populated. Use `event()` for a full view.

_Fetch one event with its share and responses:_

```python
event = api.invites.event("EVENT-UUID")

print(event.title, event.host_display_name)
print(event.time.start, event.time.end, event.time.is_all_day)
print(event.place.title, event.place.city)

for rsvp in api.invites.rsvps(event):
    print(rsvp.name, rsvp.status.name, rsvp.num_additional_adults)
```

`event()` looks in your private events first, then the shared ones, and raises
`EventNotFound` if neither has it.

## Guest photos

A guest can attach a photo to their response. `rsvp_image()` returns it, or
`None` when they attached none — most responses carry only a monogram.

```python
from pathlib import Path

event = api.invites.event("EVENT-UUID")
folder = Path("rsvp-images")
folder.mkdir(exist_ok=True)

for index, rsvp in enumerate(api.invites.rsvps(event)):
    image = api.invites.rsvp_image(rsvp)
    if image is not None:
        (folder / f"{index}.jpg").write_bytes(image)
```

`rsvp.name` is supplied by the guest, so it is not safe as a filename — a value
containing `/` or `..` would write outside the folder you intended.

The method returns the stored bytes without a content type, so the example names
the file `.jpg`; adjust the extension if the guest's photo is not a JPEG.

This works for events shared with you as well as your own: the asset URL Apple
returns is absolute and carries its own token.

## Responding to an invitation

```python
from pyicloud.services.invites import RsvpStatus

event = api.invites.event("EVENT-UUID")

api.invites.rsvp(
    event,
    RsvpStatus.GOING,
    name="Jane Appleseed",
    message="Looking forward to it",
    plus_one_adults=1,
)
```

`RsvpStatus` is `NO_RESPONSE`, `NOT_GOING`, `MAYBE` or `GOING`. Calling `rsvp()`
again updates your existing response rather than adding another. Choosing
`NOT_GOING` clears the plus-one counts, matching what the iCloud web interface
does.

## Joining an event from an invite link

An invite link looks like `https://www.icloud.com/invites/008ABCDEFGHIJ`. The
trailing part is the short guid.

```python
preview = api.invites.resolve("008ABCDEFGHIJ")
print(preview.owner_given_name, preview.participant_status)

event = api.invites.accept("008ABCDEFGHIJ")
print(event.scope.value)  # shared
```

On `ResolvedShare`, `participant_status`, `participant_type` and
`participant_permission` are plain strings as Apple returns them, not the
enums used elsewhere in this service.

`resolve()` only looks; `accept()` joins the event, after which it appears in
`events()` with the shared scope.

## Cancelling an event you host

```python
event = api.invites.event("EVENT-UUID")

cancelled = api.invites.cancel(event)
print(cancelled.is_cancelled)  # True

# Changed your mind:
api.invites.cancel(cancelled, cancelled=False)
```

Guests see the cancellation, so pass an event you really do host. The call
works in both directions, so a cancellation is recoverable.

Pass an `event` that carries a current record change tag — one from `event()`
or from a previous `cancel()`, as above. Apple rejects a stale one with an
opaque `502`.

## Publishing an event you host

```python
event = api.invites.event("EVENT-UUID")
published = api.invites.publish(event)
print(published.is_published)  # True

# Return it to an unpublished state:
unpublished = api.invites.publish(published, published=False)
```

Publishing and unpublishing update both the event and its share atomically.
Only hosted events are accepted, and the event must have a current record change
tag. If the share's current tag cannot be read, the call fails before modifying
either record. As with cancellation, the returned event carries the new tag;
call `event()` again if you need its share and RSVP details.

These operations change an invitation's availability. Live verification covered
a guest-free development event; notification behavior for events with guests
has not been verified. Creating events and renaming them are not yet exposed
through the service.

## One-time-link guest invitations

**Experimental:** verified with email invitations on one controlled account.
Two successive invitations preserved the earlier guest and link, and an owner
preview resolved that earlier link without accepting it. Push notifications
were received during the controlled tests: do not assume the operation is
silent, and deleting a test event cannot retract delivered notifications.
Email delivery, phone-only invitations, and acceptance by a separate guest account remain
unverified. Use disposable events and guest addresses you control until you
have verified behavior for your account.

```python
event = api.invites.event("EVENT-UUID")
invitation = api.invites.invite_via_link(
    event,
    emails=["guest@example.com"],
    name="Guest",
)
print(invitation.url)  # Sensitive participant-specific bearer link.
print(invitation.participant_id, invitation.record_name)
```

The result is a `OneTimeLinkInvitation`, extending the existing read-only
`OneTimeLinkGuest` model with `short_guid`, `url`, `record_change_tag`, and
`share_record_change_tag`. These tags identify guest-info and share records,
not EventDetails. The link is different from the event's general share URL.
Treat it as a credential: it is omitted from the result's representation but
is accessible through these attributes and serialization.

Only the event's current owner can create an invitation. The service reads the
latest full share rather than relying on cached participants or an event change
tag. A single atomic request creates the encrypted guest-info record and adds
its share participant. Existing participants and their full one-time-link
metadata are retained; only the new link entry requests token generation.
The response re-checks participants and each existing link's tokens; the
remaining preservation metadata is required before submission and resent
unchanged. Apple rejects an update that
retains anonymous participants but omits their link entries, so a missing
mapping or incomplete existing token-preservation metadata fails before
submission. No event creation, publication, or RSVP
acceptance is performed.

Supply at least one email address or phone number. Contacts must be sequences,
not a single string. Surrounding whitespace is stripped and exact duplicate
contacts are removed. Phone numbers must use international E.164 form, such as
`+12025550123`. The guest name may be empty. Contact data is stored as guest
metadata; it is not used to look up an iCloud account. This is not an email-send
API; receipt of email or other notifications is not guaranteed.

The captured requests contain no entitlement hint. This implementation leaves
authorization to Apple's endpoint rather than assuming the event-editing
subscription feature applies to invitations too. Live verification succeeded
with the client's normal `remapEnums=true` query mode, without
`server_crypto=true` or an explicit invitation entitlement hint. Eligibility
on accounts without a qualifying subscription has not been tested.

Invalid inputs raise `ValueError` before network access; an unreadable,
incomplete, or non-owner share fails without a write. After submission,
`InvitesInvitationError` retains `event_id`, `participant_id`, and `record_name`,
with the original error chained as `__cause__` (including authorization,
rate-limit, and conflict failures). Inspect that cause and the records before
retrying. A timeout or unusable response does **not** prove rollback, so the
service neither retries automatically nor removes participants to compensate.

There is no CLI mutation command for this API.

## Command line

```console
icloud invites list
icloud invites show 1F9D5936
icloud invites rsvps 1F9D5936
icloud invites resolve 008ABCDEFGHIJ
```

`list` shows events you host and events shared with you, with start times
rendered to the minute and labeled with their zone. Apple stores these as UTC
instants, so the label keeps a UTC time from being read as a local one.
`show` and `rsvps` take a full event id or any unambiguous prefix of one,
since the listing truncates ids to fit the terminal. `resolve` previews an
invite link — or just its trailing id — without joining it.

Every command is read-only, and each accepts `--format json`. Responding to an
invitation and joining one from a link are available on the service
(`api.invites.rsvp()` and `api.invites.accept()`) but not yet on the command
line.

## Errors

Every call raises a subclass of `InvitesError`: `InvitesAuthError` when the
session needs renewing, `InvitesRateLimited` when Apple asks you to slow down,
and `InvitesApiError` for everything else. `event()` raises `EventNotFound` for
an unknown event id, and `cancel()` raises `InvitesEntitlementError` when the
account is not entitled to edit events — Apple gates them behind an iCloud
subscription feature.
