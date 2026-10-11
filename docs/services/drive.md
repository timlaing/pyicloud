# File Storage

[← Back to the documentation index](../index.md)

## Ubiquity (legacy service)

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
>>> api.files["com~apple~Notes"]
<Folder: 'com~apple~Notes'>
>>> api.files["com~apple~Notes"].type
'folder'
>>> api.files["com~apple~Notes"].dir()
['Documents']
>>> api.files["com~apple~Notes"]["Documents"].dir()
['Some Document']
>>> api.files["com~apple~Notes"]["Documents"]["Some Document"].name
'Some Document'
>>> api.files["com~apple~Notes"]["Documents"]["Some Document"].modified
datetime.datetime(2012, 9, 13, 2, 26, 17)
>>> api.files["com~apple~Notes"]["Documents"]["Some Document"].size
1308134
>>> api.files["com~apple~Notes"]["Documents"]["Some Document"].type
'file'
```

And when you have a file that you\'d like to download, the `open` method
will return a response object from which you can read the `content`.

```pycon
>>> api.files["com~apple~Notes"]["Documents"]["Some Document"].open().content
'Hello, these are the file contents'
```

Note: the object returned from the above `open` method is a [response
object](http://www.python-requests.org/en/latest/api/#classes) and the
`open` method can accept any parameters you might normally use in a
request using [requests](https://github.com/kennethreitz/requests).

For example, if you know that the file you\'re opening has JSON content:

```pycon
>>> api.files["com~apple~Notes"]["Documents"]["information.json"].open().json()
{'How much we love you': 'lots'}
>>> api.files["com~apple~Notes"]["Documents"]["information.json"].open().json()[
...     "How much we love you"
... ]
'lots'
```

Or, if you\'re downloading a particularly large file, you may want to
use the `stream` keyword argument, and read directly from the raw
response object:

```pycon
>>> download = api.files["com~apple~Notes"]["Documents"]["big_file.zip"].open(stream=True)
>>> with open('downloaded_file.zip', 'wb') as opened_file:
        opened_file.write(download.raw.read())
```

## iCloud Drive

You can access your iCloud Drive using an API identical to the Ubiquity
one described in the previous section, except that it is rooted at
`api.drive`:

```pycon
>>> api.drive.dir()
['Holiday Photos', 'Work Files']
>>> api.drive["Holiday Photos"]["2013"]["Sicily"].dir()
['DSC08116.JPG', 'DSC08117.JPG']

>>> drive_file = api.drive["Holiday Photos"]["2013"]["Sicily"]["DSC08116.JPG"]
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
    with open(drive_file.name, "wb") as file_out:
        copyfileobj(response.raw, file_out)
```

To interact with files and directions the `mkdir`, `rename` and `delete`
functions are available for a file or folder:

```python
api.drive["Holiday Photos"].mkdir("2020")
api.drive["Holiday Photos"]["2020"].rename("2020_copy")
api.drive["Holiday Photos"]["2020_copy"].delete()
```

The `upload` method can be used to send a file-like object to the iCloud
Drive:

```python
with open("Vacation.jpeg", "rb") as file_in:
    api.drive["Holiday Photos"].upload(file_in)
```

It is strongly suggested to open file handles as binary rather than text
to prevent decoding errors further down the line.

You can also interact with files in the `trash`:

```pycon
>>> delete_output = api.drive["Holiday Photos"]["2013"]["Sicily"]["DSC08116.JPG"].delete()
>>> api.drive.trash.dir()
['DSC08116.JPG']

>>> delete_output = api.drive["Holiday Photos"]["2013"]["Sicily"]["DSC08117.JPG"].delete()
>>> api.drive.refresh_trash()
>>> api.drive.trash.dir()
['DSC08116.JPG', 'DSC08117.JPG']
```

You can interact with the `trash` similar to a standard directory, with some restrictions. In addition, files in the `trash` can be recovered back to their original location, or deleted forever:

```pycon
>>> api.drive["Holiday Photos"]["2013"]["Sicily"].dir()
[]

>>> recover_output = api.drive.trash["DSC08116.JPG"].recover()
>>> api.drive["Holiday Photos"]["2013"]["Sicily"].dir()
['DSC08116.JPG']

>>> api.drive.trash.dir()
['DSC08117.JPG']

>>> purge_output = api.drive.trash["DSC08117.JPG"].delete_forever()
>>> api.drive.refresh_trash()
>>> api.drive.trash.dir()
[]
```
