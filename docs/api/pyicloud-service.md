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

## Internal ownership

`PyiCloudService` remains defined in `pyicloud.base` and owns authentication
orchestration, lazy service creation, endpoints and shared session state.
Private `pyicloud._auth_protocol` helpers build request-local header dictionaries
and classify verification responses. They do not send requests, create sessions,
persist state or retry authentication. Public imports and method signatures are
unchanged; private helper modules are not consumer contracts.
