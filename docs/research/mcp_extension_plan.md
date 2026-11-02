# Optional MCP extension implementation plan

<!-- cspell:words ziir -->

Status: implementation roadmap. Documentation checked on 2026-11-02.
Idea suggested by **@ziir on Discord**. Implement in milestone commits and push
at the completion of each phase; do not claim release readiness before all gates.

## Objective and decisions

Expose selected PyiCloud capabilities through the Model Context Protocol (MCP)
without changing existing library or CLI contracts. Ship this as an optional
extension **inside the existing `pyicloud` distribution**, just like the CLI, not
as a separate package or service implementation.

Decisions:

- `pip install "pyicloud[mcp]"` installs the MCP extension dependencies.
- `pip install "pyicloud[cli]"` continues to install only CLI dependencies.
- `pip install "pyicloud[all]"` installs both supported user-facing extensions.
- Bare `pyicloud` remains usable without either optional dependency group.
- `[all]` means all user-facing extras, **not** development or documentation tools.
- Initial transport is local stdio, one explicitly configured account per process.
- Initial tools are read-only with respect to remote iCloud data. Authentication
  and local session persistence still have side effects, which must be documented.
- Password entry and Apple verification challenges happen outside MCP tool calls.
- Remote hosting, account multiplexing, and write tools are separate later phases.

## Scope and non-goals

The user subsequently authorized Contacts, Drive metadata, Notes metadata, Photos
metadata and Mail summaries in the same extension. These are explicit read-only
service opt-ins, not authorization for note/message bodies, downloads, writes or
remote hosting. Bounded metadata tools and scoped expiring handles cover this
expanded scope; full-text/global searches are not claimed where APIs lack them.

The first release provides authentication/capability status plus bounded calendar
and Reminders reads. Additional read adapters are admitted individually after
their behavior, privacy exposure, and service-specific limitations are tested.

Do not promise every PyiCloud method as a tool. Do not expose an arbitrary method
dispatcher, raw CloudKit queries, raw HTTP requests, or arbitrary URLs. Do not add
a second Apple transport, change public classes or exceptions, publish `py.typed`
incidentally, or weaken the released compatibility baseline.

No first-release mail bodies, attachments, photo downloads, Find My locations,
device actions, invitations, uploads, updates, or deletions. These involve wider
privacy or mutation risks and need explicit product decisions.

## Packaging and dependency design

### Installation matrix

| Install         | Runtime library | CLI | MCP | Development tooling |
| --------------- | --------------- | --- | --- | ------------------- |
| `pyicloud`      | Yes             | No  | No  | No                  |
| `pyicloud[cli]` | Yes             | Yes | No  | No                  |
| `pyicloud[mcp]` | Yes             | No  | Yes | No                  |
| `pyicloud[all]` | Yes             | Yes | Yes | No                  |
| `pyicloud[dev]` | Yes             | Yes | Yes | Yes                 |

Keep the existing development extra's CLI support and extend it to MCP so the
development environment can run the full test suite. Documentation dependencies
remain in `docs/requirements.txt`, not `[all]`.

Add `requirements_mcp.txt` as the single source of MCP-specific dependencies.
Use the official Python MCP SDK unless a compatibility spike demonstrates a
concrete reason not to. The SDK's PyPI metadata currently reports `mcp` 2.3.0 and
Python >=3.10; this is a discovery result, not a proposed permanent version pin.
Select and test the actual minimum and upper-bound policy during implementation.
SDK examples and naming differ across major versions: use the selected version's
documentation rather than mixing v1 FastMCP and v2 server APIs.

In `[tool.setuptools.dynamic]`, extend the existing file-based optional dependency
mapping:

```toml
[tool.setuptools.dynamic.optional-dependencies]
cli = { file = ["requirements_cli.txt"] }
mcp = { file = ["requirements_mcp.txt"] }
all = { file = ["requirements_cli.txt", "requirements_mcp.txt"] }
dev = { file = ["requirements_dev.txt", "requirements_cli.txt", "requirements_mcp.txt"] }
```

Replace the existing inline optional-dependency mapping with this table rather
than defining it twice. Do not use self-referential dependencies such as
`pyicloud[cli,mcp]` inside `[all]`.

Also:

1. Include `requirements_mcp.txt` in `requirements_all.txt` for development setup.
2. Run `python3 scripts/sync_prek_deps.py requirements_all.txt`; commit the resulting
   hook dependency synchronization, not a manually divergent dependency list.
