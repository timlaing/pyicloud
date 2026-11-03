# MCP write-support milestones

Roadmap: #495. User authorized write-support implementation after read work.
This authorizes code and synthetic tests, not actual account mutations.

## Verified facade inventory

| Service   | Existing public operations                                                              | Missing prerequisites                                                                                                               |
| --------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Calendar  | `add_calendar`, `remove_calendar`, `add_event`, `remove_event`                          | No dedicated update API; establish revision-safe updates from verified wire evidence rather than assuming `add_event` is an upsert. |
| Reminders | `create`, `update`, `delete` and linked-record mutations                                | Fresh model/revision checks and precise private/shared list routing.                                                                |
| Contacts  | Reads only                                                                              | A tested library mutation API and verified changeset contracts.                                                                     |
| Drive     | Folder creation, rename, move, trash, recover, permanent delete, upload                 | Fresh node/share context, etags, scoped destinations, and bounded upload source handling.                                           |
| Notes     | Reads/export/render only                                                                | A tested writer preserving encrypted body/CRDT, record revisions, folder and attachment semantics.                                  |
| Photos    | Album creation/rename/delete, asset delete and upload                                   | Reject virtual/smart collections; keep library ownership and revision checks.                                                       |
| Mail      | Draft creation/edit/discard, sender discovery, folders, moves, send, permanent deletion | Retain verified typed mailbox/draft handles and uncertain-write reconciliation.                                                     |

No unsupported operation may claim success or guess an Apple endpoint. Missing
Contacts/Notes/Calendar-update contracts are explicit blockers to those adapters.

## Approval foundation

`pyicloud.mcp.authorization` provides an internal fail-closed ledger with an empty
action allowlist by default. Previews snapshot canonical payloads; HMAC fingerprints
bind account, route, revision, action and target without exposing private bindings.
Limits are 64 pending requests, five-minute expiry, and 64 KiB payloads by default.
Consumption happens before dispatch and is single-use, including stale requests.
Missing revisions are refused by the foundation. Creation adapters must establish
the verified parent/container revision rather than inventing an object version.

This foundation exposes **no MCP approval or mutation tool** and executes nothing.
`approve_from_operator` is a trusted local hook, not evidence of human presence.
Before live tool integration, implement a separately trusted operator approval
channel; a model-generated `confirm=true` or a model-callable approval tool is
not acceptable. Describe the OS/host trust boundary explicitly. Do not claim
protection against privileged local code that can read credentials or alter the
server itself.

The local operator mailbox and standalone command are now implemented:
`icloud-mcp --approval-dir /private/approval-directory --approve-request REQUEST_ID`.
It reads the exact JSON-escaped preview and requires typing `APPROVE REQUEST_ID`
on a separate interactive terminal. No piped `--yes`, model boolean or MCP approval
tool exists. Directory/files must be owned by the operator and private; descriptor-
relative POSIX opens reject symlinks and records are size/count bounded. Receipt
consumption still passes through the HMAC-bound ledger and its expiry/revision rules.

This channel assumes a trusted OS/host and that the model has **no filesystem/shell
access to the approval directory through other tools**. It cannot distinguish a
human from privileged local code able to create a terminal or edit that directory;
do not claim hardware-backed presence. Windows live-write approval is not supported
until a reviewed native channel exists. Server publication/acceptance wiring and
live mutation tool registration are still outstanding; read-only remains default.

Runtime Reminders publication/acceptance wiring is now implemented behind explicit
`--write-action` flags plus `--service reminders` and `--approval-dir`. Only preview
and execute tools are exposed; no approval tool exists. Mailbox decisions and ledger
consumption execute on the same owner lane, and operator descriptors drain after
active work. All other writers/API-gap milestones remain incomplete.

The next verified runtime action is `drive.rename`: private discovered nodes only,
fresh account/ID/zone/etag binding, separate operator receipt, single-use dispatch
and post-write name verification. Shared mutation contracts, root/virtual targets
and other Drive writes remain unsupported rather than guessed. Rename leaf/base
name semantics preserve the existing extension metadata.

## Sequencing and validation

Verified Calendar `add_event`/`remove_event` now have runtime create/delete adapters.
Creation fixes its GUID during preview and checks the fetched post-state; deletion
uses the fresh etag/ifMatch path and requires follow-up absence proof. Account,
calendar and event revisions remain part of single-use approval. No update/upsert
endpoint or high-risk invitation/recurrence semantics were invented.

The first executable adapter module, `pyicloud.mcp.reminder_writes`, now supports
verified Reminders create/update/delete callbacks with synthetic tests. Preview
paths do not mutate; scoped list/reminder state is re-read before approval
consumption, account/route/revision changes fail closed, and uncertain callbacks
retire the approval before reporting a sanitized failure. It is intentionally
**not registered as a live MCP tool** until the independent operator channel is
implemented. The other service writers and documented API gaps remain outstanding.

1. Finish independent read validation; defer CodeRabbit until implementation end.
2. Validate approval snapshots, expiration, replay, account/route mismatch and
   revision changes using synthetic data. No runtime writes enabled at this phase.
3. Wire independent operator preview/approval and per-action allowlists.
4. Implement existing supported Reminders/Calendar actions first; never dispatch
   mutations from the preview path or retry uncertain acknowledgements.
5. Add Drive, Photos and Mail adapters at verified boundaries. Sending, uploads
   and permanent deletion need separate high-risk action selection/approval.
6. Add missing Contacts/Notes library contracts with offline wire fixtures before
   their MCP adapters. Preserve public imports, signatures and generated sources.
7. Run full suites sequentially, strict per-file coverage, all hooks, installed
   package checks and docs; commit/push relevant milestones. Final hosted gates
   remain required. No real-account tests without separately approved targets.
