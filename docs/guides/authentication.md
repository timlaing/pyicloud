# Authentication

[← Back to the documentation index](../README.md)

Authentication without using a saved password is as simple as passing your username and password to the `PyiCloudService` class:

```python
from pyicloud import PyiCloudService

api = PyiCloudService("jappleseed@apple.com", "password")
```

In the event that the username/password combination is invalid, a
`PyiCloudFailedLoginException` exception is thrown.

If the country/region setting of your Apple ID is China mainland, you
should pass `china_mainland=True` to the `PyiCloudService` class:

```python
from pyicloud import PyiCloudService

api = PyiCloudService("jappleseed@apple.com", "password", china_mainland=True)
```

If you plan to use this as a daemon or long-running service to keep the
connection alive with Apple, a refresh interval can be configured
(default: 5 minutes).

```python
from pyicloud import PyiCloudService

api = PyiCloudService(
    "jappleseed@apple.com",
    "password",
    refresh_interval=60,  # 1 minute refresh
)
api.devices
```

The background monitor refreshes device data each interval, but — when
using pyicloud as a library — it does **not** actively locate devices
(`shouldLocate`) by default; keeping GPS fresh is the caller's
responsibility via `refresh(locate=True)` (or on the CLI, see below). To
make the monitor actively locate devices on every refresh instead, pass
`monitor_locate=True`; note this costs some device battery:

```python
from pyicloud import PyiCloudService

api = PyiCloudService(
    "jappleseed@apple.com",
    "password",
    monitor_locate=True,  # each monitor refresh actively locates devices
)
```

When run through the `icloud` CLI, locating refreshes are always enabled
and the interval is configurable. On `devices list`, the interval also
turns the command into a watch loop that re-locates and re-lists at that
cadence until interrupted; pass `--iterations` to stop after a fixed
number of runs:

```shell
icloud devices list --locate --refresh-interval 60  # watch every 60s
```

When `with_family=True`, Find My polls Apple's backend while family
members' devices finish loading (`deviceFetchStatus` is `"LOADING"`). By
default it polls every `0.5s` up to `5` times before giving up; both can be
tuned via `family_poll_delay` and `family_poll_max_retries`:

```python
from pyicloud import PyiCloudService

api = PyiCloudService(
    "jappleseed@apple.com",
    "password",
    with_family=True,
    family_poll_delay=1.0,  # seconds between polls (default: 0.5)
    family_poll_max_retries=10,  # max polls (default: 5)
)
api.devices
```

## Minimal authentication for Find My (`pause_2fa`)

Some services (notably Find My) can be used without completing two-factor
authentication (2FA). To allow a Find My-only workflow on an account that has
2FA enabled, pass `pause_2fa=True` to the constructor. Authentication proceeds
with an _untrusted_ session, so 2FA is not prompted and code delivery is
deferred:

```python
from pyicloud import PyiCloudService

api = PyiCloudService("jappleseed@apple.com", "password", pause_2fa=True)
print(api.iphone.location)
```

**Security implications:** with `pause_2fa=True` the resulting session is **not
trusted** by Apple, and only a limited set of 2FA-gated operations is
available. It is intended for lightweight/headless usage (e.g. locating a
device) where completing interactive 2FA each run is undesirable. Any operation
that requires a trusted session — such as full 2FA-protected account access —
will still need the normal 2FA flow (`request_2fa_code()` /
`validate_2fa_code()`). In particular, a cached _untrusted_ session is only
reused when the current call also enables `pause_2fa`; it is rejected on the
default (trusted) flow to avoid silently bypassing 2FA.

## Two-step and two-factor authentication (2SA/2FA)

If you have enabled two-factor authentications (2FA) or [two-step
authentication (2SA)](https://support.apple.com/en-us/HT204152) for the
account you will have to do some extra work:

For HSA2 accounts, `request_2fa_code()` now starts Apple's active delivery
route for code-based challenges. Depending on the account and session, that may
be a trusted-device prompt or an SMS code. Security-key challenges are handled
separately via `security_key_names` / `confirm_security_key()`.

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
            print(
                "Failed to request trust. You will likely be prompted for confirmation again in the coming weeks"
            )

elif api.requires_2sa:
    print("Two-step authentication required. Your trusted devices are:")

    devices = api.trusted_devices
    for i, device in enumerate(devices):
        print(
            "  %s: %s"
            % (i, device.get("deviceName", "SMS to %s" % device.get("phoneNumber")))
        )

    device = click.prompt("Which device would you like to use?", default=0)
    device = devices[device]
    if not api.send_verification_code(device):
        print("Failed to send verification code")
        sys.exit(1)

    code = click.prompt("Please enter validation code")
    if not api.validate_verification_code(device, code):
        print("Failed to verify verification code")
        sys.exit(1)
```
