"""Check shared runtime requirements against the latest stable Home Assistant."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import posixpath
import re
import urllib.request

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def incompatible(requirements: str, pins: str) -> list[str]:
    """Return shared requirements that reject HA's exact pinned version."""
    versions = {}
    for line in pins.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        for specifier in requirement.specifier:
            if specifier.operator == "==" and "*" not in specifier.version:
                versions[canonicalize_name(requirement.name)] = specifier.version
    errors = []
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        version = versions.get(canonicalize_name(requirement.name))
        if version and (
            requirement.url
            or not requirement.specifier.contains(version, prereleases=True)
        ):
            errors.append(f"{requirement} rejects Home Assistant's pin {version}")
    return errors


def read_url(url: str) -> str:
    """Fetch public release metadata without repository credentials."""
    with urllib.request.urlopen(url, timeout=30) as response:
        data: bytes = response.read()
        return data.decode("utf-8")


def load_pins(tag: str, fetch: Callable[[str], str] = read_url) -> str:
    """Read requirement and constraint includes from the same immutable HA tag."""
    pending = ["requirements_all.txt"]
    seen: set[str] = set()
    lines = []
    while pending:
        filename = pending.pop()
        if filename in seen:
            continue
        if len(seen) >= 20:
            raise ValueError("Too many Home Assistant requirement includes")
        seen.add(filename)
        text = fetch(
            f"https://raw.githubusercontent.com/home-assistant/core/{tag}/{filename}"
        )
        for line in text.splitlines():
            include = re.fullmatch(
                r"\s*(?:-r|-c|--requirement|--constraint)\s+([\w./-]+)\s*", line
            )
            if include:
                path = posixpath.normpath(
                    posixpath.join(posixpath.dirname(filename), include[1])
                )
                if path.startswith(("/", "../")) or not path.endswith(".txt"):
                    raise ValueError(f"Invalid HA requirement include: {path}")
                pending.append(path)
            elif line.strip().startswith("-"):
                raise ValueError(f"Unsupported HA requirement directive: {line}")
            else:
                lines.append(line)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Resolve HA's stable release once, then compare immutable release pins."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release", help="Stable HA release resolved by authenticated CI"
    )
    tag = parser.parse_args(argv).release
    if tag is None:
        with urllib.request.urlopen(
            "https://api.github.com/repos/home-assistant/core/releases/latest",
            timeout=30,
        ) as response:
            tag = json.load(response)["tag_name"]
    if not re.fullmatch(r"\d{4}\.\d+\.\d+", tag):
        raise ValueError(f"Unexpected Home Assistant stable release: {tag}")
    print(f"Comparing runtime requirements with Home Assistant {tag}")
    pins = load_pins(tag)
    requirements = "\n".join(
        Path(name).read_text(encoding="utf-8")
        for name in ("requirements.txt", "requirements_cli.txt", "requirements_mcp.txt")
    )
    errors = incompatible(requirements, pins)
    for error in errors:
        print(f"::error::{error}")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
