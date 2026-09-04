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

## Command line

```console
icloud invites list
icloud invites show 1F9D5936
icloud invites rsvps 1F9D5936
icloud invites resolve 008ABCDEFGHIJ
```

`list` shows events you host and events shared with you, with start times
rendered in each event's own timezone (UTC when the event carries none).
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
an unknown event id.
