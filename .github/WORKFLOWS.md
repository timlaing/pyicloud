# Repository workflows

Seven workflows implement the repository policy:

| File                    | Purpose                                                                                                                                                                                             |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ci.yml`                | Tests, per-file coverage, package installation, prek, docs, Actions linting, SHA pins, HA compatibility, and PR maintainer editing.                                                                 |
| `sonar.yml`             | Safely import successful CI coverage, pass Sonar's quality gate and CodeQL, then request and wait for CodeRabbit approval. Publish the `Quality` commit status.                                     |
| `approve-workflows.yml` | Approve blocked CI for collaborators, same-repository Dependabot, and authors with a previously merged PR to this fork.                                                                             |
| `dependabot.yml`        | After successful Quality, approve the exact Dependabot head and merge or enable native auto-merge. Includes major updates.                                                                          |
| `publish.yml`           | Update the draft and publish validated main commits to TestPyPI; require successful main Quality and rerun CI on release tags before publishing stable releases to PyPI or prereleases to TestPyPI. |
| `labels.yml`            | Label issues and PRs using trusted default-branch code.                                                                                                                                             |
| `stale.yml`             | Check issues daily at 01:30 UTC, mark after 60 inactive days, and close after another 14; exempt pinned, security, and roadmap issues.                                                              |

## Validation and review

CI checks run for every PR to `main` and push to `main`. A successful CI run
is the single prerequisite for Quality, which runs Sonar and CodeQL before
requesting CodeRabbit. CodeQL uploads run in this trusted follow-up so squash
merges authored by Dependabot do not lose upload permissions. Quality consumes
coverage only from that run, with isolated Python, bounded XML validation,
symlink rejection, and scanner-only Sonar credentials. CodeRabbit is requested
only after CI, Sonar, and CodeQL pass, including on Dependabot PRs. The worker waits up
to 40 minutes for CodeRabbit's approval of the exact head, and fails if the PR
changes. After resolving review findings, rerun Quality if needed. Failed requests can
retry up to three times per revision; an active request is deduplicated, and
an abandoned request expires after 40 minutes.

Main commits must originate from a merged PR approved by CodeRabbit on its final
head. Independent CodeRabbit reviews of direct main pushes are not supported;
unreviewed direct pushes fail Quality and cannot publish to TestPyPI.

Privileged workflows check out only default-branch policy code, except the Sonar
and CodeQL scanners' explicitly isolated source checkouts. No PR script runs with approval,
merge, or publishing credentials. Workflow-run provenance is resolved back to
its original CI run; the default-branch SHA of the follow-up workflow is never
substituted for the tested revision.

## Repository settings when deploying these changes

- Enable Actions and auto-merge; allow Actions to create and approve PRs.
- Keep manual approval for first-time contributors. Automatic approval recognises
  merged PRs to this repository, not contributions only to another repository.
- Keep `.coderabbit.yaml` automatic reviews disabled and automatic approvals
  enabled. Disable author approval commands and keep the maximum merge risk
  configured so approval requires a completed assessment of the current commit.
  The `critical` limit permits every risk category. Install/enable the CodeRabbit
  app for this fork.
- Require `CI passed` and the `Quality` commit status on `main`, bound to the
  expected GitHub app. Require PRs and an up-to-date branch, dismiss stale
  approvals, and remove obsolete required-check names. `CI passed` depends on
  every CI matrix and validation job, so new tests cannot silently escape the
  merge gate. The separate CodeRabbit check may also remain required.
- Keep `SONAR_TOKEN`, the `test` and `pypi` environments, and PyPI trusted publisher
  mappings for `publish.yml` configured.
- Supply `PR_SYNC_TOKEN` as an Actions secret for Dependabot automation. Although
  it no longer repairs prek files, the existing credential name is retained. Use
  a GitHub App installation token or narrowly scoped PAT with contents and PR
  write access. A normal `GITHUB_TOKEN` merge would not trigger main CI and
  publication. The credential must not bypass branch protections. If using an
  expiring App token, mint it at runtime rather than storing the installation
  token as a long-lived secret. Latest-push approval policies may require a
  distinct approval identity; GitHub will enforce this rather than bypass it.
- These privileged workflows must land on the default branch before they can
  handle the new workflow names and scripts. The migration PR may require a
  manual bootstrap review while the previous default-branch automation is active.

## Home Assistant compatibility

The policy resolves the latest stable Home Assistant release once and compares
its release-specific `requirements_all.txt` pins, including core requirements
and constraint includes, with this package's runtime,
CLI, and MCP requirements. Shared dependencies must admit HA's pinned version.
Development tools and docs dependencies are excluded. Missing shared pins impose
no constraint. Fetch and parse failures fail the check rather than silently
waiving the policy. Incompatible updates fail CI; they are not automatically
closed. This checks declared constraints, not complete HA integration behaviour.

## Prek dependencies

Activate the environment created by `scripts/setup.sh` before running prek:

```sh
source .venv/bin/activate
prek run --all-files
```

Run `scripts/startup.sh` after editing requirements. Mypy, pylint, and manual
pytest hooks use that environment directly. Other independent hooks retain
managed environments. No requirements are copied into hook configuration, and
Dependabot no longer needs a generated follow-up commit.

## Local validation

```sh
actionlint
python scripts/check_action_refs.py .github/workflows
node --test .github/scripts/*.test.cjs
python -I .github/scripts/test_sonar_workflow.py
python -I .github/scripts/test_ha_requirements.py
python scripts/check/ha_requirements.py
prek run --all-files
```

The HA check requires network access and `packaging`. Other policy tests use
synthetic API responses and local files without credentials. Hosted validation
is still required for approval events, CodeRabbit, Sonar, and trusted publishing.
