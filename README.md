# PyiCloud

[![Build Status](https://github.com/timlaing/pyicloud/actions/workflows/tests.yml/badge.svg)](https://github.com/timlaing/pyicloud/actions/workflows/tests.yml)
[![GitHub stars](https://img.shields.io/github/stars/timlaing/pyicloud.svg)](https://github.com/timlaing/pyicloud/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/timlaing/pyicloud.svg)](https://github.com/timlaing/pyicloud/issues)
[![GitHub license](https://img.shields.io/github/license/timlaing/pyicloud.svg)](LICENSE)
[![Documentation Status](https://app.readthedocs.org/projects/pyicloud/badge/?version=latest)](https://pyicloud.readthedocs.io/en/latest/?badge=latest)

[![Library version](https://img.shields.io/pypi/v/pyicloud)](https://pypi.org/project/pyicloud)
[![Supported versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Ftimlaing%2Fpyicloud%2Fmain%2Fpyproject.toml)](https://pypi.org/project/pyicloud)
[![Downloads](https://pepy.tech/badge/pyicloud)](https://pypi.org/project/pyicloud)

[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=ncloc)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Duplicated Lines (%)](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=duplicated_lines_density)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=coverage)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)

[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=bugs)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)
[![Technical Debt](https://sonarcloud.io/api/project_badges/measure?project=timlaing_pyicloud&metric=sqale_index)](https://sonarcloud.io/summary/new_code?id=timlaing_pyicloud)

PyiCloud is a module which allows pythonistas to interact with iCloud
webservices. It's powered by the fantastic
[requests](https://github.com/kennethreitz/requests) HTTP library.

At its core, PyiCloud connects to the iCloud web application using your username and password, then performs regular queries against its API.

**Please see the [terms of use](TERMS_OF_USE.md) for your responsibilities when using this library.**

For support and discussions, join our Discord community: [Join our Discord community](https://discord.gg/nru3was4hk)

## Installation

Install the library and CLI with:

```console
$ pip install "pyicloud[cli]"
```

This installs the `icloud` command line interface alongside the Python package. To install the library on its own run pip without the `cli` extra.

```console
$ pip install pyicloud
```

## Authentication

Authentication without using a saved password is as simple as passing your
username and password to the `PyiCloudService` class:

```python
from pyicloud import PyiCloudService

api = PyiCloudService("jappleseed@apple.com", "password")
```

In the event that the username/password combination is invalid, a
`PyiCloudFailedLoginException` exception is thrown.

For China mainland accounts, long-running daemon settings, family polling,
`pause_2fa`, and the full two-step / two-factor (2SA/2FA) flow, see the
**[Authentication guide](https://github.com/timlaing/pyicloud/blob/main/docs/guides/authentication.md)**.
The `icloud` CLI can also store your password in the system keyring; see the
**[Command-Line Interface guide](https://github.com/timlaing/pyicloud/blob/main/docs/guides/cli.md)**.

## Documentation

Full documentation lives under [`docs/`](https://github.com/timlaing/pyicloud/tree/main/docs).
It is also published, with a generated API reference, at
**[pyicloud.readthedocs.io](https://pyicloud.readthedocs.io/)**.

### Guides

- **[Authentication](https://github.com/timlaing/pyicloud/blob/main/docs/guides/authentication.md)** — login, `pause_2fa`, and 2SA/2FA
- **[Command-Line Interface](https://github.com/timlaing/pyicloud/blob/main/docs/guides/cli.md)** — the `icloud` command, auth/session management, and keyring storage
- **[Diagnostics](https://github.com/timlaing/pyicloud/blob/main/docs/guides/diagnostics.md)** — `icloud doctor` and its exit codes

### Services

- **[Account](https://github.com/timlaing/pyicloud/blob/main/docs/services/account.md)** — account info, storage, and summary plan
- **[Devices & Find My](https://github.com/timlaing/pyicloud/blob/main/docs/services/devices.md)** — list devices and locate, ring, lock, or erase them
- **[Calendar](https://github.com/timlaing/pyicloud/blob/main/docs/services/calendar.md)** — calendars, events, alarms, and invitees
- **[Contacts](https://github.com/timlaing/pyicloud/blob/main/docs/services/contacts.md)** — the address book and MeCard
- **[File Storage (Drive)](https://github.com/timlaing/pyicloud/blob/main/docs/services/drive.md)** — iCloud Drive and the legacy Ubiquity API
- **[Photos](https://github.com/timlaing/pyicloud/blob/main/docs/services/photos.md)** — browse, sync, upload, and modify the photo library
- **[Hide My Email](https://github.com/timlaing/pyicloud/blob/main/docs/services/hidemyemail.md)** — generate and manage email aliases
- **[Reminders](https://github.com/timlaing/pyicloud/blob/main/docs/services/reminders.md)** — lists, reminders, alarms, tags, and attachments
- **[Notes](https://github.com/timlaing/pyicloud/blob/main/docs/services/notes.md)** — folders, note rendering, and HTML export
- **[Invites](https://github.com/timlaing/pyicloud/blob/main/docs/services/invites.md)** — read events and respond to invitations

## Examples

If you want to see some code samples, see the [examples](examples/examples.py).

## Contributing

Contributions are welcome! Please read the
**[Contributing guide](CONTRIBUTING.md)**, the
**[Code of Conduct](CODE_OF_CONDUCT.md)**, and the
**[Terms of Use](TERMS_OF_USE.md)** before getting started.

To report a security vulnerability, use
**[private vulnerability reporting](https://github.com/timlaing/pyicloud/security/advisories)**
rather than opening a public issue (see **[SECURITY.md](SECURITY.md)**).
