# PyiCloudService

The main entry point for interacting with iCloud. Every iCloud service is
reached through a property on a `PyiCloudService` instance (for example
`api.photos`, `api.drive`, or `api.devices`).

```python
from pyicloud import PyiCloudService

api = PyiCloudService("jappleseed@apple.com", "password")
```

See the [Authentication guide](../guides/authentication.md) for the full
two-step / two-factor (2SA/2FA) flow.

::: pyicloud.PyiCloudService
