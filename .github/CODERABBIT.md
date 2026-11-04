# Reviews after CI

CodeRabbit automatic and incremental reviews are disabled in `.coderabbit.yaml`.
The **CodeRabbit after checks** workflow checks the current PR head and merge
commit before requesting CodeRabbit. It checks all reported jobs,
commit statuses, workflow runs, and required check names. Failed, pending,
cancelled, neutral, or missing required checks withhold requests. Conditional
jobs that did not run are ignored, but a skipped required job blocks reviews.
Repositories with no checks do not request reviews. Draft PRs are excluded.

The workflow runs after other workflows and every 15 minutes to catch external
checks. It waits at least 90 seconds after CI activity to allow chained workflows
to start, and records requests per commit to avoid repeats.
It uses trusted inline code and never executes code from a PR.

## Dependabot auto-merge

The metadata-only workflow handles same-repository Dependabot PRs for patch and
minor updates. Major or unknown update types are withheld. Required CI checks
must be configured on the base branch before it approves and queues squash
auto-merge. It never bypasses branch protection. Enable repository auto-merge
and allow GitHub Actions to approve pull requests in repository settings.
