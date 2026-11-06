# MCP extension

The optional local Model Context Protocol (MCP) extension is under development.
Its current foundation exposes **local status and capabilities only**, without Apple authentication
or account data tools. Calendar and Reminders adapters are not yet available.

Install it with `pip install "pyicloud[mcp]"`, or install both user-facing
extensions with `pip install "pyicloud[all]"`. The `all` extra does not install
development tooling. The standalone CLI extra remains `pyicloud[cli]`.

Use the interpreter/executable from your installation environment:

```console
.venv/bin/icloud-mcp --help
.venv/bin/icloud-mcp --version
.venv/bin/icloud-mcp
```

On Windows use `.venv\Scripts\icloud-mcp.exe`. With no arguments the server
communicates over stdin/stdout using MCP; it is not an interactive terminal shell.
Configure your host to launch that executable with no arguments. Do not supply
passwords or verification codes as host arguments or tool inputs.

`icloud_status` returns structured local status and performs no Apple requests.
`icloud_capabilities` reports the selected services, availability state and fixed
read-only limits, also without contacting Apple.
No listener, write tools, account discovery, or implicit authentication is enabled.
Stdout is reserved for protocol traffic; diagnostics belong on stderr.

Core-only installations can use offline help/version, but serving requires the
`mcp` extra. Python 3.10–3.15 remain the supported interpreter range. See the
[compatibility guarantees](compatibility.md) for the distinction between offline
validation and live Apple-service compatibility.

## Internal approval lifecycle

The foundation does not expose runtime writes. Its private operator channel keeps
separate bounded preview and receipt budgets, so a full preview queue cannot
prevent the operator from approving an existing request. Accepted receipts bind
the exact displayed action, target, payload, digest, risk tier and deadline to the live
ledger; reconstructed or edited displays cannot authorize another operation.

Published records include a wall-clock deadline under the ledger's maximum
600-second lifetime; in-memory authorization still expires against monotonic
time. This keeps persisted records comparable across process restarts and reboots.
At capacity, cleanup removes only validated private regular
files whose deadline has elapsed or cannot belong to a current live lifetime.
Live files and unknown or unsafe entries are preserved. Malformed private regular
records are reclaimed only after the full 600-second maximum lifetime has elapsed
since modification. Failed writes remove only the inode they created, never a
replacement. A deliberate
`DECLINE REQUEST_ID` discards its pending record; a mistyped approval phrase does
not. Stored records without the new deadline/display-binding fields cannot be
accepted: an operator must archive legacy records and create fresh previews
before enabling the later runtime integration. No old receipt is reused after
an operator migration.

Mailbox changes use a shared, non-blocking process lock; a competing operation
fails with `approval_mailbox_busy` rather than retrying a mutation. The operator
input wait does not hold this lock. After input, the mailbox rechecks the exact
displayed record, its inode and its deadline before recording a decision.
Missing, boolean or non-finite deadlines are invalid records, never live
approvals; aged private invalid records follow the same bounded cleanup policy.
