# AGENTS.md

PyiCloud: a Python library + CLI for interacting with Apple iCloud web services.

## Package layout

- `pyicloud/` — library. Entry point `pyicloud.base.PyiCloudService` (`pyicloud/__init__.py`).
- `pyicloud/cli/` — CLI app (click/typer/rich, an optional `[cli]` extra). `icloud` console script → `pyicloud.cmdline:main` → `pyicloud.cli.app:main`.
- `pyicloud/services/` — per-service modules (drive, photos, findmyiphone, notes, reminders, invites, ...). Notes and reminders have heavier sub-packages (`models/`, `protobuf/`, `rendering/`, `_writes.py`, `_reads.py`, `_protocol.py`).
- `tests/` — pytest suite mirroring package layout; `tests/const/` holds HTTP-response fixtures.
- `scripts/` — dev/tooling helpers (see below).

## Setup

- Environment is a `.venv`; install everything with uv: `scripts/setup.sh` (full bootstrap) or `scripts/startup.sh` (re-install deps + `npm install`). `requirements_all.txt` pulls in runtime + dev + cli deps.
- Build tool is setuptools + **setuptools-scm**: version is derived from git tags via `[tool.setuptools_scm]`. Never hand-edit a `__version__` / `version =` value.

## Dev commands

- Format/lint: `ruff check --fix` and `ruff format` (Ruff is the only formatter; line length 88, `preview=true` in `[tool.ruff]`).
- Types: `mypy .` (strict mode, `[tool.mypy]`).
- Pylint: `pylint` (config in `[tool.pylint.*]`).
- Pre-commit hooks are managed by **`prek`** (a pre-commit drop-in), not `pre-commit`. Hooks & config: `.pre-commit-config.yaml`; install with `prek install`. Runs: ruff, cspell, yamllint, prettier, mypy, pylint.
- Tests: `pytest` (config in `[tool.pytest.ini_options]`). Run a single file with `pytest tests/services/test_drive.py` or single test with `pytest tests/test_base.py::test_name -k`.

## Test gotchas (important)

- `pyproject.toml` `[tool.pytest.ini_options]` adds `--disable-socket --allow-unix-socket --timeout=2` via `addopts`. **Tests must not make network calls** and must complete fast.
- `tests/conftest.py` installs autouse fixtures that **block filesystem access**: `open`, `os.open`, `os.mkdir`, `os.makedirs`, `os.chmod` all raise unless the path contains `"python-test-results"`. New tests must mock any file I/O.

## Generated protobuf

- `pyicloud/services/notes/protobuf/` and `pyicloud/services/reminders/protobuf/` contain committed, generated `*_pb2.py` files from `.proto` sources (regenerated with `grpcio-tools`, in the dev dependency group). **Do not hand-edit `*_pb2.py`**; edit the `.proto` (and `typedef.json`) instead. Package dir naming intentionally deviates (see `buf.yaml` lint `ignore_only`).

## Dependency sync quirk

- Dev dependencies live in `requirements_dev.txt` and (with runtime/cli ones) get **synced into the prek `additional_dependencies` blocks** in `.pre-commit-config.yaml` by `scripts/sync_prek_deps.py`. If you add/change a dependency, run `python3 scripts/sync_prek_deps.py requirements_all.txt`; the prek hook `check-prek-deps` enforces that the two are in sync.

## Misc

- `docs/research/invites_service_design.md` describes the invites/CloudKit wire protocol — read it before touching that service.
- `.env` (gitignored, local only) sets HTTP(S)_PROXY for the dev env; not part of the repo and not required for tests.
