"""Exact per-file coverage accounting and the shared manual validation path."""

import subprocess
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

from scripts.check.file_coverage import coverage_failures, file_totals
from scripts.check.tests import run_checks


@pytest.fixture(name="low_report")
def low_report_fixture() -> ElementTree.Element:
    """A file with 100% line coverage but only 50% branch coverage must fail."""
    return ElementTree.fromstring("""<coverage><packages><package><classes>
      <class filename="pyicloud/low.py"><lines>
        <line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>
      </lines></class>
      <class filename="pyicloud/high.py"><lines><line number="1" hits="1"/></lines></class>
    </classes></package></packages></coverage>""")


def test_combined_coverage_reports_offending_file(
    low_report: ElementTree.Element,
) -> None:
    """Report the actual filename and counts, not a rounded root/class rate."""
    assert coverage_failures(low_report) == [("pyicloud/low.py", 2, 3)]


def test_exactly_ninety_percent_fails() -> None:
    """The agreed per-file threshold is strictly above, not at least, ninety."""
    element = ElementTree.fromstring(
        '<class filename="boundary.py"><lines>'
        + "".join(
            f'<line number="{index}" hits="{int(index < 9)}"/>' for index in range(10)
        )
        + "</lines></class>"
    )
    root = ElementTree.fromstring(
        "<coverage><packages><package><classes/></package></packages></coverage>"
    )
    classes = root.find("./packages/package/classes")
    assert classes is not None
    classes.append(element)
    assert coverage_failures(root) == [("boundary.py", 9, 10)]


def test_empty_module_is_not_a_failure() -> None:
    """Zero statements/branches are valid for empty package initializers."""
    assert file_totals(ElementTree.fromstring("<class><lines/></class>")) == (0, 0)


@pytest.mark.parametrize("condition", ["unknown", "150% (3/2)"])
def test_invalid_branch_counts_fail(condition: str) -> None:
    """Malformed reports cannot silently omit uncovered branch opportunities."""
    element = ElementTree.fromstring(
        '<class><lines><line hits="1" branch="true" '
        f'condition-coverage="{condition}"/></lines></class>'
    )
    with pytest.raises(ValueError):
        file_totals(element)


def test_empty_report_fails() -> None:
    """An absent measured inventory must not pass vacuously."""
    with pytest.raises(ValueError, match="no measured"):
        coverage_failures(ElementTree.fromstring("<coverage/>"))


@pytest.mark.parametrize("exit_code", [0, 1])
def test_manual_gate_only_checks_fresh_successful_reports(exit_code: int) -> None:
    """A failed aggregate/test run never falls through to a stale XML report."""
    with (
        patch("scripts.check.tests.sys.argv", ["checks"]),
        patch(
            "scripts.check.tests.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], exit_code),
                subprocess.CompletedProcess([], 0),
            ],
        ) as run,
    ):
        assert run_checks() == exit_code
    assert run.call_count == (1 if exit_code else 2)
