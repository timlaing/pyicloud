"""Run both the aggregate test gate and the per-file coverage gate."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def run_checks() -> int:
    """Never inspect a stale report when pytest or aggregate coverage fails."""
    Path("coverage.xml").unlink(missing_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=pyicloud",
            "--cov-branch",
            "--cov-report=term-missing",
            "--cov-report=xml:coverage.xml",
            "--cov-fail-under=90",
            *sys.argv[1:],
        ],
        check=False,
    )
    if result.returncode:
        return result.returncode
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("file_coverage.py")),
            "coverage.xml",
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(run_checks())
