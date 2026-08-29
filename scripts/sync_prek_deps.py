#!/usr/bin/env python3

"""Synchronize requirements files into prek additional_dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

CONFIG_FILE = Path(".pre-commit-config.yaml")

BEGIN_MARKER = "          # BEGIN GENERATED REQUIREMENTS"
END_MARKER = "          # END GENERATED REQUIREMENTS"


def read_requirements(
    path: Path,
    seen_files: set[Path] | None = None,
) -> list[str]:
    """Recursively read a requirements file into a flat list of lines."""
    if seen_files is None:
        seen_files = set()

    path = path.resolve()

    if path in seen_files:
        return []

    seen_files.add(path)

    if not path.exists():
        raise FileNotFoundError(f"Requirements file not found: {path}")

    requirements: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("-r "):
            included = line[3:].strip()
            include_path = path.parent / included
            requirements.extend(read_requirements(include_path, seen_files))
            continue

        if line.startswith("--requirement "):
            included = line[len("--requirement ") :].strip()
            include_path = path.parent / included
            requirements.extend(read_requirements(include_path, seen_files))
            continue

        requirements.append(line)

    return requirements


def deduplicate(items: list[str]) -> list[str]:
    """Return items with duplicates removed, preserving order."""
    return list(dict.fromkeys(items))


def generate_block(requirements: list[str]) -> str:
    """Wrap requirements in the generated dependency marker block."""
    lines = [BEGIN_MARKER]

    for requirement in requirements:
        lines.append(f"          - {requirement}")

    lines.append(END_MARKER)

    return "\n".join(lines)


def update_config(
    requirements_files: list[Path],
    check_only: bool,
) -> int:
    """Synchronize the generated blocks in the config, or check staleness."""
    config = CONFIG_FILE.read_text(encoding="utf-8")
    lines = config.splitlines()

    begin_count = lines.count(BEGIN_MARKER)
    end_count = lines.count(END_MARKER)

    if begin_count == 0 or begin_count != end_count:
        print(
            f"Could not find matching generated dependency markers in {CONFIG_FILE}",
            file=sys.stderr,
        )
        return 2

    requirements: list[str] = []
    seen_files: set[Path] = set()

    for requirements_file in requirements_files:
        requirements.extend(read_requirements(requirements_file, seen_files))

    requirements = deduplicate(requirements)

    generated_lines = generate_block(requirements).splitlines()

    updated_lines: list[str] = []
    in_block = False
    for line in lines:
        if line == BEGIN_MARKER:
            in_block = True
            updated_lines.append(line)
            updated_lines.extend(generated_lines[1:-1])
            continue
        if line == END_MARKER:
            in_block = False
            updated_lines.append(line)
            continue
        if not in_block:
            updated_lines.append(line)

    updated = "\n".join(updated_lines)

    if config.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"

    if updated == config:
        print("prek dependencies are up to date")
        return 0

    if check_only:
        print(
            f"{CONFIG_FILE} is out of sync with requirements files",
            file=sys.stderr,
        )
        return 1

    CONFIG_FILE.write_text(updated, encoding="utf-8")

    print(f"Updated {CONFIG_FILE} with {len(requirements)} dependencies")

    return 0


def main() -> int:
    """Parse arguments and run the dependency sync."""
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize requirements files into prek additional_dependencies."
        )
    )

    parser.add_argument(
        "requirements",
        type=Path,
        nargs="+",
        metavar="REQUIREMENTS",
        help="Requirements files to include",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check without modifying the configuration",
    )

    args = parser.parse_args()

    try:
        return update_config(
            requirements_files=args.requirements,
            check_only=args.check,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
