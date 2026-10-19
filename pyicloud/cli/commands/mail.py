"""Read-only commands for the experimental Mail service."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import Enum
import os
from pathlib import Path
from tempfile import mkstemp
from typing import Annotated, Any, cast

from rich.text import Text
import typer

from pyicloud.base import PyiCloudService
from pyicloud.cli.context import CLIAbort, CLIState, get_state, service_call
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
from pyicloud.cli.output import console_table, print_json_text
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.mail import MailFolder, MailMessage, MailService

app = typer.Typer(help="Read Mail folders, bounded messages, and attachments.")

FolderOption = Annotated[
    str, typer.Option("--folder-id", help="Exact folder ID from mail folders.")
]
LimitOption = Annotated[
    int,
    typer.Option(
        "--limit", min=1, max=1000, help="Maximum messages or thread hits to fetch."
    ),
]
ListLimitOption = Annotated[
    int,
    typer.Option(
        "--limit",
        min=1,
        max=1000,
        help="Total messages to fetch; with --all, the per-request page size.",
    ),
]
LookupLimitOption = Annotated[
    int,
    typer.Option(
        "--limit",
        min=1,
        max=1000,
        help="Look for the message only within this many newest folder messages.",
    ),
]
_CONTROL_CHARACTERS = dict.fromkeys([*range(32), *range(127, 160)])


class SearchScope(str, Enum):
    """Supported Mail text-search projections."""

    ALL = "all"
    SUBJECT = "subject"
    FROM = "from"
    TO = "to"
    CC = "cc"
    BODY = "body"


def _mail_call(api: PyiCloudService, fn: Callable[[], Any]) -> Any:
    """Preserve shared authentication hints and translate expected Mail errors."""
    try:
        return service_call("Mail", fn, account_name=api.account_name)
    except (PyiCloudException, ValueError, OSError) as err:
        raise CLIAbort(f"Mail: {err}") from err


def _context(ctx: typer.Context) -> tuple[CLIState, PyiCloudService, MailService]:
    """Resolve the selected account and its optional Mail service."""
    state = get_state(ctx)
    api = state.get_api()
    return state, api, _mail_call(api, lambda: api.mail)


def _folder(api: PyiCloudService, mail: MailService, folder_id: str) -> MailFolder:
    """Resolve only exact folder IDs, never localized display-name guesses."""
    for folder in _mail_call(api, mail.folders):
        if folder.id == folder_id:
            return cast(MailFolder, folder)
    raise CLIAbort("Mail folder not found. Use icloud mail folders for exact IDs.")


def _message(
    api: PyiCloudService, mail: MailService, folder_id: str, message_id: str, limit: int
) -> MailMessage:
    """Look up a message in one bounded, folder-specific metadata snapshot."""
    folder = _folder(api, mail, folder_id)
    page = _mail_call(api, lambda: mail.messages(folder, limit=limit))
    for message in page.messages:
        if message.id == message_id:
            return cast(MailMessage, message)
    raise CLIAbort(
        f"Message not found in the newest {limit} folder messages. "
        "Check --folder-id and the message ID from mail list, or increase "
        "--limit (maximum 1000). Search IDs identify threads, not messages."
    )


def _table(
    state: CLIState, title: str, columns: list[str], rows: Iterable[Iterable[Any]]
) -> None:
    """Print literal metadata without interpreting markup or terminal controls."""
    table = console_table(title, columns, [])
    for row in rows:
        table.add_row(*[
            Text("" if value is None else str(value).translate(_CONTROL_CHARACTERS))
            for value in row
        ])
    state.console.print(table)


def _download(
    mail: MailService, message: MailMessage, part_id: str, output: Path
) -> int:
    """Stage private bytes beside the destination and publish without overwriting."""
    size = 0
    descriptor, temporary_path = mkstemp(dir=output.parent, prefix=".pyicloud-mail-")
    try:
        with (
            os.fdopen(descriptor, "wb") as temporary,
            mail.attachment(message, part_id) as chunks,
        ):
            for chunk in chunks:
                size += temporary.write(chunk)
        # A hard link publishes complete bytes atomically and refuses any
        # existing destination, including symlinks and files created mid-stream.
        # Close the staging handle first, including on Windows.
        os.link(temporary_path, output)
    finally:
        Path(temporary_path).unlink()
    return size


@app.command("folders")
def mail_folders(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List Mail folder IDs, names, and advertised message counts."""
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
    state, api, mail = _context(ctx)
    payload = _mail_call(api, mail.folders)
    if state.json_output:
        state.write_json(payload)
        return
    _table(
        state,
        "Mail Folders",
        ["ID", "Name", "Count"],
        [(folder.id, folder.name, folder.message_count) for folder in payload],
    )


