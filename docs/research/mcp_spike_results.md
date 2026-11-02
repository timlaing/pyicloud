# MCP compatibility spike

<!-- cspell:words mcpserver ziir -->

Roadmap: #495. Idea: @ziir on Discord.

## Evidence (2026-11-02)

- Official SDK `mcp==2.3.0` installs alongside all current development/runtime/CLI
  dependencies on CPython 3.14.9.
- Dependency resolution succeeds for Python 3.10 and 3.15 with the same SDK and
  existing `requirements_all.txt`. These are resolution checks, not interpreter
  execution tests; the installed-package CI matrix must still exercise both.
- A subprocess stdio server using `mcp.server.mcpserver.MCPServer` completes
  initialization and a synthetic tool call returning structured JSON. No Apple
  service or real credentials are constructed.
- SDK v2 server uses `run(transport="stdio")`; do not use v1-only examples.

## Implementation choices and remaining gates

Select `mcp>=2.3.0,<3` for the extension, with separate requirements metadata and
lazy imports. `[all]` is the union of the CLI and MCP extras, not development tools.
The initial server must start without authenticating or probing Apple.

The spike does not establish cancellation safety, live Apple compatibility, host
interoperability, or release readiness. Serialized session ownership, timeout/drain
behavior, protocol regression tests, and the four installed-extra combinations
remain mandatory implementation gates. No write tools or remote listener enabled.
