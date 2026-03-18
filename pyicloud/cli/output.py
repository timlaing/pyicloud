"""Shared output helpers for the Typer CLI."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

TABLE_TITLE_STYLE = "bold bright_cyan"
TABLE_HEADER_STYLE = "bold bright_cyan"
TABLE_BORDER_STYLE = None
TABLE_KEY_STYLE = "bold bright_white"
TABLE_ROW_STYLES: tuple[str, ...] = ()


class OutputFormat(str, Enum):
    """Supported output formats."""

    TEXT = "text"
    JSON = "json"


def json_default(value: Any) -> Any:
    """Serialize common CLI values to JSON-friendly structures."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, SimpleNamespace):
        return vars(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if hasattr(value, "raw_data"):
        return value.raw_data
    if hasattr(value, "data") and isinstance(value.data, dict):
        return value.data
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def to_json_string(payload: Any, *, indent: int | None = None) -> str:
    """Render a payload as JSON."""

    return json.dumps(
        payload,
        default=json_default,
        ensure_ascii=False,
        indent=indent,
        sort_keys=indent is not None,
    )


def write_json(console: Console, payload: Any) -> None:
    """Write a JSON payload to stdout."""

    console.print_json(json=to_json_string(payload))


def write_json_file(path: Path, payload: Any) -> None:
    """Write JSON to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json_string(payload, indent=2) + "\n", encoding="utf-8")


def console_table(
    title: str, columns: list[str], rows: Iterable[Iterable[Any]]
) -> Table:
    """Build a simple rich table."""

    table = Table(
        title=title,
        title_style=TABLE_TITLE_STYLE,
        header_style=TABLE_HEADER_STYLE,
        border_style=TABLE_BORDER_STYLE,
        row_styles=list(TABLE_ROW_STYLES),
    )
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*[("" if item is None else str(item)) for item in row])
    return table


def console_kv_table(title: str, rows: Iterable[tuple[str, Any]]) -> Table:
    """Build a two-column key/value table."""

    table = Table(
        title=title,
        title_style=TABLE_TITLE_STYLE,
        header_style=TABLE_HEADER_STYLE,
        border_style=TABLE_BORDER_STYLE,
        row_styles=list(TABLE_ROW_STYLES),
    )
    table.add_column("Field", style=TABLE_KEY_STYLE)
    table.add_column("Value")
    for key, value in rows:
        table.add_row(key, "" if value is None else str(value))
    return table


def print_json_text(console: Console, payload: Any) -> None:
    """Pretty-print a JSON object in text mode."""

    console.print_json(json=to_json_string(payload, indent=2))


def format_color_value(value: Any) -> str:
    """Return a compact human-friendly representation of reminder colors."""

    if not value:
        return ""

    payload = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        if not stripped.startswith("{"):
            return stripped
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped

    if isinstance(payload, dict):
        hex_value = payload.get("daHexString")
        symbolic = payload.get("ckSymbolicColorName") or payload.get(
            "daSymbolicColorName"
        )
        if hex_value and symbolic and symbolic != "custom":
            return f"{symbolic} ({hex_value})"
        if hex_value:
            return str(hex_value)
        if symbolic:
            return str(symbolic)
        return to_json_string(payload)

    return str(payload)
