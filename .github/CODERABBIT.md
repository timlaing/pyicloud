# Reviews and dependency auto-merge

CodeRabbit approvals are enabled explicitly. Configuration inheritance preserves
repository and organization preferences. Automatic and incremental reviews stay
disabled: **CodeRabbit after checks** requests reviews after successful CI.
Dependabot is excluded from that workflow and does not depend on CodeRabbit approval.

The gate checks the current PR head and merge commit, reported jobs and commit
statuses, workflow runs, and configured and live required check names. Failed,
pending, neutral, cancelled, or missing required checks withhold reviews.
Conditional skipped jobs/workflows do not block unless their check is required.
A 90-second settling window allows chained CI to start. Per-commit markers avoid
repeated requests; the gate rechecks after workflows and every 15 minutes.

## Dependabot

**Dependabot Auto Merge** uses the built-in GitHub Actions token. No separate
GitHub App, personal token, or added secret is needed.

A PR branch update runs the official metadata action. Only patch/minor updates
from same-repository Dependabot PRs receive a commit-specific eligibility marker.
Major updates, drafts, human PRs, forks, stale markers, and untrusted comments do
not qualify. Existing Dependabot PRs need a branch update or reopening once after
this workflow is installed.

After CI completes, the worker rechecks reported checks, statuses, workflow runs,
and required check names, with the same settling window. A scheduled fallback
handles external checks. It approves the exact head using `github-actions[bot]`,
then merges with an expected-head SHA or queues GitHub auto-merge. No required CI
or branch protection is bypassed. With no CI checks configured, eligible updates
can merge after the settling window and any remaining GitHub branch requirements.

Repository auto-merge and Actions approval permission must be enabled. Built-in
Actions approvals need an ordinary approval policy; latest-push or code-owner
approval requirements can leave a PR awaiting another reviewer. A failed approval
request does not prevent queueing auto-merge. GitHub still enforces its rules.
The worker reads trusted inline code only and never checks out PR code.
