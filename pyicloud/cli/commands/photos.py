"""Photos commands."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Optional

import typer

from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import (
    normalize_album,
    normalize_photo,
    normalize_photo_change,
    normalize_photo_details,
    normalize_photo_library,
    normalize_photo_sync_result,
)
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
from pyicloud.services.photos import PhotosServiceException, PhotoSyncOptions

app = typer.Typer(help="Browse and download iCloud Photos.")


def _resolve_photos_service(
    ctx: typer.Context,
    *,
    username: UsernameOption,
    session_dir: SessionDirOption,
    http_proxy: HttpProxyOption,
    https_proxy: HttpsProxyOption,
    no_verify_ssl: NoVerifySslOption,
    output_format: OutputFormatOption,
    log_level: LogLevelOption,
):
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
    photos = service_call("Photos", lambda: api.photos, account_name=api.account_name)
    return state, api, photos


@app.command("albums")
def photos_albums(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List photo albums."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    payload = [
        normalize_album(album)
        for album in service_call(
            "Photos",
            lambda: list(photos.albums),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Photo Albums",
            ["Name", "Full Name", "Count"],
            [(album["name"], album["full_name"], album["count"]) for album in payload],
        )
    )


@app.command("libraries")
def photos_libraries(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List available photo libraries and sync cursors."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    payload = [
        normalize_photo_library(key, library)
        for key, library in service_call(
            "Photos",
            lambda: photos.libraries.items(),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Photo Libraries",
            ["Key", "Scope", "Zone", "Indexing", "Sync Cursor"],
            [
                (
                    item["key"],
                    item["scope"],
                    item["zone_name"],
                    item["indexing_state"],
                    item["sync_cursor"],
                )
                for item in payload
            ],
        )
    )


@app.command("list")
def photos_list(
    ctx: typer.Context,
    album: Optional[str] = typer.Option(
        None, "--album", help="Album name. Defaults to all photos."
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum photos to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List photo assets."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    album_obj = service_call(
        "Photos",
        lambda: photos.albums.find(album) if album else photos.all,
        account_name=api.account_name,
    )
    if album and album_obj is None:
        raise CLIAbort(f"No album named '{album}' was found.")
    payload = [
        normalize_photo(item)
        for item in service_call(
            "Photos",
            lambda: list(
                islice(
                    album_obj.photos if album_obj is not None else photos.all.photos,
                    limit,
                )
            ),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Photos",
            ["ID", "Filename", "Type", "Created", "Size"],
            [
                (
                    photo["id"],
                    photo["filename"],
                    photo["item_type"],
                    photo["created"],
                    photo["size"],
                )
                for photo in payload
            ],
        )
    )


@app.command("get")
def photos_get(
    ctx: typer.Context,
    photo_id: str = typer.Argument(..., help="Photo asset id."),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        help="Album name to search before falling back to all photos.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show detailed metadata for a single photo asset."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    album_obj = service_call(
        "Photos",
        lambda: photos.albums.find(album) if album else photos.all,
        account_name=api.account_name,
    )
    if album and album_obj is None:
        raise CLIAbort(f"No album named '{album}' was found.")
    try:
        photo = service_call(
            "Photos",
            lambda: (album_obj if album_obj is not None else photos.all)[photo_id],
            account_name=api.account_name,
        )
    except KeyError as err:
        raise CLIAbort(f"No photo matched '{photo_id}'.") from err
    payload = normalize_photo_details(photo)
    if state.json_output:
        state.write_json(payload)
        return
    print_json_text(state.console, payload)


@app.command("changes")
def photos_changes(
    ctx: typer.Context,
    since: Optional[str] = typer.Option(
        None, "--since", help="Sync cursor to fetch changes after."
    ),
    limit: int = typer.Option(100, "--limit", min=1, help="Maximum changes to show."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List incremental photo change events."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    payload = [
        normalize_photo_change(change)
        for change in service_call(
            "Photos",
            lambda: list(islice(photos.iter_changes(since=since), limit)),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Photo Changes",
            ["Kind", "Record", "Type", "Deleted", "Modified"],
            [
                (
                    item["kind"],
                    item["record_name"],
                    item["record_type"],
                    item["deleted"],
                    item["modified"],
                )
                for item in payload
            ],
        )
    )


@app.command("sync-cursor")
def photos_sync_cursor(
    ctx: typer.Context,
    library: str = typer.Option("root", "--library", help="Library key."),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Show the current sync cursor for a photo library."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    libraries = service_call(
        "Photos",
        lambda: photos.libraries,
        account_name=api.account_name,
    )
    library_obj = libraries.get(library)
    if library_obj is None:
        raise CLIAbort(f"No photo library matched '{library}'.")
    if not hasattr(library_obj, "sync_cursor"):
        raise CLIAbort(f"Photo library '{library}' does not support sync cursors.")
    cursor = service_call(
        "Photos",
        lambda: library_obj.sync_cursor(),
        account_name=api.account_name,
    )
    payload = {"library": library, "sync_cursor": cursor}
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(cursor)


@app.command("download")
def photos_download(
    ctx: typer.Context,
    photo_id: str = typer.Argument(..., help="Photo asset id."),
    output: Path = typer.Option(..., "--output", help="Destination file path."),
    version: str = typer.Option(
        "original", "--version", help="Photo version to download."
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Download a photo asset."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    try:
        photo = service_call(
            "Photos",
            lambda: photos.all[photo_id],
            account_name=api.account_name,
        )
    except KeyError as err:
        raise CLIAbort(f"No photo matched '{photo_id}'.") from err
    data = service_call(
        "Photos",
        lambda: photo.download(version=version),
        account_name=api.account_name,
    )
    if data is None:
        raise CLIAbort("No data was returned for that photo version.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    if state.json_output:
        state.write_json(
            {"photo_id": photo_id, "path": str(output), "version": version}
        )
        return
    state.console.print(str(output))


@app.command("sync")
def photos_sync(
    ctx: typer.Context,
    directory: Path = typer.Option(
        ...,
        "--directory",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Destination directory for synced photos.",
    ),
    album: Optional[list[str]] = typer.Option(
        None,
        "--album",
        help="Album name to sync. Repeat to sync multiple albums.",
    ),
    library: str = typer.Option("root", "--library", help="Photo library key."),
    state_dir: Optional[Path] = typer.Option(
        None,
        "--state-dir",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Directory for persistent sync state. Defaults to <directory>/.pyicloud-state.",
    ),
    size: str = typer.Option(
        "original",
        "--size",
        help="Primary photo size to sync: original, medium, or thumb.",
    ),
    live_photo_size: str = typer.Option(
        "original",
        "--live-photo-size",
        help="Live photo video size to sync: original, medium, or thumb.",
    ),
    folder_structure: str = typer.Option(
        "none",
        "--folder-structure",
        help="Datetime folder layout, for example '{:%Y/%m}', or 'none' for a flat directory.",
    ),
    recent: Optional[int] = typer.Option(
        None,
        "--recent",
        min=1,
        help="Only sync photos added within the last N days.",
    ),
    until_found: Optional[int] = typer.Option(
        None,
        "--until-found",
        min=1,
        help="Stop after N consecutive already-current files.",
    ),
    skip_videos: bool = typer.Option(
        False,
        "--skip-videos",
        help="Skip standalone videos and live photo video companions.",
    ),
    skip_live_photos: bool = typer.Option(
        False,
        "--skip-live-photos",
        help="Skip live photo assets entirely.",
    ),
    only_print_filenames: bool = typer.Option(
        False,
        "--only-print-filenames",
        help="Print the target filenames without downloading them.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview sync actions without writing files or state.",
    ),
    auto_delete: bool = typer.Option(
        False,
        "--auto-delete",
        help="Delete local files that are no longer present remotely for this sync target.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Synchronize photo resources into a local directory."""
    state, api, photos = _resolve_photos_service(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    options = PhotoSyncOptions(
        directory=directory,
        state_dir=state_dir,
        library=library,
        albums=tuple(album or ()),
        size=size,
        live_photo_size=live_photo_size,
        folder_structure=folder_structure,
        recent=recent,
        until_found=until_found,
        skip_videos=skip_videos,
        skip_live_photos=skip_live_photos,
        only_print_filenames=only_print_filenames,
        dry_run=dry_run,
        auto_delete=auto_delete,
    )
    try:
        sync_result = service_call(
            "Photos",
            lambda: photos.sync(options),
            account_name=api.account_name,
        )
    except PhotosServiceException as err:
        raise CLIAbort(str(err)) from err
    payload = normalize_photo_sync_result(sync_result)
    if state.json_output:
        state.write_json(payload)
        return
    if only_print_filenames:
        for item in payload["items"]:
            state.console.print(item["path"])
        return
    state.console.print(
        console_table(
            "Photo Sync",
            ["Metric", "Value"],
            [
                ("Directory", payload["directory"]),
                ("State Path", payload["state_path"]),
                ("Library", payload["library"]),
                ("Albums", ", ".join(payload["albums"]) or "(all photos)"),
                ("Sync Cursor", payload["sync_cursor"] or ""),
                ("Short Circuited", payload["short_circuited"]),
                ("Downloaded", payload["downloaded_count"]),
                ("Skipped", payload["skipped_count"]),
                ("Deleted", payload["deleted_count"]),
                ("Listed", payload["listed_count"]),
            ],
        )
    )
    for item in payload["items"]:
        if item["action"] == "skipped":
            continue
        state.console.print(f"{item['action']}: {item['path']}")
