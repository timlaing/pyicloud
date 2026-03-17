"""Drive commands."""

from __future__ import annotations

from pathlib import Path

import typer

from pyicloud.cli.context import (
    CLIAbort,
    get_state,
    resolve_drive_node,
    service_call,
    write_response_to_path,
)
from pyicloud.cli.normalize import normalize_drive_node
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table

app = typer.Typer(help="Browse and download iCloud Drive files.")


@app.command("list")
@with_service_command_options
def drive_list(
    ctx: typer.Context,
    path: str = typer.Argument("/", help="Drive path, for example /Documents."),
    trash: bool = typer.Option(
        False, "--trash", help="Resolve the path from the trash root."
    ),
) -> None:
    """List a drive folder or inspect a file."""

    state = get_state(ctx)
    api = state.get_api()
    drive = service_call("Drive", lambda: api.drive)
    node = resolve_drive_node(drive, path, trash=trash)
    if node.type == "file":
        payload = normalize_drive_node(node)
        if state.json_output:
            state.write_json(payload)
            return
        state.console.print(
            console_table(
                "Drive Item",
                ["Name", "Type", "Size", "Modified"],
                [
                    (
                        payload["name"],
                        payload["type"],
                        payload["size"],
                        payload["modified"],
                    )
                ],
            )
        )
        return

    payload = [normalize_drive_node(child) for child in node.get_children()]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            f"Drive: {path}",
            ["Name", "Type", "Size", "Modified"],
            [
                (item["name"], item["type"], item["size"], item["modified"])
                for item in payload
            ],
        )
    )


@app.command("download")
@with_service_command_options
def drive_download(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Drive path to the file."),
    output: Path = typer.Option(..., "--output", help="Destination file path."),
    trash: bool = typer.Option(
        False, "--trash", help="Resolve the path from the trash root."
    ),
) -> None:
    """Download a Drive file."""

    state = get_state(ctx)
    api = state.get_api()
    drive = service_call("Drive", lambda: api.drive)
    node = resolve_drive_node(drive, path, trash=trash)
    if node.type != "file":
        raise CLIAbort("Only files can be downloaded.")
    response = node.open(stream=True)
    write_response_to_path(response, output)
    if state.json_output:
        state.write_json({"path": str(output), "name": node.name})
        return
    state.console.print(str(output))
