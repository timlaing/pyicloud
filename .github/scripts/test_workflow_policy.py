"""Regression checks for the guarded repository-settings migration."""

from __future__ import annotations

import base64
from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "workflow_policy", Path(__file__).with_name("apply_workflow_policy.py")
)
assert SPEC is not None
assert SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class MigrationTests(unittest.TestCase):
    """The migration must preserve protections and reject an unmerged policy."""

    def test_dry_run_preserves_unrelated_protections(self) -> None:
        """Only the required-check rule changes in the generated payload."""
        original = {
            "id": 1,
            "name": "main",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "bypass_actors": [
                {"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}
            ],
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {"required_approving_review_count": 1},
                },
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": []},
                },
            ],
        }
        calls: list[str] = []

        def api(path: str, method: str = "GET", body: object = None) -> object:
            del body
            self.assertEqual(method, "GET")
            calls.append(path)
            if path.startswith("rulesets?"):
                return [{"id": 1, "source_type": "Repository", "enforcement": "active"}]
            return deepcopy(original)

        output = io.StringIO()
        with (
            patch.object(sys, "argv", [__file__]),
            patch.object(POLICY, "api", side_effect=api),
            patch.object(
                Path,
                "read_text",
                return_value='{"required_checks":["Validate ci","Review passed"]}',
            ),
            redirect_stdout(output),
        ):
            POLICY.main()
        payload = json.loads(output.getvalue())["payload"]
        for key in ["name", "target", "enforcement", "conditions", "bypass_actors"]:
            self.assertEqual(payload[key], original[key])
        self.assertEqual(payload["rules"][:3], original["rules"][:3])
        expected = [
            {"context": "Validate ci", "integration_id": 15368},
            {"context": "Review passed", "integration_id": 15368},
        ]
        self.assertEqual(
            payload["rules"][-1]["parameters"]["required_status_checks"], expected
        )
        self.assertEqual(len(calls), 2)

    def test_apply_rejects_a_policy_not_yet_on_main(self) -> None:
        """No write request is made when the deployed policy does not match."""
        calls: list[str] = []

        def api(path: str, method: str = "GET", body: object = None) -> object:
            del body
            self.assertEqual(method, "GET")
            calls.append(path)
            return {
                "content": base64.b64encode(b'{"required_checks":["old"]}').decode()
            }

        with (
            patch.object(
                sys, "argv", [__file__, "--apply", "--validated-head", "a" * 40]
            ),
            patch.object(POLICY, "api", side_effect=api),
            patch.object(
                Path, "read_text", return_value='{"required_checks":["Review passed"]}'
            ),
            self.assertRaisesRegex(SystemExit, "merged into main first"),
        ):
            POLICY.main()
        self.assertEqual(len(calls), 1)


