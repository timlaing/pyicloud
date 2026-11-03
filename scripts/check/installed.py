"""Smoke-check an installed wheel outside the checkout, without Apple access.

Run with the target environment's ``python -I``; add ``--cli`` for the CLI extra.
This check intentionally uses only the standard library and installed package.
"""

# cspell:words errlog

# Imports of optional dependencies must remain behind the CLI-extra check.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import argparse
import asyncio
import importlib
from importlib.metadata import metadata, version
from importlib.resources import files
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch


def check_cli(manifest: dict[str, Any]) -> None:
    """Exercise the console entry point without Python import-path overrides."""
    executable = Path(sys.executable).with_name(
        "icloud.exe" if os.name == "nt" else "icloud"
    )
    # The parent uses -I, but a console script starts a fresh interpreter.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
    }
    for command in (
        ("--help",),
        ("--version",),
        ("photos", "--help"),
        ("reminders", "--help"),
        ("mail", "--help"),
    ):
        result = subprocess.run(
            [str(executable), *command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        assert result.stdout.strip()
        if command == ("--version",):
            assert result.stdout.strip() == version("pyicloud")

    from typer.testing import CliRunner  # pylint: disable=import-outside-toplevel

    from pyicloud.cli.app import app  # pylint: disable=import-outside-toplevel
    from pyicloud.services.reminders.models import (
        RemindersList,  # pylint: disable=import-outside-toplevel
    )

    fake_api = SimpleNamespace(
        account_name="Synthetic",
        reminders=SimpleNamespace(
            lists=lambda: [RemindersList(id="List/X", title="Synthetic")]
        ),
    )
    with patch("pyicloud.cli.context.CLIState.get_api", return_value=fake_api):
        cli_result = CliRunner().invoke(app, ["reminders", "lists", "--format", "json"])
    assert cli_result.exit_code == manifest["cli"]["lists_exit"], cli_result.exception
    rows = json.loads(cli_result.stdout)
    assert len(rows) == 1
    assert {key: rows[0][key] for key in manifest["list_shape"]} == manifest[
        "list_shape"
    ]
    invalid = CliRunner().invoke(app, ["not-a-command"])
    assert invalid.exit_code == manifest["cli"]["usage_exit"]


def check_mcp() -> None:
    """Verify the installed optional entry point and synthetic SDK tool contract."""
    executable = Path(sys.executable).with_name(
        "icloud-mcp.exe" if os.name == "nt" else "icloud-mcp"
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
    }
    for option in ("--help", "--version"):
        result = subprocess.run(
            [str(executable), option],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env=environment,
        )
        assert result.stdout.strip()

    from pyicloud.mcp.server import (
        build_server,  # pylint: disable=import-outside-toplevel
    )

    async def verify_tool() -> None:
        """No Apple account or transport is created for this installed smoke."""
        server = build_server()
        tools = await server.list_tools()
        assert [tool.name for tool in tools] == ["icloud_status", "icloud_capabilities"]
        result = await server.call_tool("icloud_status", {})
        from mcp.types import CallToolResult  # pylint: disable=import-outside-toplevel

        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        assert result.structured_content["authenticated"] is False
        assert not result.is_error

    asyncio.run(verify_tool())
    asyncio.run(check_mcp_stdio(executable, environment))
    asyncio.run(check_mcp_sdk_client(executable, environment))
    asyncio.run(check_configured_mcp(environment, challenge=False))
    asyncio.run(check_configured_mcp(environment, challenge=True))
    asyncio.run(check_configured_mcp(environment, challenge=False, content=True))
    if os.name == "posix":
        asyncio.run(check_configured_mcp(environment, challenge=False, writes=True))


async def check_configured_mcp(
    environment: dict[str, str],
    *,
    challenge: bool,
    content: bool = False,
    writes: bool = False,
) -> None:
    """Exercise worker ownership through stdio with network-blocked data."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    arguments = ["-I", str(Path(__file__).with_name("mcp_fixture.py"))]
    if challenge:
        arguments.append("--challenge")
    if content:
        arguments.append("--content")
    with (
        tempfile.TemporaryDirectory() as approval_directory,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostics,
    ):
        if writes:
            arguments.extend(["--approval-dir", approval_directory])
        parameters = StdioServerParameters(
            command=sys.executable, args=arguments, env=environment
        )
        async with stdio_client(parameters, errlog=diagnostics) as (read, write):
            connection = ClientSession(read, write, read_timeout_seconds=30)
            async with connection as session:
                await session.initialize()
                await exercise_configured_mcp(session, challenge=challenge)
                if content:
                    await exercise_content_mcp(session)
                if writes:
                    await exercise_write_gate_mcp(session, approval_directory)
                    await exercise_mail_write_gate_mcp(session, approval_directory)
                    await exercise_photo_write_gate_mcp(session, approval_directory)
                    await exercise_drive_write_gate_mcp(session, approval_directory)
                    await exercise_drive_move_gate_mcp(session, approval_directory)
        diagnostics.seek(0)
        output = diagnostics.read()
        assert output.count("MCP_SYNTHETIC_CLOSED") == 1
        assert "OMIT_ME" not in output
        assert output.count("MCP_SYNTHETIC_MUTATED") == (1 if writes else 0)
        assert output.count("MCP_SYNTHETIC_MAIL_SAVED") == (2 if writes else 0)
        assert output.count("MCP_SYNTHETIC_PHOTO_MUTATED") == (2 if writes else 0)
        assert output.count("MCP_SYNTHETIC_DRIVE_RENAMED") == (1 if writes else 0)
        assert output.count("MCP_SYNTHETIC_DRIVE_TRASHED") == (2 if writes else 0)
        assert output.count("MCP_SYNTHETIC_DRIVE_MOVED") == (2 if writes else 0)
        assert "recipient@example.invalid" not in output
        assert "Approved &lt;text&gt;" not in output


async def exercise_write_gate_mcp(session: Any, directory: str) -> None:
    """A synthetic terminal receipt gates the one fake mutation callback."""
    from pyicloud.mcp.approval_channel import ApprovalMailbox

    lists = await session.call_tool("icloud_reminders_lists", {})
    assert lists.structured_content is not None
    handle = lists.structured_content["records"][0]["handle"]
    prepared = await session.call_tool(
        "icloud_write_preview",
        {
            "action": "reminders.create",
            "list_handle": handle,
            "payload": {"title": "Approved synthetic mutation"},
        },
    )
    assert prepared.structured_content is not None
    assert prepared.structured_content["error"] is None
    request_id = prepared.structured_content["preview"]["request_id"]
    denied = await session.call_tool("icloud_write_execute", {"request_id": request_id})
    assert denied.structured_content is not None
    assert denied.structured_content["error"] == "approval_record_unavailable"

    class SyntheticTerminal(io.StringIO):
        """Test input, not actual human presence or account authorization."""

        def isatty(self) -> bool:
            """Only simulate a separate interactive operator in this test."""
            return True

    with ApprovalMailbox(directory) as operator:
        operator.decide(
            request_id, SyntheticTerminal("APPROVE " + request_id), io.StringIO()
        )
    accepted = await session.call_tool(
        "icloud_write_execute", {"request_id": request_id}
    )
    assert accepted.structured_content is not None
    assert accepted.structured_content["error"] is None
    assert accepted.structured_content["result"]["id"] == "Reminder/CREATED"
    replay = await session.call_tool("icloud_write_execute", {"request_id": request_id})
    assert replay.structured_content is not None
    assert replay.structured_content["error"] == "unknown_or_consumed_preview"


async def exercise_mail_write_gate_mcp(session: Any, directory: str) -> None:
    """Synthetic successful and uncertain saves each dispatch once, never replay."""
    from pyicloud.mcp.approval_channel import ApprovalMailbox

    class SyntheticTerminal(io.StringIO):
        """Only simulated test input, not a real operator authorization."""

        def isatty(self) -> bool:
            """Exercise the terminal-only decision path in this isolated fixture."""
            return True

    for subject, expected_error in (
        ("Approved", None),
        ("Uncertain", "mutation_uncertain"),
    ):
        payload = {
            "sender": "synthetic@example.invalid",
            "subject": subject,
            "text": "Approved <text>\nsecond line",
            "to": ["recipient@example.invalid"],
            "cc": [],
            "bcc": [],
        }
        prepared = await session.call_tool(
            "icloud_write_preview", {"action": "mail.create_draft", "payload": payload}
        )
        assert prepared.structured_content is not None
        assert prepared.structured_content["error"] is None
        preview = prepared.structured_content["preview"]
        assert preview["action"] == "mail.create_draft"
        assert preview["payload"] == payload
        request_id = preview["request_id"]
        denied = await session.call_tool(
            "icloud_write_execute", {"request_id": request_id}
        )
        assert denied.structured_content is not None
        assert denied.structured_content["error"] == "approval_record_unavailable"
        with ApprovalMailbox(directory) as operator:
            operator.decide(
                request_id, SyntheticTerminal("APPROVE " + request_id), io.StringIO()
            )
        result = await session.call_tool(
            "icloud_write_execute", {"request_id": request_id}
        )
        assert result.structured_content is not None
        assert result.structured_content["error"] == expected_error
        if expected_error is None:
            assert result.structured_content["result"] == {
                "action": "mail.create_draft",
                "id": "draft-101",
            }
        else:
            assert result.structured_content["result"] is None
        replay = await session.call_tool(
            "icloud_write_execute", {"request_id": request_id}
        )
        assert replay.structured_content is not None
        assert replay.structured_content["error"] == "unknown_or_consumed_preview"


async def exercise_photo_write_gate_mcp(session: Any, directory: str) -> None:
    """One verified and one unverified synthetic favorite change cannot replay."""
    from pyicloud.mcp.approval_channel import ApprovalMailbox

    class SyntheticTerminal(io.StringIO):
        """Only simulate separate terminal input; no actual operator approval."""

        def isatty(self) -> bool:
            """Exercise terminal checks in the isolated test."""
            return True

    libraries = await session.call_tool("icloud_photos_libraries", {})
    assert libraries.structured_content is not None
    albums = await session.call_tool(
        "icloud_photos_albums",
        {"library_handle": libraries.structured_content["records"][0]["handle"]},
    )
    assert albums.structured_content is not None
    photos = await session.call_tool(
        "icloud_photos_list",
        {"album_handle": albums.structured_content["records"][0]["handle"]},
    )
    assert photos.structured_content is not None
    handle = photos.structured_content["records"][0]["handle"]
    for value, expected in ((True, None), (False, "mutation_uncertain")):
        prepared = await session.call_tool(
            "icloud_write_preview",
            {
                "action": "photos.set_favorite",
                "target_handle": handle,
                "payload": {"favorite": value},
            },
        )
        assert prepared.structured_content is not None
        assert prepared.structured_content["error"] is None
        preview = prepared.structured_content["preview"]
        assert preview["payload"] == {"favorite": value}
        identifier = preview["request_id"]
        denied = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert denied.structured_content is not None
        assert denied.structured_content["error"] == "approval_record_unavailable"
        with ApprovalMailbox(directory) as operator:
            operator.decide(
                identifier, SyntheticTerminal("APPROVE " + identifier), io.StringIO()
            )
        applied = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert applied.structured_content is not None
        assert applied.structured_content["error"] == expected
        if expected is None:
            assert applied.structured_content["result"] == {
                "action": "photos.set_favorite",
                "changed": True,
            }
        else:
            assert applied.structured_content["result"] is None
        replay = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert replay.structured_content is not None
        assert replay.structured_content["error"] == "unknown_or_consumed_preview"


async def exercise_drive_write_gate_mcp(session: Any, directory: str) -> None:
    """Installed rename/trash results must follow exact receipts and fresh proof."""
    from pyicloud.mcp.approval_channel import ApprovalMailbox

    class SyntheticTerminal(io.StringIO):
        """Only test terminal input, never real-account authorization."""

        def isatty(self) -> bool:
            """Exercise the private terminal-only decision path."""
            return True

    discovered = await session.call_tool("icloud_drive_list", {})
    assert discovered.structured_content is not None
    first, second = [
        row["handle"]
        for row in discovered.structured_content["records"]
        if row["type"] == "file"
    ]
    for action, handle, payload, expected in (
        ("drive.rename", first, {"name": "Renamed"}, None),
        ("drive.trash_file", first, {}, None),
        ("drive.trash_file", second, {}, "mutation_uncertain"),
    ):
        prepared = await session.call_tool(
            "icloud_write_preview",
            {"action": action, "target_handle": handle, "payload": payload},
        )
        assert prepared.structured_content is not None
        assert prepared.structured_content["error"] is None
        preview = prepared.structured_content["preview"]
        assert preview["payload"] == payload
        identifier = preview["request_id"]
        denied = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert denied.structured_content is not None
        assert denied.structured_content["error"] == "approval_record_unavailable"
        with ApprovalMailbox(directory) as operator:
            operator.decide(
                identifier, SyntheticTerminal("APPROVE " + identifier), io.StringIO()
            )
        applied = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert applied.structured_content is not None
        assert applied.structured_content["error"] == expected
        assert applied.structured_content["result"] == (
            {"action": action, "changed": True} if expected is None else None
        )
        replay = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert replay.structured_content is not None
        assert replay.structured_content["error"] == "unknown_or_consumed_preview"


async def exercise_drive_move_gate_mcp(session: Any, directory: str) -> None:
    """Independent installed parent/membership proof, no model confirmation path."""
    from pyicloud.mcp.approval_channel import ApprovalMailbox

    class SyntheticTerminal(io.StringIO):
        """Test terminal input only; never actual account authorization."""

        def isatty(self) -> bool:
            """Exercise the separate private decision channel."""
            return True

    discovered = await session.call_tool("icloud_drive_list", {})
    assert discovered.structured_content is not None
    rows = {
        row["name"]: row["handle"] for row in discovered.structured_content["records"]
    }
    for name, expected in (("Destination", None), ("Other", "mutation_uncertain")):
        prepared = await session.call_tool(
            "icloud_write_preview",
            {
                "action": "drive.move_file",
                "target_handle": rows["Second.txt"],
                "destination_handle": rows[name],
                "payload": {},
            },
        )
        assert prepared.structured_content is not None
        assert prepared.structured_content["error"] is None
        identifier = prepared.structured_content["preview"]["request_id"]
        denied = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert denied.structured_content is not None
        assert denied.structured_content["error"] == "approval_record_unavailable"
        with ApprovalMailbox(directory) as operator:
            operator.decide(
                identifier, SyntheticTerminal("APPROVE " + identifier), io.StringIO()
            )
        result = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert result.structured_content is not None
        assert result.structured_content["error"] == expected
        assert result.structured_content["result"] == (
            {"action": "drive.move_file", "changed": True} if expected is None else None
        )
        replay = await session.call_tool(
            "icloud_write_execute", {"request_id": identifier}
        )
        assert replay.structured_content is not None
        assert replay.structured_content["error"] == "unknown_or_consumed_preview"


async def exercise_configured_mcp(session: Any, *, challenge: bool) -> None:
    """Validate configured tools through the wire, never direct adapter calls."""
    status = await session.call_tool("icloud_status", {})
    assert status.structured_content is not None
    assert status.structured_content["authenticated"] is False
    calendars = await session.call_tool("icloud_calendar_list", {})
    assert calendars.structured_content is not None
    if challenge:
        assert calendars.structured_content["error"] == "verification_required"
        assert not calendars.structured_content["records"]
        return
    assert calendars.structured_content["records"] == [
        {"id": "C", "title": "Synthetic calendar"}
    ]
    events = await session.call_tool(
        "icloud_calendar_events",
        {
            "calendar_id": "C",
            "start": "2026-01-01",
            "end": "2026-01-01",
        },
    )
    assert events.structured_content is not None
    assert [item["id"] for item in events.structured_content["records"]] == ["E"]
    assert events.structured_content["records"][0]["start"] == "2026-01-01T12:00:00"
    lists = await session.call_tool("icloud_reminders_lists", {})
    assert lists.structured_content is not None
    assert len(lists.structured_content["records"]) == 2
    assert "OMIT_ME" not in json.dumps(lists.structured_content)
    for row in lists.structured_content["records"]:
        reminders = await session.call_tool(
            "icloud_reminders_list", {"handle": row["handle"]}
        )
        assert reminders.structured_content is not None
        assert reminders.structured_content["records"][0]["title"] == row["title"]
        assert "OMIT_ME" not in json.dumps(reminders.structured_content)
    status = await session.call_tool("icloud_status", {})
    assert status.structured_content is not None
    assert status.structured_content["authenticated"] is True
    await exercise_additional_mcp(session)
    capabilities = await session.call_tool("icloud_capabilities", {})
    assert capabilities.structured_content is not None
    assert capabilities.structured_content["availability"] == {
        name: "available"
        for name in (
            "calendar",
            "reminders",
            "contacts",
            "drive",
            "notes",
            "photos",
            "mail",
        )
    }


async def exercise_additional_mcp(session: Any) -> None:
    """New opt-ins are checked through the wire with private fields omitted."""

    async def records(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """Reject hidden adapter failures rather than passing on an empty response."""
        result = await session.call_tool(name, arguments)
        assert result.structured_content is not None
        assert result.structured_content["error"] is None
        assert "OMIT_ME" not in json.dumps(result.structured_content)
        return cast(list[dict[str, Any]], result.structured_content["records"])

    contacts = await records("icloud_contacts_list", {"query": "Synthetic"})
    assert set(contacts[0]) == {"handle", "first_name", "last_name"}
    details = await records(
        "icloud_contact_details",
        {"handle": contacts[0]["handle"], "include_emails": True},
    )
    assert details[0]["emails"] == ["synthetic@example.invalid"]
    assert "phones" not in details[0]
    drive = await records("icloud_drive_list", {})
    assert drive[0]["name"] == "Synthetic.txt"
    notes = await records("icloud_notes_list", {"query": "Synthetic"})
    assert notes[0]["title"] == "Synthetic note"
    libraries = await records("icloud_photos_libraries", {})
    albums = await records(
        "icloud_photos_albums", {"library_handle": libraries[0]["handle"]}
    )
    photos = await records("icloud_photos_list", {"album_handle": albums[0]["handle"]})
    assert photos[0]["filename"] == "Synthetic.jpg"
    folders = await records("icloud_mail_folders", {})
    messages = await records(
        "icloud_mail_messages", {"folder_handle": folders[0]["handle"]}
    )
    assert messages[0]["subject"] == "Synthetic message"
    search = await records(
        "icloud_mail_search",
        {"folder_handle": folders[0]["handle"], "query": "Synthetic"},
    )
    assert search[0]["thread_id"] == "T"


async def exercise_content_mcp(session: Any) -> None:
    """Check opt-in content and resumed binary downloads through actual stdio."""

    async def records(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Every response must have data, not a silently masked service error."""
        result = await session.call_tool(name, arguments)
        assert result.structured_content is not None
        assert result.structured_content["error"] is None
        assert "OMIT_ME" not in json.dumps(result.structured_content)
        return cast(dict[str, Any], result.structured_content)

    notes = await records("icloud_notes_list", {})
    note = await records(
        "icloud_note_content", {"handle": notes["records"][0]["handle"]}
    )
    assert note["records"][0]["text"] == "Synthetic note body"
    folders = await records("icloud_mail_folders", {})
    messages = await records(
        "icloud_mail_messages", {"folder_handle": folders["records"][0]["handle"]}
    )
    mail = await records(
        "icloud_mail_content", {"handle": messages["records"][0]["handle"]}
    )
    assert mail["records"][0]["parts"] == [{"content": "Synthetic mail body"}]
    drive = await records("icloud_drive_list", {})
    libraries = await records("icloud_photos_libraries", {})
    albums = await records(
        "icloud_photos_albums", {"library_handle": libraries["records"][0]["handle"]}
    )
    photos = await records(
        "icloud_photos_list", {"album_handle": albums["records"][0]["handle"]}
    )
    for name, handle in [
        ("icloud_drive_download", drive["records"][0]["handle"]),
        ("icloud_photo_download", photos["records"][0]["handle"]),
    ]:
        first = await records(name, {"handle": handle, "offset": 0, "max_bytes": 4})
        assert first["records"][0]["data_base64"] == "YWJjZA=="
        assert first["records"][0]["next_offset"] == 4
        last = await records(name, {"handle": handle, "offset": 4, "max_bytes": 4})
        assert last["records"][0]["data_base64"] == "ZWZnaA=="
        assert last["complete"] is True


async def check_mcp_sdk_client(executable: Path, environment: dict[str, str]) -> None:
    """Use the maintained SDK client independently of the raw JSON-RPC harness."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parameters = StdioServerParameters(command=str(executable), env=environment)
    async with stdio_client(parameters) as (read, write):
        connection = ClientSession(read, write, read_timeout_seconds=30)
        async with connection as session:
            await session.initialize()
            result = await session.call_tool("icloud_capabilities", {})
            assert result.structured_content is not None
            assert result.structured_content["enabled_services"] == []
            assert not result.is_error


async def check_mcp_stdio(executable: Path, environment: dict[str, str]) -> None:
    """Exercise real stdio framing and structured results without an Apple account."""
    process = await asyncio.create_subprocess_exec(
        str(executable),
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None

    async def request(
        identifier: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Require a JSON-RPC response, not log output on stdout."""
        assert process.stdin is not None and process.stdout is not None
        message = {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": method,
            "params": params,
        }
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
        assert response["id"] == identifier and "result" in response, response
        return cast(dict[str, Any], response["result"])

    try:
        await request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pyicloud-installed-smoke", "version": "1"},
            },
        )
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await process.stdin.drain()
        tools = await request(2, "tools/list", {})
        assert {tool["name"] for tool in tools["tools"]} == {
            "icloud_status",
            "icloud_capabilities",
        }
        result = await request(
            3, "tools/call", {"name": "icloud_status", "arguments": {}}
        )
        assert result["structuredContent"]["authenticated"] is False
        assert not result.get("isError", False)
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def check_persistence(api: Any, manifest: dict[str, Any], cookie_dir: str) -> None:
    """Exercise recorded JSON and LWP formats using only synthetic saved state."""
    import pyicloud  # pylint: disable=import-outside-toplevel
    from pyicloud.session import (
        NON_PERSISTED_SESSION_KEYS,  # pylint: disable=import-outside-toplevel
    )

    api.session.data.update(manifest["session"])
    api.session.data.update({
        key: "not-persisted" for key in NON_PERSISTED_SESSION_KEYS
    })
    api.session.cookies.set("compatibility", "synthetic", domain="example.invalid")
    api.session._save_session_data()  # pylint: disable=protected-access
    saved = json.loads(Path(api.session.session_path).read_text(encoding="utf-8"))
    assert saved == manifest["session"]
    assert (
        Path(api.session.cookiejar_path)
        .read_text(encoding="utf-8")
        .startswith(manifest["cookie_header"])
    )
    restored = pyicloud.PyiCloudService(
        "smoke@example.invalid", cookie_directory=cookie_dir, authenticate=False
    )
    try:
        assert restored.session.data == manifest["session"]
        assert restored.session.cookies.get("compatibility") == "synthetic"
    finally:
        restored.session.close()


