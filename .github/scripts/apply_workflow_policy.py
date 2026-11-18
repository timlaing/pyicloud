"""Apply the reviewed workflow policy only after it exists on the default branch."""

from __future__ import annotations

import argparse
import base64
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

REPOSITORY = "timlaing/pyicloud"
POLICY_PATH = ".github/workflow-policy.json"
ACTIONS_PERMISSIONS = "actions/permissions"


def api(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    """Call the installed GitHub CLI without exposing authentication values."""
    executable = shutil.which("gh")
    if executable is None:
        raise SystemExit("Install and authenticate GitHub CLI first")
    command = [
        executable,
        "api",
        "--method",
        method,
        f"repos/{REPOSITORY}" + (f"/{path}" if path else ""),
    ]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(  # noqa: S603
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def match_segments(pattern: list[str], target: list[str]) -> bool:
    """Match pathname components with recursive wildcards, including zero levels."""
    if not pattern:
        return not target
    if pattern[0] == "**":
        return any(
            match_segments(pattern[1:], target[index:])
            for index in range(len(target) + 1)
        )
    return (
        bool(target)
        and fnmatchcase(target[0], pattern[0])
        and match_segments(pattern[1:], target[1:])
    )


def applies_to_main(detail: dict[str, Any]) -> bool:
    """Select only explicit main-only targets, preserving shared protections."""
    refs = detail.get("conditions", {}).get("ref_name", {})
    aliases = ["refs/heads/main", "~DEFAULT_BRANCH"]
    includes = refs.get("include", [])
    included = bool(includes) and all(pattern in aliases for pattern in includes)
    excluded = any(
        pattern in [*aliases, "~ALL"]
        or match_segments(pattern.split("/"), ["refs", "heads", "main"])
        for pattern in refs.get("exclude", [])
    )
    return detail["target"] == "branch" and included and not excluded


def main_ruleset() -> dict[str, Any]:
    """Find the active main policy while preserving all unrelated protections."""
    candidates = api("rulesets?includes_parents=true")
    rulesets = []
    for candidate in candidates:
        if (
            candidate["source_type"] != "Repository"
            or candidate["enforcement"] != "active"
        ):
            continue
        detail = api(f"rulesets/{candidate['id']}")
        if applies_to_main(detail):
            rulesets.append(detail)
    if not rulesets:
        raise SystemExit("No active main-branch ruleset; review protection manually")
    return next(
        (
            item
            for item in rulesets
            if any(rule["type"] == "required_status_checks" for rule in item["rules"])
        ),
        rulesets[0],
    )


def validate_hosted_checks(policy: dict[str, Any], revision: str | None) -> None:
    """Require real replacement checks on the reviewed PR revision before activation."""
    if revision is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise SystemExit("Provide --validated-head with the hosted PR head SHA")
    evidence = api(f"contents/{POLICY_PATH}?ref={revision}")
    if json.loads(base64.b64decode(evidence["content"])) != policy:
        raise SystemExit("Hosted evidence must use the reviewed workflow policy")
    reported = api(f"commits/{revision}/check-runs?filter=latest&per_page=100")
    statuses = api(f"commits/{revision}/statuses?per_page=100")
    if reported["total_count"] > 100 or len(statuses) == 100:
        raise SystemExit("Hosted evidence requires pagination; inspect before applying")
    checks = {}
    for item in sorted(reported["check_runs"], key=lambda item: item["id"]):
        if item.get("app", {}).get("slug") == "github-actions":
            checks[item["name"]] = (
                item["status"] == "completed" and item["conclusion"] == "success"
            )
    for item in sorted(statuses, key=lambda item: item["id"]):
        if item.get("creator", {}).get("login") == "github-actions[bot]":
            checks[item["context"]] = item["state"] == "success"
    if not all(checks.get(name, False) for name in policy["required_checks"]):
        raise SystemExit(
            "Replacement checks have not succeeded on the hosted PR revision"
        )


def main() -> None:
    """Plan changes, or apply them after verifying the policy on main."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--validated-head",
        help="Exact hosted PR revision proving the replacement checks",
    )
    args = parser.parse_args()
    policy = json.loads(Path(POLICY_PATH).read_text(encoding="utf-8"))
    if args.apply:
        remote = api(f"contents/{POLICY_PATH}?ref=main")
        deployed = json.loads(base64.b64decode(remote["content"]))
        if deployed != policy:
            raise SystemExit("The reviewed policy must be merged into main first")
    selected = main_ruleset()
    payload = {
        key: selected[key]
        for key in [
            "name",
            "target",
            "enforcement",
            "conditions",
            "bypass_actors",
            "rules",
        ]
        if key in selected
    }
    rule = next(
        (item for item in payload["rules"] if item["type"] == "required_status_checks"),
        None,
    )
    if rule is None:
        rule = {"type": "required_status_checks", "parameters": {}}
        payload["rules"].append(rule)
    rule["parameters"] = {
        "strict_required_status_checks_policy": True,
        "do_not_enforce_on_create": False,
        "required_status_checks": [
            {"context": name, "integration_id": 15368}
            for name in policy["required_checks"]
        ],
    }
    print(
        json.dumps(
            {
                "repository": REPOSITORY,
                "ruleset_id": selected["id"],
                "payload": payload,
            },
            indent=2,
        )
    )
    if not args.apply:
        return
    for workflow in [
        "sonar.yml",
        "dependabot.yml",
        "review-events.yml",
        "review-status.yml",
        "review-notifier-policy.yml",
    ]:
        api(f"actions/workflows/{workflow}/enable", "PUT")
    validate_hosted_checks(policy, args.validated_head)
    api(f"rulesets/{selected['id']}", "PUT", payload)
    current = api(ACTIONS_PERMISSIONS)
    api(
        ACTIONS_PERMISSIONS,
        "PUT",
        {
            "enabled": current["enabled"],
            "allowed_actions": current["allowed_actions"],
            "sha_pinning_required": True,
        },
    )
    workflow_permissions = api("actions/permissions/workflow")
    api(
        "actions/permissions/workflow",
        "PUT",
        {
            "default_workflow_permissions": "read",
            "can_approve_pull_request_reviews": workflow_permissions[
                "can_approve_pull_request_reviews"
            ],
        },
    )
    api(
        "",
        "PATCH",
        {
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    api("automated-security-fixes", "PUT")
    actual = api(f"rulesets/{selected['id']}")
    actual_checks = next(
        item for item in actual["rules"] if item["type"] == "required_status_checks"
    )
    if actual_checks != rule:
        raise SystemExit("Required checks did not match after update")
    if not api(ACTIONS_PERMISSIONS)["sha_pinning_required"]:
        raise SystemExit("SHA enforcement did not activate")
    print("Workflow policy applied and required checks verified")


if __name__ == "__main__":
    main()
