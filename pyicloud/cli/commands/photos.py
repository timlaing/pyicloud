"""Photos commands."""

from __future__ import annotations

import time
from itertools import islice
from pathlib import Path
from typing import Any, Iterator, Optional

import typer

from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import (
    normalize_album,
    normalize_photo,
    normalize_photo_change,
    normalize_photo_details,
    normalize_photo_library,
    normalize_photo_sync_result,
    normalize_sync_cursor,
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
from pyicloud.cli.output import console_table, print_json_text, to_json_string
from pyicloud.services.photos import PhotosServiceException, PhotoSyncOptions
from pyicloud.services.photos_cloudkit.constants import (
    legacy_shared_stream_unsupported_message,
    unsupported_shared_library_album_message,
)

_PHOTO_LIBRARY_KEY_HELP = "Photo library key."

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


def _resolve_photo_library(api: Any, photos: Any, library_key: str) -> Any:
    libraries = service_call(
        "Photos",
        lambda: photos.libraries,
        account_name=api.account_name,
    )
    library = libraries.get(library_key)
    if library is None:
        raise CLIAbort(f"No photo library matched '{library_key}'.")
    return library


def _resolve_cloudkit_photo_library(api: Any, photos: Any, library_key: str) -> Any:
    library = _resolve_photo_library(api, photos, library_key)
    zone_id = getattr(library, "zone_id", None)
    if getattr(library, "scope", None) == "shared-stream" or not isinstance(
        zone_id, dict
    ):
        raise CLIAbort(legacy_shared_stream_unsupported_message(library_key))
    return library


def _album_lookup_error(library: Any, library_key: str, album_name: str) -> CLIAbort:
    if getattr(library, "scope", None) == "shared-library":
        return CLIAbort(
            unsupported_shared_library_album_message(library_key, album_name)
        )
    return CLIAbort(f"No album named '{album_name}' was found.")


def _resolve_album(
    api: Any,
    photos: Any,
    *,
    album: Optional[str],
    library: str,
    shared_stream: bool,
) -> Any:
    """Resolve album object from either CloudKit library or shared streams.

    Args:
        api: PyiCloudService instance
        photos: Photos service instance
        album: Album name (optional for CloudKit, required for shared streams)
        library: Library key (used only for CloudKit path)
        shared_stream: Whether to use shared streams instead of CloudKit

    Returns:
        Resolved album object

    Raises:
        CLIAbort: If album resolution fails or constraints are violated
    """
    if not shared_stream:
        library_obj = _resolve_cloudkit_photo_library(api, photos, library)
        album_obj = service_call(
            "Photos",
            lambda: library_obj.albums.find(album) if album else library_obj.all,
            account_name=api.account_name,
        )

        if album and album_obj is None:
            raise _album_lookup_error(library_obj, library, album)

    elif album:
        album_obj = service_call(
            "Photos",
            lambda: photos.shared_streams.find(album),
            account_name=api.account_name,
        )
        if album_obj is None:
            raise _album_lookup_error(photos.shared_streams, library, album)

    else:
        raise CLIAbort("The --shared-stream option requires an --album name.")

    return album_obj


def _build_photo_sync_options(
    *,
    directory: Path,
    state_dir: Optional[Path],
    library: str,
    album: Optional[list[str]],
    size: str,
    live_photo_size: str,
    folder_structure: str,
    recent: Optional[int],
    until_found: Optional[int],
    skip_videos: bool,
    skip_live_photos: bool,
    align_raw: str,
    xmp_sidecar: bool,
    set_exif_datetime: bool,
    keep_icloud_recent_days: Optional[int],
    only_print_filenames: bool,
    dry_run: bool,
    auto_delete: bool,
) -> PhotoSyncOptions:
    """Build one canonical sync options object for sync-style commands."""

    return PhotoSyncOptions(
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
        align_raw=align_raw,
        xmp_sidecar=xmp_sidecar,
        set_exif_datetime=set_exif_datetime,
        keep_icloud_recent_days=keep_icloud_recent_days,
        only_print_filenames=only_print_filenames,
        dry_run=dry_run,
        auto_delete=auto_delete,
    )


def _render_photo_sync_result(
    state: Any, payload: dict[str, Any], *, title: str
) -> None:
    """Render one photo sync result in text mode."""

    state.console.print(
        console_table(
            title,
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


def _iter_photo_watch_results(
    *,
    api: Any,
    photos: Any,
    options: PhotoSyncOptions,
    interval_seconds: int,
    iterations: int | None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized sync payloads from the watch iterator."""

    watch_iter = service_call(
        "Photos",
        lambda: photos.watch(
            options,
            interval_seconds=interval_seconds,
            iterations=iterations,
        ),
        account_name=api.account_name,
    )
    run_number = 0
    while True:
        try:
            sync_result = service_call(
                "Photos",
                lambda: next(watch_iter),
                account_name=api.account_name,
            )
        except StopIteration:
            return
        run_number += 1
        payload = normalize_photo_sync_result(sync_result)
        payload["iteration"] = run_number
        yield payload


def _print_photo_watch_start(
    state: Any,
    *,
    iteration: int,
    interval_seconds: int,
    iterations: int | None,
) -> None:
    """Print a lightweight progress message before one watch iteration starts."""

    if iterations is None:
        state.console.print(
            f"Starting photo watch run {iteration} (poll interval {interval_seconds}s)..."
        )
        return
    state.console.print(
        "Starting photo watch run "
        f"{iteration} of {iterations} (poll interval {interval_seconds}s)..."
    )


def _print_photo_watch_wait(
    state: Any,
    *,
    interval_seconds: int,
    next_iteration: int,
    iterations: int | None,
) -> None:
    """Print a progress message between completed watch iterations."""

    if iterations is None:
        state.console.print(
            f"Waiting {interval_seconds}s before photo watch run {next_iteration}..."
        )
        return
    state.console.print(
        "Waiting "
        f"{interval_seconds}s before photo watch run {next_iteration} of {iterations}..."
    )


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


@app.command("shared-streams")
def photos_shared_streams(
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """List shared photo streams."""
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
            lambda: list(photos.shared_streams),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(
        console_table(
            "Shared Photo Streams",
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
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
    shared_stream: bool = typer.Option(
        False, "--shared-stream", help="Use shared photo stream."
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

    album_obj = _resolve_album(
        api,
        photos,
        album=album,
        library=library,
        shared_stream=shared_stream,
    )

    payload = [
        normalize_photo(item)
        for item in service_call(
            "Photos",
            lambda: list(islice(album_obj.photos, limit)),
            account_name=api.account_name,
        )
    ]
    if state.json_output:
        state.write_json(payload)
        return

    state.console.print(
        console_table(
            "Photos",
            ["ID", "Filename", "Type", "Created", "Size"]
            + (["Liked", "Like Count"] if shared_stream else []),
            [
                (
                    photo["id"],
                    photo["filename"],
                    photo["item_type"],
                    photo["created"],
                    photo["size"],
                )
                + ((photo["liked"], photo["like_count"]) if shared_stream else ())
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
    shared_stream: bool = typer.Option(
        False, "--shared-stream", help="Use shared photo stream."
    ),
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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

    album_obj = _resolve_album(
        api,
        photos,
        album=album,
        library=library,
        shared_stream=shared_stream,
    )

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
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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
    library_obj = _resolve_cloudkit_photo_library(api, photos, library)
    payload = [
        normalize_photo_change(change)
        for change in service_call(
            "Photos",
            lambda: list(islice(library_obj.iter_changes(since=since), limit)),
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
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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
    library_obj = _resolve_cloudkit_photo_library(api, photos, library)
    if not hasattr(library_obj, "sync_cursor"):
        raise CLIAbort(f"Photo library '{library}' does not support sync cursors.")
    cursor = service_call(
        "Photos",
        lambda: library_obj.sync_cursor(),
        account_name=api.account_name,
    )
    payload = normalize_sync_cursor(cursor, library=library)
    if state.json_output:
        state.write_json(payload)
        return
    state.console.print(cursor)


@app.command("download")
def photos_download(
    ctx: typer.Context,
    photo_id: str = typer.Argument(..., help="Photo asset id."),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        help="Album name to search before falling back to all photos.",
    ),
    shared_stream: bool = typer.Option(
        False, "--shared-stream", help="Use shared photo stream."
    ),
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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

    album_obj = _resolve_album(
        api,
        photos,
        album=album,
        library=library,
        shared_stream=shared_stream,
    )

    try:
        photo = service_call(
            "Photos",
            lambda: (album_obj if album_obj is not None else photos.all)[photo_id],
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
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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
    align_raw: str = typer.Option(
        "as-is",
        "--align-raw",
        help="Treat RAW+JPEG pairs as: as-is, original, or alternative.",
    ),
    xmp_sidecar: bool = typer.Option(
        False,
        "--xmp-sidecar",
        help="Export generated XMP sidecars next to synced primary photo files.",
    ),
    set_exif_datetime: bool = typer.Option(
        False,
        "--set-exif-datetime",
        help="Set JPEG EXIF created timestamps when the file does not already have them.",
    ),
    keep_icloud_recent_days: Optional[int] = typer.Option(
        None,
        "--keep-icloud-recent-days",
        min=0,
        help="Delete remote assets after local confirmation unless they were taken within N days.",
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
    options = _build_photo_sync_options(
        directory=directory,
        state_dir=state_dir,
        library=library,
        album=album,
        size=size,
        live_photo_size=live_photo_size,
        folder_structure=folder_structure,
        recent=recent,
        until_found=until_found,
        skip_videos=skip_videos,
        skip_live_photos=skip_live_photos,
        align_raw=align_raw,
        xmp_sidecar=xmp_sidecar,
        set_exif_datetime=set_exif_datetime,
        keep_icloud_recent_days=keep_icloud_recent_days,
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
    _render_photo_sync_result(state, payload, title="Photo Sync")


@app.command("watch")
def photos_watch(
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
    library: str = typer.Option("root", "--library", help=_PHOTO_LIBRARY_KEY_HELP),
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
    align_raw: str = typer.Option(
        "as-is",
        "--align-raw",
        help="Treat RAW+JPEG pairs as: as-is, original, or alternative.",
    ),
    xmp_sidecar: bool = typer.Option(
        False,
        "--xmp-sidecar",
        help="Export generated XMP sidecars next to synced primary photo files.",
    ),
    set_exif_datetime: bool = typer.Option(
        False,
        "--set-exif-datetime",
        help="Set JPEG EXIF created timestamps when the file does not already have them.",
    ),
    keep_icloud_recent_days: Optional[int] = typer.Option(
        None,
        "--keep-icloud-recent-days",
        min=0,
        help="Delete remote assets after local confirmation unless they were taken within N days.",
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
    interval: int = typer.Option(
        300,
        "--interval",
        min=1,
        help="Poll interval in seconds between sync runs.",
    ),
    iterations: Optional[int] = typer.Option(
        None,
        "--iterations",
        min=1,
        help="Stop after N sync runs. Defaults to watching until interrupted.",
    ),
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Watch a photo sync target and rerun it on a fixed interval."""

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
    options = _build_photo_sync_options(
        directory=directory,
        state_dir=state_dir,
        library=library,
        album=album,
        size=size,
        live_photo_size=live_photo_size,
        folder_structure=folder_structure,
        recent=recent,
        until_found=until_found,
        skip_videos=skip_videos,
        skip_live_photos=skip_live_photos,
        align_raw=align_raw,
        xmp_sidecar=xmp_sidecar,
        set_exif_datetime=set_exif_datetime,
        keep_icloud_recent_days=keep_icloud_recent_days,
        only_print_filenames=only_print_filenames,
        dry_run=dry_run,
        auto_delete=auto_delete,
    )
    try:
        if state.json_output:
            if iterations is None:
                for payload in _iter_photo_watch_results(
                    api=api,
                    photos=photos,
                    options=options,
                    interval_seconds=interval,
                    iterations=iterations,
                ):
                    state.console.print(to_json_string(payload))
                return
            payloads = list(
                _iter_photo_watch_results(
                    api=api,
                    photos=photos,
                    options=options,
                    interval_seconds=interval,
                    iterations=iterations,
                )
            )
            state.write_json(payloads)
            return

        completed_iterations = 0
        while iterations is None or completed_iterations < iterations:
            next_iteration = completed_iterations + 1
            if completed_iterations > 0:
                _print_photo_watch_wait(
                    state,
                    interval_seconds=interval,
                    next_iteration=next_iteration,
                    iterations=iterations,
                )
                time.sleep(interval)
                state.console.print()
            _print_photo_watch_start(
                state,
                iteration=next_iteration,
                interval_seconds=interval,
                iterations=iterations,
            )
            sync_result = service_call(
                "Photos",
                lambda: photos.sync(options),
                account_name=api.account_name,
            )
            completed_iterations += 1
            payload = normalize_photo_sync_result(sync_result)
            payload["iteration"] = completed_iterations
            if only_print_filenames:
                if iterations is None or (iterations and iterations > 1):
                    state.console.print(f"run {payload['iteration']}")
                for item in payload["items"]:
                    state.console.print(item["path"])
            else:
                _render_photo_sync_result(
                    state,
                    payload,
                    title=f"Photo Watch Run {payload['iteration']}",
                )
    except PhotosServiceException as err:
        raise CLIAbort(str(err)) from err
    except KeyboardInterrupt as err:
        raise typer.Exit(code=130) from err
