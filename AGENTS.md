# AGENTS.md

PyiCloud: a Python library + CLI for interacting with Apple iCloud web services.

## Package layout

- `pyicloud/` — library. Entry point `pyicloud.base.PyiCloudService` (`pyicloud/__init__.py`).
- `pyicloud/cli/` — CLI app (click/typer/rich, an optional `[cli]` extra). `icloud` console script → `pyicloud.cmdline:main` → `pyicloud.cli.app:main`.
- `pyicloud/services/` — per-service modules (drive, photos, findmyiphone, notes, reminders, invites, ...). Notes and reminders have heavier sub-packages (`models/`, `protobuf/`, `rendering/`, `_writes.py`, `_reads.py`, `_protocol.py`).
- `tests/` — pytest suite mirroring package layout; `tests/const/` holds HTTP-response fixtures.
- `scripts/` — dev/tooling helpers (see below).
- `docs/` — user documentation: `docs/guides/` (cross-cutting how-tos), `docs/services/` (one page per service), and `docs/research/` (maintainer design notes). Built as an MkDocs site and published to Read the Docs (see Documentation below).

## Setup

- Environment is a `.venv`; install everything with uv: `scripts/setup.sh` (full bootstrap) or `scripts/startup.sh` (re-install deps + `npm install`). `requirements_all.txt` pulls in runtime + dev + cli deps.
- Build tool is setuptools + **setuptools-scm**: version is derived from git tags via `[tool.setuptools_scm]`. Never hand-edit a `__version__` / `version =` value.

## Dev commands

- Format/lint: `ruff check --fix` and `ruff format` (Ruff is the only formatter; line length 88, `preview=true` in `[tool.ruff]`).
- Types: `mypy .` (strict mode, `[tool.mypy]`).
- Pylint: `pylint` (config in `[tool.pylint.*]`).
- Pre-commit hooks are managed by **`prek`** (a pre-commit drop-in), not `pre-commit`. Hooks & config: `.pre-commit-config.yaml`; install with `prek install`. Runs: ruff, cspell, yamllint, prettier, mypy, pylint. A manual `pytest` hook additionally gates coverage at `--cov-fail-under=90`.
- Every source file must keep coverage above 90% (per-file requirement); the CI workflow and the manual `pytest` hook enforce the aggregate `--cov-fail-under=90`. PRs must not introduce new SonarQube issues (the SonarQube workflow waits for the quality gate configured in SonarQube Cloud).
- Tests: `pytest` (config in `[tool.pytest.ini_options]`). Run a single file with `pytest tests/services/test_drive.py` or single test with `pytest tests/test_base.py::test_name -k`.

## Test gotchas (important)

- `pyproject.toml` `[tool.pytest.ini_options]` adds `--disable-socket --allow-unix-socket --timeout=2` via `addopts`. **Tests must not make network calls** and must complete fast.
- `tests/conftest.py` **blocks filesystem access outside the `python-test-results` sandbox** (`tempfile.gettempdir()/python-test-results`; a resolved-prefix match, so on macOS it sits under `/var/folders/...`, not `/tmp`). Reads (`os.stat`/`lstat`/`listdir`/`scandir`/`walk`/`access`/`readlink`), mutations (`os.mkdir`/`makedirs`/`chmod`/`remove`/`unlink`/`rename`/`replace`/…), `shutil`, and `builtins.open`/`os.open`/`io.open` all raise outside it. Mock the call in the test (`patch("os.path.exists", return_value=False)`, …) or write inside the sandbox.
- Sanctioned exceptions are library/tooling behaviour, never test I/O: gettext/click `.mo` lookups, coverage's path-only metadata resolution plus its reads of package sources and write-only `coverage.xml` output, fixture reads made through `tests/fixture_loader.load_fixture`, and the inventory scan's package-source reads via `tests.conftest.allow_source_reads`.
- Load fixtures with `load_fixture(...)` from `tests/fixture_loader.py`; it wraps the read in the sanctioned exemption, so don't reach for `Path.read_text` directly.
- **Do not disable coverage** to make a target pass: never add `# pragma: no cover` (or other `exclude_lines`/coverage-exclusion comments) without explicit approval — close the gap with a test instead.

## Generated protobuf

- `pyicloud/services/notes/protobuf/` and `pyicloud/services/reminders/protobuf/` contain committed, generated `*_pb2.py` files from `.proto` sources (regenerated with `grpcio-tools`, in the dev dependency group). **Do not hand-edit `*_pb2.py`**; edit the `.proto` (and `typedef.json`) instead. Package dir naming intentionally deviates (see `buf.yaml` lint `ignore_only`).

## Dependency sync quirk

- Dev dependencies live in `requirements_dev.txt` and (with runtime/cli ones) get **synced into the prek `additional_dependencies` blocks** in `.pre-commit-config.yaml` by `scripts/sync_prek_deps.py`. If you add/change a dependency, run `python3 scripts/sync_prek_deps.py requirements_all.txt`; the prek hook `check-prek-deps` enforces that the two are in sync.

## Documentation

- The `docs/` tree is an MkDocs site published to Read the Docs via `.readthedocs.yaml`; `.github/workflows/docs.yml` runs `mkdocs build --strict` on pushes and PRs to `main`.
- Docs-only dependencies live in `docs/requirements.txt`; preview locally with `pip install -r docs/requirements.txt`, `pip install ".[cli]"`, then `mkdocs serve`.
- Register every new page in the `nav:` of `mkdocs.yml` — pages absent from the nav are not published. `docs/research/` and `docs/hooks/` are excluded from the published site.

## Misc

- `docs/research/invites_service_design.md` describes the invites/CloudKit wire protocol — read it before touching that service.
- `.env` (gitignored, local only) sets HTTP(S)_PROXY for the dev env; not part of the repo and not required for tests.

## Changelog

- Maintain `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.
- Releases up to `2.7.0` follow [Semantic Versioning](https://semver.org/); from `2026.10.0` onwards, versions follow [CalVer](https://calver.org/) (`YYYY.M.PATCH`).
- All PRs must add user-facing changes to `CHANGELOG.md` under a top `[Unreleased]` heading (create it if it is missing), using the appropriate categories (Added, Changed, Fixed, Removed, Deprecated, Security, Refactored, Tests, etc.).
- To release, rename the `[Unreleased]` heading to `[YYYY.M.PATCH] - YYYY-MM-DD` (e.g., `[2026.10.0] - 2026-10-12`); leave no empty `[Unreleased]` section behind, and do not add a comparison-links footer.
- Cut the release by publishing the draft release that Release Drafter maintains (`.github/workflows/release-drafter.yml`): set its tag and title to the CalVer version (e.g., `2026.10.0`, matching the `CHANGELOG.md` heading) and publish it. `.github/workflows/publish.yml` then runs on the release event and publishes to PyPI, or to TestPyPI when the release is marked as a prerelease. Pushes to `main` also publish development builds to TestPyPI. TestPyPI publishing uses the `test` GitHub environment.
- Never hand-edit version numbers - they come from git tags via setuptools-scm.
- **Never bypass commit checks** (e.g., `git commit --no-verify`). All pre-commit hooks must pass locally before committing changes. If a hook fails due to false positives, fix the configuration or the files properly - do not skip the checks. Only bypass checks with explicit case-by-case approval from the user.
- When adding words to cspell dictionaries (e.g., `.vscode/cspell.json`), ensure words are in **lowercase** and in **alphabetical order**.
