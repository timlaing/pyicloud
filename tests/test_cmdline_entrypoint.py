"""Tests for the backward-compatible command line entrypoint."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import patch

import pytest

from pyicloud import cmdline


def test_main_shows_install_hint_when_typer_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return 1 and print an install hint when Typer is unavailable."""

    with patch(
        "builtins.__import__",
        side_effect=ModuleNotFoundError("No module named 'typer'", name="typer"),
    ):
        code = cmdline.main()

    captured = capsys.readouterr()
    assert code == 1
    assert "dependencies are not installed" in captured.err
    assert "[cli]" in captured.err


def test_main_calls_cli_main_when_available() -> None:
    """Delegate to pyicloud.cli.app.main when imports succeed."""

    fake_cli_app = ModuleType("pyicloud.cli.app")
    fake_cli_app.main = lambda: 7  # type: ignore[assignment]

    with patch.dict("sys.modules", {"pyicloud.cli.app": fake_cli_app}):
        assert cmdline.main() == 7


def test_main_reraises_unrelated_missing_module() -> None:
    """Do not swallow ModuleNotFoundError for unrelated imports."""

    with patch(
        "builtins.__import__",
        side_effect=ModuleNotFoundError(
            "No module named 'pyicloud.cli.commands'", name="pyicloud.cli.commands"
        ),
    ):
        with pytest.raises(ModuleNotFoundError):
            cmdline.main()
