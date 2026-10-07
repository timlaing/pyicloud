#!/usr/bin/env python3

"""Validate GitHub Actions references in the workflow files."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys

WORKFLOW_DIR = Path(".github/workflows")

USES_PATTERN = re.compile(
    r"^[^#]*['\"]?uses['\"]?\s*:\s*"
    r"(?P<action>[\w.\-]+(?:/[\w.\-]+)+)@(?P<ref>[\w.\-+/]+)"
)

SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

VERSION_PATTERN = re.compile(r"^v?\d+(?:\.\d+)*$")

# Actions whose ref is a supported floating ref or commit SHA rather than a
# version tag:
# - pypa/gh-action-pypi-publish's release/v1 is the publisher's documented
#   stable ref (its tags are not guaranteed to move with release/v1).
# - SonarSource/sonarqube-scan-action is pinned to a commit SHA in sonar.yml as
#   part of the workflow_run fork-PR hardening; the trailing `# vX.Y.Z` comment
#   is what lets Dependabot raise version bumps (see #366).
BRANCH_ALLOWLIST = {
    "pypa/gh-action-pypi-publish@release/v1",
}

SHA_ALLOWLIST = {
    "SonarSource/sonarqube-scan-action",
}

IN_CI = "GITHUB_ACTIONS" in os.environ


@dataclass(frozen=True)
class Reference:
    """A single `uses: owner/action@ref` reference."""

    action: str
    ref: str
    path: Path
    line: int


def collect_references(paths: list[Path]) -> list[Reference]:
    """Collect every remote action reference from the given workflow files."""
    references: list[Reference] = []

    for path in paths:
        for number, text in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = USES_PATTERN.search(text)

            if match is None:
                continue

            references.append(
                Reference(
                    action=match["action"],
                    ref=match["ref"],
                    path=path,
                    line=number,
                )
            )

    return references


def resolve_paths(paths: list[Path]) -> list[Path]:
    """Expand directories to workflow files, keeping the given order."""
    resolved: list[Path] = []

    for path in paths:
        if path.is_dir():
            resolved.extend(sorted(path.glob("*.y*ml")))
        else:
            resolved.append(path)

    return resolved


def report(level: str, message: str, reference: Reference) -> None:
    """Print a finding, using workflow annotations when running in CI."""
    location = f"{reference.path}:{reference.line}"

    if IN_CI:
        print(
            f"::{level} file={reference.path},line={reference.line}::{message}",
        )
        return

    print(f"{location}: {message}")


def check_sha_pins(references: list[Reference]) -> int:
    """Fail when an action is pinned to a commit SHA instead of a tag."""
    failures = 0

    for reference in references:
        if not SHA_PATTERN.match(reference.ref):
            continue

        if reference.action in SHA_ALLOWLIST:
            continue

        report(
            "error",
            f"{reference.action} is pinned to commit {reference.ref}. Use a "
            "version tag (for example @v4) so Dependabot can raise version "
            "update pull requests",
            reference,
        )
        failures += 1

    return failures


def check_consistent_refs(references: list[Reference]) -> int:
    """Fail when the same action repository is referenced with mixed refs."""
    by_repo: dict[str, list[Reference]] = defaultdict(list)

    for reference in references:
        owner_repo = "/".join(reference.action.split("/")[:2])
        by_repo[owner_repo].append(reference)

    failures = 0

    for owner_repo, refs in sorted(by_repo.items()):
        distinct = {reference.ref for reference in refs}

        if len(distinct) > 1:
            rendered = ", ".join(sorted(distinct))
            for reference in refs:
                report(
                    "error",
                    f"{owner_repo} is referenced with mixed versions ({rendered}). "
                    f"This file uses {reference.ref}; pin every step of an action "
                    "to the same tag so they cannot resolve to different releases",
                    reference,
                )
                failures += 1

    return failures


def check_version_tags(references: list[Reference]) -> None:
    """Warn when an action tracks a branch instead of a version tag."""
    for reference in references:
        if VERSION_PATTERN.match(reference.ref) or SHA_PATTERN.match(reference.ref):
            continue

        if f"{reference.action}@{reference.ref}" in BRANCH_ALLOWLIST:
            continue

        report(
            "warning",
            f"{reference.action} tracks the branch {reference.ref}. Prefer a "
            "version tag where the action publishes one",
            reference,
        )


def main() -> int:
    """Parse arguments and validate the workflow action references."""
    parser = argparse.ArgumentParser(
        description="Validate GitHub Actions references in the workflow files.",
    )

    parser.add_argument(
        "workflows",
        type=Path,
        nargs="*",
        default=[WORKFLOW_DIR],
        metavar="WORKFLOW",
        help=(f"Workflow files or directories to check (default: {WORKFLOW_DIR})"),
    )

    args = parser.parse_args()

    try:
        paths = resolve_paths(args.workflows)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not paths:
        print("no workflow files found", file=sys.stderr)
        return 2

    references = collect_references(paths)

    check_version_tags(references)

    failures = check_sha_pins(references) + check_consistent_refs(references)

    if failures:
        print(
            f"{failures} action reference problem(s) found",
            file=sys.stderr,
        )
        return 1

    print(f"{len(references)} action references use version tags")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
