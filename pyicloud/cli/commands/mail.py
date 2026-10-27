"""Mail reads and explicit single-message flag commands."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from enum import Enum
import os
from pathlib import Path
from tempfile import mkstemp
from typing import Annotated, Any, BinaryIO, cast

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
from pyicloud.services.mail import (
    MailFolder,
    MailMessage,
    MailMessageExport,
    MailService,
)
from pyicloud.services.mail.service import MAX_SEARCH_FOLDERS

app = typer.Typer(help="Read Mail and explicitly update individual message flags.")

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
PageSizeOption = Annotated[
    int, typer.Option("--page-size", min=1, max=1000, help="Messages per UID page.")
]
MaxPagesOption = Annotated[
    int,
    typer.Option(
        "--max-pages", min=1, help="Request budget, including the empty final page."
    ),
]
LookupLimitOption = Annotated[
    int | None,
    typer.Option(
        "--limit",
        min=1,
        max=1000,
        help="Search only the newest N messages instead of using exact ID lookup.",
    ),
]
_CONTROL_CHARACTERS = dict.fromkeys([*range(32), *range(127, 160)])
_THREAD_ID_COLUMN = "Thread ID"
_MESSAGE_ID_COLUMN = "Message ID"
_MESSAGE_ID_HELP = "Message ID from mail list."


class SearchScope(str, Enum):
    """Supported Mail text-search projections."""

    ALL = "all"
    SUBJECT = "subject"
    FROM = "from"
    TO = "to"
    CC = "cc"
    BODY = "body"


class MessageFilterScope(str, Enum):
    """Individual-message predicates, separate from grouped thread filters."""

    UNREAD = "unread"
    FLAGGED = "flagged"


class ThreadFilter(str, Enum):
    """Separate observed browser filters, without guessed combination semantics."""

    UNREAD = "unread"
    FLAGGED = "flagged"


class FlagAction(str, Enum):
    """One explicit reversible action; no implicit default or bulk selector."""

    READ = "read"
    UNREAD = "unread"
    FLAG = "flag"
    UNFLAG = "unflag"


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
    api: PyiCloudService,
    mail: MailService,
    folder_id: str,
    message_id: str,
    limit: int | None,
) -> MailMessage:
    """Resolve an exact message ID, preserving explicitly bounded legacy lookups."""
    folder = _folder(api, mail, folder_id)
    if limit is None:
        if folder.uid_validity is None or folder.uid_validity < 1:
            raise CLIAbort(
                "Exact lookup needs a folder with UID validity. "
                "Use --limit N to search the newest N folder messages."
            )
        message = _mail_call(api, lambda: mail.message(folder, message_id))
        if message is not None:
            return cast(MailMessage, message)
        raise CLIAbort(
            "Message not found in this folder. Check --folder-id and the message "
            "ID from mail list. Search IDs identify threads, not messages."
        )
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


@contextmanager
def _private_output(output: Path) -> Generator[BinaryIO, None, None]:
    """Publish a closed private staging file atomically, without overwriting."""
    descriptor, temporary_path = mkstemp(dir=output.parent, prefix=".pyicloud-mail-")
    try:
        try:
            temporary = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with temporary:
            yield temporary
        # A hard link publishes complete bytes atomically and refuses any
        # existing destination, including symlinks and files created mid-stream.
        # Close the staging handle first, including on Windows.
        os.link(temporary_path, output)
    finally:
        Path(temporary_path).unlink()


def _download(
    mail: MailService, message: MailMessage, part_id: str, output: Path
) -> int:
    """Stream an attachment into a private, non-overwriting destination."""
    size = 0
    with (
        _private_output(output) as temporary,
        mail.attachment(message, part_id) as chunks,
    ):
        for chunk in chunks:
            size += temporary.write(chunk)
    return size


def _write_export(payload: MailMessageExport, output: Path) -> int:
    """Save UTF-8 JSON data; never treat message content as markup or paths."""
    with _private_output(output) as temporary:
        return temporary.write(
            (payload.model_dump_json(indent=2) + "\n").encode("utf-8")
        )


def _print_enumeration(
    state: CLIState,
    messages: list[MailMessage],
    page_size: int,
    scope: dict[str, Any],
) -> None:
    """Publish only fully consumed scans, never partial-success JSON."""
    if state.json_output:
        state.write_json({
            **scope,
            "messages": messages,
            "page_size": page_size,
            "exhausted": True,
        })
        return
    _table(
        state,
        "Mail Messages (Scoped UID Enumeration)",
        [_MESSAGE_ID_COLUMN, "From", "Subject"],
        [(message.id, message.sender, message.subject) for message in messages],
    )
    state.console.print(
        "UID range exhausted in this folder; concurrent changes and flag changes "
        "are not an atomic snapshot."
    )


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


@app.command("mark")
def mail_mark(
    ctx: typer.Context,
    message_id: Annotated[str, typer.Argument(help=_MESSAGE_ID_HELP)],
    folder_id: FolderOption,
    action: Annotated[
        FlagAction, typer.Option("--action", help="Required flag change.")
    ],
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Explicitly mark one exact message read/unread or flag/unflag it.

    No scans or automatic retries. An uncertain outcome requires fresh metadata
    before an explicit retry; the acknowledgement is not a current-state snapshot.
    """
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
    message = _mail_call(api, lambda: mail.message(folder, message_id))
    if message is None:
        raise CLIAbort("Message not found in this folder; no flag update submitted.")
    if action in (FlagAction.READ, FlagAction.UNREAD):
        result = _mail_call(
            api, lambda: mail.set_read(message, read=action is FlagAction.READ)
        )
    else:
        result = _mail_call(
            api, lambda: mail.set_flagged(message, flagged=action is FlagAction.FLAG)
        )
    if state.json_output:
        state.write_json(result)
    else:
        _table(
            state,
            "Mail Flag Acknowledgement",
            [_MESSAGE_ID_COLUMN, "Flag", "Enabled", "Modseq"],
            [(result.message_id, result.flag, result.enabled, result.modseq)],
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
        [_THREAD_ID_COLUMN, "Subject", "Count"],
        [(hit.id, hit.subject, hit.count) for hit in payload.results],
    )
    state.console.print("Incomplete snapshot; thread IDs are not message IDs.")


