"""Check reviewed public call contracts without installing an old release."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from pathlib import Path
from typing import Any


def resolve(target: str) -> Any:
    """Resolve a manifest module:symbol path without calling the symbol."""
    module, symbol = target.split(":", 1)
    value: Any = importlib.import_module(module)
    for part in symbol.split("."):
        value = getattr(value, part)
    return value


def signature_errors(function: Any, expected: list[dict[str, Any]]) -> list[str]:
    """Reject removals, shifted positions, changed defaults and new requirements."""
    current = inspect.signature(function).parameters
    errors = []
    positional = [
        p.name
        for p in current.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    old_positional = [
        p["name"]
        for p in expected
        if p["kind"] in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")
    ]
    if positional[: len(old_positional)] != old_positional:
        errors.append("positional parameter order changed")
    for previous in expected:
        name = previous["name"]
        if name not in current:
            errors.append(f"missing parameter: {name}")
            continue
        parameter = current[name]
        if parameter.kind.name != previous["kind"]:
            errors.append(f"parameter kind changed: {name}")
        required = parameter.default is inspect.Parameter.empty
        if required != previous["required"] or (
            not required and repr(parameter.default) != previous["default"]
        ):
            errors.append(f"default changed: {name}")
    old_names = {p["name"] for p in expected}
    for name, parameter in current.items():
        if (
            name not in old_names
            and parameter.default is inspect.Parameter.empty
            and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        ):
            errors.append(f"new required parameter: {name}")
    return errors


def check_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return actionable failures, allowing deliberate API additions."""
    errors = []
    for target in manifest["imports"]:
        try:
            resolve(target)
        except (ImportError, AttributeError):
            errors.append(f"missing import: {target}")
    for target, parameters in manifest["signatures"].items():
        try:
            errors.extend(
                f"{target}: {error}"
                for error in signature_errors(resolve(target), parameters)
            )
        except (ImportError, AttributeError):
            errors.append(f"missing callable: {target}")
    for target, parent in manifest["exception_bases"].items():
        if not issubclass(resolve(target), resolve(parent)):
            errors.append(f"exception base changed: {target}")
    for target in manifest["properties"]:
        if not isinstance(resolve(target), property):
            errors.append(f"property changed: {target}")
    return errors


def main() -> None:
    """Run against checkout imports or an installed wheel under python -I."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = check_manifest(manifest)
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Compatibility baseline {manifest['baseline']['release']} passed")


if __name__ == "__main__":
    main()
