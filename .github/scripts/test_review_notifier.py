"""Exercise the trusted default-branch notification policy with hostile PR data."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / ".github/workflows"
POLICY = ROOT / "review-notifier-policy.yml"
NOTIFIER = ROOT / "review-events.yml"
DOCUMENT = yaml.safe_load(POLICY.read_text())
STEP = next(
    item
    for item in DOCUMENT["jobs"]["validate"]["steps"]
    if item.get("id") == "validate"
)
SCRIPT = "\n".join(STEP["run"].splitlines()[1:-1])


class ReviewNotifierTests(unittest.TestCase):
    """Execute the real isolated validator, without credentials or network calls."""

    def setUp(self) -> None:
        """Initialize captured validator output for each fixture."""
        self.output = ""

    def execute(
        self, notifier: str, candidate: str
    ) -> subprocess.CompletedProcess[str]:
        """Run against a temporary candidate and the trusted workflow."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notifier.yml").write_text(notifier)
            protected = {}
            for relative in json.loads(
                DOCUMENT["jobs"]["validate"]["env"]["PROTECTED_PATHS"]
            ):
                trusted = (REPOSITORY / relative).read_text()
                protected[relative] = {
                    "trusted": trusted,
                    "candidate": candidate
                    if relative.endswith("review-notifier-policy.yml")
                    else trusted,
                }
            (root / "protected.json").write_text(json.dumps(protected))
            (root / "yaml.py").write_text('raise RuntimeError("PR module executed")')
            output = root / "output"
            output.write_text("")
            result = subprocess.run(  # noqa: S603 - trusted isolated validator
                [sys.executable, "-I", "-c", SCRIPT],
                cwd=root,
                env={"REVIEW_POLICY_INPUTS": str(root), "GITHUB_OUTPUT": str(output)},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.output = output.read_text()
            return result

    def test_current_policy_is_valid_and_imports_are_isolated(self) -> None:
        """The committed notifier and trusted policy agree."""
        result = self.execute(NOTIFIER.read_text(), POLICY.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output, "changed-actions=[]\n")

    def test_dismissal_or_readiness_cannot_be_removed(self) -> None:
        """A PR cannot remove either required lifecycle notification."""
        for event in ["dismissed", "ready_for_review"]:
            candidate = NOTIFIER.read_text().replace(event, "edited")
            with self.subTest(event=event):
                self.assertNotEqual(
                    self.execute(candidate, POLICY.read_text()).returncode, 0
                )

    def test_extra_filters_and_jobs_cannot_delay_notifications(self) -> None:
        """Filters, code execution and extended timeouts fail closed."""
        original = yaml.safe_load(NOTIFIER.read_text())
        original["on"]["pull_request"]["branches-ignore"] = ["**"]
        self.assertNotEqual(
            self.execute(yaml.safe_dump(original), POLICY.read_text()).returncode, 0
        )
        for candidate in [
            NOTIFIER.read_text().replace('"true"', '"sleep 600"'),
            NOTIFIER.read_text().replace("timeout-minutes: 2", "timeout-minutes: 60"),
        ]:
            self.assertNotEqual(
                self.execute(candidate, POLICY.read_text()).returncode, 0
            )

    def test_enforcement_code_cannot_be_replaced_or_removed(self) -> None:
        """Keeping the trigger while replacing the trusted validator is rejected."""
        for candidate in [
            "",
            POLICY.read_text().replace("createCommitStatus", "getCommit"),
        ]:
            self.assertNotEqual(
                self.execute(NOTIFIER.read_text(), candidate).returncode, 0
            )

    def test_aliases_are_rejected(self) -> None:
        """Alias expansion cannot exhaust the trusted validator."""
        candidate = POLICY.read_text() + "\nextra: &value [*value]\n"
        self.assertNotEqual(self.execute(NOTIFIER.read_text(), candidate).returncode, 0)

    def test_duplicate_keys_are_rejected(self) -> None:
        """YAML ambiguity cannot alter the effective workflow behind the check."""
        candidate = POLICY.read_text() + "\nname: Replaced\n"
        self.assertNotEqual(self.execute(NOTIFIER.read_text(), candidate).returncode, 0)

    def test_action_updates_require_separate_provenance_verification(self) -> None:
        """Only same-action SHA changes reach the official-commit check."""
        pin = next(
            step["uses"]
            for step in DOCUMENT["jobs"]["validate"]["steps"]
            if step.get("uses", "").startswith("actions/github-script@")
        )
        trusted_text = "".join(
            (REPOSITORY / path).read_text()
            for path in json.loads(
                DOCUMENT["jobs"]["validate"]["env"]["PROTECTED_PATHS"]
            )
        )
        fixture_sha = next(
            letter * 40 for letter in "abcdef" if letter * 40 not in trusted_text
        )
        candidate = POLICY.read_text().replace(
            pin, "actions/github-script@" + fixture_sha
        )
        result = self.execute(NOTIFIER.read_text(), candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = json.loads(self.output.removeprefix("changed-actions="))
        self.assertEqual(
            updates, [{"repository": "actions/github-script", "sha": fixture_sha}]
        )
        candidate = POLICY.read_text().replace(
            "actions/github-script@", "attacker/action@"
        )
        self.assertNotEqual(self.execute(NOTIFIER.read_text(), candidate).returncode, 0)


if __name__ == "__main__":
    unittest.main()
