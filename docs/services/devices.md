# Devices

[← Back to the documentation index](../README.md)

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
>>> api.devices["i9vbKRGIcLYqJnXMd1b257kUWnoyEBcEh6yM+IfmiMLh7BmOpALS+w=="]
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
phone_number = "555-373-383"
message = "Thief! Return my phone immediately."
api.iphone.lost_device(phone_number, message)
```

### Erase Device

Erase Device functionality, forces the device to be erased when next connected to a network. It allows the person who picks up the phone to see a custom message which the device will display, if it\'s not overridden the custom message of \"This iPhone has been lost. Please call me.\" is used.

```python
message = "Thief! Return my phone immediately."
api.iphone.erase_device(message)
```
