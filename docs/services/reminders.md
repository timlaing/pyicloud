# Reminders

[← Back to the documentation index](../index.md)

You can access your iCloud Reminders through the `reminders` property:

```python
reminders = api.reminders
```

The high-level Reminders service exposes typed list, reminder, alarm, hashtag,
attachment, and recurrence-rule models for both snapshot reads and mutations.

_List reminder lists:_

```python
for lst in api.reminders.lists():
    print(lst.id, lst.title, lst.color, lst.count)
```

_List reminders globally or within one list:_

```python
reminders = api.reminders

target_list = next(iter(reminders.lists()), None)
if target_list:
    for reminder in reminders.reminders(list_id=target_list.id):
        print(reminder.id, reminder.title, reminder.completed)

for reminder in reminders.reminders():
    print(reminder.title)
```

_Fetch one reminder by ID:_

```python
reminder_id = "YOUR_REMINDER_ID"
reminder = api.reminders.get(reminder_id)

print(reminder.title)
print(reminder.desc)
print(reminder.due_date)
```

_Create, update, and delete a reminder:_

```python
from datetime import datetime, timedelta, timezone

reminders = api.reminders
target_list = next(iter(reminders.lists()), None)
if target_list is None:
    raise RuntimeError("No reminder lists found")

created = reminders.create(
    list_id=target_list.id,
    title="Buy milk",
    desc="2 percent",
    due_date=datetime.now(timezone.utc) + timedelta(days=1),
    priority=1,
    flagged=True,
)

created.desc = "2 percent organic"
created.completed = True
reminders.update(created)

fresh = reminders.get(created.id)
reminders.delete(fresh)
```

`priority` uses Apple's numeric values. Common values are `0` (none), `1`
(high), `5` (medium), and `9` (low).

_Work with a compound list snapshot:_

```python
reminders = api.reminders
target_list = next(iter(reminders.lists()), None)
if target_list is None:
    raise RuntimeError("No reminder lists found")

result = api.reminders.list_reminders(
    list_id=target_list.id,
    include_completed=True,
    results_limit=200,
)

print(len(result.reminders))
print(result.alarms.keys())
print(result.attachments.keys())
print(result.hashtags.keys())
```

`list_reminders()` returns a `ListRemindersResult` containing:

- `reminders`
- `alarms`
- `triggers`
- `attachments`
- `hashtags`
- `recurrence_rules`

_Track incremental changes:_

```python
reminders = api.reminders

# Earlier run: capture and persist a cursor somewhere durable.
cursor = reminders.sync_cursor()
# save cursor to disk / database here

# Later run: reload the previously saved cursor from disk / database.
loaded_cursor = stored_cursor_value
next_cursor = reminders.sync_cursor()
for event in reminders.iter_changes(since=loaded_cursor):
    print(event.type, event.reminder_id)
    if event.reminder is not None:
        print(event.reminder.title)

# After processing, persist next_cursor for the next run.
```

`iter_changes(since=...)` yields `ReminderChangeEvent` objects. Updated
reminders include a hydrated `reminder` payload. Deleted events may still carry
`event.reminder` for soft-deleted records; only true tombstones guarantee
`event.reminder is None`, in which case you should rely on `event.reminder_id`.

_Add location triggers and inspect alarms:_

```python
from pyicloud.services.reminders.models import Proximity

reminders = api.reminders
reminder = next(iter(reminders.reminders()), None)
if reminder is None:
    raise RuntimeError("No reminders found")

alarm, trigger = reminders.add_location_trigger(
    reminder,
    title="Office",
    address="1 Infinite Loop, Cupertino, CA",
    latitude=37.3318,
    longitude=-122.0312,
    radius=150.0,
    proximity=Proximity.ARRIVING,
)

for row in reminders.alarms_for(reminder):
    print(row.alarm.id, row.trigger.id if row.trigger else None)
```

_Add hashtags, URL attachments, and recurrence rules:_

```python
from pyicloud.services.reminders.models import RecurrenceFrequency

reminders = api.reminders
reminder = next(iter(reminders.reminders()), None)
if reminder is None:
    raise RuntimeError("No reminders found")

hashtag = reminders.create_hashtag(reminder, "errands")
attachment = reminders.create_url_attachment(
    reminder,
    url="https://example.com/checklist",
)
rule = reminders.create_recurrence_rule(
    reminder,
    frequency=RecurrenceFrequency.WEEKLY,
    interval=1,
)

print(reminders.tags_for(reminder))
print(reminders.attachments_for(reminder))
print(reminders.recurrence_rules_for(reminder))
```

You can also update and delete related records:

```python
reminders.update_attachment(attachment, url="https://example.org/checklist")
reminders.update_recurrence_rule(rule, interval=2)
reminders.delete_hashtag(reminder, hashtag)
reminders.delete_attachment(reminder, attachment)
reminders.delete_recurrence_rule(reminder, rule)
```

_Set early reminders and Urgent alarms:_

```python
from pyicloud.services.reminders.models import EarlyReminder, EarlyReminderUnit

# "3 hours before" and "1 day before" the due date
reminders.set_early_reminders(
    reminder,
    [
        EarlyReminder(count=-3, unit=EarlyReminderUnit.HOURS),
        EarlyReminder(count=-1, unit=EarlyReminderUnit.DAYS),
    ],
)
print(reminder.early_reminders)

# Remove them again
reminders.set_early_reminders(reminder, [])

# Urgent (iOS 26.2+); person_id is per-account and only readable from a
# reminder the Reminders app itself marked Urgent
reminders.set_urgent(reminder, True, person_id="<learned-from-app-record>")
print(reminder.urgent.enabled)
```

Reminders caveats:

- Reminder mutations operate on typed models. The normal pattern is to fetch a
  reminder, mutate fields locally, then call `update(reminder)`.
