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
- `tests/conftest.py` **blocks filesystem access outside the
  `python-test-results` sandbox** (`tempfile.gettempdir()/python-test-results`;
  a resolved-prefix match, so on macOS it lives under `/var/folders/...`, not
  `/tmp`). Reads (`os.stat`/`listdir`/`scandir`/`walk`/…), writes and removals
  (`os.mkdir`/`makedirs`/`chmod`/`remove`/`rename`/…), `shutil`, and
  `builtins.open`/`os.open`/`io.open` all raise outside it. Mock the call in the
  test (e.g. `patch("os.path.exists", return_value=False)`) or write inside the
  sandbox.
- Tooling and library behaviour is exempt, never test I/O: gettext/click `.mo`
  lookups, coverage's path-only metadata resolution plus its reads of package
  sources and write-only `coverage.xml` output, fixture reads performed through
  `tests/fixture_loader.load_fixture`, and the inventory scan's package-source
  reads via `tests.conftest.allow_source_reads`.
- Add JSON fixtures under `tests/fixtures/` and load them with `load_fixture(...)`
  from `tests/fixture_loader.py`. Standalone response constants live in the
  `tests/const/` package and are imported directly.

## Coverage

Run coverage to review what your change exercises:

```sh
pytest --cov=pyicloud --cov-branch --cov-report=term-missing
```

Inspect the per-file table before claiming compliance; the aggregate percentage
does not tell you which files are under-covered. The project requires every
source file to stay above 90% coverage, but the CI workflow and the manual
`pytest` prek hook enforce an aggregate `--cov-fail-under=90` threshold. Coverage
is also reported to SonarQube Cloud. Pull requests must not introduce new
SonarQube issues; the SonarQube workflow waits for the quality gate configured
in SonarQube Cloud. The filesystem guard allows coverage's own metadata
resolution, package-source reads, and write-only `coverage.xml` output, so these
commands run unchanged.

## Documentation

User documentation is an [MkDocs](https://www.mkdocs.org/) site under `docs/`,
published to [Read the Docs](https://pyicloud.readthedocs.io/). The API reference
is generated from docstrings with `mkdocstrings`, and the CLI reference from the
Typer app with `mkdocs-typer2`.

- `mkdocs.yml` — site configuration and navigation.
- `.readthedocs.yaml` — the Read the Docs build; it installs
  `docs/requirements.txt` and the package with the `cli` extra.
- `docs/requirements.txt` — docs-only dependencies (MkDocs, Material, plugins).
- `.github/workflows/docs.yml` — runs `mkdocs build --strict` on pushes and pull
  requests to `main`.

Preview the site locally:

```sh
pip install -r docs/requirements.txt
pip install ".[cli]"
mkdocs serve           # live preview at http://127.0.0.1:8000
mkdocs build --strict  # the check CI and Read the Docs run
```

`docs/research/` (maintainer design notes) and `docs/hooks/` are excluded from
the published site. Add any new page to the `nav:` in `mkdocs.yml`, or it will
not appear.

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

- Bugs → [`.github/ISSUE_TEMPLATE/bug.yml`](.github/ISSUE_TEMPLATE/bug.yml)
- Feature requests → [`.github/ISSUE_TEMPLATE/feature_request.yml`](.github/ISSUE_TEMPLATE/feature_request.yml)

Blank issues are disabled. Questions and help requests belong in
[GitHub Discussions](https://github.com/timlaing/pyicloud/discussions).

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
