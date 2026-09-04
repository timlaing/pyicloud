# Contributing to PyiCloud

First off, thanks for taking the time to contribute! This project is open
source, community-driven, and relies on people like you.

By participating, you agree to abide by our **[Code of Conduct](CODE_OF_CONDUCT.md)**.
Please read the **[Terms of Use](TERMS_OF_USE.md)** before using or
contributing to this library. PyiCloud interacts with Apple iCloud web
services; please only build against your own iCloud account and remain
mindful of Apple's Terms of Service.

## Getting started

1. Fork the repository and clone it locally.
2. Create a branch for your change (`git checkout -b feature/your-branch`).
3. Set up a development environment (see below).
4. Make your changes, add tests, and run the checks.
5. Push your branch and open a pull request (see
   [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)).

## Development environment

The recommended workflow uses `uv`. From the repository root:

```sh
scripts/setup.sh            # full bootstrap (creates .venv, installs deps)
scripts/startup.sh          # re-install deps + npm install
```

`scripts/setup.sh` (and `startup.sh`) install everything needed for runtime,
dev, and CLI work via `requirements_all.txt`. Activate the environment with
`.venv/bin/...`.

## Code style & checks

The project uses **Ruff** as its sole formatter and linter (line length 88,
`preview=true`), plus **mypy** (strict), **pylint**, **cspell**, **yamllint**,
and **prettier**. Pre-commit hooks are managed with **`prek`** (a pre-commit
drop-in), not `pre-commit`.

Format and lint:

```sh
ruff check --fix .
ruff format .
mypy .                     # strict mode
pylint pyicloud
```

Run the full default hook set (installs and runs all hooks):

```sh
prek install
prek run --all-files
```

Anything that fails in `prek run --all-files` must be addressed before a pull
request can be merged.

## Tests

Run the test suite:

```sh
pytest
```

Run a single file or test:

```sh
pytest tests/services/test_drive.py
pytest tests/test_base.py::test_name -k
```

Important test constraints:

- Tests **must not make network calls** and must complete fast. `pyproject.toml`
  adds `--disable-socket --allow-unix-socket --timeout=2` via `addopts`.
- `tests/conftest.py` installs autouse fixtures that **block most filesystem
  access**. `open`, `os.open`, `os.mkdir`, `os.makedirs` and `os.chmod` raise
  `FileSystemAccessError` unless the path contains `python-test-results`, which
  is the sanctioned location for a test that genuinely needs a temporary file
  or directory.
- Two things the guard does not cover, both used deliberately across the suite:
  `pathlib` reads such as `Path.read_text()` go through `io.open` rather than
  the patched `builtins.open`, and module-level code runs before the
  session-scoped fixtures install. Loading a JSON fixture at import time relies
  on both and is the established pattern.
- Mock file I/O that falls outside those two cases.
- Add fixtures under `tests/const/` (HTTP-response fixtures) or `tests/fixtures/`.

## Coverage

Run coverage to review what your change exercises:

```sh
pytest --cov=pyicloud --cov-branch --cov-report=term-missing
```

Inspect the per-file table before claiming compliance; the aggregate percentage
does not tell you which files are under-covered. Coverage is also measured by
CI and reported to SonarQube Cloud.

## Adding or changing dependencies

Dependencies are spread across `requirements.txt` (runtime),
`requirements_dev.txt` (dev), and `requirements_cli.txt` (CLI extra), and are
aggregated in `requirements_all.txt`.

If you add or change a dependency, also sync the prek `additional_dependencies`
blocks in `.pre-commit-config.yaml`:

```sh
python3 scripts/sync_prek_deps.py requirements_all.txt
```

The `check-prek-deps` hook enforces that the two stay in sync.

## Generated protobuf

`pyicloud/services/notes/protobuf/` and
`pyicloud/services/reminders/protobuf/` contain committed, generated
`*_pb2.py` files from `.proto` sources.

- **Do not hand-edit `*_pb2.py` files.** Edit the `.proto` (and
  `typedef.json`/`typedef.py` where present) and regenerate with
  `grpcio-tools` (in the dev dependency group).

## Versioning

The version is derived from git tags via **setuptools-scm**. Never hand-edit a
`__version__` / `version =` value.

## Architecture notes

- `pyicloud/` is the library. `pyicloud.base.PyiCloudService` is the entry point
  (re-exported from `pyicloud/__init__.py`).
- `pyicloud/cli/` holds the optional CLI app (`icloud` console script →
  `pyicloud.cmdline:main` → `pyicloud.cli.app:main`). Keep the CLI thin; it
  should delegate to service libraries.
- `pyicloud/services/` holds per-service modules. Notes and reminders have
  heavier sub-packages (`models/`, `protobuf/`, `rendering/`, and for reminders
  `_reads.py`/`_writes.py`/`_protocol.py`).
- `docs/research/invites_service_design.md` describes the invites/CloudKit wire
  protocol — read it before touching that service.

## Reporting bugs & requesting features

Use the GitHub issue templates:

- Bugs → [`.github/ISSUE_TEMPLATE/BUG.md`](.github/ISSUE_TEMPLATE/BUG.md)
- Feature requests → [`.github/ISSUE_TEMPLATE/FEATURE_REQUEST.md`](.github/ISSUE_TEMPLATE/FEATURE_REQUEST.md)
- Support → [`.github/ISSUE_TEMPLATE/SUPPORT.md`](.github/ISSUE_TEMPLATE/SUPPORT.md)

For security vulnerabilities, please follow the guidance in
[`SECURITY.md`](SECURITY.md) and **do not** open a public issue.

## Pull requests

- Use a pull request description that follows
  [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md).
- Use conventional-commit-style titles (`fix:`, `feat:`, `docs:`, `refactor:`,
  `test:`, `chore:`, ...).
- Keep changes focused; prefer splitting unrelated work into separate PRs.
- Make sure local tests pass — **a PR cannot be merged unless tests pass.**
- Run `prek run --all-files` and the test suite before pushing.

## License

By contributing, you agree that your contributions are licensed under the same
[MIT License](LICENSE) as the project.
