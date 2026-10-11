# Diagnostics

[← Back to the documentation index](../index.md)

Apple changes the service map it advertises without notice: hosts move
between partitions, keys stop being advertised, and endpoints get
withdrawn. When that happens the failure usually surfaces deep inside a
service call, which makes it hard to tell an Apple-side change apart from
a stale session or a wrong argument.

`icloud doctor` answers that question directly:

```console
icloud doctor
icloud doctor --username jappleseed@apple.com
icloud doctor --format json
```

```text
         Environment
┌──────────┬────────────────┐
│ pyicloud │ 2.6.5          │
│ Python   │ 3.13.1         │
│ Platform │ darwin (arm64) │
└──────────┴────────────────┘

                       Webservices
┌────────┬──────────────┬─────────────────────────┬──────────────────┐
│ Status │ Key          │ Used by                 │ Host             │
├────────┼──────────────┼─────────────────────────┼──────────────────┤
│ ok     │ ckdatabasews │ photos, reminders,      │ p51-ckdatabasews │
│        │              │ notes, invites          │ .icloud.com:443  │
│ ok     │ drivews      │ drive                   │ p51-drivews...   │
└────────┴──────────────┴─────────────────────────┴──────────────────┘

No problems found. Apple is advertising every service this version of
pyicloud needs.
```

The command is strictly read-only: it inspects the local install, the
stored session, and the service map Apple returned at login. It never
writes to the account.

It reports what it can even when there is no usable session, because
"you are not logged in" is itself one of the answers.

Exit codes are meant for scripting:

- `0`: Apple is advertising every service this version of pyicloud needs.
- `1`: something the library depends on is missing or unusable, **or**
  there was no session, so the checks that matter could not run.

Services Apple advertises that pyicloud does not wrap are listed as
context rather than as problems, and `--format json` returns the whole
report, including every finding, for use in bug reports and scripts.
