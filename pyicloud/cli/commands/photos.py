"""Photos commands."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Optional

import typer

from pyicloud.cli.context import CLIAbort, get_state, service_call
from pyicloud.cli.normalize import normalize_album, normalize_photo
from pyicloud.cli.options import with_service_command_options
from pyicloud.cli.output import console_table

app = typer.Typer(help="Browse and download iCloud Photos.")


@app.command("albums")
@with_service_command_options
def photos_albums(ctx: typer.Context) -> None:
    """List photo albums."""

    state = get_state(ctx)
    api = state.get_api()
    photos = service_call("Photos", lambda: api.photos)
    payload = [normalize_album(album) for album in photos.albums]
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


@app.command("list")
@with_service_command_options
def photos_list(
    ctx: typer.Context,
    album: Optional[str] = typer.Option(
        None, "--album", help="Album name. Defaults to all photos."
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum photos to show."),
) -> None:
    """List photo assets."""

    state = get_state(ctx)
    api = state.get_api()
    photos = service_call("Photos", lambda: api.photos)
    album_obj = photos.albums.find(album) if album else photos.all
    if album and album_obj is None:
        raise CLIAbort(f"No album named '{album}' was found.")
    photos_iter = album_obj.photos if album_obj is not None else photos.all.photos
    payload = [normalize_photo(item) for item in islice(photos_iter, limit)]
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


@app.command("download")
@with_service_command_options
def photos_download(
    ctx: typer.Context,
    photo_id: str = typer.Argument(..., help="Photo asset id."),
    output: Path = typer.Option(..., "--output", help="Destination file path."),
    version: str = typer.Option(
        "original", "--version", help="Photo version to download."
    ),
) -> None:
    """Download a photo asset."""

    state = get_state(ctx)
    api = state.get_api()
    photos = service_call("Photos", lambda: api.photos)
    photo = photos.all[photo_id]
    data = photo.download(version=version)
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