3. Inspect sdist contents and any manifest rules so the new requirements file is
   present when building a wheel from the sdist.
4. Test resolution alongside runtime, CLI, and development dependencies, especially
   Pydantic and the SDK's asynchronous dependencies, at minimum and current versions.
5. Preserve Python 3.10 support for core and extensions; a higher SDK floor is a
   blocker to resolve, not permission to raise the project's Python requirement.
6. Ensure core-only imports do not load MCP, Typer, Click, or Rich.

### Entry point

Proposed console script: `icloud-mcp = "pyicloud.mcp.cmdline:main"`.

Console scripts cannot be reliably made available only when an extra is selected.
Keep the entry-point module dependency-free until after argument parsing, and
provide an actionable missing-extra error when serving without MCP dependencies:
`Install the MCP extension with pip install "pyicloud[mcp]".` Help and version
must work offline in a core-only installation. Do not add a Typer dependency just
to launch MCP; standard-library argument parsing is sufficient.

## Internal architecture

Proposed layout, subject to existing module conventions:

```text
pyicloud/mcp/
    __init__.py       # Lightweight; no server startup or optional SDK import
    cmdline.py        # Arguments, help/version, missing-extra diagnostics
    config.py         # Validated account/session/service allowlist configuration
    server.py         # SDK registration, lifecycle, stdio transport
    context.py        # Single session owner and serialized blocking operations
    models.py         # Explicit tool input/output contracts
    errors.py         # Sanitized service-to-tool error mapping
    tools/
        status.py
        calendar.py
        reminders.py
tests/mcp/
    ...              # Mirrored lifecycle, adapter, safety, and protocol tests
requirements_mcp.txt
docs/guides/mcp.md
```

MCP adapters use the existing `PyiCloudService` and public service methods. They
must not reuse CLI command handlers, terminal UI state, or private service internals.
Sharing a small neutral configuration helper with the CLI is acceptable only after
its ownership is clear and existing CLI behavior remains covered.

Keep SDK-specific registration separate from tool business logic. This permits
offline adapter tests and limits changes required for SDK migrations.

## Session lifecycle, authentication, and concurrency

1. Startup validates configuration without contacting Apple. Do not authenticate
   at import time or claim `PyiCloudService` default construction is offline.
2. The host or operator explicitly enables an account and a private session-state
   directory. Do not silently search arbitrary account directories or browser data.
3. Prefer established local authentication and saved state. If credentials are
   needed, obtain them through an explicit local credential mechanism, never tool
   arguments, process command-line passwords, server resource contents, or logs.
4. Perform interactive login/2FA in a separate operator workflow. Investigate
   reuse of supported CLI authentication first, without making `[mcp]` depend on
   `[cli]`; document an optional CLI bootstrap or provide an MCP-extra-local
   interactive setup command if needed.
5. During serving, challenges return a sanitized `authentication_required` or
   `verification_required` result. Never prompt on protocol stdin or automatically
   trigger repeated SMS, trust-device actions, or credential retries.
6. One server process owns its PyiCloud session. Serialize operations touching
   shared session/service state, including refresh and shutdown. Do not share one
   session across unconstrained executor threads.
7. Run blocking library operations off the asynchronous protocol loop using a
   bounded executor and a single operation lane. Bound queue depth and reject
   overload rather than spawning unlimited workers.
8. A timed-out/cancelled await does not necessarily stop the underlying HTTP work.
   Keep ownership locked until that work actually ends, enforce transport timeouts,
   and do not concurrently reuse or close its session. Discard late results safely.
9. Shutdown stops accepting work and drains or bounds outstanding work before
   releasing resources. Test abnormal host disconnects and restart behavior.

Session directories must be private, owned by the operator, and writable as
required by existing atomic persistence. Respect established symlink handling.
Do not imply that atomic saves provide cross-file transactions, power-loss
durability, or protection from competing processes/stale writers. Document that
the CLI and MCP server must not concurrently own the same saved-state directory.

## Initial tool contracts

Names below are proposed MCP contracts, not existing library methods. Verify each
adapter against actual service APIs before freezing the public MCP surface.

