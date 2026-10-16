# Invites

[← Back to the documentation index](../index.md)

Apple Invites events: create, read, publish, cancel, and RSVP. Events you created live in your private
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

## Creating an event

```python
from datetime import datetime, timezone
from pyicloud.services.invites import EventPlace, EventTime

event = api.invites.create_event(
    "Dinner",
    EventTime(start=datetime(2030, 6, 1, 18, tzinfo=timezone.utc)),
    place=EventPlace(city="Paris", time_zone_identifier="Europe/Paris"),
    notes="Bring something to share.",
    max_additional_guests_per_rsvp=1,
)
print(event.event_id, event.record_change_tag)
print(event.is_published)  # False
print(event.share.url)
```

Creation returns an **unpublished draft**, with a share but no invited guests.
Publish it separately when ready. Creating a share does not publish the event.
Apple's subscription entitlement is checked before creating a zone.

`title` must be non-empty and `time` must be an `EventTime` with timezone-aware
dates. An optional end cannot precede the start; an open-ended event cannot
have an end. Optional `max_attendees` must be positive, and
`max_additional_guests_per_rsvp` must be non-negative. `is_private=True` sets
the event's private flag. A supplied place time-zone identifier must be a
valid IANA name. The returned event includes the server-assigned change tag
and share; use `event()` for a fresh, fully hydrated view.

Creation requires three requests: create a new zone, create EventDetails, then
create its share. They are not one transaction. If a later step fails,
pyicloud attempts to delete **only the new zone**. `InvitesCreationError`
exposes `event_id`, `cleanup_succeeded` (`True`, `False`, or `None` when zone
creation was not confirmed), and `cleanup_error`; the original failure is
chained as `__cause__`. If cleanup fails or the creation response is lost,
inspect that event ID before retrying: an incomplete draft may remain. Calls
are not automatically retried because that could create duplicate events.
Input validation and entitlement failures occur before any zone is created.

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
has not been verified. Renaming events and generating one-time guest links
are not yet exposed through the service.

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
