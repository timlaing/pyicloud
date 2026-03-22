"""Backward-compatible command line entrypoint."""

from pyicloud.cli.app import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
