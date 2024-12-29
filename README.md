# pyiCloud

![Build Status](https://github.com/timlaing/pyicloud/actions/workflows/tests.yml/badge.svg)
[![Library version](https://img.shields.io/pypi/v/pyicloud)](https://pypi.org/project/pyicloud)
[![Supported versions](https://img.shields.io/pypi/pyversions/pyicloud)](https://pypi.org/project/pyicloud)
[![Downloads](https://pepy.tech/badge/pyicloud)](https://pypi.org/project/pyicloud)
[![Formatted with Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
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
    print("Two-factor authentication required.")
    code = input("Enter the code you received of one of your approved devices: ")
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
            print("Failed to request trust. You will likely be prompted for the code again in the coming weeks")
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
>>> api.iphone.location()
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

## Calendar

The calendar webservice currently only supports fetching events.

### Events

Returns this month\'s events:

``` python
api.calendar.events()
```

Or, between a specific date range:

``` python
from_dt = datetime(2012, 1, 1)
to_dt = datetime(2012, 1, 31)
api.calendar.events(from_dt, to_dt)
```

Alternatively, you may fetch a single event\'s details, like so:

``` python
api.calendar.get_event_detail('CALENDAR', 'EVENT_ID')
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

Consider using ``shutil.copyfileobj`` or another buffered strategy for downloading so that the whole file isn't read into memory before writing.

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

## Code samples

If you wanna see some code samples see the [code samples
file](/CODE_SAMPLES.md).
