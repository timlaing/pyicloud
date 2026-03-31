"""Tests for CLI output helpers."""

from pyicloud.cli.output import (
    TABLE_BORDER_STYLE,
    TABLE_HEADER_STYLE,
    TABLE_KEY_STYLE,
    TABLE_ROW_STYLES,
    TABLE_TITLE_STYLE,
    console_kv_table,
    console_table,
    format_color_value,
)


def test_console_table_uses_shared_styles() -> None:
    """console_table should apply the shared table palette."""

    table = console_table("Devices", ["ID", "Name"], [("device-1", "Phone")])

    assert table.title == "Devices"
    assert table.title_style == TABLE_TITLE_STYLE
    assert table.header_style == TABLE_HEADER_STYLE
    assert table.border_style == TABLE_BORDER_STYLE
    assert tuple(table.row_styles) == TABLE_ROW_STYLES


def test_console_kv_table_styles_key_column() -> None:
    """console_kv_table should style the key column consistently."""

    table = console_kv_table("Auth Status", [("Account", "user@example.com")])

    assert table.title == "Auth Status"
    assert table.title_style == TABLE_TITLE_STYLE
    assert table.header_style == TABLE_HEADER_STYLE
    assert table.border_style == TABLE_BORDER_STYLE
    assert tuple(table.row_styles) == TABLE_ROW_STYLES
    assert table.columns[0].style == TABLE_KEY_STYLE


def test_format_color_value_handles_symbolic_payloads() -> None:
    """Reminder color payloads should render as a compact symbolic label."""

    assert (
        format_color_value('{"daHexString":"#007AFF","ckSymbolicColorName":"blue"}')
        == "blue (#007AFF)"
    )


def test_format_color_value_handles_plain_values() -> None:
    """Plain, empty, and malformed color values should degrade gracefully."""

    assert format_color_value("#34C759") == "#34C759"
    assert format_color_value("") == ""
    assert format_color_value("{not-json}") == "{not-json}"
