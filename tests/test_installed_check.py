"""Regression checks for isolation of the installed-wheel CLI smoke check."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts.check.installed import check_cli


def test_cli_children_ignore_python_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console children cannot inherit source paths from an isolated parent."""
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONOPTIMIZE"):
        monkeypatch.setenv(name, "checkout-override")
    monkeypatch.setenv("PYICLOUD_SMOKE_SENTINEL", "preserved")
    outputs = ["Usage: icloud", "2026.10.1", "Usage: icloud photos"]
    with (
        patch("scripts.check.installed.version", return_value="2026.10.1"),
        patch(
            "scripts.check.installed.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess[str]([], 0, stdout=text) for text in outputs
            ],
        ) as run,
    ):
        check_cli()

    assert [call.args[0][1:] for call in run.call_args_list] == [
        ["--help"],
        ["--version"],
        ["photos", "--help"],
    ]
    for call in run.call_args_list:
        assert Path(call.args[0][0]).parent == Path(sys.executable).parent
        assert call.kwargs["check"] is True
        assert call.kwargs["timeout"] == 30
        environment = call.kwargs["env"]
        assert not any(key.startswith("PYTHON") for key in environment)
        assert environment["PYICLOUD_SMOKE_SENTINEL"] == "preserved"
        assert environment["PATH"]
