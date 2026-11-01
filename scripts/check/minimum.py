"""Ensure the minimum-runtime constraints really pin declared direct lower bounds."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


def lower_bounds(lines: list[str]) -> dict[str, str]:
    """Read this project's simple runtime requirements, rejecting unsupported forms."""
    result: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)(?:>=|==)([\d.]+)(?:,<[^, ]+)?", text)
        if match is None:
            raise ValueError(f"Unsupported minimum requirement: {text}")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in result:
            raise ValueError(f"Duplicate requirement: {name}")
        result[name] = match[2]
    return result


def check_minimum(declared: list[str], constraints: list[str]) -> None:
    """Reject missing, extra or widened direct pins with useful diagnostics."""
    actual = [
        line.strip()
        for line in constraints
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if any(">=" in line or "," in line or "==" not in line for line in actual):
        raise ValueError("Minimum constraints must use exact == pins")
    expected = lower_bounds(declared)
    pins = lower_bounds(actual)
    if expected != pins:
        raise ValueError(
            "Minimum pins differ from runtime lower bounds: "
            f"expected {expected}, got {pins}"
        )


def main() -> None:
    """Validate explicit input files without downloading or modifying dependencies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", type=Path)
    parser.add_argument("constraints", type=Path)
    args = parser.parse_args()
    check_minimum(
        args.requirements.read_text(encoding="utf-8").splitlines(),
        args.constraints.read_text(encoding="utf-8").splitlines(),
    )
    print("Minimum runtime pins match declared lower bounds")


if __name__ == "__main__":
    main()
