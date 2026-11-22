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


def applies_to_main(detail: dict[str, Any], default_branch: str = "main") -> bool:
    """Select only explicit main-only targets, preserving shared protections."""
    refs = detail.get("conditions", {}).get("ref_name", {})
    aliases = ["refs/heads/main"]
    if default_branch == "main":
        aliases.append("~DEFAULT_BRANCH")
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
    default_branch = api("")["default_branch"]
    candidates = api("rulesets?includes_parents=true")
    rulesets = []
    for candidate in candidates:
        if (
            candidate["source_type"] != "Repository"
            or candidate["enforcement"] != "active"
        ):
            continue
        detail = api(f"rulesets/{candidate['id']}")
        if applies_to_main(detail, default_branch):
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


STATUS_CONTEXTS = {
    "Review passed",
    "Review notifier policy",
    "Quality",
    "Analysis passed",
}


def reported_checks(
    reported: dict[str, Any], statuses: list[dict[str, Any]]
) -> dict[str, bool]:
    """Read results from the expected producer and authoritative result type.

    A check run is authoritative for its context. A same-named commit status must
    not overwrite it, otherwise a successful status could mask a failed check.
    """
    checks: dict[str, bool] = {}
    for item in sorted(reported["check_runs"], key=lambda item: item["id"]):
        if (
            item.get("app", {}).get("slug") == "github-actions"
            and item["name"] not in STATUS_CONTEXTS
        ):
            checks[item["name"]] = (
                item["status"] == "completed" and item["conclusion"] == "success"
            )
    # statuses arrive newest-first from the API; the first eligible result per
    # context is the current one, so do not reorder or overwrite it.
    for item in statuses:
        if (
            item.get("creator", {}).get("login") == "github-actions[bot]"
            and item["context"] in STATUS_CONTEXTS
            and item["context"] not in checks
        ):
            checks[item["context"]] = item["state"] == "success"
    return checks


def validate_reviewed_head(policy: dict[str, Any], revision: str) -> None:
    """Bind activation to the reviewed installation PR merged into main."""
    number = policy.get("installation_pr")
    if not isinstance(number, int):
        raise SystemExit("The reviewed policy must name its installation PR")
    associated = api(f"commits/{revision}/pulls?per_page=100")
    matches = [
        pr
        for pr in associated
        if pr["number"] == number
        and pr["state"] == "closed"
        and pr.get("merged_at")
        and pr["head"]["sha"] == revision
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == REPOSITORY
    ]
    if len(matches) != 1:
        raise SystemExit("--validated-head must be the merged installation PR head")


def validate_hosted_checks(policy: dict[str, Any], revision: str | None) -> None:
    """Require real replacement checks on the reviewed PR revision before activation."""
    if not policy["required_checks"]:
        raise SystemExit("Required check policy is empty")
    if revision is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise SystemExit("Provide --validated-head with the hosted PR head SHA")
    validate_reviewed_head(policy, revision)
    evidence = api(f"contents/{POLICY_PATH}?ref={revision}")
    if json.loads(base64.b64decode(evidence["content"])) != policy:
        raise SystemExit("Hosted evidence must use the reviewed workflow policy")
    reported = api(f"commits/{revision}/check-runs?filter=latest&per_page=100")
    statuses = api(f"commits/{revision}/statuses?per_page=100")
    # Re-read check runs after statuses: a check that changed between the two
    # reads must not be treated as a stable success.
    confirmed = api(f"commits/{revision}/check-runs?filter=latest&per_page=100")
    for snapshot in (reported, confirmed):
        if snapshot["total_count"] >= 100 or len(snapshot["check_runs"]) >= 100:
            raise SystemExit(
                "Hosted evidence requires pagination; inspect before applying"
            )
    if len(statuses) >= 100:
        raise SystemExit("Hosted evidence requires pagination; inspect before applying")
    first = reported_checks(reported, statuses)
    second = reported_checks(confirmed, statuses)
    if not all(
        first.get(name, False) and second.get(name, False)
        for name in policy["required_checks"]
    ):
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
    if args.apply and (
        args.validated_head is None
        or re.fullmatch(r"[a-f0-9]{40}", args.validated_head) is None
    ):
        raise SystemExit("Provide --validated-head with the hosted PR head SHA")
    if args.apply:
        remote = api(f"contents/{POLICY_PATH}?ref=main")
        deployed = json.loads(base64.b64decode(remote["content"]))
        if deployed != policy:
            raise SystemExit("The reviewed policy must be merged into main first")
    selected = main_ruleset()
    original = json.loads(json.dumps(selected))
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
    # Retain the previous rule so a partial activation can be rolled back.
    previous_rule = json.loads(json.dumps(rule)) if rule is not None else None
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
    # Detect settings drift before any write, enable the replacement workflows
    # so they can produce hosted proof, then keep the ruleset unchanged until
    # that proof has succeeded on the reviewed head.
    current_ruleset = api(f"rulesets/{selected['id']}")
    if any(current_ruleset.get(key) != original.get(key) for key in payload):
        raise SystemExit(
            "Ruleset changed during validation; serialise settings changes and retry"
        )
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
    try:
        current = api(ACTIONS_PERMISSIONS)
        api(
            ACTIONS_PERMISSIONS,
            "PUT",
            {
                "enabled": current["enabled"],
                "allowed_actions": current["allowed_actions"],
                "sha_pinning_required": policy["sha_pinning_required"],
            },
        )
        workflow_permissions = api("actions/permissions/workflow")
        api(
            "actions/permissions/workflow",
            "PUT",
            {
                "default_workflow_permissions": policy["default_workflow_permissions"],
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
        if (
            api(ACTIONS_PERMISSIONS)["sha_pinning_required"]
            != policy["sha_pinning_required"]
        ):
            raise SystemExit("SHA enforcement did not activate")
    except (SystemExit, subprocess.CalledProcessError) as failure:
        # Restore the previous required-check rule; other independent settings
        # are left as-is because a partial write cannot roll those back safely.
        rollback = json.loads(json.dumps(payload))
        if previous_rule is None:
            rollback["rules"] = [
                item
                for item in rollback["rules"]
                if item["type"] != "required_status_checks"
            ]
        else:
            target = next(
                item
                for item in rollback["rules"]
                if item["type"] == "required_status_checks"
            )
            target["parameters"] = previous_rule["parameters"]
        try:
            api(f"rulesets/{selected['id']}", "PUT", rollback)
        except (SystemExit, subprocess.CalledProcessError) as rollback_failure:
            raise SystemExit(
                f"Activation failed ({failure}); rollback also failed "
                f"({rollback_failure}). The replacement rule may still be active."
            ) from rollback_failure
        print("Partial activation rolled back to the previous required-check rule")
        raise
    print("Workflow policy applied and required checks verified")


if __name__ == "__main__":
    main()
