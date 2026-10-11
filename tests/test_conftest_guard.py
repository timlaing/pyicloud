"""Regression tests for the pytest filesystem guard in ``tests/conftest.py``."""

# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from tests import conftest
from tests.conftest import FileSystemAccessError

_SANDBOX = Path(tempfile.gettempdir()) / "python-test-results"
_OUTSIDE = os.path.join(tempfile.gettempdir(), "outside-the-sandbox")


@pytest.fixture(scope="module")
def escaping_symlink() -> Iterator[Path]:
    """Plant a sandbox symlink that escapes it, before the guards install.

    ``os.symlink`` is itself guarded inside a test body, so the link is created
    by a module-scoped fixture, which pytest sets up before the function-scoped
    mutation guards.
    """

    _SANDBOX.mkdir(parents=True, exist_ok=True)
    link = _SANDBOX / "escaping-symlink"
    link.unlink(missing_ok=True)
    os.symlink(tempfile.gettempdir(), link)
    try:
        yield link
    finally:
        link.unlink(missing_ok=True)


def test_escaping_symlink_is_rejected(escaping_symlink: Path) -> None:
    """A sandbox symlink must not smuggle access to a target outside it."""

    assert conftest.in_sandbox(escaping_symlink) is False
    with pytest.raises(FileSystemAccessError):
        escaping_symlink.read_text()


_COVERAGE_CASES: list[tuple[str, str, str | None, int | None, bool]] = [
    # Content reads are only allowed for the measured package sources.
    (conftest._PACKAGE_SOURCE_ROOT, "read", None, None, True),
    (os.path.join(conftest._PACKAGE_SOURCE_ROOT, "base.py"), "read", None, None, True),
    (_OUTSIDE, "read", None, None, False),
    # Metadata resolution is path-only, so coverage may canonicalize anywhere.
    (_OUTSIDE, "stat", None, None, True),
    # Only a write-only write to the report path is allowed.
    (conftest._COVERAGE_REPORT_PATH, "write", "w", None, True),
    (conftest._COVERAGE_REPORT_PATH, "write", "w+", None, False),
    (conftest._COVERAGE_REPORT_PATH, "write", "a", None, False),
    (conftest._COVERAGE_REPORT_PATH, "write", None, os.O_WRONLY, True),
    (conftest._COVERAGE_REPORT_PATH, "write", None, os.O_RDWR, False),
    (os.path.join(conftest._PROJECT_ROOT, "other.xml"), "write", "w", None, False),
    (_OUTSIDE, "write", None, os.O_WRONLY, False),
    # Mutations and unknown operations never qualify.
    (conftest._COVERAGE_REPORT_PATH, "other", None, None, False),
]


@pytest.mark.parametrize(
    ("path", "operation", "mode", "flags", "expected"), _COVERAGE_CASES
)
def test_coverage_exemption_is_scoped(
    path: str,
    operation: str,
    mode: str | None,
    flags: int | None,
    expected: bool,
) -> None:
    """The coverage exemption covers only narrow reads/writes, nothing else."""

    with patch.object(conftest, "_is_coverage_collecting", return_value=True):
        assert (
            conftest._coverage_access_allowed(
                path, operation=operation, mode=mode, flags=flags
            )
            is expected
        )


def test_coverage_exemption_requires_a_coverage_frame() -> None:
    """Without a coverage frame on the stack nothing is exempted."""

    with patch.object(conftest, "_is_coverage_collecting", return_value=False):
        assert not conftest._coverage_access_allowed(
            conftest._PACKAGE_SOURCE_ROOT, operation="read"
        )


def test_sanctioned_source_reads_cover_metadata() -> None:
    """The inventory scan's exemption must authorise ``stat``, not just reads."""

    with pytest.raises(FileSystemAccessError):
        conftest._reject(
            "call os.stat", conftest._PACKAGE_SOURCE_ROOT, operation="stat"
        )
    with conftest.allow_source_reads():
        conftest._reject(
            "call os.stat", conftest._PACKAGE_SOURCE_ROOT, operation="stat"
        )


@pytest.mark.parametrize("operation", ["write", "other"])
def test_sanctioned_source_reads_reject_mutations(operation: str) -> None:
    """The read-only scan exemption must not authorise writes or mutations."""

    with (
        conftest.allow_source_reads(),
        pytest.raises(FileSystemAccessError),
    ):
        conftest._reject(
            "be writing", conftest._PACKAGE_SOURCE_ROOT, operation=operation
        )


def test_reject_blocks_writes_outside_sandbox_under_coverage() -> None:
    """A coverage frame must not sanction a write anywhere else."""

    with (
        patch.object(conftest, "_is_coverage_collecting", return_value=True),
        pytest.raises(FileSystemAccessError),
    ):
        conftest._reject("be writing", _OUTSIDE, operation="write", mode="w")