@app.command("thread")
def mail_thread(
    ctx: typer.Context,
    folder_id: FolderOption,
    thread_id: str = typer.Argument(..., help="Thread-group ID from mail search."),
    limit: ListLimitOption = 50,
    all_messages: bool = typer.Option(
        False, "--all", help="Exhaust this folder's thread members with UID paging."
    ),
    max_pages: MaxPagesOption = 10000,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Read bounded thread metadata; --all opts into folder-scoped UID paging."""
    if not thread_id.strip():
        raise typer.BadParameter("Thread ID must not be empty.")
    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        log_level=log_level,
        output_format=output_format,
    )
    state, api, mail = _context(ctx)
    folder = _folder(api, mail, folder_id)
    if all_messages:
        messages = _mail_call(
            api,
            lambda: list(
                mail.iter_thread_messages(
                    folder, thread_id, page_size=limit, max_pages=max_pages
                )
            ),
        )
        _print_enumeration(state, messages, limit, {"thread_id": thread_id})
        return
    payload = _mail_call(
        api, lambda: mail.thread_messages(folder, thread_id, limit=limit)
    )
    if state.json_output:
        state.write_json(payload)
        return
    _table(
        state,
        "Mail Thread (Bounded Folder Snapshot)",
        [_MESSAGE_ID_COLUMN, "From", "Subject"],
        [(message.id, message.sender, message.subject) for message in payload.messages],
    )
    state.console.print(
        "Incomplete snapshot; only messages in this folder are included."
    )


@app.command("filter-messages")
def mail_filter_messages(
    ctx: typer.Context,
    folder_id: FolderOption,
    filter_by: MessageFilterScope = typer.Option(..., "--filter"),
    page_size: PageSizeOption = 50,
    max_pages: MaxPagesOption = 10000,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Enumerate unread or flagged individual messages, not grouped threads."""
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
    messages = _mail_call(
        api,
        lambda: list(
            mail.iter_filtered_messages(
                folder,
                filter_by=filter_by.value,
                page_size=page_size,
                max_pages=max_pages,
            )
        ),
    )
    _print_enumeration(state, messages, page_size, {"filter_by": filter_by.value})


@app.command("search-folders")
def mail_search_folders(
    ctx: typer.Context,
    folder_ids: Annotated[
        list[str],
        typer.Option("--folder-id", help="Explicit folder ID; repeat for each folder."),
    ],
    query: str = typer.Argument(
        ..., help="Text to search within each selected folder."
    ),
    field: SearchScope = typer.Option(SearchScope.ALL, "--field"),
    limit_per_folder: Annotated[
        int, typer.Option("--limit-per-folder", min=1, max=1000)
    ] = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Search selected folders separately; results are bounded thread groups."""
    if not query.strip():
        raise typer.BadParameter("Search text must not be empty.")
    if (
        not 1 <= len(folder_ids) <= MAX_SEARCH_FOLDERS
        or any(not folder_id.strip() for folder_id in folder_ids)
        or len(set(folder_ids)) != len(folder_ids)
    ):
        raise typer.BadParameter(
            f"Select 1 to {MAX_SEARCH_FOLDERS} unique, non-empty folder IDs."
        )
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
    available = {folder.id: folder for folder in _mail_call(api, mail.folders)}
    if any(folder_id not in available for folder_id in folder_ids):
        raise CLIAbort("Mail folder not found. Use icloud mail folders for exact IDs.")
    payload = _mail_call(
        api,
        lambda: mail.search_folders(
            [available[folder_id] for folder_id in folder_ids],
            query,
            field=field.value,
            limit_per_folder=limit_per_folder,
        ),
    )
    if state.json_output:
        state.write_json(payload)
        return
    _table(
        state,
        "Mail Search (Bounded Per Folder)",
        ["Folder ID", "Folder", _THREAD_ID_COLUMN, "Subject"],
        [
            (page.folder.id, page.folder.name, hit.id, hit.subject)
            for page in payload.pages
            for hit in page.results
        ],
    )
    state.console.print("Incomplete per-folder results; not globally ranked messages.")


@app.command("export")
def mail_export(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    output: Path = typer.Option(..., "--output", help="New private JSON file."),
    include_body: bool = typer.Option(
        False,
        "--include-body",
        help="Explicitly fetch and include unrendered body data.",
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
    """Export selected message metadata to JSON, not raw MIME or a mailbox backup."""
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
    payload = _mail_call(
        api, lambda: mail.export_message(message, include_body=include_body)
    )
    size = _mail_call(api, lambda: _write_export(payload, output))
    result = {"message_id": message.id, "path": str(output), "size": size}
    if state.json_output:
        state.write_json(result)
        return
    state.console.print(Text(str(output)))


@app.command("preview")
def mail_preview(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    limit: LookupLimitOption = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Fetch an advertised snippet as data, without opening or rendering a body."""
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
    payload = _mail_call(api, lambda: mail.preview(message))
    if payload is None:
        raise CLIAbort("Mail returned no preview for this message.")
    if state.json_output:
        state.write_json(payload)
        return
    print_json_text(state.console, payload)


@app.command("filter")
def mail_filter(
    ctx: typer.Context,
    folder_id: FolderOption,
    filter_by: ThreadFilter = typer.Option(
        ..., "--filter", help="One browser thread filter."
    ),
    limit: LimitOption = 50,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List bounded Unread or Flagged thread groups, not per-message flag matches."""
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
        api, lambda: mail.filtered_threads(folder, filter_by.value, limit=limit)
    )
    if state.json_output:
        state.write_json(payload)
        return
    _table(
        state,
        "Mail Filter (Bounded Thread Groups)",
        [_THREAD_ID_COLUMN, "Subject", "Count"],
        [(hit.id, hit.subject, hit.count) for hit in payload.results],
    )
    state.console.print(
        "Incomplete thread groups; not individual unread/flagged messages."
    )


@app.command("body")
def mail_body(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    limit: LookupLimitOption = None,
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
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    part_id: str = typer.Option(
        ..., "--part-id", help="Attachment part ID from JSON metadata."
    ),
    output: Path = typer.Option(
        ..., "--output", help="New destination file in an existing directory."
    ),
    limit: LookupLimitOption = None,
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
