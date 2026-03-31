# pyiCloud

![Build Status](https://github.com/timlaing/pyicloud/actions/workflows/tests.yml/badge.svg)
[![Library version](https://img.shields.io/pypi/v/pyicloud)](https://pypi.org/project/pyicloud)
[![Supported versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Ftimlaing%2Fpyicloud%2Fmain%2Fpyproject.toml)](https://pypi.org/project/pyicloud)
[![Downloads](https://pepy.tech/badge/pyicloud)](https://pypi.org/project/pyicloud)
[![Formatted with Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://pypi.python.org/pypi/ruff)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=bugs)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Technical Debt](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=sqale_index)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Duplicated Lines (%)](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=duplicated_lines_density)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=coverage)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=ncloc)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)

PyiCloud is a module which allows pythonistas to interact with iCloud
webservices. It's powered by the fantastic
[requests](https://github.com/kennethreitz/requests) HTTP library.

At its core, PyiCloud connects to the iCloud web application using your username and password, then performs regular queries against its API.

**Please see the [terms of use](TERMS_OF_USE.md) for your responsibilities when using this library.**

For support and discussions, join our Discord community: [Join our Discord community](https://discord.gg/nru3was4hk)

## Installation

Install the library and CLI with:

```console
$ pip install pyicloud
```

This installs the `icloud` command line interface alongside the Python package.

## Authentication

Authentication without using a saved password is as simple as passing your username and password to the `PyiCloudService` class:

```python
from pyicloud import PyiCloudService
api = PyiCloudService('jappleseed@apple.com', 'password')
```

In the event that the username/password combination is invalid, a
`PyiCloudFailedLoginException` exception is thrown.

If the country/region setting of your Apple ID is China mainland, you
should pass `china_mainland=True` to the `PyiCloudService` class:

```python
from pyicloud import PyiCloudService
api = PyiCloudService('jappleseed@apple.com', 'password', china_mainland=True)
```

If you plan to use this as a daemon or long-running service to keep the
connection alive with Apple, a refresh interval can be configured
(default: 5 minutes).

```python
from pyicloud import PyiCloudService

api = PyiCloudService(
    'jappleseed@apple.com',
    'password',
    refresh_interval=60,  # 1 minute refresh
)
api.devices
```

## Command-Line Interface

The `icloud` command line interface is organized around top-level
subcommands such as `auth`, `account`, `devices`, `calendar`,
`contacts`, `drive`, `photos`, and `hidemyemail`.

Command options belong on the final command that uses them. For example:

```console
$ icloud auth login --username jappleseed@apple.com
$ icloud account summary --format json
```

The root command only exposes help and shell-completion utilities.

You can store your password in the system keyring using the
command-line tool:

```console
$ icloud auth login --username jappleseed@apple.com
Enter iCloud password for jappleseed@apple.com:
Save password in keyring? (y/N)
```

If you have stored a password in the keyring, you will not be required
to provide a password when interacting with the command-line tool or
instantiating the `PyiCloudService` class for that username.

```python
api = PyiCloudService('jappleseed@apple.com')
```

CLI examples:

```console
$ icloud auth status
$ icloud auth login --username jappleseed@apple.com
$ icloud auth login --username jappleseed@apple.com --china-mainland
$ icloud auth login --username jappleseed@apple.com --accept-terms
$ icloud account summary
$ icloud account summary --format json
$ icloud devices list --locate
$ icloud devices list --with-family
$ icloud devices show "Example iPhone"
$ icloud devices export "Example iPhone" --output ./iphone.json
$ icloud calendar events --username jappleseed@apple.com --period week
$ icloud contacts me --username jappleseed@apple.com
$ icloud drive list /Documents --username jappleseed@apple.com
$ icloud photos albums --username jappleseed@apple.com
$ icloud hidemyemail list --username jappleseed@apple.com
$ icloud auth logout
$ icloud auth logout --keep-trusted
$ icloud auth logout --all-sessions
$ icloud auth logout --keep-trusted --all-sessions
$ icloud auth logout --remove-keyring
$ icloud auth keyring delete --username jappleseed@apple.com
```

If you would like to delete a password stored in your system keyring,
use the dedicated keyring subcommand:

```console
$ icloud auth keyring delete --username jappleseed@apple.com
```

The `auth` command group lets you inspect and manage persisted sessions:

- `icloud auth status`: report active logged-in iCloud sessions without prompting for password or 2FA
- `icloud auth login`: ensure a usable authenticated session exists
- `icloud auth logout`: sign out and clear the local session so the next login will typically require 2FA again
- `icloud auth logout --keep-trusted`: sign out while asking Apple to preserve trusted-browser state for the next login
- `icloud auth logout --all-sessions`: attempt to sign out all browser sessions
- `icloud auth logout --remove-keyring`: also delete the stored password for the selected account
- `icloud auth keyring delete --username <apple-id>`: delete the stored password without logging out
- `icloud auth logout --keep-trusted --all-sessions`: experimental combination that requests both behaviors

When only one local account is known, `auth login` can omit
`--username`. Service commands, `auth status`, and `auth logout` without
`--username` operate on active logged-in sessions only, similar to `gh`.
If no active sessions exist, service commands and `auth status` report
that no iCloud accounts are logged in and direct you to
`icloud auth login --username <apple-id>`. If multiple logged-in
accounts exist, pass `--username` to disambiguate account-targeted
operations.

`--keep-trusted` and `--all-sessions` are translated to Apple's logout
payload internally; the CLI intentionally exposes user-facing semantics
instead of the raw wire field names.

Stored passwords in the system keyring are treated separately from
authenticated sessions. A plain `icloud auth logout` ends the session
but keeps the stored password. Use `icloud auth logout --remove-keyring`
or `icloud auth keyring delete --username <apple-id>` if you also want
to forget the saved password.

**Note**: Authentication expires on an interval set by Apple, at which
point you will have to authenticate again.

**Note**: Apple will require you to accept new terms and conditions to
access the iCloud web service. This will result in login failures until
the terms are accepted. This can be automatically accepted by PyiCloud
using `icloud auth login --accept-terms`. Alternatively you can visit
the iCloud web site to view and accept the terms.

### Two-step and two-factor authentication (2SA/2FA)

If you have enabled two-factor authentications (2FA) or [two-step
authentication (2SA)](https://support.apple.com/en-us/HT204152) for the
account you will have to do some extra work:

For HSA2 accounts, `request_2fa_code()` now starts Apple's active delivery
route for the current challenge. Depending on the account and session, that may
be a trusted-device prompt, an SMS code, or a security-key flow.

```python
import sys

import click

if api.requires_2fa:
    security_key_names = api.security_key_names

    if security_key_names:
        print(
            f"Security key confirmation is required. "
            f"Please plug in one of the following keys: {', '.join(security_key_names)}"
        )

        devices = api.fido2_devices

        print("Available FIDO2 devices:")

        for idx, dev in enumerate(devices, start=1):
            print(f"{idx}: {dev}")

        choice = click.prompt(
            "Select a FIDO2 device by number",
            type=click.IntRange(1, len(devices)),
            default=1,
        )
        selected_device = devices[choice - 1]

        print("Please confirm the action using the security key")

        api.confirm_security_key(selected_device)

    else:
        print("Two-factor authentication required.")
        api.request_2fa_code()
        code = input(
            "Enter the code you received of one of your approved devices: "
        )
        result = api.validate_2fa_code(code)
        print("Code validation result: %s" % result)

        if not result:
            print("Failed to verify security code")
            sys.exit(1)

    if not api.is_trusted_session:
        print("Session is not trusted. Requesting trust...")
        result = api.trust_session()
        print("Session trust result %s" % result)

        if not result:
            print(
                "Failed to request trust. You will likely be prompted for confirmation again in the coming weeks"
            )

elif api.requires_2sa:
    print("Two-step authentication required. Your trusted devices are:")

    devices = api.trusted_devices
    for i, device in enumerate(devices):
        print(
            "  %s: %s" % (i, device.get('deviceName',
            "SMS to %s" % device.get('phoneNumber')))
        )

    device = click.prompt('Which device would you like to use?', default=0)
    device = devices[device]
    if not api.send_verification_code(device):
        print("Failed to send verification code")
        sys.exit(1)

    code = click.prompt('Please enter validation code')
    if not api.validate_verification_code(device, code):
        print("Failed to verify verification code")
        sys.exit(1)
```

## Account

You can access information about your iCloud account using the `account` property:

```pycon
>>> api.account
{devices: 5, family: 3, storage: 8990635296 bytes free}
```

### Summary Plan

you can access information about your iCloud account\'s summary plan using the `account.summary_plan` property:

```pycon
>>> api.account.summary_plan
{'featureKey': 'cloud.storage', 'summary': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAccountPurchasedPlan': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAppleOnePlan': {'includedInPlan': False}, 'includedWithSharedPlan': {'includedInPlan': False}, 'includedWithCompedPlan': {'includedInPlan': False}, 'includedWithManagedPlan': {'includedInPlan': False}}
```

### Storage

You can get the storage information of your iCloud account using the `account.storage` property:

```pycon
>>> api.account.storage
{usage: 85.12% used of 53687091200 bytes, usages_by_media: {'photos': <AccountStorageUsageForMedia: {key: photos, usage: 41785285900 bytes}>, 'backup': <AccountStorageUsageForMedia: {key: backup, usage: 27250085 bytes}>, 'docs': <AccountStorageUsageForMedia: {key: docs, usage: 3810332430 bytes}>, 'mail': <AccountStorageUsageForMedia: {key: mail, usage: 26208942 bytes}>, 'messages': <AccountStorageUsageForMedia: {key: messages, usage: 1379351 bytes}>}}
```

You even can generate a pie chart:

```python
......
storage = api.account.storage
y = []
colors = []
labels = []
for usage in storage.usages_by_media.values():
    y.append(usage.usage_in_bytes)
    colors.append(f"#{usage.color}")
    labels.append(usage.label)

plt.pie(y,
        labels=labels,
        colors=colors,
        )
plt.title("Storage Pie Test")
plt.show()
```

## Devices

You can list which devices associated with your account by using the
`devices` property:

```pycon
>>> api.devices
{
'i9vbKRGIcLYqJnXMd1b257kUWnoyEBcEh6yM+IfmiMLh7BmOpALS+w==': <AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>,
'reGYDh9XwqNWTGIhNBuEwP1ds0F/Lg5t/fxNbI4V939hhXawByErk+HYVNSUzmWV': <AppleDevice(MacBook Air 11": Johnny Appleseed's MacBook Air)>
}
```

and you can access individual devices by either their index, or their
ID:

```pycon
>>> api.devices[0]
<AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>
>>> api.devices['i9vbKRGIcLYqJnXMd1b257kUWnoyEBcEh6yM+IfmiMLh7BmOpALS+w==']
<AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>
```

or, as a shorthand if you have only one associated apple device, you can
simply use the `iphone` property to access the first device associated
with your account:

```pycon
>>> api.iphone
<AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>
```

Note: the first device associated with your account may not necessarily
be your iPhone.

## Find My iPhone

Once you have successfully authenticated, you can start querying your
data!

### Location

Returns the device\'s last known location. The Find My iPhone app must
have been installed and initialized.

```pycon
>>> api.iphone.location
{'timeStamp': 1357753796553, 'locationFinished': True, 'longitude': -0.14189, 'positionType': 'GPS', 'locationType': None, 'latitude': 51.501364, 'isOld': False, 'horizontalAccuracy': 5.0}
```

### Status

The Find My iPhone response is quite bloated, so for simplicity\'s sake
this method will return a subset of the properties.

```pycon
>>> api.iphone.status()
{'deviceDisplayName': 'iPhone 5', 'deviceStatus': '200', 'batteryLevel': 0.6166913, 'name': "Peter's iPhone"}
```

If you wish to request further properties, you may do so by passing in a
list of property names.

### Play Sound

Sends a request to the device to play a sound, if you wish pass a custom
message you can do so by changing the subject arg.

```python
api.iphone.play_sound()
```

A few moments later, the device will play a ringtone, display the
default notification (\"Find My iPhone Alert\") and a confirmation email
will be sent to you.

### Lost Mode

Lost mode is slightly different to the \"Play Sound\" functionality in
that it allows the person who picks up the phone to call a specific
phone number _without having to enter the passcode_. Just like \"Play
Sound\" you may pass a custom message which the device will display, if
it\'s not overridden the custom message of \"This iPhone has been lost.
Please call me.\" is used.

```python
phone_number = '555-373-383'
message = 'Thief! Return my phone immediately.'
api.iphone.lost_device(phone_number, message)
```

### Erase Device

Erase Device functionality, forces the device to be erased when next connected to a network. It allows the person who picks up the phone to see a custom message which the device will display, if it\'s not overridden the custom message of \"This iPhone has been lost. Please call me.\" is used.

```python
message = 'Thief! Return my phone immediately.'
api.iphone.erase_device(message)
```

## Calendar

The calendar webservice supports fetching, creating, and removing calendars and events, with support for alarms, and invitees.

### Calendars

The calendar functionality is based around the `CalendarObject` dataclass. Every variable has a default value named according to the http payload parameters from the icloud API. The `guid` is a uuid4 identifier unique to each calendar. The class will create one automatically if it is left blank when the `CalendarObject` is instanced. the `guid` parameter should only be set when you know the guid of an existing calendar. The color is an rgb hex value and will be a random color if not set.

#### Functions

**get_calendars(as_objs:bool=False) -> list**<br>
_returns a list of the user's calendars_<br>
if `as_objs` is set to `True`, the returned list will be of CalendarObjects; else it will be of dictionaries.

**add_calendar(calendar:CalendarObject) -> None:**<br>
_adds a calendar to the users apple calendar_

**remove_calendar(cal_guid:str) -> None**<br>
_Removes a Calendar from the apple calendar given the provided guid_

#### Examples

_Create and add a new calendar:_

```python
from pyicloud import PyiCloudService
from pyicloud.services.calendar import CalendarObject

api = PyiCloudService("username", "password")
calendar_service = api.calendar
cal = CalendarObject(title="My Calendar", share_type="published")
cal.color = "#FF0000"
calendar_service.add_calendar(cal)
```

_Remove an existing calendar:_

```python
cal = calendar_service.get_calendars(as_objs=True)[1]
calendar_service.remove_calendar(cal.guid)
```

### Events

The events functionality is based around the `EventObject` dataclass with support for alarms and invitees. `guid` is the unique identifier of each event, while `pguid` is the identifier of the calendar to which this event belongs. `pguid` is the only required parameter. The `EventObject` includes automatic validation, dynamic timezone detection, and multiple methods for event management.

#### Key Features

- **Automatic Validation**: Events validate required fields, date ranges, and calendar GUIDs
- **Dynamic Timezone Detection**: Automatically detects and uses the user's local timezone
- **Alarm Support**: Add alarms at event time or before the event with flexible timing
- **Invitee Management**: Add multiple invitees who will receive email notifications

#### Functions

**get_events(from_dt:datetime=None, to_dt:datetime=None, period:str="month", as_objs:bool=False)**<br>
_Returns a list of events from `from_dt` to `to_dt`. If `period` is provided, it will return the events in that period referencing `from_dt` if it was provided; else using today's date. IE if `period` is "month", the events for the entire month that `from_dt` falls within will be returned._

**get_event_detail(pguid, guid, as_obj:bool=False)**<br>
_Returns a specific event given that event's `guid` and `pguid`_

**add_event(event:EventObject) -> None**<br>
_Adds an Event to a calendar specified by the event's `pguid`._

**remove_event(event:EventObject) -> None**<br>
_Removes an Event from a calendar specified by the event's `pguid`._

#### EventObject Methods

**add_invitees(emails: list) -> None**<br>
_Adds a list of email addresses as invitees to the event. They will receive email notifications when the event is created._

**add_alarm_at_time() -> str**<br>
_Adds an alarm that triggers at the exact time of the event. Returns the alarm GUID for reference._

**add_alarm_before(minutes=0, hours=0, days=0, weeks=0) -> str**<br>
_Adds an alarm that triggers before the event starts. You can specify any combination of time units. Returns the alarm GUID for reference._

#### Examples

_Create an event with invitees and alarms:_

```python
from datetime import datetime, timedelta
from pyicloud import PyiCloudService
from pyicloud.services.calendar import EventObject

api = PyiCloudService("username", "password")
calendar_service = api.calendar

# Get a calendar to use
calendars = calendar_service.get_calendars(as_objs=True)
calendar_guid = calendars[0].guid

# Create an event with proper validation
event = EventObject(
    pguid=calendar_guid,
    title="Team Meeting",
    start_date=datetime.now() + timedelta(hours=2),
    end_date=datetime.now() + timedelta(hours=3),
    location="Conference Room A",
    all_day=False
)

# Add invitees (they'll receive email notifications)
event.add_invitees(["colleague1@company.com", "colleague2@company.com"])

# Add alarms
event.add_alarm_before(minutes=15)  # 15 minutes before
event.add_alarm_before(days=1)      # 1 day before

# Add the event to the calendar
calendar_service.add_event(event)
```

_Create a simple event:_

```python
# Basic event creation
event = EventObject(
    pguid=calendar_guid,
    title="Doctor Appointment",
    start_date=datetime(2024, 1, 15, 14, 0),
    end_date=datetime(2024, 1, 15, 15, 0)
)

# Add a 30-minute warning alarm
event.add_alarm_before(minutes=30)

calendar_service.add_event(event)
```

_Get events in a specific date range:_

```python
from_dt = datetime(2024, 1, 1)
to_dt = datetime(2024, 1, 31)
events = calendar_service.get_events(from_dt, to_dt, as_objs=True)

for event in events:
    print(f"Event: {event.title} at {event.start_date}")
```

_Get next week's events:_

```python
next_week_events = calendar_service.get_events(
    from_dt=datetime.today() + timedelta(days=7),
    period="week",
    as_objs=True
)
```

_Remove an event:_

```python
calendar_service.remove_event(event)
```

## Contacts

You can access your iCloud contacts/address book through the `contacts`
property:

```pycon
>>> for c in api.contacts.all:
...     print(c.get('firstName'), c.get('phones'))
John [{'field': '+1 555-55-5555-5', 'label': 'MOBILE'}]
```

Note: These contacts do not include contacts federated from e.g.
Facebook, only the ones stored in iCloud.

### MeCard

You can access the user's info (contact information) using the `me` property:

```pycon
>>> api.contacts.me
Tim Cook
```

And get the user's profile picture:

```pycon
>>> api.contacts.me.photo
{'signature': 'the signature', 'url': 'URL to the picture', 'crop': {'x': 0, 'width': 640, 'y': 110, 'height': 640}}
```

## File Storage (Ubiquity) - Legacy service

You can access documents stored in your iCloud account by using the
`files` property\'s `dir` method:

**NOTE** If you receive a `Account migrated` error, apple has migrated your account to iCloud drive. Please use the `api.drive` API instead.

```pycon
>>> api.files.dir()
['.do-not-delete',
 '.localized',
 'com~apple~Notes',
 'com~apple~Preview',
 'com~apple~mail',
 'com~apple~shoebox',
 'com~apple~system~spotlight'
]
```

You can access children and their children\'s children using the
filename as an index:

```pycon
>>> api.files['com~apple~Notes']
<Folder: 'com~apple~Notes'>
>>> api.files['com~apple~Notes'].type
'folder'
>>> api.files['com~apple~Notes'].dir()
['Documents']
>>> api.files['com~apple~Notes']['Documents'].dir()
['Some Document']
>>> api.files['com~apple~Notes']['Documents']['Some Document'].name
'Some Document'
>>> api.files['com~apple~Notes']['Documents']['Some Document'].modified
datetime.datetime(2012, 9, 13, 2, 26, 17)
>>> api.files['com~apple~Notes']['Documents']['Some Document'].size
1308134
>>> api.files['com~apple~Notes']['Documents']['Some Document'].type
'file'
```

And when you have a file that you\'d like to download, the `open` method
will return a response object from which you can read the `content`.

```pycon
>>> api.files['com~apple~Notes']['Documents']['Some Document'].open().content
'Hello, these are the file contents'
```

Note: the object returned from the above `open` method is a [response
object](http://www.python-requests.org/en/latest/api/#classes) and the
`open` method can accept any parameters you might normally use in a
request using [requests](https://github.com/kennethreitz/requests).

For example, if you know that the file you\'re opening has JSON content:

```pycon
>>> api.files['com~apple~Notes']['Documents']['information.json'].open().json()
{'How much we love you': 'lots'}
>>> api.files['com~apple~Notes']['Documents']['information.json'].open().json()['How much we love you']
'lots'
```

Or, if you\'re downloading a particularly large file, you may want to
use the `stream` keyword argument, and read directly from the raw
response object:

```pycon
>>> download = api.files['com~apple~Notes']['Documents']['big_file.zip'].open(stream=True)
>>> with open('downloaded_file.zip', 'wb') as opened_file:
        opened_file.write(download.raw.read())
```

## File Storage (iCloud Drive)

You can access your iCloud Drive using an API identical to the Ubiquity
one described in the previous section, except that it is rooted at
`api.drive`:

```pycon
>>> api.drive.dir()
['Holiday Photos', 'Work Files']
>>> api.drive['Holiday Photos']['2013']['Sicily'].dir()
['DSC08116.JPG', 'DSC08117.JPG']

>>> drive_file = api.drive['Holiday Photos']['2013']['Sicily']['DSC08116.JPG']
>>> drive_file.name
'DSC08116.JPG'
>>> drive_file.date_modified
datetime.datetime(2013, 3, 21, 12, 28, 12) # NB this is UTC
>>> drive_file.size
2021698
>>> drive_file.type
'file'
```

The `open` method will return a response object from which you can read
the file\'s contents:

```python
from shutil import copyfileobj
with drive_file.open(stream=True) as response:
    with open(drive_file.name, 'wb') as file_out:
        copyfileobj(response.raw, file_out)
```

To interact with files and directions the `mkdir`, `rename` and `delete`
functions are available for a file or folder:

```python
api.drive['Holiday Photos'].mkdir('2020')
api.drive['Holiday Photos']['2020'].rename('2020_copy')
api.drive['Holiday Photos']['2020_copy'].delete()
```

The `upload` method can be used to send a file-like object to the iCloud
Drive:

```python
with open('Vacation.jpeg', 'rb') as file_in:
    api.drive['Holiday Photos'].upload(file_in)
```

It is strongly suggested to open file handles as binary rather than text
to prevent decoding errors further down the line.

You can also interact with files in the `trash`:

```pycon
>>> delete_output = api.drive['Holiday Photos']['2013']['Sicily']['DSC08116.JPG'].delete()
>>> api.drive.trash.dir()
['DSC08116.JPG']

>>> delete_output = api.drive['Holiday Photos']['2013']['Sicily']['DSC08117.JPG'].delete()
>>> api.drive.refresh_trash()
>>> api.drive.trash.dir()
['DSC08116.JPG', 'DSC08117.JPG']
```

You can interact with the `trash` similar to a standard directory, with some restrictions. In addition, files in the `trash` can be recovered back to their original location, or deleted forever:

```pycon
>>> api.drive['Holiday Photos']['2013']['Sicily'].dir()
[]

>>> recover_output = api.drive.trash['DSC08116.JPG'].recover()
>>> api.drive['Holiday Photos']['2013']['Sicily'].dir()
['DSC08116.JPG']

>>> api.drive.trash.dir()
['DSC08117.JPG']

>>> purge_output = api.drive.trash['DSC08117.JPG'].delete_forever()
>>> api.drive.refresh_trash()
>>> api.drive.trash.dir()
[]
```

## Photo Library

You can access the iCloud Photo Library through the `photos` property.

```pycon
>>> api.photos.all
<PhotoAlbum: 'All Photos'>
```

Individual albums are available through the `albums` property:

```pycon
>>> api.photos.albums['Screenshots']
<PhotoAlbum: 'Screenshots'>
```

To delete an individual album, call the `delete` method.

```pycon
>>> api.photos.albums['MyAlbum']
<PhotoAlbum: 'MyAlbum'>
>>> api.photos.albums['MyAlbum'].delete()
True
```

Which you can iterate to access the photo assets. The "All Photos"
album is sorted by `added_date` so the most recently added
photos are returned first. All other albums are sorted by
`asset_date` (which represents the exif date) :

```pycon
>>> for photo in api.photos.albums['Screenshots']:
        print(photo, photo.filename)
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
```

To download a photo, use the `download` method, which will return a raw stream:

```python
photo = next(iter(api.photos.albums['Screenshots']), None)
with open(photo.filename, 'wb') as opened_file:
    opened_file.write(photo.download())
```

Information about each version can be accessed through the `versions`
property:

```pycon
>>> photo.versions.keys()
['medium', 'original', 'thumb']
```

To download a specific version of the photo asset, pass the version to
`download()`:

```python
with open(photo.versions['thumb']['filename'], 'wb') as thumb_file:
    thumb_file.write(photo.download('thumb'))
```

To upload a photo use the `upload` method, which will upload the file to the requested album
this will appear automatically in your 'ALL PHOTOS' album. This will return the uploaded
PhotoAsset for further information.

```python
api.photos.albums['Screenshots'].upload(file_path)
```

```pycon
>>> album = api.photos.albums['Screenshots']
>>> album
<PhotoAlbum: 'Screenshots'>
>>> album.upload("./my_test_image.jpg")
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jdQ> my_test_image.jpg
```

Note: Only limited media types are accepted. Unsupported types (e.g., PNG) will return a TYPE_UNSUPPORTED error.

To delete a photo, use the `delete` method on the PhotoAsset. It returns a bool indicating success.

```pycon
>>> photo = api.photos.albums['Screenshots'][0]
>>> photo
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
>>> photo.delete()
True
```

To add an existing photo to an album, use the `add_photo` method, which will link the PhotoAsset to the requested album.
It returns a bool indicating success.

```python
api.photos.albums['Screenshots'].add_photo(photo_asset)
```

```pycon
>>> photo = api.photos.albums['Screenshots'][0]
>>> photo
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
>>> my_album = api.photos.albums['MyAlbum']
>>> my_album
<PhotoAlbum: 'MyAlbum'>
>>> my_album.add_photo(photo)
True
```

## Hide My Email

You can access the iCloud Hide My Email service through the `hidemyemail` property

To generate a new email alias use the `generate` method.

```python
# Generate a new email alias
new_email = api.hidemyemail.generate()
print(f"Generated new email: {new_email}")
```

To reserve the generated email with a custom label

```python
reserved = api.hidemyemail.reserve(new_email, "Shopping")
print(f"Reserved email - response: {reserved}")
```

To get the anonymous_id (unique identifier) from the reservation.

```python
anonymous_id = reserved.get("anonymousId")
print(anonymous_id)
```

To list the current aliases

```python
# Print details of each alias
for alias in api.hidemyemail:
    print(f"- {alias.get('hme')}: {alias.get('label')} ({alias.get('anonymousId')})")
```

Additional detail usage

```python
# Get detailed information about a specific alias
alias_details = api.hidemyemail[anonymous_id]
print(f"Alias details: {alias_details}")

# Update the alias metadata (label and note)
updated = api.hidemyemail.update_metadata(
    anonymous_id,
    "Online Shopping",
    "Used for e-commerce websites"
)
print(f"Updated alias: {updated}")

# Deactivate an alias (stops email forwarding but keeps the alias for future reactivation)
deactivated = api.hidemyemail.deactivate(anonymous_id)
print(f"Deactivated alias: {deactivated}")

# Reactivate a previously deactivated alias (resumes email forwarding)
reactivated = api.hidemyemail.reactivate(anonymous_id)
print(f"Reactivated alias: {reactivated}")

# Delete the alias when no longer needed
deleted = api.hidemyemail.delete(anonymous_id)
print(f"Deleted alias: {deleted}")
```

## Reminders

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
for event in reminders.iter_changes(since=loaded_cursor):
    print(event.type, event.reminder_id)
    if event.reminder is not None:
        print(event.reminder.title)

# After processing, persist the new high-water mark for the next run.
next_cursor = reminders.sync_cursor()
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

Reminders caveats:

- Reminder mutations operate on typed models. The normal pattern is to fetch a
  reminder, mutate fields locally, then call `update(reminder)`.
- Naive `datetime` values passed to `create()` are interpreted as UTC by the
  service.
- `update_hashtag()` exists, but the iCloud Reminders web app currently treats
  hashtag names as effectively read-only in some live flows, so rename behavior
  may not be reflected consistently outside the API.

### Reminders Example Scripts

[`example_reminders.py`](example_reminders.py) is a comprehensive live
integration validator for the Reminders service. It exercises list discovery,
read paths, write paths, location triggers, hashtags, attachments, recurrence
rules, and delete flows against a real iCloud account.

[`example_reminders_delta.py`](example_reminders_delta.py) is a smaller live
validator focused on `sync_cursor()` and `iter_changes(since=...)`.

## Notes

You can access your iCloud Notes through the `notes` property:

```python
notes = api.notes
```

The high-level Notes service exposes typed note, folder, and attachment models
for common workflows such as recent-note listings, full-note retrieval, HTML
rendering, and on-disk exports. Prefer `api.notes` for normal use and treat
`api.notes.raw` as an advanced/debug escape hatch when you need direct access to
the underlying CloudKit client.

_List recent notes:_

```python
notes = api.notes

for summary in notes.recents(limit=10):
    print(summary.id, summary.title, summary.modified_at)
```

_Iterate folders and list notes in one folder:_

```python
notes = api.notes

folder = next(iter(notes.folders()), None)
if folder:
    print(folder.id, folder.name, folder.has_subfolders)
    for summary in notes.in_folder(folder.id, limit=5):
        print(summary.title)
```

_Iterate all notes or capture a sync cursor for later incremental work:_

```python
notes = api.notes

for summary in notes.iter_all():
    print(summary.id, summary.title)

cursor = notes.sync_cursor()
print(cursor)
```

Persist the sync cursor from `sync_cursor()` and pass it back to
`iter_all(since=...)` or `iter_changes(since=...)` on a later run to enumerate
only newer changes.

_Fetch a full note with attachment metadata:_

```python
note_id = "YOUR_NOTE_ID"
note = api.notes.get(note_id, with_attachments=True)

print(note.title)
print(note.text)

for attachment in note.attachments or []:
    print(attachment.id, attachment.filename, attachment.uti, attachment.size)
```

_Render a note to an HTML fragment:_

```python
html_fragment = api.notes.render_note(
    note_id,
    preview_appearance="light",
    pdf_object_height=600,
)

print(html_fragment[:200])
```

`render_note()` returns an HTML fragment string and does not download assets or
write files to disk.

_Export a note to HTML on disk:_

```python
path = api.notes.export_note(
    note_id,
    "./exports/notes_html",
    export_mode="archival",
    assets_dir="./exports/assets",
    full_page=True,
)

print(path)
```

`export_note()` accepts `ExportConfig` keyword arguments such as
`export_mode`, `assets_dir`, `full_page`, `preview_appearance`, and
`pdf_object_height`.

- `export_mode="archival"` downloads assets locally and rewrites the HTML to
  use local file references for stable, offline-friendly output.
- `export_mode="lightweight"` skips local downloads and keeps remote/preview
  asset references for quick inspection.

_Save or stream an attachment:_

```python
note = api.notes.get(note_id, with_attachments=True)
attachment = next(iter(note.attachments or []), None)

if attachment:
    saved_path = attachment.save_to("./exports/notes_attachments", service=api.notes)
    print(saved_path)

    with open("./attachment-copy.bin", "wb") as file_out:
        for chunk in attachment.stream(service=api.notes):
            file_out.write(chunk)
```

Notes caveats:

- `get()` raises `NoteLockedError` for passphrase-locked notes whose content
  cannot be read.
- `get()`, `render_note()`, and `export_note()` raise `NoteNotFound` when the
  note ID does not exist.
- `api.notes.raw` is available for advanced/debug workflows, but it is not the
  primary Notes API surface.

### Notes CLI

### Notes CLI

The official Typer CLI exposes `icloud notes ...` for recent-note inspection,
folder browsing, title-based search, HTML rendering, and note-id-based export.

_List recent notes, folders, or one folder’s notes:_

```bash
uv run icloud notes recent --username you@example.com
uv run icloud notes folders --username you@example.com
uv run icloud notes list --username you@example.com --folder-id FOLDER_ID
uv run icloud notes list --username you@example.com --all --since PREVIOUS_CURSOR
```

_Search notes by title:_

```bash
uv run icloud notes search --username you@example.com --title "Daily Plan"
uv run icloud notes search --username you@example.com --title-contains "meeting"
```

`icloud notes search` is the official title-filter workflow. It uses a
recents-first search strategy and falls back to a full feed scan when needed.

_Fetch, render, and export one note by id:_

```bash
uv run icloud notes get NOTE_ID --username you@example.com --with-attachments
uv run icloud notes render NOTE_ID --username you@example.com --preview-appearance dark
uv run icloud notes export NOTE_ID \
  --username you@example.com \
  --output-dir ./exports/notes_html \
  --export-mode archival \
  --assets-dir ./exports/assets
```

`icloud notes export` stays explicit by note id. Title filters are intentionally
handled by `icloud notes search` rather than by bulk export flags.

_Inspect incremental changes:_

```bash
uv run icloud notes changes --username you@example.com --since PREVIOUS_CURSOR
uv run icloud notes sync-cursor --username you@example.com
```

### Notes CLI Example

### Notes CLI Example

[`examples/notes_cli.py`](examples/notes_cli.py) is a local developer utility
built on top of `api.notes`. It is useful for searching notes, inspecting the
rendering pipeline, and exporting HTML, but its selection heuristics and debug
output are convenience behavior rather than part of the Notes service contract.

_Archival export (downloads local assets):_

```bash
uv run python examples/notes_cli.py \
  --username you@example.com \
  --title "My Note" \
  --max 1 \
  --output-dir ./exports/notes_html \
  --assets-dir ./exports/assets \
  --export-mode archival \
  --full-page
```

_Lightweight export (skips local asset downloads):_

```bash
uv run python examples/notes_cli.py \
  --username you@example.com \
  --title-contains "meeting" \
  --max 3 \
  --output-dir ./exports/notes_html \
  --export-mode lightweight
```

Important CLI flags:

- `--title` filters by exact note title.
- `--title-contains` filters by case-insensitive title substring.
- `--max` limits how many matching notes are exported.
- `--output-dir` selects the directory for saved HTML output.
- `--export-mode archival|lightweight` controls whether assets are downloaded
  locally (`archival`) or left as remote/preview references (`lightweight`).
- `--assets-dir` selects the base directory for downloaded assets in archival
  mode.
- `--full-page` wraps saved output in a complete HTML page. If omitted, the CLI
  saves an HTML fragment.
- `--notes-debug` enables verbose Notes/export debugging.
- `--dump-runs` prints attribute runs and writes an annotated mapping under
  `workspace/notes_runs`.
- `--preview-appearance light|dark` selects the preferred preview variant when
  multiple appearances are available.
- `--pdf-height` sets the pixel height for embedded PDF `<object>` elements.

`--download-assets` is no longer supported in the example CLI. Use
`--export-mode` to choose between archival and lightweight export behavior.

## Examples

If you want to see some code samples, see the [examples](examples.py).
