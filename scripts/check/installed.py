"""Smoke-check an installed wheel outside the checkout, without Apple access.

Run with the target environment's ``python -I``; add ``--cli`` for the CLI extra.
This check intentionally uses only the standard library and installed package.
"""

from __future__ import annotations

import argparse
import importlib
from importlib.metadata import version
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main() -> None:
    """Check installed imports, offline construction and the optional CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true")
    args = parser.parse_args()

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
        importlib.import_module(f"pyicloud.services.{name}")

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
        assert api.session.data["client_id"]
        api.session.close()

    if args.cli:
        executable = Path(sys.executable).with_name(
            "icloud.exe" if os.name == "nt" else "icloud"
        )
        for command in (("--help",), ("--version",), ("photos", "--help")):
            result = subprocess.run(
                [str(executable), *command],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.stdout.strip()
            if command == ("--version",):
                assert result.stdout.strip() == version("pyicloud")
    else:
        for name in ("click", "typer", "rich"):
            assert importlib.util.find_spec(name) is None, name

    print(f"Installed package checks passed on Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
