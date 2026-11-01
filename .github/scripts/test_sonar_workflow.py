"""Exercise the trusted Sonar workflow commands with untrusted local inputs.

Run directly with Python; these integration tests need only the standard
library, bash and jq, and never use GitHub or Sonar credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "sonar.yml"
HEAD_SHA = "a" * 40


def run_command(name: str) -> str:
    """Read the actual literal run block, avoiding a second validator copy."""
    section = WORKFLOW.read_text(encoding="utf-8").split(f"      - name: {name}\n")[1]
    section = section.split("\n      - ", 1)[0]
    match = re.search(r"        run: \|\n((?:          .*\n|\n)+)", section)
    if match is None:
        raise AssertionError(f"No literal run block for {name}")
    return textwrap.dedent(match.group(1))


class SonarWorkflowTests(unittest.TestCase):
    """Run workflow blocks in temporary checkout and artifact directories."""

    def setUp(self) -> None:
        # unittest cleanups run even if setup fails; the lifetime spans test calls.
        self.directory = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.coverage = self.root / "coverage"
        self.coverage.mkdir()
        self.report = self.coverage / "coverage.xml"
        self.report.write_text("<coverage/>\n", encoding="utf-8")
        self.output = self.root / "outputs"
        self.env = {
            "PATH": os.environ["PATH"],
            "COVERAGE_DIR": str(self.coverage),
            "GITHUB_OUTPUT": str(self.output),
            "HEAD_SHA": HEAD_SHA,
            "RUN_EVENT": "pull_request",
            "PR_NUMBER": "123",
            "HEAD_REF": "contributor/topic",
            "BASE_REF": "main",
        }

    def execute(self, name: str) -> subprocess.CompletedProcess[str]:
        """Execute the production block without inheriting process secrets."""
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", run_command(name)],
            cwd=self.checkout,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_valid_reports(self) -> None:
        """UTF-8 reports and a plain legacy DOCTYPE remain accepted."""
        for text in ("<coverage/>", "<!DOCTYPE coverage><coverage/>"):
            with self.subTest(text=text):
                self.report.write_text(text, encoding="utf-8")
                result = self.execute("Validate coverage report")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejected_reports(self) -> None:
        """Malformed, oversized, entity and alternate-encoding inputs fail."""
        for data in (
            b"<coverage>",
            b" " * 10_000_000,
            b'<!DOCTYPE coverage [<!ENTITY x "value">]><coverage>&x;</coverage>',
            '<!DOCTYPE coverage [<!ENTITY x "value">]><coverage>&x;</coverage>'.encode(
                "utf-16"
            ),
        ):
            with self.subTest(size=len(data)):
                self.report.write_bytes(data)
                self.assertNotEqual(
                    self.execute("Validate coverage report").returncode, 0
                )

    def test_missing_report(self) -> None:
        """Missing input must not become a successful scan without coverage."""
        self.report.unlink()
        self.assertNotEqual(self.execute("Validate coverage report").returncode, 0)

    def test_linked_report(self) -> None:
        """An artifact symlink must not expose a runner file to the parser."""
        target = self.root / "outside.xml"
        self.report.rename(target)
        self.report.symlink_to(target)
        self.assertNotEqual(self.execute("Validate coverage report").returncode, 0)

    def test_checkout_python_modules_are_not_executed(self) -> None:
        """Both module/package shadowing and PYTHONPATH are isolated."""
        marker = self.root / "executed"
        payload = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        module = self.checkout / "xml.py"
        package = self.checkout / "xml"
        self.env["PYTHONPATH"] = str(self.checkout)
        for as_package in (False, True):
            with self.subTest(as_package=as_package):
                if as_package:
                    module.unlink()
                    package.mkdir()
                    (package / "__init__.py").write_text(payload, encoding="utf-8")
                else:
                    module.write_text(payload, encoding="utf-8")
                result = self.execute("Validate coverage report")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(marker.exists())

    def test_prepare_removes_scanner_config_without_loading_modules(self) -> None:
        """The preparation block is isolated too and removes scanner config."""
        config = self.checkout / "sonar-project.properties"
        config.write_text("sonar.host.url=https://example.invalid", encoding="utf-8")
        (self.checkout / "pathlib.py").write_text(
            "raise SystemExit('untrusted import')", encoding="utf-8"
        )
        result = self.execute("Prepare scan inputs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(config.exists())

    def test_prepare_rejects_linked_inputs(self) -> None:
        """Source, coverage and scanner-work links fail before artifact download."""
        for name in (
            "pyicloud",
            ".coverage",
            ".scannerwork",
            "sonar-project.properties",
        ):
            with self.subTest(name=name):
                link = self.checkout / name
                link.symlink_to(self.coverage)
                self.assertNotEqual(self.execute("Prepare scan inputs").returncode, 0)
                link.unlink()

    def test_push_metadata(self) -> None:
        """A branch scan keeps revision attribution without PR outputs."""
        self.env["RUN_EVENT"] = "push"
        result = self.execute("Validate run metadata")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), f"head-sha={HEAD_SHA}\n")

    def test_same_repository_metadata(self) -> None:
        """The direct event path preserves all scanner PR arguments."""
        result = self.execute("Validate run metadata")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.output.read_text(),
            f"head-sha={HEAD_SHA}\npr-number=123\n"
            "head-ref=contributor/topic\nbase-ref=main\n",
        )

    def test_fork_metadata(self) -> None:
        """Fork runs resolve by exact head SHA without invoking real gh."""
        self.env["PR_NUMBER"] = ""
        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        fake_gh = binary_dir / "gh"
        response = json.dumps([
            {"number": 1, "headRefOid": "b" * 40},
            {
                "number": 456,
                "headRefOid": HEAD_SHA,
                "headRefName": "fork/topic",
                "baseRefName": "main",
            },
        ])
        fake_gh.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{response}'\n", encoding="utf-8"
        )
        fake_gh.chmod(0o700)
        self.env["PATH"] = str(binary_dir) + os.pathsep + self.env["PATH"]
        result = self.execute("Validate run metadata")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pr-number=456\nhead-ref=fork/topic\n", self.output.read_text())

    def test_untrusted_metadata_is_not_executed(self) -> None:
        """Shell metacharacters in branch names are data, then rejected."""
        marker = self.root / "executed"
        self.env["HEAD_REF"] = f'branch";touch {marker};#'
        self.assertNotEqual(self.execute("Validate run metadata").returncode, 0)
        self.assertFalse(marker.exists())

    def test_token_is_scoped_to_scanners(self) -> None:
        """The real token is absent from job and validation environments."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        prefix, scanners = workflow.split("      - name: SonarQube Scan (branch)", 1)
        self.assertNotIn("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", prefix)
        self.assertEqual(scanners.count("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}"), 2)
        self.assertIn("HAS_SONAR_TOKEN: ${{ secrets.SONAR_TOKEN != '' }}", prefix)
        self.assertNotIn("pull-requests: write", workflow)


if __name__ == "__main__":
    unittest.main()
