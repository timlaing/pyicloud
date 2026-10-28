"""Mail reads and explicit message and folder mutation commands."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from enum import Enum
import os
from pathlib import Path
from tempfile import mkstemp
from typing import Annotated, Any, BinaryIO, cast

from rich.text import Text
import typer

from pyicloud.base import PyiCloudService
from pyicloud.cli.command_options import CommandOptions, with_command_options
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
    MailDraft,
    MailFolder,
    MailMessage,
    MailMessageExport,
    MailSender,
    MailService,
)
from pyicloud.services.mail.service import MAX_SEARCH_FOLDERS

app = typer.Typer(help="Read Mail and explicitly change messages or folders.")

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
    int, typer.Option("--page-size", min=1, max=1000, help="Records per page.")
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
_FOLDER_ID_COLUMN = "Folder ID"
_MESSAGE_ID_HELP = "Message ID from mail list."
_DESTINATION_EXISTS = "Destination already exists; choose a new --output path."


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
    *,
    bounded_fallback: bool = True,
) -> MailMessage:
    """Resolve an exact message ID, preserving explicitly bounded legacy lookups."""
    folder = _folder(api, mail, folder_id)
    if limit is None:
        if folder.uid_validity is None or folder.uid_validity < 1:
            if not bounded_fallback:
                raise CLIAbort(
                    "Exact lookup needs a folder with UID validity. "
                    "Refresh folders with 'icloud mail folders' and retry."
                )
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
    return _save_stream(lambda: mail.attachment(message, part_id), output)


def _save_stream(
    stream_factory: Callable[[], AbstractContextManager[Iterator[bytes]]], output: Path
) -> int:
    """Publish unchanged bytes only after the stream and its final guards succeed."""
    size = 0
    with (
        _private_output(output) as temporary,
        stream_factory() as stream,
    ):
        for chunk in stream:
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


def _draft_input(path: Path | None) -> str:
    """Read explicit local UTF-8 input without putting body text in shell arguments."""
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CLIAbort(
            "Draft input could not be read as UTF-8; no save submitted."
        ) from exc


def _require_draft_sender(sender: str) -> None:
    """Reject an unusable --sender before authentication or any lookup.

    ``MailSender.address`` is a strict nonempty string, so an empty value would
    otherwise escape as a raw pydantic error outside ``_mail_call``.
    """
    if not sender.strip():
        raise CLIAbort("Mail: --sender must be a non-empty enabled address.")


def _draft_acknowledgement(state: CLIState, draft: MailDraft) -> None:
    """Report the replacement identity, never echo draft body content."""
    if state.json_output:
        state.write_json(draft)
    else:
        _table(
            state,
            "Saved Mail Draft",
            ["Message ID", "UID", _FOLDER_ID_COLUMN],
            [
                (
                    draft.message.id,
                    draft.message.uid,
                    draft.message.folder.id,
                )
            ],
        )


@app.command("senders")
@with_command_options
def mail_senders(ctx: typer.Context, common: CommandOptions) -> None:
    """Read enabled sender addresses; do not assume the login is a Mail address."""
    common.store(ctx)
    state, api, mail = _context(ctx)
    result = _mail_call(api, mail.senders)
    if state.json_output:
        state.write_json(result)
    else:
        _table(
            state,
            "Enabled Mail Senders",
            ["Address", "Name"],
            [(sender.address, sender.display_name) for sender in result],
        )


@app.command("create-draft")
@with_command_options
def mail_create_draft(
    ctx: typer.Context,
    common: CommandOptions,
    sender: Annotated[
        str, typer.Option("--sender", help="Enabled address from mail senders.")
    ],
    subject: Annotated[str, typer.Option("--subject")] = "",
    text_file: Annotated[
        Path | None,
        typer.Option("--text-file", help="UTF-8 plain-text body; omitted means empty."),
    ] = None,
    html_file: Annotated[
        Path | None,
        typer.Option(
            "--html-file", help="UTF-8 HTML; omitted derives HTML from plain text."
        ),
    ] = None,
) -> None:
    """Save one recipient-free, attachment-free draft without sending it."""
    text, html = _draft_input(text_file), _draft_input(html_file)
    common.store(ctx)
    state, api, mail = _context(ctx)
    result = _mail_call(
        api,
        lambda: mail.create_draft(sender=sender, subject=subject, text=text, html=html),
    )
    _draft_acknowledgement(state, result)


@app.command("edit-draft")
@with_command_options
def mail_edit_draft(
    ctx: typer.Context,
    common: CommandOptions,
    message_id: str,
    folder_id: FolderOption,
    sender: Annotated[
        str, typer.Option("--sender", help="Current enabled draft sender.")
    ],
    subject: Annotated[
        str, typer.Option("--subject", help="Full replacement subject.")
    ],
    text_file: Annotated[
        Path,
        typer.Option(
            "--text-file", help="Full UTF-8 plain-text replacement; may be empty."
        ),
    ],
    html_file: Annotated[
        Path,
        typer.Option(
            "--html-file", help="UTF-8 HTML; empty derives HTML from plain text."
        ),
    ],
) -> None:
    """Replace one recipient-free draft; use its returned UID and message ID."""
    _require_draft_sender(sender)
    text, html = _draft_input(text_file), _draft_input(html_file)
    common.store(ctx)
    state, api, mail = _context(ctx)
    draft = MailDraft(
        message=_message(api, mail, folder_id, message_id, None),
        sender=MailSender(address=sender),
    )
    result = _mail_call(
        api, lambda: mail.edit_draft(draft, subject=subject, text=text, html=html)
    )
    _draft_acknowledgement(state, result)


@app.command("discard-draft")
@with_command_options
def mail_discard_draft(
    ctx: typer.Context,
    common: CommandOptions,
    message_id: str,
    folder_id: FolderOption,
    trash_id: Annotated[
        str, typer.Option("--trash-id", help="Exact Deleted Messages folder ID.")
    ],
    sender: Annotated[
        str, typer.Option("--sender", help="Current enabled draft sender.")
    ],
) -> None:
    """Move only this recipient-free draft to Trash; request seen, never destroy."""
    _require_draft_sender(sender)
    common.store(ctx)
    state, api, mail = _context(ctx)
    draft = MailDraft(
        message=_message(api, mail, folder_id, message_id, None),
        sender=MailSender(address=sender),
    )
    trash = _folder(api, mail, trash_id)
    result = _mail_call(api, lambda: mail.discard_draft(draft, trash))
    if state.json_output:
        state.write_json(result)
    else:
        _table(
            state,
            "Discarded Mail Draft",
            ["Source Message ID", "Trash Folder ID"],
            [
                (
                    result.source_message_id,
                    result.destination_folder_id,
                )
            ],
        )


def _folder_acknowledgement(state: CLIState, folder: MailFolder) -> None:
    """Keep user-visible folder results distinct from mutation failure maps."""
    if state.json_output:
        state.write_json(folder)
    else:
        _table(
            state,
            "Mail Folder",
            ["ID", "Server Path", "UID Validity"],
            [(folder.id, folder.name, folder.uid_validity)],
        )


@app.command("create-folder")
def mail_create_folder(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="New Unicode leaf name, not a path.")],
    parent_id: Annotated[
        str | None, typer.Option("--parent-id", help="Optional exact parent folder ID.")
    ] = None,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Create one custom folder without overwriting or retrying uncertain writes."""
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
    parent = None if parent_id is None else _folder(api, mail, parent_id)
    result = _mail_call(api, lambda: mail.create_folder(name, parent=parent))
    _folder_acknowledgement(state, result)


