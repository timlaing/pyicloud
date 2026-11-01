"""Persisted session recovery preserves valid dictionaries and independent cookies."""

from collections.abc import Iterator
import json
import logging
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from pyicloud import PyiCloudService
from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.session import PyiCloudSession


@pytest.fixture(name="session_directory")
def session_directory_fixture() -> Iterator[Path]:
    """Allocate files inside the repository's required filesystem sandbox."""
    root = Path(tempfile.gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        path = Path(directory)
        try:
            yield path
        finally:
            # Use absolute paths; the filesystem guard deliberately rejects
            # shutil's descriptor-relative unlink operations.
            for child in path.iterdir():
                child.unlink()


@pytest.mark.parametrize(
    "content", [b"[]", b"null", b"42", b'"secret-marker"', b"true", b"{", b"\xff"]
)
def test_invalid_session_recovers_and_keeps_cookies(
    pyicloud_service_working: PyiCloudService,
    session_directory: Path,
    caplog: pytest.LogCaptureFixture,
    content: bytes,
) -> None:
    """Invalid JSON shapes, syntax and encoding do not prevent construction."""
    session = PyiCloudSession(pyicloud_service_working, "old", str(session_directory))
    Path(session.session_path).write_bytes(content)
    session.cookies.set("retained", "cookie-secret", domain="icloud.com", path="/")
    assert isinstance(session.cookies, PyiCloudCookieJar)
    session.cookies.save()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pyicloud.session"):
        recovered = PyiCloudSession(
            pyicloud_service_working, "fresh", str(session_directory)
        )
    assert recovered.data == {"client_id": "fresh"}
    assert recovered.cookies.get("retained") == "cookie-secret"
    assert "ignoring persisted state" in caplog.text
    assert "secret-marker" not in caplog.text
    assert "cookie-secret" not in caplog.text


def test_valid_session_keeps_existing_values(
    pyicloud_service_working: PyiCloudService, session_directory: Path
) -> None:
    """Existing client IDs and unknown fields survive without a schema migration."""
    session = PyiCloudSession(pyicloud_service_working, "old", str(session_directory))
    values = {
        "client_id": "restored",
        "future_field": {"value": [1, True]},
        "session_token": "synthetic",
    }
    Path(session.session_path).write_text(json.dumps(values), encoding="utf-8")
    restored = PyiCloudSession(
        pyicloud_service_working, "fresh", str(session_directory)
    )
    assert restored.data == values


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError(), "does not exist"),
        (PermissionError("secret-marker"), "could not be read"),
    ],
)
def test_session_read_errors_have_safe_distinct_diagnostics(
    pyicloud_service_working: PyiCloudService,
    session_directory: Path,
    caplog: pytest.LogCaptureFixture,
    error: OSError,
    message: str,
) -> None:
    """Read errors recover without including potentially sensitive exception text."""
    with (
        patch("pyicloud.session.open", side_effect=error),
        caplog.at_level(logging.INFO, logger="pyicloud.session"),
    ):
        session = PyiCloudSession(
            pyicloud_service_working, "fresh", str(session_directory)
        )
    assert session.data == {"client_id": "fresh"}
    assert message in caplog.text
    assert "secret-marker" not in caplog.text