- Naive `datetime` values passed to `create()` are interpreted as UTC by the
  service.
- `update()` reads the reminder's current server copy first. A changed title or
  notes text is written as an edit on top of that document (the old text is
  marked deleted under a new replica), which is what Apple devices need to
  accept it; unchanged text documents are not rewritten. A stored copy that
  decodes to text but carries no CRDT structure is rewritten from scratch,
  while one that carries structure pyicloud cannot edit (for example a missing
  vector clock or start sentinel) raises rather than discarding that structure.
  Inline formatting and inline hashtags in a changed text are not preserved.
- A title or notes text is only written when it differs from the text the
  reminder had when it was read. Updating another field on an older reminder
  object therefore keeps a newer text edited on a device. If you do change the
  text, your value wins, as it does for the other fields.
- Apple devices show a timed due date without a time zone as floating time
  (its UTC clock time as local time). `create()` therefore stores a time zone
  when `time_zone` is not given: the IANA name of a `zoneinfo.ZoneInfo`, or
  `UTC` for naive and UTC values. A reminder created from a naive or UTC due
  date therefore displays at its true local time rather than the literal clock
  time. `update()` only fills in the zone of a `ZoneInfo` due date; to set or
  clear the zone yourself, pass `time_zone` to `create()`, or set
  `reminder.time_zone` before calling `update(reminder)`. All-day reminders
  stay floating.
- `update_hashtag()` exists, but the iCloud Reminders web app currently treats
  hashtag names as effectively read-only in some live flows, so rename behavior
  may not be reflected consistently outside the API.
- `RecurrenceFrequency` values mirror Apple's wire encoding, which is
  zero-based (`DAILY=0`, `WEEKLY=1`, `MONTHLY=2`, `YEARLY=3`). Earlier releases
  were one-based and wrote every frequency one step late on the wire.
- Early reminders (`set_early_reminders()`) live in the reminder record's
  `DueDateDeltaAlertsData` field, not in alarm records. The per-account
  identifier is only readable from a reminder the Reminders app already gave an
  early reminder, so read one first (or pass `account_identifier`) when setting
  the account's first early reminder.
- Urgent presentation alarms (`set_urgent()`) are best-effort: the checksum
  Apple writes is not reproducible (the algorithm is unknown), so pyicloud
  writes the SHA-512 of the asset bytes in its place. Both the per-account
  `person_id` and the checksum format were reverse-engineered from a single
  account and may change with iOS.

## Reminders Example Scripts

[`example_reminders.py`](https://github.com/timlaing/pyicloud/blob/main/examples/example_reminders.py) is a comprehensive live
integration validator for the Reminders service. It exercises list discovery,
read paths, write paths, location triggers, hashtags, attachments, recurrence
rules, and delete flows against a real iCloud account.

[`example_reminders_delta.py`](https://github.com/timlaing/pyicloud/blob/main/examples/example_reminders_delta.py) is a smaller live
validator focused on `sync_cursor()` and `iter_changes(since=...)`.

## Reminders CLI

The official Typer CLI exposes `icloud reminders ...` for common read and write
flows.

_List reminder lists and open reminders:_

```bash
icloud reminders lists --username you@example.com
icloud reminders list --username you@example.com
icloud reminders list --username you@example.com --list-id INBOX --include-completed
```

`icloud reminders list` defaults to open reminders only. Use
`--include-completed` to include completed reminders, and `--list-id` to scope
the query to one list.

_Get, create, update, and delete reminders:_

```bash
icloud reminders get REMINDER_ID --username you@example.com
icloud reminders create --username you@example.com --list-id INBOX --title "Buy milk"
icloud reminders update REMINDER_ID --username you@example.com --title "Buy oat milk"
icloud reminders set-status REMINDER_ID --username you@example.com --completed
icloud reminders delete REMINDER_ID --username you@example.com
```

_Set early reminders and Urgent alarms:_

```bash
icloud reminders update REMINDER_ID --username you@example.com \
  --early-reminder "3 hours" --early-reminder "1d"
icloud reminders update REMINDER_ID --username you@example.com \
  --early-reminder "1 day" --early-reminder-account-id ACCOUNT_ID
icloud reminders update REMINDER_ID --username you@example.com \
  --clear-early-reminders
icloud reminders update REMINDER_ID --username you@example.com --urgent
icloud reminders update REMINDER_ID --username you@example.com --not-urgent
```

The account identifier and Urgent person id are per-account and only readable
from a record the Reminders app itself wrote; set the account's first early
reminder or Urgent alarm by first reading one such reminder (its JSON output
carries `early_reminder_account_id` and `urgent.person_id`).

_Inspect snapshots and incremental changes:_

```bash
icloud reminders snapshot --username you@example.com --list-id INBOX
icloud reminders changes --username you@example.com --since PREVIOUS_CURSOR
icloud reminders sync-cursor --username you@example.com
```

_Work with reminder sub-records:_

```bash
icloud reminders alarm add-location REMINDER_ID \
  --username you@example.com \
  --title "Office" \
  --address "1 Infinite Loop, Cupertino, CA" \
  --latitude 37.3318 \
  --longitude -122.0312

icloud reminders hashtag create REMINDER_ID errands --username you@example.com
icloud reminders attachment create-url REMINDER_ID \
  --username you@example.com \
  --url https://example.com/checklist
icloud reminders recurrence create REMINDER_ID \
  --username you@example.com \
  --frequency weekly \
  --interval 1
```

The reminder CLI is organized as core commands plus `alarm`, `hashtag`,
`attachment`, and `recurrence` subgroups. Hashtag rename is exposed through
`icloud reminders hashtag update`, but Apple’s web app may still treat hashtag
names as effectively read-only in some live flows.
