# Command-Line Interface

[← Back to the documentation index](../index.md)

The `icloud` command line interface is organized around top-level
subcommands such as `auth`, `account`, `devices`, `calendar`,
`contacts`, `drive`, `photos`, `hidemyemail`, `mail`, `notes`, `reminders`, and
`invites`,
plus a standalone `doctor` command described under
[Diagnostics](diagnostics.md).

Command options belong on the final command that uses them. For example:

```console
icloud auth login --username jappleseed@apple.com
icloud account summary --format json
```

The root command only exposes help and shell-completion utilities.

You can store your password in the system keyring using the
command-line tool:

```console
icloud auth login --username jappleseed@apple.com
Enter iCloud password for jappleseed@apple.com:
Save password in keyring? (y/N)
```

If you have stored a password in the keyring, you will not be required
to provide a password when interacting with the command-line tool or
instantiating the `PyiCloudService` class for that username.

```python
api = PyiCloudService("jappleseed@apple.com")
```

CLI examples:

```console
icloud auth status
icloud auth login --username jappleseed@apple.com
icloud auth login --username jappleseed@apple.com --china-mainland
icloud auth login --username jappleseed@apple.com --accept-terms
icloud account summary
icloud account summary --format json
icloud devices list --locate
icloud devices list --with-family
icloud devices list --one-factor
icloud devices show "Example iPhone"
icloud devices export "Example iPhone" --output ./iphone.json
icloud calendar events --username jappleseed@apple.com --period week
icloud contacts me --username jappleseed@apple.com
icloud drive list /Documents --username jappleseed@apple.com
icloud mail folders --format json
icloud mail list --folder-id '<folder-id>' --limit 50 --format json
icloud mail get '<message-id>' --folder-id '<folder-id>' --format json
icloud mail get --uid 7 --folder-id '<folder-id>' --format json
icloud mail search example --folder-id '<folder-id>' --field subject --format json
icloud mail thread '<thread-id>' --folder-id '<folder-id>' --limit 50 --format json
icloud mail search-folders example --folder-id '<first-id>' --folder-id '<second-id>' --format json
icloud mail export '<message-id>' --folder-id '<folder-id>' --output ./message.json
icloud mail preview '<message-id>' --folder-id '<folder-id>' --format json
icloud mail filter --folder-id '<folder-id>' --filter unread --format json
icloud mail filter-threads --folder-id '<folder-id>' --filter flagged --page-size 50 --format json
icloud mail body '<message-id>' --folder-id '<folder-id>' --format json
icloud mail download '<message-id>' --folder-id '<folder-id>' --part-id 2 --output ./attachment.pdf
icloud photos libraries --username jappleseed@apple.com
icloud photos albums --username jappleseed@apple.com
icloud photos list --album Screenshots --limit 20 --username jappleseed@apple.com
icloud photos get photo-id-123 --format json --username jappleseed@apple.com
icloud photos sync --directory ./downloads --username jappleseed@apple.com
icloud photos watch --directory ./downloads --recent 1 --interval 300 --username jappleseed@apple.com
icloud photos sync --directory ./downloads --album Favorites --folder-structure '{:%Y/%m}' --username jappleseed@apple.com
icloud photos sync-cursor --username jappleseed@apple.com
icloud photos changes --since '<sync-cursor>' --username jappleseed@apple.com
icloud hidemyemail list --username jappleseed@apple.com
icloud auth logout
icloud auth logout --keep-trusted
icloud auth logout --all-sessions
icloud auth logout --keep-trusted --all-sessions
icloud auth logout --remove-keyring
icloud auth keyring delete --username jappleseed@apple.com
```

If you would like to delete a password stored in your system keyring,
use the dedicated keyring subcommand:

```console
icloud auth keyring delete --username jappleseed@apple.com
```

The `auth` command group lets you inspect and manage persisted sessions:

- `icloud auth status`: report active logged-in iCloud sessions without prompting for password or 2FA
- `icloud auth login`: ensure a usable authenticated session exists
- `icloud auth logout`: sign out and clear the local session so the next login will typically require 2FA again
- `icloud auth logout --keep-trusted`: sign out while asking Apple to preserve trusted-browser state for the next login
- `icloud auth logout --all-sessions`: attempt to sign out all browser sessions
- `icloud auth logout --remove-keyring`: also delete the stored password for the selected account
- `icloud auth keyring delete --username <apple-id>`: delete the stored password without logging out
- `icloud auth logout --keep-trusted --all-sessions`: experimental combination that requests both behaviors

When only one local account is known, `auth login` can omit
`--username`. Service commands, `auth status`, and `auth logout` without
`--username` operate on active logged-in sessions only, similar to `gh`.
If no active sessions exist, service commands and `auth status` report
that no iCloud accounts are logged in and direct you to
`icloud auth login --username <apple-id>`. If multiple logged-in
accounts exist, pass `--username` to disambiguate account-targeted
operations.

`--keep-trusted` and `--all-sessions` are translated to Apple's logout
payload internally; the CLI intentionally exposes user-facing semantics
instead of the raw wire field names.

Stored passwords in the system keyring are treated separately from
authenticated sessions. A plain `icloud auth logout` ends the session
but keeps the stored password. Use `icloud auth logout --remove-keyring`
or `icloud auth keyring delete --username <apple-id>` if you also want
to forget the saved password.

**Note**: Authentication expires on an interval set by Apple, at which
point you will have to authenticate again.

**Note**: Apple will require you to accept new terms and conditions to
access the iCloud web service. This will result in login failures until
the terms are accepted. This can be automatically accepted by PyiCloud
using `icloud auth login --accept-terms`. Alternatively you can visit
the iCloud web site to view and accept the terms.

## Find My without a two-factor code

Find My is the one service Apple lets through on the password alone. Of the
apps it advertises for an account, `find` is the only one flagged
`canLaunchWithOneFactor` — the same reason `icloud.com/find` works in a private
window after just a password.

```console
icloud devices list --one-factor
icloud devices sound "Example iPhone" --one-factor
```

The password comes from your keyring, or is prompted for.

It is an option on the `devices` commands rather than a way to log in because
Apple issues no `X-APPLE-WEBAUTH-TOKEN` for a session that skipped the 2FA
challenge and answers `/validate` on one with a `421`. Such a session cannot be
saved and reopened; it lasts for the one command that created it. Its cookies go
to a temporary directory, so running this never disturbs a session you already
have.

The `list`, `show`, `sound`, `message`, `lost-mode` and `export` subcommands all
accept the option. Note that `lost-mode` locks the device and can set a new
passcode, so it is worth the same care on a password-only sign-in as it is on a
full one.

`icloud devices erase` is the exception and does not accept `--one-factor`. A
remote wipe is the one irreversible action in the group, and it should cost a
full session:

```console
icloud auth login --username jappleseed@apple.com
```

Everything outside Find My needs that full session too — on a password-only
sign-in, `icloud account` answers `401` and `icloud drive` answers `421`.