@app.command("rename-folder")
def mail_rename_folder(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Replacement Unicode leaf name.")],
    folder_id: FolderOption,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Rename one exact custom folder within its current parent namespace."""
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
    result = _mail_call(api, lambda: mail.rename_folder(folder, name))
    _folder_acknowledgement(state, result)


@app.command("delete-folder")
def mail_delete_folder(
    ctx: typer.Context,
    folder_id: FolderOption,
    confirm_folder_id: Annotated[
        str,
        typer.Option(
            "--confirm-folder-id",
            help=(
                "Repeat the exact ID to confirm irreversible deletion "
                "and racy emptiness checks."
            ),
        ),
    ],
    allow_nonempty: Annotated[
        bool,
        typer.Option(
            "--allow-nonempty",
            help="Also permit permanent deletion of messages observed in this folder.",
        ),
    ] = False,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Permanently delete one custom folder; no child-tree cascade is supported.

    This does not move the folder to Trash. New mail can race the emptiness
    check; confirming the ID explicitly acknowledges that irreversible risk.
    """
    if folder_id != confirm_folder_id:
        raise CLIAbort(
            "Confirmation does not match the folder ID; no deletion submitted."
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
    folder = _folder(api, mail, folder_id)
    result = _mail_call(
        api,
        lambda: mail.delete_folder(folder, confirm=True, allow_nonempty=allow_nonempty),
    )
    if state.json_output:
        state.write_json(result)
    else:
        _table(
            state,
            "Permanent Mail Folder Deletion",
            [_FOLDER_ID_COLUMN, "Permanent"],
            [(result.folder_id, result.permanent)],
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


@app.command("move")
def mail_move(
    ctx: typer.Context,
    message_id: Annotated[str, typer.Argument(help=_MESSAGE_ID_HELP)],
    folder_id: FolderOption,
    destination_id: Annotated[
        str, typer.Option("--destination-id", help="Exact destination folder ID.")
    ],
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Move one exact message, preserving flags; never retry an uncertain write.

    Trash is a move destination, not permanent deletion. Junk and Drafts moves
    are unsupported. IDs can change: refresh destination metadata afterward.
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
    source = _folder(api, mail, folder_id)
    destination = _folder(api, mail, destination_id)
    message = _mail_call(api, lambda: mail.message(source, message_id))
    if message is None:
        raise CLIAbort("Message not found in this folder; no move submitted.")
    result = _mail_call(api, lambda: mail.move(message, destination))
    if state.json_output:
        state.write_json(result)
    else:
        _table(
            state,
            "Mail Move Acknowledgement",
            ["Source Message ID", "Source Folder ID", "Destination Folder ID"],
            [
                (
                    result.source_message_id,
                    result.source_folder_id,
                    result.destination_folder_id,
                )
            ],
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


@app.command("get")
def mail_get(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str | None = typer.Argument(None, help=_MESSAGE_ID_HELP),
    uid: Annotated[int | None, typer.Option("--uid", min=1)] = None,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Read exact folder-scoped metadata by message ID or UID, without bodies."""
    if (message_id is None) == (uid is None):
        raise typer.BadParameter("Supply exactly one message ID or --uid.")
    if message_id is not None and not message_id.strip():
        raise typer.BadParameter("Message ID must not be empty.")
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
        lambda: (
            mail.message_by_uid(folder, uid)
            if uid is not None
            else mail.message(folder, cast(str, message_id))
        ),
    )
    if payload is None:
        raise CLIAbort("Message not found in this folder.")
    if state.json_output:
        state.write_json(payload)
        return
    print_json_text(state.console, payload)


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
        [_FOLDER_ID_COLUMN, "Folder", _THREAD_ID_COLUMN, "Subject"],
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
    limit: LookupLimitOption = None,
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
        raise CLIAbort(_DESTINATION_EXISTS)
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


@app.command("download-maildrop")
def mail_download_maildrop(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    part_index: int = typer.Option(
        ..., "--part-index", min=1, help="1-based position in the message parts array."
    ),
    output: Path = typer.Option(
        ..., "--output", help="New private destination; never a remote filename."
    ),
    max_bytes: int = typer.Option(
        104857600, "--max-bytes", min=1, help="Hard byte limit; default 100 MiB."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Download a verified Mail Drop part without forwarding account credentials."""
    if os.path.lexists(output):
        raise CLIAbort(_DESTINATION_EXISTS)
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
    message = _message(api, mail, folder_id, message_id, None, bounded_fallback=False)
    size = _mail_call(
        api,
        lambda: _save_stream(
            lambda: mail.mail_drop_attachment(message, part_index, max_bytes=max_bytes),
            output,
        ),
    )
    if state.json_output:
        state.write_json({"message_id": message.id, "path": str(output), "size": size})
        return
    state.console.print(Text(str(output)))


@app.command("export-eml")
def mail_export_eml(
    ctx: typer.Context,
    folder_id: FolderOption,
    message_id: str = typer.Argument(..., help=_MESSAGE_ID_HELP),
    output: Path = typer.Option(
        ..., "--output", help="New private file for original EML bytes."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Save original MIME bytes from Apple; never reconstruct or render the message."""
    if os.path.lexists(output):
        raise CLIAbort(_DESTINATION_EXISTS)
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
    message = _message(api, mail, folder_id, message_id, None, bounded_fallback=False)
    size = _mail_call(
        api, lambda: _save_stream(lambda: mail.raw_message(message), output)
    )
    payload = {"message_id": message.id, "path": str(output), "size": size}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(Text(str(output)))


@app.command("preview")
def mail_preview(
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


@app.command("filter-threads")
def mail_filter_threads(
    ctx: typer.Context,
    folder_id: FolderOption,
    filter_by: ThreadFilter = typer.Option(..., "--filter"),
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
    """Page filtered thread IDs and timestamps, not digests or individual messages."""
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
    threads = _mail_call(
        api,
        lambda: list(
            mail.iter_filtered_threads(
                folder,
                filter_by=filter_by.value,
                page_size=page_size,
                max_pages=max_pages,
            )
        ),
    )
    if state.json_output:
        state.write_json({
            "threads": threads,
            "filter_by": filter_by.value,
            "page_size": page_size,
            "exhausted": True,
            "snapshot": False,
        })
        return
    _table(
        state,
        "Mail Filtered Thread IDs (Cursor Enumeration)",
        [_THREAD_ID_COLUMN, "Timestamp"],
        [(thread.id, thread.timestamp) for thread in threads],
    )
    state.console.print(
        "Cursor exhausted; live thread identities, not a snapshot or digests."
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
        raise CLIAbort(_DESTINATION_EXISTS)
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
