"""Enforce that a pull request is editable by the repository maintainers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, cast


def get_event() -> dict[str, Any]:
    """Load the GitHub Actions event payload from the event path."""
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH is not set")
    with Path(event_path).open(encoding="utf-8") as event_file:
        return cast("dict[str, Any]", json.load(event_file))


def main() -> None:
    """Check that the pull request is editable by the maintainers."""
    event = get_event()
    pull_request = event["pull_request"]
    repository = event["repository"]["full_name"]
    head_repo = pull_request["head"]["repo"]["full_name"]

    if head_repo == repository:
        # Same-repo PRs share the repository, so collaborators can always edit
        # the branch; maintainer_can_modify is not applicable here.
        print("PR is in the same repository and is editable by maintainers")
        return

    if pull_request["maintainer_can_modify"]:
        print("PR is editable by the maintainers")
        return

    print("::error::The PR is not editable by the maintainers")
    sys.exit(1)


if __name__ == "__main__":
    main()
