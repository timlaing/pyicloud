"""Compare public interfaces to a reviewed, released baseline, without network I/O."""

# AST field names are Python's public API, not natural-language spelling.
# cspell:ignore posonlyargs vararg kwonlyargs

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
from pathlib import Path
from typing import Any


def resolve_symbol(path: str) -> Any:
    """Resolve a supported dotted import without guessing replacement symbols."""
    parts = path.split(".")
    obj: Any = importlib.import_module(parts[0])
    for part in parts[1:]:
        if inspect.ismodule(obj) and not hasattr(obj, part):
            importlib.import_module(f"{obj.__name__}.{part}")
        obj = getattr(obj, part)
    return obj


def public_signature(obj: Any) -> str:
    """Record call parameters, kinds and defaults, independent of annotations."""
    signature = inspect.signature(obj)
    return str(
        signature.replace(
            parameters=[
                parameter.replace(annotation=inspect.Parameter.empty)
                for parameter in signature.parameters.values()
            ],
            return_annotation=inspect.Signature.empty,
        )
    )


def signature_parts(signature: str) -> list[tuple[str, str, str | None]]:
    """Parse signatures as data, never execute manifest contents."""
    function = ast.parse(f"def baseline{signature}: pass").body[0]
    assert isinstance(function, ast.FunctionDef)
    args = function.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts = [
        (
            arg.arg,
            "positional-only"
            if index < len(args.posonlyargs)
            else "positional-or-keyword",
            ast.dump(default) if default is not None else None,
        )
        for index, (arg, default) in enumerate(zip(positional, defaults, strict=True))
    ]
    if args.vararg:
        parts.append((args.vararg.arg, "var-positional", None))
    parts.extend(
        (arg.arg, "keyword-only", ast.dump(default) if default is not None else None)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    )
    if args.kwarg:
        parts.append((args.kwarg.arg, "var-keyword", None))
    return parts


def compatible_signature(expected: str, actual: str) -> bool:
    """Allow optional keyword-only additions, never positional shifts or removals."""
    baseline = signature_parts(expected)
    current = signature_parts(actual)
    names = {part[0] for part in baseline}
    additions = [part for part in current if part[0] not in names]
    return (
        all(
            kind == "keyword-only" and default is not None
            for _, kind, default in additions
        )
        and [part for part in current if part[0] in names] == baseline
    )


def check_manifest(manifest: dict[str, Any]) -> None:
    """Fail with the affected symbol when a recorded consumer contract changes."""
    for path, expected in manifest["signatures"].items():
        actual = public_signature(resolve_symbol(path))
        assert compatible_signature(expected, actual), f"{path}: {expected} != {actual}"
    for path, module in manifest["class_modules"].items():
        assert resolve_symbol(path).__module__ == module, path
    for path, bases in manifest["exceptions"].items():
        actual = resolve_symbol(path)
        for base in bases:
            assert issubclass(actual, resolve_symbol(base)), (
                f"{path} no longer derives from {base}"
            )


def main() -> None:
    """Read a frozen manifest; there is deliberately no baseline-update command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    with args.manifest.open(encoding="utf-8") as handle:
        check_manifest(json.load(handle))
    print("Released public compatibility checks passed")


if __name__ == "__main__":
    main()
