"""Tests for CLI output helpers."""

from pyicloud.cli.output import (
    TABLE_BORDER_STYLE,
    TABLE_HEADER_STYLE,
    TABLE_KEY_STYLE,
    TABLE_ROW_STYLES,
    TABLE_TITLE_STYLE,
    console_kv_table,
    console_table,
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
