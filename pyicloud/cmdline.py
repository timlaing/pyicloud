"""Backward-compatible command line entrypoint."""
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import sys

__all__: list[str] = ["main"]


def main() -> int:
    """Run the CLI app or show an actionable error when optional deps are missing."""

    try:
        from pyicloud.cli.app import main as cli_main
    except ModuleNotFoundError as err:
        missing_root: str = (err.name or "").split(".", 1)[0]  # pylint: disable=no-member
        if missing_root in {"click", "typer"}:
            sys.stderr.write(
                "PyiCloud CLI dependencies are not installed. "
                "Install with: pip install 'pyicloud[cli]'\n"
            )
            return 1
        raise
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