def main() -> None:
    """Check installed imports, offline construction and the optional CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    requirements = metadata("pyicloud").get_all("Requires-Dist") or []

    def extra_requirements(extra: str) -> set[str]:
        """Validate optional metadata without depending on a resolver package."""
        return {
            requirement.split(";")[0].strip()
            for requirement in requirements
            if f'extra == "{extra}"' in requirement
        }

    assert extra_requirements("all") == extra_requirements("cli") | extra_requirements(
        "mcp"
    )
    with args.manifest.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    spec = importlib.util.spec_from_file_location(
        "compatibility", Path(__file__).with_name("compatibility.py")
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.check_manifest(manifest)

    # A source-tree import can otherwise hide missing wheel files/dependencies.
    import pyicloud  # pylint: disable=import-outside-toplevel

    assert "mcp" not in sys.modules, "Core import eagerly loaded the optional SDK"

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
        module = importlib.import_module(f"pyicloud.services.{name}")
        assert module.__file__ is not None
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        ), name
    assert files("pyicloud.services.notes.protobuf").joinpath("notes_pb2.pyi").is_file()

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
        try:
            assert api.session.data["client_id"]
            check_persistence(api, manifest, cookie_dir)
            if args.cli:
                check_cli(manifest)
            else:
                # The SDK's HTTP transport dependency uses Click; MCP-only still
                # must not pull in the PyiCloud CLI's Typer/Rich dependencies.
                for name in (
                    ("typer", "rich") if args.mcp else ("click", "typer", "rich")
                ):
                    assert importlib.util.find_spec(name) is None, name
        finally:
            api.session.close()

    if args.mcp:
        check_mcp()
    else:
        assert importlib.util.find_spec("mcp") is None

    print(f"Installed package checks passed on Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
