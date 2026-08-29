---
name: code-review
description: "Use when reviewing code changes, PRs, or commits in the pyicloud project. Covers architecture, typing, service conventions, linting, test coverage, and SonarQube quality rules. Trigger keywords: review, pr, code review, linting, typing, coverage, sonarqube, prek."
---

# Code Review Skill — pyicloud

Systematic review checklist for the `pyicloud` Python library & CLI for interacting
with Apple iCloud web services.

## Review Standard

- Establish the review target before inspecting code: working-tree changes, staged changes, a commit or commit range, or a PR against its merge base. State the selected target and base in the result.
- Review only changes in scope. Do not report pre-existing problems unless the change makes them materially worse; mention important out-of-scope risks separately without presenting them as findings.
- Report a finding only when the changed code introduces a demonstrable functional, security, reliability, compatibility, or maintainability defect. Explain the triggering conditions and impact, and cite the smallest useful `file:line` range.
- Do not report speculative problems, personal style preferences, or issues already caught reliably by required automated tooling. Do not invent findings when none qualify.
- Classify findings as:
  - **P0**: catastrophic or release-blocking in nearly all uses.
  - **P1**: likely functional, security, or data-integrity defect.
  - **P2**: real defect under plausible conditions.
  - **P3**: worthwhile, non-blocking correctness or maintainability improvement.
- Keep suggestions separate from defects. A suggestion must not be presented as a blocker.

## 1. Architecture & Boundaries

- `pyicloud/` is the library; `pyicloud/base.py` defines `PyiCloudService` and is the
  public entry point re-exported from `pyicloud/__init__.py`. `pyicloud/cli/` holds the
  optional CLI app (`icloud` console script → `pyicloud.cmdline:main` → `pyicloud.cli.app:main`).
- Per-service modules live under `pyicloud/services/` (account, calendar, contacts, drive,
  findmyiphone, hidemyemail, invites, notes, photos, photos_cloudkit, reminders, ubiquity).
  Notes and reminders are heavier and carry sub-packages: `models/`, `protobuf/`,
  `rendering/`, and for reminders `_reads.py`, `_writes.py`, `_protocol.py`, `_mappers.py`,
  `_support.py`. Keep service logic inside the owning service; do not mix concerns.
- Services consume Apple iCloud web services and (for invites/notes/reminders/photos) the
  CloudKit client in `pyicloud/common/`. Read `docs/research/invites_service_design.md`
  before touching the invites/CloudKit wire protocol.
- Library code must never depend on the CLI package (`pyicloud.cli`) and vice versa
  (kebab: CLI is an optional extra). No bidirectional imports between service modules.

## 2. Imports & Circular Dependencies

- Import the entry point as `pyicloud.PyiCloudService` / `from pyicloud import PyiCloudService`,
  never from `pyicloud.base` across services.
- `pyicloud/__init__.py` is the canonical public surface; services expose their public API
  via their package `__init__.py` (read defaults rather than internal modules).
- `[tool.ruff.lint.isort] known-first-party = ["custom_components"]` in `pyproject.toml` is a
  stale leftover from another project — treat `pyicloud` as the first-party package when
  reviewing import ordering, and never add new `custom_components` imports.
- Pydantic v2 (`pydantic>=2.13.4,<3`) is used for DTOs; protobuf (`protobuf>=6.32.0,<8`)
  for the notes/reminders wire format. Match the import style of the surrounding service.

## 3. Type Safety & Typing

- Python `>=3.10` (target `py310`, mypy reads `python_version = "3.14"`, runs strict).
  Prefer modern union syntax (`X | None`) unless `UP007` is suppressed project-wide.
- Mypy runs in **strict mode** (`[tool.mypy] strict = true`, `warn_return_any = true`,
  `check_untyped_defs = true`, `show_error_codes = true`). Type-check with `mypy .`.
- In production source, `# type: ignore` suppressions are acceptable only for third-party
  libraries missing stubs (e.g. the `srp`, `fido2`/`hsa2` auth stack) or unavoidable
  framework typing gaps. Require the specific error code; never add a bare or unexplained
  `# type: ignore`. Prefer fixing the type when practical.
- Pydantic models: use `model_config`/`Field` typing; do not shadow fields with untyped
  attributes or bypass validation silently.
- `bool` is a subclass of `int` — when a value must distinguish bool from int, check
  `type(x) is bool` first.

