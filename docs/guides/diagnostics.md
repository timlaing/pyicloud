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

## Probing the services (`--probe`)

The service map only shows what Apple _advertises_. A host can be
advertised and reachable while the endpoint behind it has been withdrawn,
which is what happened to the Photos upload endpoint. `--probe` catches
that by additionally calling each service once with a read-only request:

```console
icloud doctor --probe
```

```text
                             Service probes
┌─────────────┬─────────────┬────────────────────────────────────┬──────┐
│ Status      │ Service     │ Read                               │ ms   │
├─────────────┼─────────────┼────────────────────────────────────┼──────┤
│ ok          │ account     │ reads storage usage                │ 537  │
│ ok          │ devices     │ refreshes Find My without locating │ 633  │
│ unavailable │ files       │ reads the Ubiquity root            │ 83   │
│ ok          │ reminders   │ reads the reminders sync cursor    │ 336  │
└─────────────┴─────────────┴────────────────────────────────────┴──────┘
```

It is opt-in because it makes one real request per service — roughly ten
seconds in total. Every probe is a GET or the service's own documented
refresh; none of them writes, and Find My is refreshed without asking your
devices to report their location.

Two outcomes are shown but do not fail the run, because neither is a
pyicloud defect: `unavailable` means Apple reports the service as
unavailable for your account (a Ubiquity library migrated to iCloud Drive,
for example), and `auth` means the session needs re-authenticating.
A service whose webservice key was already reported missing is marked
`skipped` rather than called.
