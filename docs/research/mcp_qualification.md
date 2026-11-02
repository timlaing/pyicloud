# MCP read-only extension qualification

<!-- cspell:words ziir -->

Status: local implementation qualification complete; hosted PR quality gates and
live Apple checks are separate evidence. Roadmap: #495.

Implemented phases are committed separately on `feature/mcp-extension`:

- SDK spike and roadmap, with attribution to @ziir on Discord.
- Optional `mcp`/`all` packaging, lazy entry point and isolated installation matrix.
- Serialized saved-session ownership, bounded transport/queue and cancellation.
- Read-only Calendar/Reminders tools, minimized fields, opaque shared-list routes,
  safe errors, operator guide and protocol checks.

Local qualification on 2026-11-02:

- Full offline suite: 4,722 tests and 69 subtests, 98.36% combined coverage.
- Every measured source file strictly exceeds 90% combined line/branch coverage.
- All hooks pass; no coverage exclusions or network/filesystem guard weakening.
- Wheel built from source distribution with optional requirements included.
- Actual Python 3.10.22 and 3.15.2: independent core, CLI, MCP and all installations
  pass public-baseline checks and optional entry-point/protocol checks.
- Python 3.10 minimum runtime floors plus MCP 2.3.0 and CLI minimum dependencies
  resolve, install and pass the installed checks.
- `all` metadata is exactly the union of `cli` and `mcp` dependencies.
- Independent raw JSON-RPC and official SDK-client stdio harnesses initialize the
  installed server and call offline tools successfully. This is not a claim of
  desktop-host certification.
- Synthetic service tests cover calendar range/filtering, output budgets, private
  versus shared ID collisions, handle expiration/refresh, challenge errors,
  overload, late results and shutdown ownership.

No live Apple credentials were read and no account requests were performed in
qualification. Host-specific configuration and Apple's undocumented behavior may
still vary. Read-only does not make exposed account data non-sensitive.

Deferred by design: write tools, remote hosting, multiple accounts per process,
arbitrary URL/path access, contact/mail/photo/device adapters and typing publication.
They are not enabled implicitly and are not prerequisites for this local MVP.

Shutdown has a bounded drain await and queues closure after active work. Bodies
use identity encoding, per-byte deadline checks and a 4 MiB bound; non-HTTPS
requests are rejected. Transport/429 failures are intercepted before the public
saved-session probe can classify them as credential failures. CLI parser errors
never echo unsupported or malformed option values. A caller timeout is not a hard
kill of custom worker code; see the operator guide's process-exit caveats. Saved
sessions must not be concurrently owned by CLI/server.

Follow-up qualification covers body-read transport exceptions (including wrapped
urllib3 timeouts and truncated chunks), sanitized session cleanup failures, and
Calendar's inclusive end-date semantics. Transport dispatch and body policy have
separate responsibilities to keep their cognitive complexity within quality gates.

Configured stdio checks now run the real serialized worker against synthetic
Calendar/Reminders services with outbound IPv4/IPv6 sockets blocked. On installed
wheels for Python 3.10 and 3.15 they verify inclusive date bounds, colliding
private/shared routes, minimized wire fields, authentication challenge results,
availability observations, and exact-once cleanup without private stderr output.
These checks are part of the installed-extra CI matrix, not live account tests.