## 4. Dead Code & Redundancy

- Remove unreachable branches (e.g. `if x is None` after a callee that guarantees
  `x is not None`) rather than leaving them as defensive noise.
- Avoid duplicate logic across service modules; consolidate shared helpers in the
  appropriate common module (`pyicloud/common/`, `pyicloud/utils.py`) or the service package.
- Do not pre-allocate `dict.fromkeys(keys, [mutable])` — it shares one list object across
  keys (SonarQube S8508); use a dict comprehension `{k: [list] for k in keys}`.

## 5. Service & CLI Conventions

- `PyiCloudService` authenticates once and lazily exposes service objects (e.g.
  `service.drive`, `service.notes`, `service.reminders`, `service.photos`). New services
  should follow this lazy-access pattern and the existing service-registration in `base.py`.
- CLI commands live in `pyicloud/cli/` (click/typer/rich). Keep CLI thin: it should delegate
  to service libraries, not reimplement protocol logic. Use rich/typer idioms consistent
  with neighboring commands.
- `setuptools-scm` drives the version from git tags — never hand-edit a `__version__` or
  `version =` value.
- Do not hand-edit generated `*_pb2.py` files in `pyicloud/services/*/protobuf/` — edit the
  `.proto` (and `typedef.json`/`typedef.py` where present) and regenerate with `grpcio-tools`.

## 6. Test Conventions

- Tests live in `tests/` mirroring the package layout (`tests/services/`), with fixtures in
  `tests/const/` and `tests/fixtures/`.
- `[tool.pytest.ini_options]` adds `--disable-socket --allow-unix-socket --timeout=2` via
  `addopts`. **Tests must not make network calls** and must complete fast.
- `tests/conftest.py` installs autouse fixtures that **block filesystem access** (`open`,
  `os.open`, `os.mkdir`, `os.makedirs`, `os.chmod` all raise unless the path contains
  `"python-test-results"`). New tests must mock any file I/O.
- Tests exercise **public** entry points where practical; fakes/patching of network and
  filesystem boundaries is expected. Do not write tests that touch the live iCloud service.
- Tests verifying an expected exception/absence should assert meaningfully; do not use a bare
  `assert True` placeholder — add a comment explaining intent or make a real assertion.
- Prefer module-level pytest functions over class-based tests; share setup via fixtures.

## 7. Coverage Requirements

- SonarQube coverage is driven by `tests.yml` (`pytest --cov=pyicloud --cov-report=xml`).
  Library sources under `pyicloud/` (excluding `examples.py`, `fetch_devices_error.py`, and
  CLI smoke shims) should target high line and branch coverage. Not every file must exceed
  90%, but treat notable uncovered regions in changed code as a review point.
- Run locally (requires `.venv`):
  ```sh
  .venv/bin/python -m pytest --cov=pyicloud --cov-branch --cov-report=term-missing
  ```
  Inspect the per-file table before claiming compliance; do not infer coverage from the
  aggregate percentage alone.

## 8. Linting & Formatting

- Use `.venv/bin/prek run --all-files` for the full default-stage check set. Do not use
  `pre-commit` directly.
- All applicable hooks configured in `.pre-commit-config.yaml` must pass (ruff-check,
  ruff-format, cspell, yamllint, prettier, mypy, pylint, and the various pre-commit-hooks).
  Manual and `commit-msg` stages are not part of the default all-files run.
- How fixes behave: some hooks apply fixes. A review-only request does not authorize worktree
  edits — record `git status --short` first and use non-mutating tool modes where practical.
  If a required check modifies files, disclose exactly what changed and do not silently
  include those edits in the review.
- cspell words in `.vscode/cspell.json` must be **lowercase and sorted alphabetically**.
- Ruff is the only formatter and linter for Python (`line-length = 88`, `preview=true`).
  `ruff check --fix` and `ruff format` handle it. Respect the project `select`/`ignore` in
  `pyproject.toml` (e.g. `UP007` is ignored project-wide — do not fight that).
- Pylint: config in `[tool.pylint.*]` disables several `too-*` checks and
  `duplicate-code`. When adding `# pylint: disable` or `# noqa`, prefer a real fix; if a
  suppression is genuinely needed, scope it narrowly and pair it with the specific code.

## 9. SonarQube Rules

