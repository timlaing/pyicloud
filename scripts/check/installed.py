"""Smoke-check an installed wheel outside the checkout, without Apple access.

Run with the target environment's ``python -I``; add ``--cli`` for the CLI extra.
This check intentionally uses only the standard library and installed package.
"""

# Imports of optional dependencies must remain behind the CLI-extra check.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import argparse
import importlib
from importlib.metadata import version
from importlib.resources import files
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


def check_cli(manifest: dict[str, Any]) -> None:
    """Exercise the console entry point without Python import-path overrides."""
    executable = Path(sys.executable).with_name(
        "icloud.exe" if os.name == "nt" else "icloud"
    )
    # The parent uses -I, but a console script starts a fresh interpreter.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
    }
    for command in (
        ("--help",),
        ("--version",),
        ("photos", "--help"),
        ("reminders", "--help"),
        ("mail", "--help"),
    ):
        result = subprocess.run(
            [str(executable), *command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        assert result.stdout.strip()
        if command == ("--version",):
            assert result.stdout.strip() == version("pyicloud")

    from typer.testing import CliRunner  # pylint: disable=import-outside-toplevel

    from pyicloud.cli.app import app  # pylint: disable=import-outside-toplevel
    from pyicloud.services.reminders.models import (
        RemindersList,  # pylint: disable=import-outside-toplevel
    )

    fake_api = SimpleNamespace(
        account_name="Synthetic",
        reminders=SimpleNamespace(
            lists=lambda: [RemindersList(id="List/X", title="Synthetic")]
        ),
    )
    with patch("pyicloud.cli.context.CLIState.get_api", return_value=fake_api):
        cli_result = CliRunner().invoke(app, ["reminders", "lists", "--format", "json"])
    assert cli_result.exit_code == manifest["cli"]["lists_exit"], cli_result.exception
    rows = json.loads(cli_result.stdout)
    assert len(rows) == 1
    assert {key: rows[0][key] for key in manifest["list_shape"]} == manifest[
        "list_shape"
    ]
    invalid = CliRunner().invoke(app, ["not-a-command"])
    assert invalid.exit_code == manifest["cli"]["usage_exit"]


def check_persistence(api: Any, manifest: dict[str, Any], cookie_dir: str) -> None:
    """Exercise recorded JSON and LWP formats using only synthetic saved state."""
    import pyicloud  # pylint: disable=import-outside-toplevel
    from pyicloud.session import (
        NON_PERSISTED_SESSION_KEYS,  # pylint: disable=import-outside-toplevel
    )

    api.session.data.update(manifest["session"])
    api.session.data.update({
        key: "not-persisted" for key in NON_PERSISTED_SESSION_KEYS
    })
    api.session.cookies.set("compatibility", "synthetic", domain="example.invalid")
    api.session._save_session_data()  # pylint: disable=protected-access
    saved = json.loads(Path(api.session.session_path).read_text(encoding="utf-8"))
    assert saved == manifest["session"]
    assert (
        Path(api.session.cookiejar_path)
        .read_text(encoding="utf-8")
        .startswith(manifest["cookie_header"])
    )
    restored = pyicloud.PyiCloudService(
        "smoke@example.invalid", cookie_directory=cookie_dir, authenticate=False
    )
    try:
        assert restored.session.data == manifest["session"]
        assert restored.session.cookies.get("compatibility") == "synthetic"
    finally:
        restored.session.close()


def main() -> None:
    """Check installed imports, offline construction and the optional CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    with args.manifest.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    spec = importlib.util.spec_from_file_location(
        "compatibility", Path(__file__).with_name("compatibility.py")
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.check_manifest(manifest)

    # A source-tree import can otherwise hide missing wheel files/dependencies.
    import pyicloud  # pylint: disable=import-outside-toplevel

    assert pyicloud.__file__ is not None
    assert Path(pyicloud.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert pyicloud.AppleDevice is not None
    for name in (
        "account",
        "calendar",
        "contacts",
        "drive",
        "findmyiphone",
        "hidemyemail",
        "invites",
        "mail",
        "notes",
        "photos",
        "reminders",
        "ubiquity",
        "notes.protobuf.notes_pb2",
        "reminders.protobuf.reminders_pb2",
    ):
        module = importlib.import_module(f"pyicloud.services.{name}")
        assert module.__file__ is not None
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        ), name
    assert files("pyicloud.services.notes.protobuf").joinpath("notes_pb2.pyi").is_file()

    with (
        tempfile.TemporaryDirectory() as cookie_dir,
        patch("socket.socket", side_effect=AssertionError("Unexpected network access")),
        patch(
            "pyicloud.base.get_password_from_keyring",
            side_effect=AssertionError("Unexpected keyring access"),
        ),
    ):
        api = pyicloud.PyiCloudService(
            "smoke@example.invalid", cookie_directory=cookie_dir, authenticate=False
        )
        try:
            assert api.session.data["client_id"]
            check_persistence(api, manifest, cookie_dir)
            if args.cli:
                check_cli(manifest)
            else:
                for name in ("click", "typer", "rich"):
                    assert importlib.util.find_spec(name) is None, name
        finally:
            api.session.close()

    print(f"Installed package checks passed on Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
