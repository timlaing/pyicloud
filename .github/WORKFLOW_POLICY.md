# Workflow policy

CI validates its event revision; Quality requires SonarQube, CodeQL and current-head CodeRabbit approval. Review requests wait for all reported checks. Automatic and incremental push reviews remain disabled. The original40-minute review window is preserved with one-minute polling.

`Review notifier policy` is written by a trusted default-branch metadata worker. It validates the notification structure and enforcement/receiver files without checking out or executing PR code. A same-named CheckRun cannot replace that commit status. Pinned action changes are checked against the official action repository. Security-policy logic changes need a maintainer-reviewed trusted baseline update.

After this PR merges, inspect `python3 .github/scripts/apply_workflow_policy.py`, then apply with `--apply --validated-head <full-PR-head-SHA>`. It verifies the deployed manifest before writing, selects only a main-only ruleset, preserves unrelated protections and workflow approval privileges, installs app-bound strict required checks, and enables the trusted policy/review workflows.

Privileged Sonar workers perform static source/report analysis only, with `sonar.sca.enabled=false` preventing implicit dependency/build-tool execution. Fork-triggered CI never receives the scanner token; the trusted receiver consumes validated source and coverage without running fork-selected commands. Existing dependency audits and Dependabot protection remain independent. Every configured coverage report must be present before scanning.

Settings activation enables replacement workflows first, then verifies successful hosted replacement checks on the explicit reviewed PR head before writing required-check rules. Keep existing protection until that proof exists; failed enablement or missing proof leaves the rules unchanged.

The repository owner bootstraps or recovers the notifier by inspecting the exact revision and dispatching `review-notifier-policy.yml` on that reviewed branch with its full `revision`. Current-head status acceptance verifies the actual successful workflow run and owner attestation. After the other checks succeed, a maintainer can request the CodeRabbit review; no new token is needed for this rollout.