| Tool                     | Purpose                                                   | Limits and identity                                                             |
| ------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `icloud_status`          | Report local configuration and known authentication state | No implicit login/network probe; no tokens, account email, or saved-state paths |
| `icloud_capabilities`    | Describe configured tool/service availability             | Distinguish configured, verified available, unavailable, and unknown            |
| `icloud_calendar_list`   | List calendars                                            | Bounded results and stable calendar identifiers                                 |
| `icloud_calendar_events` | Read events in an explicit time interval                  | Bounded date span, result count, and response bytes                             |
| `icloud_reminders_lists` | Enumerate accessible private/shared lists                 | Preserve scope, zone name, and owner identity in opaque route handles           |
| `icloud_reminders_list`  | Read reminders in one explicitly selected list            | Bound results; preserve completion filters and precise list routing             |

Proposed defaults: 50 records, hard maximum 200 records per response, calendar
range maximum 31 days, response budget 256 KiB, queue maximum 8 operations, and
configurable bounded request timeouts. These are starting product choices, not
assertions about Apple limits; validate them in the spike and document final values.

Return structured output with explicit schemas: records, `complete`, and optional
`next_cursor`/truncation metadata. Never claim a partial snapshot is complete.
Provide pagination only where the underlying API genuinely supports it. Otherwise
return explicit bounded/truncated results, not invented resumable cursors.

If cursors or route handles are introduced, bind them to account, service, filters,
scope, zone owner, and expiry; validate them server-side. Reject foreign/stale
handles and ambiguous string IDs rather than falling back to a private zone.
Do not expose raw signed Apple URLs, authentication headers, or CloudKit payloads.

Tool annotations should accurately describe remote read behavior and external
network access. They are client hints, **not authorization controls**. Explain
local authentication/session writes even on otherwise read-only data tools.

## Privacy and security model

- The trusted local operator chooses the exposed services using an allowlist.
  Register only enabled tools; do not allow a model to enable additional services.
- Treat event/reminder titles, descriptions, filenames, and other Apple content as
  untrusted data. Never execute instructions, fetch embedded links, evaluate code,
  or change server policy based on returned content.
- Read-only does not mean non-sensitive. Minimize fields, default to metadata,
  exclude unnecessary attendee/contact details, and document that results become
  visible to the connected host and potentially its model/provider.
- Validate schemas, ranges, identifiers, cursor binding, and output budgets before
  requests or serialization. Do not accept arbitrary filesystem paths from tools.
- Stdout is exclusively MCP protocol traffic, including startup and shutdown.
  Send diagnostics to stderr through redacted logging, not `print()`.
- Never log passwords, verification codes, session cookies/tokens, signed URL
  queries, full records, tool arguments containing private data, or raw responses.
- Map errors to stable safe categories: authentication/verification required,
  unavailable service, invalid argument, ambiguous route, expired cursor, rate
  limited, timeout, and upstream failure. Do not leak traceback details or payloads.
- Respect upstream retry guidance where available. Use bounded retries only for
  safe reads, no unbounded polling, and no automatic retries of future mutations.
- Do not introduce HTTP listeners, browser authentication redirects, telemetry, or
  automatic account discovery in the initial release.

## Future writes and remote transports

Write tools require a separate reviewed milestone. Explicit operator opt-in and
service/action allowlists are mandatory. Client confirmation and MCP annotations
alone are insufficient. Design previews and server-enforced authorization tied to
the exact account, action, target route, payload, and expiry; a model-generated
`confirm=true` must not count as independent human approval.

Account for partial failures, revision conflicts, idempotency, and retries before
enabling creation, completion updates, or deletion. Do not promise atomic
transactions unsupported by Apple. Destructive operations remain disabled by default.

A remote Streamable HTTP transport also needs a separate threat model and release
decision: transport authentication/authorization, TLS, origin validation, tenant
isolation, secret storage, deployment limits, and session ownership. Apple account
authentication is not authentication of the MCP caller. No public unauthenticated
listener is acceptable; stdio MVP does not establish remote-hosting readiness.

## Validation and compatibility gates

Use existing socket/filesystem protections for all pytest tests. Synthetic accounts,
mock transports, and approved fixture loaders only; no Apple calls in CI. Protocol
tests should use SDK in-memory transports where possible and tightly bounded
subprocess stdio tests where necessary.

Required tests:

- Core-only imports and offline help/version without MCP/CLI dependencies.
- Configuration validation, lazy startup, missing saved state, challenges, expired
  authentication, unavailable services, and sanitized error conversion.