@app.command("list")
def mail_list(
    ctx: typer.Context,
    folder_id: FolderOption,
    limit: ListLimitOption = 50,
    all_messages: bool = typer.Option(
        False,
        "--all",
        help="Exhaust UID paging; --limit becomes the per-request page size.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List metadata, bounded by default; explicitly opt into UID paging with --all."""
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
    state, api, mail = _context(ctx)
    folder = _folder(api, mail, folder_id)
    if all_messages:
        messages = _mail_call(
            api, lambda: list(mail.iter_messages(folder, page_size=limit))
        )
        if state.json_output:
            state.write_json({
                "messages": messages,
                "page_size": limit,
                "exhausted": True,
            })
            return
    else:
        payload = _mail_call(api, lambda: mail.messages(folder, limit=limit))
        messages = payload.messages
        if state.json_output:
            state.write_json(payload)
            return
    _table(
        state,
        "Mail Messages (UID Enumeration)"
        if all_messages
        else "Mail Messages (Bounded Snapshot)",
        ["ID", "From", "Subject"],
        [(message.id, message.sender, message.subject) for message in messages],
    )
    state.console.print(
        "UID range exhausted; concurrent changes are not an atomic snapshot."
        if all_messages
        else "Incomplete snapshot; no additional pages were fetched."
    )


@app.command("search")
def mail_search(
    ctx: typer.Context,
    folder_id: FolderOption,
    text: str = typer.Argument(..., help="Text to match within one folder."),
    field: SearchScope = typer.Option(SearchScope.ALL, "--field"),
    limit: LimitOption = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Search a bounded set of thread-grouped hits, not individual message IDs."""
    if not text.strip():
        raise typer.BadParameter("Search text must not be empty.")
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
    state, api, mail = _context(ctx)
    folder = _folder(api, mail, folder_id)
    payload = _mail_call(
        api,
        lambda: mail.search(
            folder,
            text,
            field=field.value,
            limit=limit,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    _table(
        state,
        "Mail Search (Thread Groups)",
        ["Thread ID", "Subject", "Count"],
        [(hit.id, hit.subject, hit.count) for hit in payload.results],
    )
    state.console.print("Incomplete snapshot; thread IDs are not message IDs.")


@app.command("body")
def mail_body(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help="Message ID from mail list."),
    limit: LookupLimitOption = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Print body data without rendering HTML or requesting a read-state change."""
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
    state, api, mail = _context(ctx)
    message = _message(api, mail, folder_id, message_id, limit)
    payload = _mail_call(api, lambda: mail.body(message))
    if state.json_output:
        state.write_json(payload)
        return
    print_json_text(state.console, payload)


@app.command("download")
def mail_download(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help="Message ID from mail list."),
    part_id: str = typer.Option(
        ..., "--part-id", help="Attachment part ID from JSON metadata."
    ),
    output: Path = typer.Option(
        ..., "--output", help="New destination file in an existing directory."
    ),
    limit: LookupLimitOption = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Save an ordinary attachment to an explicit path, without overwriting."""
    if os.path.lexists(output):
        raise CLIAbort("Destination already exists; choose a new --output path.")
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
    state, api, mail = _context(ctx)
    message = _message(api, mail, folder_id, message_id, limit)
    size = _mail_call(api, lambda: _download(mail, message, part_id, output))
    payload = {
        "message_id": message.id,
        "part_id": part_id,
        "path": str(output),
        "size": size,
    }
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(Text(str(output)))
