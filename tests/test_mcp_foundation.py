"""Offline extension launch and protocol registration contracts."""

import builtins
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from mcp.types import CallToolResult
import pytest

from pyicloud.mcp.cmdline import main, quiet_library_logging
from pyicloud.mcp.server import ServerStatus, build_server


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_offline_options_do_not_start_server(
    option: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Help/version exit before constructing the optional server."""
    with (
        patch("sys.argv", ["icloud-mcp", option]),
        patch("pyicloud.mcp.cmdline.version", return_value="synthetic"),
        pytest.raises(SystemExit) as caught,
    ):
        main()
    assert caught.value.code == 0
    assert capsys.readouterr().out


def test_launcher_runs_stdio_only() -> None:
    """The foundation offers no remote transport or automatic account access."""
    server = MagicMock()
    with (
        patch("sys.argv", ["icloud-mcp"]),
        patch("pyicloud.mcp.cmdline.version", return_value="synthetic"),
        patch("pyicloud.mcp.server.build_server", return_value=server),
        patch("pyicloud.mcp.cmdline.quiet_library_logging"),
    ):
        main()
    server.run.assert_called_once_with(transport="stdio")


def test_invalid_launch_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    """An account cannot be enabled without its explicit session directory."""
    with (
        patch("sys.argv", ["icloud-mcp", "--username", "synthetic"]),
        patch("pyicloud.mcp.cmdline.version", return_value="synthetic"),
        pytest.raises(SystemExit) as caught,
    ):
        main()
    assert caught.value.code == 2
    assert "invalid arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--password", "SECRET_VALUE"],
        ["--service", "SECRET_VALUE"],
        ["--timeout", "SECRET_VALUE"],
    ],
)
def test_invalid_argument_values_are_not_echoed(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither unknown nor known-but-invalid options leak values to stderr."""
    with (
        patch("sys.argv", ["icloud-mcp", *arguments]),
        patch("pyicloud.mcp.cmdline.version", return_value="synthetic"),
        pytest.raises(SystemExit) as caught,
    ):
        main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "SECRET_VALUE" not in output.err
    assert not output.out


def test_private_logging_is_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process-level launch logging must not leak library messages to the host."""
    logger = logging.getLogger("pyicloud")
    for name in ("disabled", "level", "propagate", "handlers"):
        monkeypatch.setattr(logger, name, getattr(logger, name))
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    quiet_library_logging()
    assert logger.disabled
    assert not logger.propagate
    assert logger.level > logging.CRITICAL


def test_status_defaults_are_non_sensitive() -> None:
    """Only safe, deterministic local status fields are exposed."""
    assert ServerStatus().model_dump() == {
        "authenticated": False,
        "remote_services_enabled": False,
        "message": "No account configured. Remote tools are not enabled.",
    }
    assert build_server().name == "PyiCloud"


@pytest.mark.asyncio
async def test_status_tool_contract() -> None:
    """The SDK advertises and serializes the local-only structured tool."""
    server = build_server()
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == ["icloud_status", "icloud_capabilities"]
    result = await server.call_tool("icloud_status", {})
    assert isinstance(result, CallToolResult)
    assert result.structured_content == ServerStatus().model_dump()
    assert not result.is_error


@pytest.mark.parametrize("dependency", ["mcp", "mcp.server", "unrelated_dependency"])
def test_missing_optional_dependency(
    dependency: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only SDK import failures produce the installation hint; others propagate."""
    original_import = builtins.__import__

    def import_module(name: str, *args: Any, **kwargs: Any) -> object:
        if name == "pyicloud.mcp.server":
            raise ModuleNotFoundError("Missing dependency", name=dependency)
        return original_import(name, *args, **kwargs)

    with (
        patch("sys.argv", ["icloud-mcp"]),
        patch("pyicloud.mcp.cmdline.version", return_value="synthetic"),
        patch("builtins.__import__", side_effect=import_module),
    ):
        if dependency == "unrelated_dependency":
            with pytest.raises(ModuleNotFoundError):
                main()
        else:
            with pytest.raises(SystemExit) as caught:
                main()
            assert caught.value.code == 2
            output = capsys.readouterr()
            assert "pyicloud[mcp]" in output.err
            assert not output.out