| Rule  | Meaning                            | Fix                                                     |
| ----- | ---------------------------------- | ------------------------------------------------------- |
| S8508 | Mutable default in `dict.fromkeys` | Use dict comprehension                                  |
| S1172 | Unused function parameter          | Remove or accept if an interface requires it            |
| S5914 | Constant boolean expression        | Remove or replace with meaningful assertion             |
| S9081 | Lambda should use `return_value`   | Use `patch(..., return_value=x)` instead of `lambda: x` |
| S7502 | Untracked asyncio task             | Save `create_task()` return to prevent GC               |
| S3776 | Cognitive complexity               | Refactor into smaller functions                         |

SonarQube project key: `timlaing_pyicloud` (`sonar.project.properties`). When reviewing,
avoid introducing issues that must be suppressed; prefer fixing them in code.

## 10. CI & Workflows

- `linting.yml`: runs `.venv/bin/prek` (via `j178/prek-action`) on push/PR.
- `tests.yml`: matrix across Python 3.10–3.14, creates a `venv`, installs
  `requirements_all.txt` via uv, then runs pytest with coverage (`--cov-report=xml`) and
  junit output.
- `sonarcube.yml`: generates a coverage artifact on push to `main`, then runs the
  SonarQube Cloud scan; also handles PRs (`pull_request_target`, fork/bot path).
- `checks.yml`: runs `python3 -m scripts.check.edits` (editable-install sanity check).
- If you change/remove/add a dependency, keep `requirements_all.txt` and the prek
  `additional_dependencies` blocks in sync by running
  `python3 scripts/sync_prek_deps.py requirements_all.txt`; the `check-prek-deps` hook
  enforces this.

## 11. Commit & PR Conventions

- Conventional commits (`fix:`, `chore:`, `feat:`, `refactor:`, `test:`, `docs:`) are expected.
- PR description should follow `.github/PULL_REQUEST_TEMPLATE.md` (breaking-change note,
- proposed change, type-of-change, example, additional information, checklist).
- A review-only request does not authorize edits, commits, pushes, or merges. A fix-review
  request may include those actions only when the user explicitly requests them.
- Commit messages should be concise and describe the change, not the process.
- Run `prek run --all-files` and the test suite before pushing.

## 12. Common Pitfalls

- This skill was adapted from a Home Assistant custom integration; none of the HA
  `custom_components/` / `homeassistant.*` conventions apply here. Do not apply them.
- Tests must not perform network calls (socket access is disabled) and must not touch real
  files (blocked by conftest fixtures) — always mock boundaries.
- Payloads to iCloud/CloudKit are sensitive to exact wire shapes; treat field names, zones,
  and share/record topology (per `docs/research/invites_service_design.md`) as contractual.
- Auth flows (`hsa2`, `srp`, `fido2`, cookies) are security-sensitive — never log secrets,
  and never weaken validation when touching `session.py`, `srp_password.py`, `base.py`, or
  `cookie_jar.py`.
- Generated protobuf must be regenerated, never hand-edited. Do not commit edited `_pb2.py`.
- `setuptools-scm` derives the version from git tags — do not hand-edit version values.

## 13. Review Workflow

1. Resolve and state the review target and base. Read that diff and identify all changed files and their categories (source, tests, config, CI).
2. Check each source file against architecture rules (Section 1) and import rules (Section 2).
3. Check typing against Section 3 rules. Verify no new `# type: ignore` without justification.
4. Check for dead code patterns (Section 4).
5. Verify service/CLI conventions (Section 5) for any service or CLI changes; read
   `docs/research/invites_service_design.md` if the change touches the invites/CloudKit service.
6. Check test conventions (Section 6) and coverage (Section 7) for test changes.
7. Record the initial worktree status. Run non-mutating checks where practical; if running
   `prek run --all-files`, detect and disclose any files it changes.
8. If coverage is in scope, run the coverage command and inspect every changed file's line and
   branch results. Do not infer compliance from the aggregate percentage.
9. Check for SonarQube-tractable issues (Section 9).
10. Report findings in priority order with tight `file:line` references, triggering conditions,
    impact, and a concrete fix. Separate suggestions from defects. If there are no actionable
    findings, say so explicitly.
11. Add a verification summary listing each command run and its pass, fail, or not-run status.
    Include relevant failures and environmental limitations; never imply a check ran when it did not.