- Single session ownership, serialized calls, queue limits, timeouts, cancellation,
  late results, shutdown, and restart behavior.
- Exact stdio framing: no stdout contamination from imports, logs, or errors.
- Tool discovery, schema validation, structured output, limits, truncation, invalid
  cursors/handles, private/shared ID collisions, and precise route retention.
- Credential/private-content redaction and rejection of arbitrary URL/path access.
- Host-facing MCP compatibility with at least two maintained clients or test
  harnesses against the selected protocol/SDK version.

Build wheels and source distributions; test installed packages outside the checkout with inherited
Python path overrides removed. Extend `scripts/check/installed.py` with optional
MCP checks and verify the entry point and a synthetic protocol interaction.
Test **core, cli, mcp, and all** independently: the CLI-only environment must not
accidentally depend on MCP, and MCP-only must not accidentally depend on CLI.
Check `[all]` exposes both extension entry points and its metadata requirements
equal the union of `[cli]` and `[mcp]` after normalizing duplicates/markers.

Run minimum/current dependency combinations and supported CPython 3.10–3.15.
Extend the minimum-dependency gate for optional extensions explicitly instead of
assuming the runtime-only constraints cover the SDK. A dependency resolution
failure or unsupported Python version blocks release.

All hooks, strict typing, Sonar quality gates, aggregate coverage, and strictly
above 90% per-file combined line/branch coverage remain mandatory. No new coverage
exclusions. Retain the released core/CLI baseline; add MCP contracts separately so
new optional exports cannot silently rewrite established compatibility evidence.

## Documentation and release deliverables

Publish `docs/guides/mcp.md` and register it in `mkdocs.yml`. Include installation
for `[mcp]`/`[all]`, POSIX/Windows executable paths, local-host configuration,
credential bootstrap and 2FA recovery, service allowlists, schemas and limits,
privacy exposure, shared-list routing, stderr troubleshooting, and safe shutdown.

Update README installation choices, contributor setup, package-layout documentation,
and the top `[Unreleased]` changelog. This research plan remains unpublished by
design; user-facing instructions belong in the registered guide. Version remains
derived from git tags through setuptools-scm, not a hand-edited constant.

## Implementation sequence and exit criteria

1. **Compatibility spike:** select SDK/minimum versions; demonstrate Python 3.10
   and 3.15 resolution, an offline synthetic stdio exchange, structured output,
   and cancellation/session ownership. Stop if extension isolation cannot hold.
2. **Packaging foundation:** add `[mcp]`, `[all]`, requirements integration, lazy
   entry point, and four-way installed-package tests. No Apple tools yet.
3. **Lifecycle and security:** configuration, operator authentication workflow,
   serialized execution, redaction, errors, allowlists, and limits with regressions.
4. **Read-only adapters:** status/capabilities, calendar, and Reminders separately;
   prove scope/owner routing, filtering, limits, and error behavior for each.
5. **Release qualification:** client interoperability, wheel/sdist/minimum matrix,
   strict documentation build, all quality gates, and published security caveats.
6. **Optional operator live checks:** separate, explicit authorization for a
   synthetic account; no credentials/content committed or copied to logs. Record
   what was exercised without claiming offline tests prove live compatibility.

Use focused PRs with reviewed, testable boundaries. The MVP is complete only when
all four install modes work independently, local authentication recovery is
documented, read-only tools satisfy limits and routing guarantees, and every gate
passes on the release candidate. Track remote transport and write support as
separate proposals, not unfinished promises in the MVP.

## Effort and open decisions

Allow approximately 1–3 engineering days for the compatibility spike and a narrow
prototype, then 1–2 weeks for a release-quality read-only extension. Client quirks,
Apple authentication behavior, and optional dependency constraints can extend this;
these estimates exclude remote hosting, broad service coverage, and write tools.

Before implementation, confirm SDK version/bounds, operator credential/bootstrap
workflow, initial service allowlist, final limits, supported client matrix, and
whether resource/prompt primitives add value beyond tools. Do not expose account
content through automatically discovered resources or implicit startup reads.

## References

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [SDK server hosting documentation](https://py.sdk.modelcontextprotocol.io/run/)
- [Model Context Protocol specification](https://modelcontextprotocol.io/specification)
- Repository: `pyproject.toml`, `requirements_cli.txt`, `requirements_all.txt`,
  `scripts/check/installed.py`, and the released compatibility baseline.
