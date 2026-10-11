# Contacts

[← Back to the documentation index](../README.md)

You can access your iCloud contacts/address book through the `contacts`
property:

```pycon
>>> for c in api.contacts.all:
...     print(c.get("firstName"), c.get("phones"))
John [{'field': '+1 555-55-5555-5', 'label': 'MOBILE'}]
```

Note: These contacts do not include contacts federated from e.g.
Facebook, only the ones stored in iCloud.

## MeCard

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
