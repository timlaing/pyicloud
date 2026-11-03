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

- Full offline suite after service expansion: 4,738 tests and 69 subtests,
  98.37% combined coverage.
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

Excluded by design: remote hosting, multiple accounts per process, arbitrary
URL/path access, device actions and typing publication. The user subsequently
authorized separately opted-in bodies/downloads and write implementation; metadata
defaults remain unchanged and live mutations are not enabled by the approval foundation.
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

The authorized expansion adds Contacts, Drive, Notes, Photos and Mail metadata
opt-ins. Configured stdio checks exercise all seven services, including explicit
contact detail fields, recent-title-only Notes search, opaque photo-library/album
routes and typed subject-only mailbox queries. All eight isolated install modes
on Python 3.10.22/3.15.2 pass again with the expanded fixture; no outbound Apple
requests or real credentials are used. The transport refuses PCS device-consent
negotiation rather than triggering device notifications in protected account modes.

Installed content qualification also runs an explicit content-enabled stdio process.
The synthetic fixture verifies note plaintext, fresh typed MIME mail content,
header/URL minimization, and two-chunk Drive/Photos assembly with stable ETags and
If-Match. All eight isolated install combinations on Python 3.10/3.15 pass after
these checks were added. Networking remains denied; no actual data is downloaded.
The latest full offline suite after internal Reminders write adapters is 4,779
tests plus 69 subtests, 98.35% combined coverage, with every measured source file
strictly above 90%. These were earlier milestone counts, not final write qualification.

The operator mailbox and opt-in Reminders/Drive-rename/Calendar create-delete runtime
gate are implemented. The installed stdio fixture now verifies an independently
simulated terminal decision: a missing receipt denies execution, an exact receipt
permits exactly one synthetic callback, and replay is rejected. Mutation/cleanup
markers on stderr establish the callback count without logging private fields.
All eight isolated install modes on actual Python 3.10.22/3.15.2 pass with the new
gate, content and resumed-download checks. No real account, approval or write was
performed. The latest full offline suite is 4,820 tests plus 69 subtests, 98.34%
combined coverage, with strict per-file coverage and all hooks passing. Remaining
writers and unverified API gaps are still unfinished; this is not final release
qualification or a claim of hardware-backed operator presence.

The installed stdio fixture also exercises `mail.create_draft` through the shared
private receipt channel. Both a successful exact-body case and a deliberately
different post-save body first deny execution without a receipt; each simulated
terminal approval dispatches exactly one fake save. The different-body result is
`mutation_uncertain`, returns no success identity, and cannot replay its consumed
approval. Independent literal escaped HTML in the synthetic facade checks the
body conversion rather than reusing the verifier to generate expectations. Stderr
markers count two total Mail saves and one cleanup, with recipient/body data absent.
The fixture has no sending or upload methods, and outbound sockets remain blocked.
This is installed protocol qualification, not live Apple Mail wire validation.
