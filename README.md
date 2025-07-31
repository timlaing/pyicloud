# pyiCloud

![Build Status](https://github.com/timlaing/pyicloud/actions/workflows/tests.yml/badge.svg)
[![Library version](https://img.shields.io/pypi/v/pyicloud)](https://pypi.org/project/pyicloud)
[![Supported versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Ftimlaing%2Fpyicloud%2Fmain%2Fpyproject.toml)](https://pypi.org/project/pyicloud)
[![Downloads](https://pepy.tech/badge/pyicloud)](https://pypi.org/project/pyicloud)
[![Formatted with Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](ttps://pypi.python.org/pypi/ruff)
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
webservices. It\'s powered by the fantastic
[requests](https://github.com/kennethreitz/requests) HTTP library.

At its core, PyiCloud connects to iCloud using your username and
password, then performs calendar and iPhone queries against their API.

For support and discussions, join our Discord community: [Join our Discord community](https://discord.gg/nru3was4hk)

## Authentication

Authentication without using a saved password is as simple as passing
your username and password to the `PyiCloudService` class:

``` python
from pyicloud import PyiCloudService
api = PyiCloudService('jappleseed@apple.com', 'password')
```

In the event that the username/password combination is invalid, a
`PyiCloudFailedLoginException` exception is thrown.

If the country/region setting of your Apple ID is China mainland, you
should pass `china_mainland=True` to the `PyiCloudService` class:

``` python
from pyicloud import PyiCloudService
api = PyiCloudService('jappleseed@apple.com', 'password', china_mainland=True)
```

You can also store your password in the system keyring using the
command-line tool:

``` console
$ icloud --username=jappleseed@apple.com
Enter iCloud password for jappleseed@apple.com:
Save password in keyring? (y/N)
```

If you have stored a password in the keyring, you will not be required
to provide a password when interacting with the command-line tool or
instantiating the `PyiCloudService` class for the username you stored
the password for.

``` python
api = PyiCloudService('jappleseed@apple.com')
```

If you would like to delete a password stored in your system keyring,
you can clear a stored password using the `--delete-from-keyring`
command-line option:

``` console
$ icloud --username=jappleseed@apple.com --delete-from-keyring
Enter iCloud password for jappleseed@apple.com:
Save password in keyring? [y/N]: N
```

**Note**: Authentication will expire after an interval set by Apple, at
which point you will have to re-authenticate. This interval is currently
two months.

### Two-step and two-factor authentication (2SA/2FA)

If you have enabled two-factor authentications (2FA) or [two-step
authentication (2SA)](https://support.apple.com/en-us/HT204152) for the
account you will have to do some extra work:

``` python
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
    import click
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

``` pycon
>>> api.account
{devices: 5, family: 3, storage: 8990635296 bytes free}
```

### Summary Plan

you can access information about your iCloud account\'s summary plan using the `account.summary_plan` property:

``` pycon
>>> api.account.summary_plan
{'featureKey': 'cloud.storage', 'summary': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAccountPurchasedPlan': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAppleOnePlan': {'includedInPlan': False}, 'includedWithSharedPlan': {'includedInPlan': False}, 'includedWithCompedPlan': {'includedInPlan': False}, 'includedWithManagedPlan': {'includedInPlan': False}}
```

### Storage

You can get the storage information of your iCloud account using the `account.storage` property:

``` pycon
>>> api.account.storage
{usage: 85.12% used of 53687091200 bytes, usages_by_media: {'photos': <AccountStorageUsageForMedia: {key: photos, usage: 41785285900 bytes}>, 'backup': <AccountStorageUsageForMedia: {key: backup, usage: 27250085 bytes}>, 'docs': <AccountStorageUsageForMedia: {key: docs, usage: 3810332430 bytes}>, 'mail': <AccountStorageUsageForMedia: {key: mail, usage: 26208942 bytes}>, 'messages': <AccountStorageUsageForMedia: {key: messages, usage: 1379351 bytes}>}}
```

You even can generate a pie chart:

``` python
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

``` pycon
>>> api.devices
{
'i9vbKRGIcLYqJnXMd1b257kUWnoyEBcEh6yM+IfmiMLh7BmOpALS+w==': <AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>,
'reGYDh9XwqNWTGIhNBuEwP1ds0F/Lg5t/fxNbI4V939hhXawByErk+HYVNSUzmWV': <AppleDevice(MacBook Air 11": Johnny Appleseed's MacBook Air)>
}
```

and you can access individual devices by either their index, or their
ID:

``` pycon
>>> api.devices[0]
<AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>
>>> api.devices['i9vbKRGIcLYqJnXMd1b257kUWnoyEBcEh6yM+IfmiMLh7BmOpALS+w==']
<AppleDevice(iPhone 4S: Johnny Appleseed's iPhone)>
```

or, as a shorthand if you have only one associated apple device, you can
simply use the `iphone` property to access the first device associated
with your account:

``` pycon
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

``` pycon
>>> api.iphone.location
{'timeStamp': 1357753796553, 'locationFinished': True, 'longitude': -0.14189, 'positionType': 'GPS', 'locationType': None, 'latitude': 51.501364, 'isOld': False, 'horizontalAccuracy': 5.0}
```

### Status

The Find My iPhone response is quite bloated, so for simplicity\'s sake
this method will return a subset of the properties.

``` pycon
>>> api.iphone.status()
{'deviceDisplayName': 'iPhone 5', 'deviceStatus': '200', 'batteryLevel': 0.6166913, 'name': "Peter's iPhone"}
```

If you wish to request further properties, you may do so by passing in a
list of property names.

### Play Sound

Sends a request to the device to play a sound, if you wish pass a custom
message you can do so by changing the subject arg.

``` python
api.iphone.play_sound()
```

A few moments later, the device will play a ringtone, display the
default notification (\"Find My iPhone Alert\") and a confirmation email
will be sent to you.

### Lost Mode

Lost mode is slightly different to the \"Play Sound\" functionality in
that it allows the person who picks up the phone to call a specific
phone number *without having to enter the passcode*. Just like \"Play
Sound\" you may pass a custom message which the device will display, if
it\'s not overridden the custom message of \"This iPhone has been lost.
Please call me.\" is used.

``` python
phone_number = '555-373-383'
message = 'Thief! Return my phone immediately.'
api.iphone.lost_device(phone_number, message)
```

### Erase Device

Erase Device functionality, forces the device to be erased when next connected to a network. It allows the person who picks up the phone to see a custom message which the device will display, if it\'s not overridden the custom message of \"This iPhone has been lost. Please call me.\" is used.

``` python
message = 'Thief! Return my phone immediately.'
api.iphone.erase_device(message)
```

## Calendar

The calendar webservice supports fetching, creating, and removing calendars and events, with support for alarms, and invitees.

### Calendars

The calendar functionality is based around the `CalendarObject` dataclass. Every variable has a default value named according to the http payload parameters from the icloud API. The `guid` is a uuid4 identifier unique to each calendar. The class will create one automatically if it is left blank when the `CalendarObject` is instanced. the `guid` parameter should only be set when you know the guid of an existing calendar. The color is an rgb hex value and will be a random color if not set.

#### Functions

**get_calendars(as_objs:bool=False) -> list**<br>
*returns a list of the user's calendars*<br>
if `as_objs` is set to `True`, the returned list will be of CalendarObjects; else it will be of dictionaries.

**add_calendar(calendar:CalendarObject) -> None:**<br>
*adds a calendar to the users apple calendar*

**remove_calendar(cal_guid:str) -> None**<br>
*Removes a Calendar from the apple calendar given the provided guid*

#### Examples

*Create and add a new calendar:*

``` python
from pyicloud import PyiCloudService
from pyicloud.services.calendar import CalendarObject

api = PyiCloudService("username", "password")
calendar_service = api.calendar
cal = CalendarObject(title="My Calendar", share_type="published")
cal.color = "#FF0000"
calendar_service.add_calendar(cal)
```

*Remove an existing calendar:*

``` python
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
*Returns a list of events from `from_dt` to `to_dt`. If `period` is provided, it will return the events in that period referencing `from_dt` if it was provided; else using today's date. IE if `period` is "month", the events for the entire month that `from_dt` falls within will be returned.*

**get_event_detail(pguid, guid, as_obj:bool=False)**<br>
*Returns a specific event given that event's `guid` and `pguid`*

**add_event(event:EventObject) -> None**<br>
*Adds an Event to a calendar specified by the event's `pguid`.*

**remove_event(event:EventObject) -> None**<br>
*Removes an Event from a calendar specified by the event's `pguid`.*

#### EventObject Methods

**add_invitees(emails: list) -> None**<br>
*Adds a list of email addresses as invitees to the event. They will receive email notifications when the event is created.*

**add_alarm_at_time() -> str**<br>
*Adds an alarm that triggers at the exact time of the event. Returns the alarm GUID for reference.*

**add_alarm_before(minutes=0, hours=0, days=0, weeks=0) -> str**<br>
*Adds an alarm that triggers before the event starts. You can specify any combination of time units. Returns the alarm GUID for reference.*

#### Examples

*Create an event with invitees and alarms:*

``` python
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

*Create a simple event:*

``` python
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

*Get events in a specific date range:*

``` python
from_dt = datetime(2024, 1, 1)
to_dt = datetime(2024, 1, 31)
events = calendar_service.get_events(from_dt, to_dt, as_objs=True)

for event in events:
    print(f"Event: {event.title} at {event.start_date}")
```

*Get next week's events:*

``` python
next_week_events = calendar_service.get_events(
    from_dt=datetime.today() + timedelta(days=7),
    period="week",
    as_objs=True
)
```

*Remove an event:*

``` python
calendar_service.remove_event(event)
```

## Contacts

You can access your iCloud contacts/address book through the `contacts`
property:

``` pycon
>>> for c in api.contacts.all():
>>> print(c.get('firstName'), c.get('phones'))
John [{'field': '+1 555-55-5555-5', 'label': 'MOBILE'}]
```

Note: These contacts do not include contacts federated from e.g.
Facebook, only the ones stored in iCloud.

### MeCard

You can access the user's info (contact information) using the `me` property:

``` pycon
>>> api.contacts.me
Tim Cook
```

And get the user's  profile picture:

``` pycon
>>> api.contacts.me.photo
{'signature': 'the signature', 'url': 'URL to the picture', 'crop': {'x': 0, 'width': 640, 'y': 110, 'height': 640}}
```

## File Storage (Ubiquity)

You can access documents stored in your iCloud account by using the
`files` property\'s `dir` method:

``` pycon
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

``` pycon
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

``` pycon
>>> api.files['com~apple~Notes']['Documents']['Some Document'].open().content
'Hello, these are the file contents'
```

Note: the object returned from the above `open` method is a [response
object](http://www.python-requests.org/en/latest/api/#classes) and the
`open` method can accept any parameters you might normally use in a
request using [requests](https://github.com/kennethreitz/requests).

For example, if you know that the file you\'re opening has JSON content:

``` pycon
>>> api.files['com~apple~Notes']['Documents']['information.json'].open().json()
{'How much we love you': 'lots'}
>>> api.files['com~apple~Notes']['Documents']['information.json'].open().json()['How much we love you']
'lots'
```

Or, if you\'re downloading a particularly large file, you may want to
use the `stream` keyword argument, and read directly from the raw
response object:

``` pycon
>>> download = api.files['com~apple~Notes']['Documents']['big_file.zip'].open(stream=True)
>>> with open('downloaded_file.zip', 'wb') as opened_file:
        opened_file.write(download.raw.read())
```

## File Storage (iCloud Drive)

You can access your iCloud Drive using an API identical to the Ubiquity
one described in the previous section, except that it is rooted at
`api.drive`:

``` pycon
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

``` python
from shutil import copyfileobj
with drive_file.open(stream=True) as response:
    with open(drive_file.name, 'wb') as file_out:
        copyfileobj(response.raw, file_out)
```

To interact with files and directions the `mkdir`, `rename` and `delete`
functions are available for a file or folder:

``` python
api.drive['Holiday Photos'].mkdir('2020')
api.drive['Holiday Photos']['2020'].rename('2020_copy')
api.drive['Holiday Photos']['2020_copy'].delete()
```

The `upload` method can be used to send a file-like object to the iCloud
Drive:

``` python
with open('Vacation.jpeg', 'rb') as file_in:
    api.drive['Holiday Photos'].upload(file_in)
```

It is strongly suggested to open file handles as binary rather than text
to prevent decoding errors further down the line.

You can also interact with files in the `trash`:

``` pycon
>>> delete_output = api.drive['Holiday Photos']['2013']['Sicily']['DSC08116.JPG'].delete()
>>> api.drive.trash.dir()
['DSC08116.JPG']

>>> delete_output = api.drive['Holiday Photos']['2013']['Sicily']['DSC08117.JPG'].delete()
>>> api.drive.refresh_trash()
>>> api.drive.trash.dir()
['DSC08116.JPG', 'DSC08117.JPG']
```

You can interact with the `trash` similar to a standard directory, with some restrictions. In addition, files in the `trash` can be recovered back to their original location, or deleted forever:

``` pycon
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

``` pycon
>>> api.photos.all
<PhotoAlbum: 'All Photos'>
```

Individual albums are available through the `albums` property:

``` pycon
>>> api.photos.albums['Screenshots']
<PhotoAlbum: 'Screenshots'>
```

Which you can iterate to access the photo assets. The "All Photos"
album is sorted by `added_date` so the most recently added
photos are returned first. All other albums are sorted by
`asset_date` (which represents the exif date) :

``` pycon
>>> for photo in api.photos.albums['Screenshots']:
        print(photo, photo.filename)
<PhotoAsset: id=AVbLPCGkp798nTb9KZozCXtO7jds> IMG_6045.JPG
```

To download a photo use the `download` method, which will
return a [Response
object](https://requests.readthedocs.io/en/latest/api/#requests.Response),
initialized with `stream` set to `True`, so you can read from the raw
response object:

``` python
photo = next(iter(api.photos.albums['Screenshots']), None)
download = photo.download()
with open(photo.filename, 'wb') as opened_file:
    opened_file.write(download.raw.read())
```

Consider using `shutil.copyfileobj` or another buffered strategy for downloading so that the whole file isn't read into memory before writing.

``` python
import shutil
photo = next(iter(api.photos.albums['Screenshots']), None)
response_obj = photo.download()
with open(photo.filename, 'wb') as f:
    shutil.copyfileobj(response_obj.raw, f)
```

Information about each version can be accessed through the `versions`
property:

``` pycon
>>> photo.versions.keys()
['medium', 'original', 'thumb']
```

To download a specific version of the photo asset, pass the version to
`download()`:

``` python
download = photo.download('thumb')
with open(photo.versions['thumb']['filename'], 'wb') as thumb_file:
    thumb_file.write(download.raw.read())
```

To upload an image

``` python
api.photos.upload_file(file_path)
```

Note: Only limited media type is accepted, upload not support types (e.g. png) will get TYPE_UNSUPPORTED error.

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

``` python
anonymous_id = reserved.get("anonymousId")
print(anonymous_id)
```

To list the current aliases

``` python
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

## Examples

If you want to see some code samples, see the [examples](/examples.py).
`
