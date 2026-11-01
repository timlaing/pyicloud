# Compatibility and operational guarantees

[← Back to the documentation index](../index.md)

PyiCloud uses Apple's undocumented web services. The project can preserve its own
interfaces; it cannot promise that Apple endpoints, account entitlements, response
fields or authentication challenges remain available. Offline CI checks are not
evidence of live Apple or downstream-client compatibility.

## Installation and offline quick start

Use standard CPython **3.10 through 3.15**, in an isolated environment:

```console
python -m venv .venv
.venv/bin/python -m pip install pyicloud
```

On Windows, use `.venv\Scripts\python` instead. The library does not require the
CLI extra. Install the CLI separately with `python -m pip install "pyicloud[cli]"`;
then run `icloud --help` or `icloud --version`.

This complete example constructs a client without contacting Apple or consulting
the password keyring. It needs no real account credentials:

```python
from tempfile import TemporaryDirectory

from pyicloud import PyiCloudService

with TemporaryDirectory() as cookie_directory:
    api = PyiCloudService(
        "offline@example.invalid",
        cookie_directory=cookie_directory,
        authenticate=False,
    )
    try:
        assert api.session.data["client_id"]
    finally:
        api.session.close()
```

`authenticate=False` means **offline construction**, not an offline service
emulator. Accessing service properties, probing, reading or mutating services can
make requests or require authentication. For real account login and 2FA, see
[Authentication](authentication.md); for CLI installation and credentials, see
[Command-Line Interface](cli.md). Free-threaded Python is not a support claim.

## Project-controlled compatibility

The released baseline is **2026.10.1**, commit
`2d3d137e17cdd06e4e58a4d0e0e400dccb31b08f`. Its frozen manifest records selected
contracts; tests do not regenerate expectations from the implementation.

- Root imports `from pyicloud import PyiCloudService, AppleDevice` retain their
  class identities and defining modules.
- The `pyicloud.services` facades for Account, Calendar, Contacts, Drive, Find My,
  Hide My Email, Notes, Photos, Reminders and Ubiquity remain supported. See the
  [service reference](../api/services.md) for imports and signatures. Mail and
  Invites are additive, post-baseline service imports.
- Recorded constructors and selected authentication/Drive/Reminders methods retain
  positional parameter order, parameter kinds and defaults. Optional keyword-only
  additions can be compatible; inserting positional arguments or changing defaults
  requires an explicit compatibility decision. Private underscore-prefixed helpers,
  wire/protobuf internals and incidental imported names are not supported facades.
- Representative Reminders models retain recorded fields and defaults. A reminder
  includes `id`, `list_id`, `title`, completion/due-date fields and related record
  IDs; a list includes `id`, `title`, `color`, `count`, grouping/deletion metadata
  and membership IDs. Models can gain fields. Preserve the distinction between
  typed models and their serialized JSON; do not assume every service returns the
  same object shape or freeze arbitrary Apple response dictionaries.
- Recorded library exceptions retain catch relationships. In particular,
  `PyiCloudConnectionException` and endpoint/service response failures remain
  catchable as `PyiCloudAPIResponseException`, itself a `PyiCloudException`.
  Service-specific error hierarchies are described in the service guides. Exception
  prose, raw response bodies and private diagnostics are not automation schemas.

**Experimental surfaces** are explicitly marked in their service guides (notably
[Mail](../services/mail.md)). Their availability and protocol coverage can change;
the experimental label is not permission to silently break recorded constructors,
imports or return fields. Annotations are not a promise that every private symbol
is a supported typed API; publishing a `py.typed` contract is a separate decision.

The previously approved Photos module migration/removal in #472 is recorded as an
intentional exception to defining-module preservation. It does not establish a
general allowance to relocate public classes or remove aliases during refactors.

## CLI automation

Use explicit commands and `--format json`; do not scrape tables or help prose.
The frozen representative `icloud reminders lists --format json` contract returns
an array with list fields including `id`, `title`, `color`, `count`,
`badge_emblem`, `sorting_style`, `is_group`, `deleted`, `reminder_ids`, `guid`
and `record_change_tag`. Consumers should tolerate additive fields. Other
commands have their own documented shapes; watches can produce multiple results
and are not interchangeable with a single snapshot.

Successful representative reads/help/version checks exit **0**; invalid command
usage exits **2**. Operational errors are nonzero, but there is no universal
meaning for every nonzero code. Follow the [diagnostics exit-code contract](diagnostics.md)
and service guides rather than inventing a global JSON error envelope. Destructive
and uncertain-write outcomes require explicit handling, not a blanket retry.

## CalVer and compatibility decisions

