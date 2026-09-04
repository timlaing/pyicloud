"""Tests for the filesystem guard the other tests run under.

The guard in `tests/conftest.py` is described in CONTRIBUTING.md and AGENTS.md,
and those descriptions had drifted from it -- which produced repeated review
findings against tests that were using the guard exactly as intended. These
tests pin the behaviour the documentation now describes, so the two cannot
disagree again without something failing.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from tests.conftest import FileSystemAccessError

ALLOWED_MARKER = "python-test-results"


def _allowed_path(name: str) -> str:
    """Return a path inside the sanctioned location."""

    return os.path.join(tempfile.gettempdir(), ALLOWED_MARKER, name)


def test_open_is_blocked_outside_the_sanctioned_path() -> None:
    """`builtins.open` raises rather than touching the developer's disk."""

    with (
        pytest.raises(FileSystemAccessError),
        open(  # noqa: PTH123
            "/etc/hosts", encoding="utf-8"
        ) as handle,
    ):
        handle.read()


def test_making_directories_is_blocked_outside_the_sanctioned_path() -> None:
    """The same applies to directory creation."""

    with pytest.raises(FileSystemAccessError):
        os.mkdir("/tmp/pyicloud-should-not-exist")  # noqa: PTH102

    with pytest.raises(FileSystemAccessError):
        os.makedirs("/tmp/pyicloud-should-not-exist/nested")  # noqa: PTH103


def test_the_sanctioned_path_is_an_escape_hatch_not_a_loophole() -> None:
    """`python-test-results` paths are permitted, by design.

    `tests/test_cmdline.py` relies on this for its session directories. A
    reviewer reading only "new tests must mock any file I/O" would call that a
    violation; it is the guard working as intended.
    """

    target = _allowed_path("guard-check")
    os.makedirs(target, exist_ok=True)  # noqa: PTH103
    assert ALLOWED_MARKER in target


def test_pathlib_reads_are_not_intercepted() -> None:
    """`Path.read_text` goes through `io.open`, which the guard does not patch.

    This is why loading a JSON fixture works, and it works inside a test body
    as well as at import time -- so the ordering of the session-scoped fixtures
    is not the whole explanation. Seven test modules depend on this.
    """

    conftest = Path(__file__).resolve().parent / "conftest.py"

    content = conftest.read_text(encoding="utf-8")

    assert "FileSystemAccessError" in content
