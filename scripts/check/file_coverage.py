"""Enforce strictly above 90% combined line-and-branch coverage per measured file."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from xml.etree import ElementTree


def file_totals(element: ElementTree.Element) -> tuple[int, int]:
    """Count covered/possible lines and branches rather than rounded XML rates."""
    covered = 0
    total = 0
    for line in element.findall("./lines/line"):
        total += 1
        covered += int(int(line.attrib["hits"]) > 0)
        if line.get("branch") == "true":
            match = re.fullmatch(
                r"[\d.]+% \((\d+)/(\d+)\)", line.attrib["condition-coverage"]
            )
            if match is None:
                raise ValueError("Missing or invalid branch coverage counts")
            hits, possible = int(match[1]), int(match[2])
            if not 0 <= hits <= possible:
                raise ValueError("Invalid branch coverage counts")
            covered += hits
            total += possible
    return covered, total


def coverage_failures(root: ElementTree.Element) -> list[tuple[str, int, int]]:
    """Return actionable failures, preserving omissions made by coverage itself."""
    files = root.findall("./packages/package/classes/class")
    if not files:
        raise ValueError("Coverage report contains no measured source files")
    failures = []
    for element in files:
        covered, total = file_totals(element)
        if total and covered * 100 <= total * 90:
            failures.append((element.attrib["filename"], covered, total))
    return sorted(failures)


def main() -> None:
    """Read coverage.py XML and fail with filenames and exact counter-based rates."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    failures = coverage_failures(ElementTree.parse(args.report).getroot())
    for filename, covered, total in failures:
        print(
            f"{filename}: {100 * covered / total:.4f}% ({covered}/{total}); "
            "required >90%"
        )
    if failures:
        raise SystemExit(1)
    print("Every measured source file exceeds 90% combined line-and-branch coverage")


if __name__ == "__main__":
    main()
