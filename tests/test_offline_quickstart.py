"""The documented library-only offline example must not request credentials or Apple."""

from pathlib import Path
import tempfile
from unittest.mock import patch

from pyicloud import PyiCloudService


def test_offline_quickstart() -> None:
    """Construction stays offline; service probing is deliberately not exercised."""
    root = Path(tempfile.gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=root) as cookie_directory,
        patch("socket.socket", side_effect=AssertionError("Unexpected network")),
        patch(
            "pyicloud.base.get_password_from_keyring",
            side_effect=AssertionError("Unexpected keyring"),
        ),
    ):
        api = PyiCloudService(
            "offline@example.invalid",
            cookie_directory=cookie_directory,
            authenticate=False,
        )
        try:
            assert api.session.data["client_id"]
        finally:
            api.session.close()
