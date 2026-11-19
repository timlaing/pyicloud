# Reviews and dependency auto-merge

CodeRabbit automatic and incremental reviews are disabled. CI validates the event revision. The Quality workflow runs SonarQube and CodeQL after successful CI, then requests CodeRabbit review. A successful Quality result requires all three stages and CodeRabbit approval on the exact current head. A successful CodeRabbit check alone is not an approval.

The review worker never submits an approval as GitHub Actions. Keep native required reviews and stale-approval dismissal enabled. Resolving findings requires a fresh current-head CodeRabbit approval; workflow tokens do not replace that review.

Dependabot updates use the repository's existing dependency workflow and remain subject to native branch protection. Never bypass required validation or approval to merge a dependency update.

Checkout-free review event notifications cause the trusted Quality worker to re-evaluate withdrawn approvals and draft readiness. External reported checks must succeed before a review request is posted.
