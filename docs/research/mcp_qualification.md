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

- Full offline suite: 4,704 tests and 69 subtests, 98.36% combined coverage.
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

Shutdown waits for the active worker before closing the session. A caller timeout
is not a hard kill of running HTTP work; see the operator guide's inactivity and
process-exit caveats. Saved sessions must not be concurrently owned by CLI/server.
