# Invites fixtures

These fixtures are synthetic CloudKit payloads for the `com.apple.icloud.events`
container (Apple's "iCloud Invites" service, internally named "Events").

They are manually constructed to match the wire shape observed in local
Proxyman captures, but contain no real account data: identifiers (zone names,
participant IDs, user record names, shortGUIDs, device IDs), timestamps,
change tags, email addresses, and PCS / protection-info byte blobs are all
fake or omitted, and stable across test runs.

## Files

| File                                      | Shape                                                                                                          |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `events_query_response.json`              | `CKQueryResponse` for a `zoneWide` `EventDetails` query (two events: a full one and a minimal open-ended one). |
| `event_lookup_response.json`              | `CKLookupResponse` for an `EventDetails` + `cloudkit.zoneshare` lookup against one event's zone.               |
| `rsvp_query_response.json`                | `CKQueryResponse` for an `RSVP` query against one event's zone (one going RSVP with a plus-one).               |
| `rsvp_modify_response.json`               | `CKModifyResponse` after toggling an existing RSVP to `Maybe` with no plus-ones (used by Phase 2 write tests). |
| `one_time_link_query_empty_response.json` | `CKQueryResponse` empty record set, for the OneTimeLinkGuestInfo case when no link invitations exist.          |
| `resolve_response.json`                   | Raw JSON from `public/records/resolve` for the owner viewing their own share.                                  |
| `accept_response.json`                    | Raw JSON from `public/records/accept` for a guest joining via the shortGUID.                                   |

## Encoding notes

`time`, `place`, `background`, `style`, and `integrations` fields on
`EventDetails` are typed `ENCRYPTED_BYTES` on the wire but carry
base64-encoded JSON. The fixture values base64 to inspectable JSON:

- `time`: `{"startSince1970": <ms>, "endSince1970": <ms>?, "isAllDay": bool, "isOpenEnded": bool}`
- `place`: `{"latitude", "longitude", "title", "subtitle", "city", "timeZoneIdentifier", "url"}`
- `background`: `{"kind": "image", "visibility": 1, "image": {"cropRect": [...]}}`
- `style`: `{"titleFont": 0}`
- `integrations`: `{"version": "1", "data": [{"type": "com.apple.widget.weather"}, ...]}`