Versions from `2026.10.0` use `YYYY.M.PATCH`; a date-based version is **not** a
SemVer guarantee that an upgrade cannot break an interface. Project-controlled
breaking changes, removed aliases, module moves and deprecations require a
reviewed decision, an explicit changelog entry and migration guidance. Update the
baseline only with that decision, never just because a test fails.

There is no promised fixed deprecation/grace period. This avoids inventing a
guarantee the project cannot support. Apple-driven failures must be distinguished
from intentional project-controlled changes; endpoint removal or a new challenge
may require a release without notice from Apple. Pin versions when deploying and
review the changelog before upgrading.

## Saved state, permissions and ownership

Session files use JSON dictionaries; recorded valid client IDs, session fields
and unknown dictionary fields are retained, while designated transient fields
are filtered on save. Cookie files use the LWP cookie format. They contain
sensitive authentication state, not merely preferences.

Use a private cookie directory you own, preferably POSIX mode `0700`. Directory
creation uses a restrictive umask, but existing directory/file permissions are
not automatically a guarantee of private ownership. Do not store state in a
shared writable location. Give **one owning process** each directory; shared
client/session mutation is not a thread/process-safe contract. Lazy service
creation, cached results and mutable shared request parameters are not isolation
boundaries between simultaneous callers.

Recovery and atomic-save improvements are tracked in [#475](https://github.com/timlaing/pyicloud/issues/475)
and [#476](https://github.com/timlaing/pyicloud/issues/476), with PRs #487 and #488.
Check the installed release's changelog before relying on these improvements.
Do not infer recovery from arbitrary malformed or invalidly encoded state solely
from the valid-state baseline. Independently usable cookies should not be deleted
merely because session JSON needs recovery.

When atomic-save support is present, each file is staged in its destination
directory, closed and replaced with owner-only POSIX permissions. This is atomic
visibility **per file**, not a transaction across cookie/session files, protection
against stale writers, or a thread-safety guarantee. No `fsync`/power-loss durability
is promised. Without that support, failed direct writes can truncate the last
usable file. Keep backups private and avoid editing saved state while a client
is using it.

## Timeouts and retry risks

| Request path                                                                                                       | Current timeout behavior                                                           |
| ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `PyiCloudSession.request` / `request_raw`                                                                          | Default `timeout=None`; forwards caller-provided values, no global deadline        |
| Core login/authentication and Account, Calendar, Contacts, Drive, Find My, Hide My Email, Ubiquity direct requests | Many calls supply no timeout and inherit `None`                                    |
| Shared CloudKit client, including typed Photos/Notes/Reminders paths                                               | Default `(10.0, 60.0)` connect/read seconds; low-level configuration where exposed |
| Invites client and Mail metadata/content requests                                                                  | Default `(10.0, 60.0)` connect/read seconds                                        |
| Mail draft attachment upload                                                                                       | Explicit upload timeout, documented by the Mail implementation                     |
| Photos raw REST fallbacks                                                                                          | Some requests provide no timeout; typed-client limits do not cover all fallbacks   |

Connect/read timeouts are not a whole-operation wall-clock deadline. Streaming,
polling, DNS/system behavior and multiple requests can extend elapsed time.
Photos upload hydration has a separate polling budget (default 60 seconds,
2-second interval), not an HTTP timeout for every underlying request.

There is no blanket automatic mutation-retry or exactly-once guarantee. A timeout,
connection loss or invalid response after a write may leave its server-side result
unknown. Inspect fresh state before retrying, especially sends, deletions, uploads
and moves; service-specific uncertain-write exceptions must be handled explicitly.
Read polling/backoff and authentication recovery do not authorize replaying an
arbitrary write. This guide adds no default timeout or new retries. Any future
timeout configuration must be additive and initially preserve existing defaults.

## Contributor coverage policy

The maintainer-approved policy is **strictly above 90% combined line-and-branch
coverage for every measured source file**, plus the existing aggregate **at least
90%** gate. The per-file checker uses exact covered/possible counts, not rounded
display percentages; 90.0000% does not pass. Coverage's existing generated-code
omissions remain unchanged. Empty modules with no executable lines/branches do
not fail. A report with no measured files does fail.

CI and the manual pytest hook run both gates. Run
`python scripts/check/tests.py` or `prek run --hook-stage manual pytest --all-files`.
Failures name the file, actual percentage and covered/possible count. See
[CONTRIBUTING](https://github.com/timlaing/pyicloud/blob/main/CONTRIBUTING.md)
and historical [#394](https://github.com/timlaing/pyicloud/issues/394); the historical
issue is context for policy, not a claim of a current low-coverage defect.
