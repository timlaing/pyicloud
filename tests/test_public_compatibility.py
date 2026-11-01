"""The frozen contract is reviewed independently of the current implementation."""

import json
from pathlib import Path
import tempfile
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from pyicloud import AppleDevice, PyiCloudService
from pyicloud.base import AppleDevice as BaseAppleDevice
from pyicloud.cli.app import app
from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.services.mail import MailService
from pyicloud.services.mail.service import MailService as MailImplementation
from pyicloud.services.photos import PhotosService
from pyicloud.services.photos.service import PhotosService as PhotosImplementation
from pyicloud.session import PyiCloudSession
from scripts.check.compatibility import check_manifest, signature_errors
from tests.fixture_loader import load_fixture


def test_released_signatures_and_imports() -> None:
    """A checked-in released snapshot must survive internal refactoring."""
    assert not check_manifest(load_fixture("compatibility", "public.json"))
    assert AppleDevice is BaseAppleDevice


def test_signature_checker_detects_breaks() -> None:
    """The gate rejects positional drift, default drift and newly required inputs."""

    def changed(second: str, first: str = "new", *, required: str) -> None:
        """Synthetic incompatible implementation."""
        del second, first, required

    expected: list[dict[str, Any]] = [
        {
            "name": "first",
            "kind": "POSITIONAL_OR_KEYWORD",
            "default": None,
            "required": True,
        },
        {
            "name": "second",
            "kind": "POSITIONAL_OR_KEYWORD",
            "default": "'old'",
            "required": False,
        },
    ]
    errors = signature_errors(changed, expected)
    assert "positional parameter order changed" in errors
    assert "default changed: second" in errors
    assert "new required parameter: required" in errors


def test_manifest_checker_detects_removed_import() -> None:
    """A missing symbol must fail even when ordinary success tests pass."""
    manifest = load_fixture("compatibility", "public.json")
    manifest["imports"].append("pyicloud:RemovedPublicSymbol")
    assert "missing import: pyicloud:RemovedPublicSymbol" in check_manifest(manifest)


def test_auth_status_return_shape(pyicloud_service_working: PyiCloudService) -> None:
    """Keep the no-session result useful to existing clients."""
    manifest = load_fixture("compatibility", "public.json")
    pyicloud_service_working.session.data.clear()
    result = pyicloud_service_working.get_auth_status()
    assert set(result) == set(manifest["auth_status_keys"])
    assert set(result.values()) == {False}


def test_doctor_json_contract_and_exit_code() -> None:
    """Offline diagnostics retain the automation-facing keys and failure status."""
    manifest = load_fixture("compatibility", "public.json")
    session = {"authenticated": False, "error": "synthetic missing session"}
    with (
        patch("pyicloud.cli.commands.doctor.environment", return_value={}),
        patch(
            "pyicloud.cli.commands.doctor._session_report", return_value=(session, None)
        ),
    ):
        result = CliRunner().invoke(app, ["doctor", "--format", "json"])
    assert result.exit_code == 1, (result.output, result.exception)
    payload = json.loads(result.output)
    assert set(payload) == set(manifest["doctor_keys"])
    assert payload["ok"] is False
    assert payload["webservices"] == []
    assert payload["probes"] == []


def test_released_persisted_formats(pyicloud_service_working: PyiCloudService) -> None:
    """Read and re-save synthetic release-format JSON and LWP cookies."""
    manifest = load_fixture("compatibility", "public.json")
    root = Path(tempfile.gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        path = Path(directory)
        try:
            session = PyiCloudSession(pyicloud_service_working, "fresh", directory)
            Path(session.session_path).write_text(
                json.dumps(manifest["saved_session"]), encoding="utf-8"
            )
            Path(session.cookiejar_path).write_text(
                manifest["saved_cookies"], encoding="utf-8"
            )
            restored = PyiCloudSession(pyicloud_service_working, "fresh", directory)
            assert restored.data == manifest["saved_session"]
            assert restored.cookies.get("compatibility") == "synthetic-cookie"
            restored._save_session_data()  # pylint: disable=protected-access
            assert (
                json.loads(Path(session.session_path).read_text(encoding="utf-8"))
                == manifest["saved_session"]
            )
            jar = PyiCloudCookieJar(session.cookiejar_path)
            jar.load(ignore_discard=True, ignore_expires=True)
            assert jar.get("compatibility") == "synthetic-cookie"
        finally:
            for child in path.iterdir():
                child.unlink()


def test_current_service_facades_keep_class_identity() -> None:
    """Later Photos/Mail APIs keep their defining modules during private refactors."""
    assert MailService is MailImplementation
    assert MailService.__module__ == "pyicloud.services.mail.service"
    assert PhotosService is PhotosImplementation
    assert PhotosService.__module__ == "pyicloud.services.photos.service"
