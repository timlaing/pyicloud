"""Exercise HA compatibility policy without network access."""

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "ha_requirements",
    Path(__file__).resolve().parents[2] / "scripts/check/ha_requirements.py",
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class HomeAssistantPolicyTests(unittest.TestCase):
    """Check compatibility rather than treating every newer release as a conflict."""

    def test_compatible_range_and_unshared_tools(self) -> None:
        """Only dependencies shared with HA impose a constraint."""
        self.assertEqual(
            POLICY.incompatible("requests>=2\nother>=99", "requests==2.33"), []
        )

    def test_incompatible_lower_upper_and_exact_bounds(self) -> None:
        """Reject every declared range that excludes the HA pin."""
        for requirement in ("requests>=3", "requests<2", "requests==2.32"):
            with self.subTest(requirement=requirement):
                self.assertEqual(
                    len(POLICY.incompatible(requirement, "requests==2.33")), 1
                )

    def test_normalization_extras_markers_and_comments(self) -> None:
        """Normalize package names and accept normal requirement metadata."""
        self.assertEqual(
            POLICY.incompatible(
                "# comment\nSome_Package[extra]>=2; python_version>'3'",
                "some-package==2.1",
            ),
            [],
        )

    def test_direct_url_cannot_establish_compatibility(self) -> None:
        """A direct URL cannot prove compatibility with the shared pin."""
        self.assertEqual(
            len(
                POLICY.incompatible(
                    "requests @ https://example.invalid/pkg.whl", "requests==2.33"
                )
            ),
            1,
        )

    def test_inactive_requirement_is_not_a_conflict(self) -> None:
        """Ignore an incompatible bound that cannot apply in this environment."""
        self.assertEqual(
            POLICY.incompatible("requests>=3; python_version<'0'", "requests==2.33"),
            [],
        )

    def test_inactive_pin_cannot_replace_an_applicable_pin(self) -> None:
        """Ignore inactive pins whether they precede or follow the active pin."""
        active = "requests==2.33; python_version>='3'"
        inactive = "requests==1.0; python_version<'0'"
        for pins in (f"{inactive}\n{active}", f"{active}\n{inactive}"):
            with self.subTest(pins=pins):
                self.assertEqual(POLICY.incompatible("requests>=2", pins), [])
                self.assertEqual(len(POLICY.incompatible("requests>=3", pins)), 1)

    def test_core_and_constraint_includes_use_the_same_release(self) -> None:
        """Follow core pins and constraints instead of discarding includes."""
        files = {
            "requirements_all.txt": "-r requirements.txt\nrequests==2.33",
            "requirements.txt": (
                "-c homeassistant/package_constraints.txt\ncryptography==50.0.2"
            ),
            "homeassistant/package_constraints.txt": "protobuf==7.0",
        }
        fetched = []

        def fetch(url: str) -> str:
            prefix = "https://raw.githubusercontent.com/home-assistant/core/2026.11.1/"
            self.assertTrue(url.startswith(prefix))
            name = url.removeprefix(prefix)
            fetched.append(name)
            return files[name]

        pins = POLICY.load_pins("2026.11.1", fetch)
        self.assertEqual(set(fetched), set(files))
        self.assertEqual(len(POLICY.incompatible("cryptography>=51", pins)), 1)
        self.assertEqual(len(POLICY.incompatible("protobuf<7", pins)), 1)


class ReleaseResolutionTests(unittest.TestCase):
    """CI may resolve the stable release with its read-only authenticated client."""

    def test_explicit_release_avoids_anonymous_metadata_api(self) -> None:
        """A CI-provided immutable tag still runs the full compatibility check."""
        with (
            patch.object(POLICY.urllib.request, "urlopen") as request,
            patch.object(POLICY, "load_pins", return_value="requests==2.33") as pins,
            patch.object(Path, "read_text", return_value="requests>=2"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(POLICY.main(["--release", "2026.11.1"]), 0)
            request.assert_not_called()
            pins.assert_called_once_with("2026.11.1")

    def test_invalid_release_never_fetches_pins(self) -> None:
        """An unchecked reference cannot select arbitrary branch content."""
        with (
            patch.object(POLICY, "load_pins") as pins,
            self.assertRaisesRegex(
                ValueError, "Unexpected Home Assistant stable release"
            ),
        ):
            POLICY.main(["--release", "main"])
        pins.assert_not_called()

    def test_local_default_resolves_release_and_reports_conflicts(self) -> None:
        """Local use retains metadata resolution and rejects incompatible bounds."""
        with (
            patch.object(
                POLICY.urllib.request,
                "urlopen",
                return_value=io.StringIO('{"tag_name":"2026.11.1"}'),
            ),
            patch.object(POLICY, "load_pins", return_value="requests==2.33"),
            patch.object(Path, "read_text", return_value="requests>=3"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(POLICY.main([]), 1)


if __name__ == "__main__":
    unittest.main()
