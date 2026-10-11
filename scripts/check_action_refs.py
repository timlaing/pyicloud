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
    r"^[^#]*['\"]?uses['\"]?\s*:\s*['\"]?"
    r"(?P<action>[\w.\-]+(?:/[\w.\-]+)+)@(?P<ref>[\w.\-+/]+)['\"]?"
)

FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

SHORT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,39}$")

VERSION_PATTERN = re.compile(r"^v?\d+(?:\.\d+)*$")

VERSION_COMMENT_PATTERN = re.compile(r"#\s*v?\d+(?:\.\d+)*\s*$")

# Actions that only publish on a floating ref rather than a version tag:
# pypa/gh-action-pypi-publish's release/v1 is the publisher's documented
# stable ref (its tags are not guaranteed to move with release/v1).
#
# Full-length 40-character commit SHA pins are accepted for any action: a
# full SHA is the only ref GitHub treats as immutable (a retargeted tag can
# silently swap what CI runs). Such pins must carry a trailing `# vX.Y.Z`
# comment so Dependabot can still raise version bumps -- that is how the
# SonarSource/sonarqube-scan-action pin in sonar.yml is updated (see #366).
BRANCH_ALLOWLIST = {
    "pypa/gh-action-pypi-publish@release/v1",
}

IN_CI = "GITHUB_ACTIONS" in os.environ


@dataclass(frozen=True)
class Reference:
    """A single `uses: owner/action@ref` reference."""

    action: str
    ref: str
    path: Path
    line: int
    line_text: str


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
                    line_text=text,
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
    """Fail when an action is pinned to a short, non-immutable commit SHA."""
    failures = 0

    for reference in references:
        if not SHORT_SHA_PATTERN.match(reference.ref):
            continue

        report(
            "error",
            f"{reference.action} is pinned to the short commit {reference.ref}. "
            "Use the full 40-character SHA (the only immutable ref GitHub "
            "verifies) or a version tag (for example @v4)",
            reference,
        )
        failures += 1

    return failures


def check_sha_annotations(references: list[Reference]) -> None:
    """Warn when a full-SHA pin lacks the version comment Dependabot reads."""
    for reference in references:
        if not FULL_SHA_PATTERN.match(reference.ref):
            continue

        if VERSION_COMMENT_PATTERN.search(reference.line_text):
            continue

        report(
            "warning",
            f"{reference.action} is pinned to commit {reference.ref} without a "
            "trailing `# vX.Y.Z` comment; add one so Dependabot can raise "
            "version update pull requests",
            reference,
        )


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
        if VERSION_PATTERN.match(reference.ref):
            continue

        if FULL_SHA_PATTERN.match(reference.ref) or SHORT_SHA_PATTERN.match(
            reference.ref
        ):
            # SHA pins are dealt with by the SHA checks above.
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
    check_sha_annotations(references)

    failures = check_sha_pins(references) + check_consistent_refs(references)

    if failures:
        print(
            f"{failures} action reference problem(s) found",
            file=sys.stderr,
        )
        return 1

    print(f"{len(references)} action references use version tags or full-SHA pins")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
