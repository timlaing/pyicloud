"""Tests for the backward-compatible command line entrypoint."""

from __future__ import annotations

import builtins
from collections.abc import Callable
import sys
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest

from pyicloud import cmdline


class _FakeCliApp(ModuleType):
    """Typed fake of the ``pyicloud.cli.app`` module."""

    app: object
    main: Callable[[], int]


class _FakeCli(ModuleType):
    """Typed fake of the ``pyicloud.cli`` package module."""

    app: _FakeCliApp


def test_main_shows_install_hint_when_typer_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return 1 and print an install hint when Typer is unavailable."""
    _real = cast(Callable[..., object], builtins.__import__)

    def _side_effect(name: str, *args: object, **kwargs: object) -> object:
        if name in {"typer", "click"}:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return _real(name, *args, **kwargs)

    # Remove cached CLI modules so they are freshly imported and trigger the
    # typer import (which our side_effect then blocks).
    cli_keys = [k for k in sys.modules if k.startswith("pyicloud.cli")]
    with patch.dict("sys.modules", {}, clear=False):
        for k in cli_keys:
            del sys.modules[k]
        with patch("builtins.__import__", side_effect=_side_effect):
            code = cmdline.main()

    captured = capsys.readouterr()
    assert code == 1
    assert "dependencies are not installed" in captured.err
    assert "[cli]" in captured.err


def test_main_calls_cli_main_when_available() -> None:
    """Delegate to pyicloud.cli.app.main when imports succeed."""

    fake_cli_app = _FakeCliApp("pyicloud.cli.app")
    fake_cli_app.main = lambda: 7
    fake_cli_app.app = None
    fake_cli = _FakeCli("pyicloud.cli")
    fake_cli.app = fake_cli_app

    with patch.dict(
        "sys.modules",
        {"pyicloud.cli": fake_cli, "pyicloud.cli.app": fake_cli_app},
    ):
        assert cmdline.main() == 7


def test_main_reraises_unrelated_missing_module() -> None:
    """Do not swallow ModuleNotFoundError for unrelated imports."""
    _real = cast(Callable[..., object], builtins.__import__)

    def _side_effect(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("pyicloud.cli.commands"):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return _real(name, *args, **kwargs)

    cli_keys = [k for k in sys.modules if k.startswith("pyicloud.cli")]
    with patch.dict("sys.modules", {}, clear=False):
        for k in cli_keys:
            del sys.modules[k]
        with (
            patch("builtins.__import__", side_effect=_side_effect),
            pytest.raises(ModuleNotFoundError),
        ):
            cmdline.main()
