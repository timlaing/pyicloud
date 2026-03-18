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
from pyicloud.cli.options import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_OUTPUT_FORMAT,
    HttpProxyOption,
    HttpsProxyOption,
    LogLevelOption,
    NoVerifySslOption,
    OutputFormatOption,
    SessionDirOption,
    UsernameOption,
    store_command_options,
)
from pyicloud.cli.output import console_table

app = typer.Typer(help="Browse and download iCloud Drive files.")


def _resolve_drive_node_or_abort(drive, path: str, *, trash: bool = False):
    """Resolve a drive path or raise a user-facing CLI error."""

    try:
        return resolve_drive_node(drive, path, trash=trash)
    except KeyError as err:
        raise CLIAbort(f"Path not found: {path}") from err


@app.command("list")
def drive_list(
    ctx: typer.Context,
    path: str = typer.Argument("/", help="Drive path, for example /Documents."),
    trash: bool = typer.Option(
        False, "--trash", help="Resolve the path from the trash root."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List a drive folder or inspect a file."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)
    api = state.get_api()
    drive = service_call("Drive", lambda: api.drive, account_name=api.account_name)
    node = _resolve_drive_node_or_abort(drive, path, trash=trash)
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
def drive_download(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Drive path to the file."),
    output: Path = typer.Option(..., "--output", help="Destination file path."),
    trash: bool = typer.Option(
        False, "--trash", help="Resolve the path from the trash root."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Download a Drive file."""

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)
    api = state.get_api()
    drive = service_call("Drive", lambda: api.drive, account_name=api.account_name)
    node = _resolve_drive_node_or_abort(drive, path, trash=trash)
    if node.type != "file":
        raise CLIAbort("Only files can be downloaded.")
    response = node.open(stream=True)
    write_response_to_path(response, output)
    if state.json_output:
        state.write_json({"path": str(output), "name": node.name})
        return
    state.console.print(str(output))