class ApplyTests(unittest.TestCase):
    """Exercise the complete settings transaction and its verification failures."""

    def exercise_apply(self, mismatch: str = "") -> list[tuple[str, str, object]]:
        """Run with a fake API that records writes and serves deployed policy."""
        policy = {"required_checks": ["Validate ci", "Review passed"]}
        ruleset = {
            "id": 1,
            "name": "main",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": [],
            "rules": [{"type": "deletion"}],
        }
        calls: list[tuple[str, str, object]] = []

        def api(path: str, method: str = "GET", body: object = None) -> object:
            calls.append((path, method, body))
            if path.startswith("contents/"):
                return {
                    "content": base64.b64encode(json.dumps(policy).encode()).decode()
                }
            if path.startswith("commits/"):
                if "/check-runs?" in path:
                    return {
                        "total_count": len(policy["required_checks"]),
                        "check_runs": [
                            {
                                "id": index,
                                "name": name,
                                "app": {"slug": "github-actions"},
                                "status": "completed",
                                "conclusion": "failure"
                                if mismatch == "evidence"
                                else "success",
                            }
                            for index, name in enumerate(policy["required_checks"])
                        ],
                    }
                return []
            if mismatch == "enable" and path.endswith("/enable"):
                raise SystemExit("Workflow enablement failed")
            if path.startswith("rulesets?"):
                return [
                    {"id": 2, "source_type": "Organization", "enforcement": "active"},
                    {"id": 3, "source_type": "Repository", "enforcement": "disabled"},
                    {"id": 1, "source_type": "Repository", "enforcement": "active"},
                ]
            if path == "rulesets/1":
                if method == "PUT":
                    ruleset.update(deepcopy(body))
                result = deepcopy(ruleset)
                if mismatch == "checks" and len(calls) > 5:
                    result["rules"][-1]["parameters"]["required_status_checks"] = []
                return result
            return {
                "actions/permissions/workflow": {
                    "can_approve_pull_request_reviews": False
                },
                POLICY.ACTIONS_PERMISSIONS: {
                    "enabled": True,
                    "allowed_actions": "all",
                    "sha_pinning_required": mismatch != "sha",
                },
            }.get(path)

        with (
            patch.object(
                sys, "argv", [__file__, "--apply", "--validated-head", "a" * 40]
            ),
            patch.object(POLICY, "api", side_effect=api),
            patch.object(Path, "read_text", return_value=json.dumps(policy)),
            redirect_stdout(io.StringIO()),
        ):
            expected = self.assertRaises(SystemExit) if mismatch else nullcontext()
            with expected:
                POLICY.main()
        return calls

    def test_apply_preserves_protections_and_verifies_enabled_policy(self) -> None:
        """All intended settings and replacement workflows are enabled together."""
        calls = self.exercise_apply()
        payload = next(
            body
            for path, method, body in calls
            if path == "rulesets/1" and method == "PUT"
        )
        self.assertEqual(payload["rules"][0], {"type": "deletion"})
        self.assertIn(("automated-security-fixes", "PUT", None), calls)
        self.assertIn(
            (
                "actions/permissions/workflow",
                "PUT",
                {
                    "default_workflow_permissions": "read",
                    "can_approve_pull_request_reviews": False,
                },
            ),
            calls,
        )
        self.assertIn(("actions/workflows/sonar.yml/enable", "PUT", None), calls)
        self.assertIn(("actions/workflows/dependabot.yml/enable", "PUT", None), calls)

    def test_apply_rejects_failed_settings_verification(self) -> None:
        """An API accepting writes does not establish that the policy took effect."""
        for mismatch in ["checks", "sha"]:
            with self.subTest(mismatch=mismatch):
                self.exercise_apply(mismatch)

    def test_missing_hosted_proof_or_failed_enablement_never_writes_rules(self) -> None:
        """Missing proof or enablement must leave existing rules intact."""
        for mismatch in ["evidence", "enable"]:
            with self.subTest(mismatch=mismatch):
                calls = self.exercise_apply(mismatch)
                self.assertFalse(
                    any(
                        path == "rulesets/1" and method == "PUT"
                        for path, method, _ in calls
                    )
                )

    def test_no_active_main_ruleset_fails_closed(self) -> None:
        """The migration must not silently select unrelated protections."""
        with (
            patch.object(POLICY, "api", return_value=[]),
            self.assertRaisesRegex(SystemExit, "No active main-branch ruleset"),
        ):
            POLICY.main_ruleset()

    def test_api_transport_preserves_json_and_reports_missing_cli(self) -> None:
        """Settings data uses stdin, never shell interpolation."""
        with (
            patch.object(POLICY.shutil, "which", return_value=None),
            self.assertRaisesRegex(SystemExit, "authenticate GitHub CLI"),
        ):
            POLICY.api("rulesets")
        response = type("Response", (), {"stdout": '{"ok":true}'})()
        with (
            patch.object(POLICY.shutil, "which", return_value="/usr/bin/gh"),
            patch.object(POLICY.subprocess, "run", return_value=response) as transport,
        ):
            self.assertEqual(
                POLICY.api("", "PATCH", {"allow_auto_merge": True}), {"ok": True}
            )
            self.assertEqual(
                transport.call_args.kwargs["input"], '{"allow_auto_merge": true}'
            )
            response.stdout = ""
            self.assertIsNone(POLICY.api("automated-security-fixes", "PUT"))


class SelectionTests(unittest.TestCase):
    """A policy excluding main must never be selected or changed."""

    def test_exclusion_wins_over_inclusion(self) -> None:
        """Exact names, aliases, and matching wildcards exclude main."""
        for excluded in [
            "refs/heads/main",
            "~DEFAULT_BRANCH",
            "~ALL",
            "refs/heads/m*",
            "refs/heads/*",
            "refs/heads/**/main",
        ]:
            ruleset = {
                "target": "branch",
                "conditions": {
                    "ref_name": {"include": ["refs/heads/main"], "exclude": [excluded]}
                },
            }
            with self.subTest(excluded=excluded):
                self.assertFalse(POLICY.applies_to_main(ruleset))
        ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/release/*"]
        self.assertTrue(POLICY.applies_to_main(ruleset))
        ruleset["conditions"]["ref_name"]["exclude"] = ["refs/*main"]
        self.assertTrue(POLICY.applies_to_main(ruleset))
        for included in [
            ["~ALL"],
            ["refs/heads/m*"],
            ["refs/heads/main", "refs/heads/release/*"],
        ]:
            ruleset["conditions"]["ref_name"] = {"include": included, "exclude": []}
            self.assertFalse(POLICY.applies_to_main(ruleset))

    def test_selects_applicable_ruleset_instead_of_excluded_one(self) -> None:
        """Ignore an earlier active ruleset even when it has check requirements."""
        details = {
            "rulesets/1": {
                "id": 1,
                "target": "branch",
                "conditions": {
                    "ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/main"]}
                },
                "rules": [{"type": "required_status_checks"}],
            },
            "rulesets/2": {
                "id": 2,
                "target": "branch",
                "conditions": {
                    "ref_name": {"include": ["refs/heads/main"], "exclude": []}
                },
                "rules": [],
            },
        }
        candidates = [
            {"id": number, "source_type": "Repository", "enforcement": "active"}
            for number in [1, 2]
        ]

        def api(path: str) -> object:
            if path.startswith("rulesets?"):
                return candidates
            return details[path]

        with patch.object(POLICY, "api", side_effect=api):
            self.assertEqual(POLICY.main_ruleset()["id"], 2)


if __name__ == "__main__":
    unittest.main()
