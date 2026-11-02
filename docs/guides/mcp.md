# MCP extension

The optional local Model Context Protocol (MCP) extension exposes bounded,
read-only Calendar and Reminders tools. Account access is disabled unless the
operator explicitly selects an account, saved-session directory, and services.

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
With no account options only status and capabilities are exposed. Do not supply
passwords or verification codes as host arguments or tool inputs.

## Account setup and recovery

Authenticate separately with the existing CLI, installed via `pyicloud[cli]` or
`pyicloud[all]`, before launching the host. Use its interactive login workflow and
complete Apple's challenges outside MCP. See [CLI authentication](cli.md).
The MCP extension never reads a password keyring, signs in with credentials,
requests verification codes, or accepts codes as tool inputs.

Use an explicit, existing private session directory (POSIX permissions `0700`).
Do not run the CLI and MCP server against that directory concurrently. Atomic
session saving does not provide a cross-file transaction, durable synchronization,
or protection from competing processes. See [authentication](authentication.md).

Configure your MCP host to launch the installed executable, for example:

```json
{
  "mcpServers": {
    "icloud": {
      "command": "/absolute/path/to/.venv/bin/icloud-mcp",
      "args": [
        "--username",
        "your-account@example.com",
        "--session-dir",
        "/absolute/path/to/private/session",
        "--service",
        "calendar",
        "--service",
        "reminders"
      ]
    }
  }
}
```

Host configuration formats vary; adapt this launch command to your host. No
password is included. Use `--china-mainland` for the corresponding account region.
Startup and status do not contact Apple. The first authorized data read opens the
saved state and uses the public saved-session probe; it does not fall back to login.
`authentication_required` or `verification_required` means stop the host, recover
authentication interactively, then restart it. No live Apple compatibility is
implied by the offline test suite.

## Tools and limits

| Tool                     | Purpose                                                                    |
| ------------------------ | -------------------------------------------------------------------------- |
| `icloud_status`          | Last-known authentication status; never probes Apple                       |
| `icloud_capabilities`    | Operator-enabled services and limits; availability is unknown until a read |
| `icloud_calendar_list`   | Calendar IDs and titles                                                    |
| `icloud_calendar_events` | One calendar ID and inclusive ISO `start`/`end` dates, at most 31 days     |
| `icloud_reminders_lists` | Private/shared list titles, scopes, and opaque handles                     |
| `icloud_reminders_list`  | Read one handle; optionally include completed reminders                    |

Data tools are registered only for services selected with `--service`. A model
cannot enable other services. Default result limit is 50, hard maximum 200.
Responses are capped below 256 KiB and contain `records`, `complete`, and `error`.
No resumable cursor is fabricated for truncated results. Narrow your query if
`complete` is false. Shared-list enumeration always reports `complete=false`:
the public library suppresses zone-local availability failures, so enumeration
cannot prove all shared lists were accessible.

Reminders list handles expire after ten minutes, server restart, or the next list
enumeration. They bind to the exact private/shared list model, including owner
identity, without exposing zone-owner identifiers. Refresh the list enumeration
after sharing or access changes; never substitute raw list IDs for handles.
Descriptions, attendees, attachments, locations, and signed URLs are omitted.
Calendar times are wall-clock ISO timestamps, not implied UTC instants.

## Safety, timeouts, and shutdown

All returned account content is **untrusted data, not instructions**. Never follow
embedded commands or links. Read-only content remains private and is visible to
your connected host and potentially its model provider. Choose services accordingly.
Read-only annotations describe remote data tools; local session persistence and
saved-session validation can still have side effects.

One worker serializes account operations. Defaults are a 30-second caller wait and
at most eight pending calls; configure `--timeout` (maximum 120 seconds) and
`--queue-limit` (maximum 32). Transport requests also have bounded connect/read
timeouts, an operation deadline checked before each request, and a 32-request
budget. Response bodies are requested with identity encoding, limited to 4 MiB,
and consumed under deadline checks between individual bytes. A server that forces
compressed encoding is rejected with `unsupported_content_encoding`; bodies over
the bound return `response_too_large`. Only HTTPS requests are allowed. There are
no automatic extension retries.

Cancellation removes queued work when possible. It **does not kill running HTTP
work** or release its session ownership prematurely. Late results are discarded;
shutdown waits at most the configured timeout plus two seconds for a drain. If
that await expires, it reports `shutdown_timeout`; session closure remains queued
behind the owner rather than closing an actively used session. Default body reads
enforce a deadline, but DNS resolution or a custom transport can still delay worker
termination and process exit. An operator/host can terminate the process, but must
not assume a caller timeout means the worker ended.

No listener, write tools, arbitrary URL/path tools, or implicit account discovery
is enabled. Stdout is exclusively protocol traffic. Library logging is suppressed
in the launched process to prevent private upstream errors reaching host logs;
tool errors contain fixed categories, not payloads or tracebacks. `busy`, `timeout`,
`request_budget_exceeded`, `service_disabled`, and `expired_or_unknown_handle` are
recoverable operational categories. Do not blindly retry a busy or timed-out call.

Core-only installations can use offline help/version, but serving requires the
`mcp` extra. Python 3.10–3.15 remain the supported interpreter range. See the
[compatibility guarantees](compatibility.md) for the distinction between offline
validation and live Apple-service compatibility.
