"""State-backed sync pipeline for the modern Photos service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator

from .constants import (
    legacy_shared_stream_unsupported_message,
    unsupported_shared_library_album_message,
)
from .materialize import (
    apply_align_raw_policy,
    set_exif_datetime_if_missing,
    write_xmp_sidecar,
)
from .models import PhotoResource, PhotosServiceException
from .state import PhotoSyncState, SyncedPhotoResource, create_photo_sync_state

DEFAULT_FOLDER_STRUCTURE = "none"
PRIMARY_SYNC_VERSIONS = {"original", "medium", "thumb"}
LIVE_PHOTO_SYNC_VERSIONS = {"original", "medium", "thumb"}
UNSAFE_PATH_COMPONENT_CHARS = re.compile(r"[\x00-\x1f/\\:]+")
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PhotoSyncOptions:
    """Options controlling a photos sync target and materialization policy."""

    directory: Path
    state_dir: Path | None = None
    library: str = "root"
    albums: tuple[str, ...] = ()
    size: str = "original"
    live_photo_size: str = "original"
    folder_structure: str = DEFAULT_FOLDER_STRUCTURE
    recent: int | None = None
    until_found: int | None = None
    skip_videos: bool = False
    skip_live_photos: bool = False
    align_raw: str = "as-is"
    xmp_sidecar: bool = False
    set_exif_datetime: bool = False
    keep_icloud_recent_days: int | None = None
    only_print_filenames: bool = False
    dry_run: bool = False
    auto_delete: bool = False

    def normalized_albums(self) -> tuple[str, ...]:
        """Return a stable album selection tuple."""

        return tuple(sorted(album for album in self.albums if album))

    def target_payload(self) -> dict[str, Any]:
        """Return the persisted sync-target identity payload."""

        return {
            "library": self.library,
            "albums": self.normalized_albums(),
            "directory": str(self.directory.resolve()),
            "size": self.size,
            "live_photo_size": self.live_photo_size,
            "folder_structure": self.folder_structure,
            "recent": self.recent,
            "skip_videos": self.skip_videos,
            "skip_live_photos": self.skip_live_photos,
            "align_raw": self.align_raw,
        }

    def target_key(self) -> str:
        """Return a stable identifier for the current sync target."""

        payload = json.dumps(
            self.target_payload(), sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]  # nosec B324
        album_label = "all"
        if self.albums:
            album_label = "-".join(
                _sanitize_name(name) for name in self.normalized_albums()
            )
            album_label = album_label[:48] or "albums"
        return f"{_sanitize_name(self.library)}-{album_label}-{digest}"

    def state_root(self) -> Path:
        """Return the directory where sync state should be stored."""

        return self.state_dir or self.directory / ".pyicloud-state"

    def state_path(self) -> Path:
        """Return the SQLite path for this sync target."""

        return self.state_root() / f"{self.target_key()}.sqlite3"


@dataclass(slots=True)
class PhotoSyncItem:
    """One file-level action performed or considered by the sync engine."""

    asset_id: str
    resource_key: str
    path: str
    action: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly item payload."""

        return {
            "asset_id": self.asset_id,
            "resource_key": self.resource_key,
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(slots=True)
class PhotoSyncResult:
    """Summary of one sync run."""

    directory: str
    state_path: str
    library: str
    albums: list[str]
    sync_cursor: str | None = None
    short_circuited: bool = False
    downloaded_count: int = 0
    skipped_count: int = 0
    deleted_count: int = 0
    listed_count: int = 0
    items: list[PhotoSyncItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary payload."""

        return {
            "directory": self.directory,
            "state_path": self.state_path,
            "library": self.library,
            "albums": self.albums,
            "sync_cursor": self.sync_cursor,
            "short_circuited": self.short_circuited,
            "downloaded_count": self.downloaded_count,
            "skipped_count": self.skipped_count,
            "deleted_count": self.deleted_count,
            "listed_count": self.listed_count,
            "items": [item.as_dict() for item in self.items],
        }


def watch_photo_sync(
    service: Any,
    options: PhotoSyncOptions,
    *,
    interval_seconds: int,
    iterations: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Iterator[PhotoSyncResult]:
    """Yield repeated sync runs for the given sync target."""

    if interval_seconds < 1:
        raise PhotosServiceException("--interval must be at least 1 second.")
    if iterations is not None and iterations < 1:
        raise PhotosServiceException("--iterations must be at least 1.")

    completed = 0
    while iterations is None or completed < iterations:
        yield run_photo_sync(service, options)
        completed += 1
        if iterations is not None and completed >= iterations:
            return
        sleep_fn(interval_seconds)


def run_photo_sync(service: Any, options: PhotoSyncOptions) -> PhotoSyncResult:
    """Synchronize selected photo resources into a local output directory."""

    if options.size not in PRIMARY_SYNC_VERSIONS:
        raise PhotosServiceException(
            f"Unsupported photo size '{options.size}'. Choose from: original, medium, thumb."
        )
    if options.live_photo_size not in LIVE_PHOTO_SYNC_VERSIONS:
        raise PhotosServiceException(
            "Unsupported live photo size "
            f"'{options.live_photo_size}'. Choose from: original, medium, thumb."
        )
    if options.align_raw not in {"as-is", "original", "alternative"}:
        raise PhotosServiceException(
            "Unsupported RAW alignment "
            f"'{options.align_raw}'. Choose from: as-is, original, alternative."
        )
    if options.auto_delete and options.until_found is not None:
        raise PhotosServiceException(
            "--auto-delete cannot be combined with --until-found."
        )
    if options.until_found is not None and options.until_found < 1:
        raise PhotosServiceException("--until-found must be at least 1.")
    if options.recent is not None and options.recent < 1:
        raise PhotosServiceException("--recent must be at least 1 day.")
    if (
        options.keep_icloud_recent_days is not None
        and options.keep_icloud_recent_days < 0
    ):
        raise PhotosServiceException("--keep-icloud-recent-days must be at least 0.")
    if options.keep_icloud_recent_days is not None and options.until_found is not None:
        raise PhotosServiceException(
            "--keep-icloud-recent-days cannot be combined with --until-found."
        )

    if not options.dry_run and not options.only_print_filenames:
        options.directory.mkdir(parents=True, exist_ok=True)
    result = PhotoSyncResult(
        directory=str(options.directory),
        state_path=str(options.state_path()),
        library=options.library,
        albums=list(options.normalized_albums()),
    )

    state_backend = create_photo_sync_state(
        options.state_path(),
        ephemeral=(options.dry_run or options.only_print_filenames)
        and not options.state_path().exists(),
    )

    with state_backend as state:
        selected_library = _resolve_library(service, options.library)
        current_cursor = _sync_cursor(selected_library, service)
        result.sync_cursor = current_cursor
        sync_complete = True
        tracked_resources = list(state.iter_resources())
        tracked_paths = {
            entry.relative_path: (entry.asset_id, entry.resource_key)
            for entry in tracked_resources
        }
        if _can_short_circuit(
            state=state,
            directory=options.directory,
            current_cursor=current_cursor,
            auto_delete=options.auto_delete,
            dry_run=options.dry_run,
            only_print_filenames=options.only_print_filenames,
            xmp_sidecar=options.xmp_sidecar,
            set_exif_datetime=options.set_exif_datetime,
            keep_icloud_recent_days=options.keep_icloud_recent_days,
        ):
            result.short_circuited = True
            return result

        current_entries: set[tuple[str, str]] = set()
        reserved_paths: set[str] = set()
        consecutive_seen = 0
        cutoff = None
        if options.recent is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=options.recent)
        now_local = datetime.now().astimezone()

        for asset in _iter_sync_assets(service, selected_library, options):
            added_at = _asset_datetime(asset, "added_date", "asset_date")
            if cutoff is not None and (added_at is None or added_at < cutoff):
                continue
            resources = _select_resources(asset, options)
            if not resources:
                continue
            asset_ready_for_delete = True
            asset_confirmed_local = False
            asset_paths: list[str] = []
            for resource_key, resource in resources:
                relative_path = _unique_relative_path(
                    candidate=_render_relative_path(
                        asset, resource, options.folder_structure
                    ),
                    asset_id=asset.id,
                    resource_key=resource_key,
                    reserved_paths=reserved_paths,
                    tracked_paths=tracked_paths,
                )
                reserved_paths.add(relative_path)
                current_entries.add((asset.id, resource_key))
                asset_paths.append(relative_path)
                try:
                    target_path = _safe_target_path(options.directory, relative_path)
                except PhotosServiceException as exc:
                    _LOGGER.warning(
                        "Skipping resource with unsafe path '%s' under '%s': %s",
                        relative_path,
                        options.directory,
                        exc,
                    )
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=asset.id,
                            resource_key=resource_key,
                            path=relative_path,
                            action="skipped",
                            reason="unsafe-path",
                        )
                    )
                    result.skipped_count += 1
                    asset_ready_for_delete = False
                    consecutive_seen = 0
                    sync_complete = False
                    continue
                manifest = state.get_resource(asset.id, resource_key)
                if _is_current_file(target_path, manifest, resource, relative_path):
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=asset.id,
                            resource_key=resource_key,
                            path=relative_path,
                            action="skipped",
                            reason="already-current",
                        )
                    )
                    result.skipped_count += 1
                    asset_confirmed_local = True
                    _apply_local_metadata(
                        asset=asset,
                        resource=resource,
                        resource_key=resource_key,
                        target_path=target_path,
                        options=options,
                    )
                    consecutive_seen += 1
                    if (
                        options.until_found is not None
                        and consecutive_seen >= options.until_found
                    ):
                        break
                    continue

                consecutive_seen = 0
                action = "listed" if options.only_print_filenames else "downloaded"
                if options.dry_run:
                    action = "listed"
                if options.only_print_filenames or options.dry_run:
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=asset.id,
                            resource_key=resource_key,
                            path=relative_path,
                            action=action,
                            reason="dry-run" if options.dry_run else "print-only",
                        )
                    )
                    result.listed_count += 1
                    asset_ready_for_delete = False
                    continue

                data = asset.download(version=resource_key)
                if data is None:
                    sync_complete = False
                    asset_ready_for_delete = False
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=asset.id,
                            resource_key=resource_key,
                            path=relative_path,
                            action="skipped",
                            reason="missing-download-data",
                        )
                    )
                    result.skipped_count += 1
                    continue
                _atomic_write_bytes(target_path, data)
                _apply_local_metadata(
                    asset=asset,
                    resource=resource,
                    resource_key=resource_key,
                    target_path=target_path,
                    options=options,
                )
                downloaded_at = datetime.now(timezone.utc).isoformat()
                state.upsert_resource(
                    SyncedPhotoResource(
                        asset_id=asset.id,
                        resource_key=resource_key,
                        relative_path=relative_path,
                        size=resource.size,
                        checksum=getattr(resource, "checksum", None),
                        downloaded_at=downloaded_at,
                    )
                )
                result.items.append(
                    PhotoSyncItem(
                        asset_id=asset.id,
                        resource_key=resource_key,
                        path=relative_path,
                        action="downloaded",
                    )
                )
                result.downloaded_count += 1
                asset_confirmed_local = True

            if _should_delete_remote_asset(
                asset=asset,
                options=options,
                asset_ready_for_delete=asset_ready_for_delete,
                asset_confirmed_local=asset_confirmed_local,
                now_local=now_local,
            ):
                deleted = asset.delete()
                sync_complete = False
                if deleted:
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=asset.id,
                            resource_key="remote",
                            path=asset_paths[0] if asset_paths else asset.filename,
                            action="deleted",
                            reason="keep-icloud-recent-days",
                        )
                    )
                    result.deleted_count += 1
            if (
                options.until_found is not None
                and consecutive_seen >= options.until_found
            ):
                break

        if (
            options.auto_delete
            and not options.only_print_filenames
            and not options.dry_run
        ):
            for stale in tracked_resources:
                key = (stale.asset_id, stale.resource_key)
                if key in current_entries:
                    continue
                try:
                    stale_path = _safe_target_path(
                        options.directory, stale.relative_path
                    )
                except PhotosServiceException as exc:
                    _LOGGER.warning(
                        "Ignoring unsafe stale local photo path '%s': %s",
                        stale.relative_path,
                        exc,
                    )
                    state.delete_resource(stale.asset_id, stale.resource_key)
                    result.items.append(
                        PhotoSyncItem(
                            asset_id=stale.asset_id,
                            resource_key=stale.resource_key,
                            path=stale.relative_path,
                            action="skipped",
                            reason="unsafe-path",
                        )
                    )
                    result.skipped_count += 1
                    continue
                if stale_path.exists():
                    try:
                        stale_path.unlink()
                    except OSError as exc:
                        _LOGGER.warning(
                            "Failed to remove stale local photo '%s': %s",
                            stale_path,
                            exc,
                        )
                        continue
                state.delete_resource(stale.asset_id, stale.resource_key)
                result.items.append(
                    PhotoSyncItem(
                        asset_id=stale.asset_id,
                        resource_key=stale.resource_key,
                        path=stale.relative_path,
                        action="deleted",
                    )
                )
                result.deleted_count += 1

        if sync_complete and not options.only_print_filenames and not options.dry_run:
            state.set_sync_cursor(current_cursor)

    return result


def _resolve_library(service: Any, library_key: str):
    libraries = getattr(service, "libraries", {})
    if not isinstance(libraries, dict):
        raise PhotosServiceException(
            "Photos service does not expose syncable libraries."
        )
    library = libraries.get(library_key)
    if library is None:
        raise PhotosServiceException(f"No photo library matched '{library_key}'.")
    if library_key == "shared" or getattr(library, "scope", None) == "shared-stream":
        raise PhotosServiceException(
            legacy_shared_stream_unsupported_message(library_key)
        )
    return library


def _sync_cursor(library: Any, service: Any) -> str | None:
    if hasattr(library, "sync_cursor"):
        return library.sync_cursor()
    if hasattr(service, "sync_cursor"):
        return service.sync_cursor()
    return None


def _can_short_circuit(
    *,
    state: PhotoSyncState,
    directory: Path,
    current_cursor: str | None,
    auto_delete: bool,
    dry_run: bool,
    only_print_filenames: bool,
    xmp_sidecar: bool,
    set_exif_datetime: bool,
    keep_icloud_recent_days: int | None,
) -> bool:
    if (
        auto_delete
        or dry_run
        or only_print_filenames
        or xmp_sidecar
        or set_exif_datetime
        or keep_icloud_recent_days is not None
    ):
        return False
    if current_cursor is None or state.get_sync_cursor() != current_cursor:
        return False
    if state.resource_count() == 0:
        return False
    for entry in state.iter_resources():
        try:
            path = _safe_target_path(directory, entry.relative_path)
        except PhotosServiceException:
            return False
        if not path.exists():
            return False
        if entry.size is not None:
            try:
                if path.stat().st_size != entry.size:
                    return False
            except OSError:
                return False
    return True


def _iter_sync_assets(
    service: Any,
    library: Any,
    options: PhotoSyncOptions,
) -> Iterator[Any]:
    seen: set[str] = set()
    album_names = options.normalized_albums()
    if album_names:
        album_container = getattr(library, "albums", getattr(service, "albums", None))
        if album_container is None or not hasattr(album_container, "find"):
            raise PhotosServiceException(
                f"Photo library '{options.library}' does not support album-based sync."
            )
        for album_name in album_names:
            album = album_container.find(album_name)
            if album is None:
                if getattr(library, "scope", None) == "shared-library":
                    raise PhotosServiceException(
                        unsupported_shared_library_album_message(
                            options.library,
                            album_name,
                        )
                    )
                raise PhotosServiceException(
                    f"No album named '{album_name}' was found."
                )
            for asset in getattr(album, "photos"):
                if asset.id in seen:
                    continue
                seen.add(asset.id)
                yield asset
        return

    if getattr(library, "recently_added", None) is not None and (
        options.recent is not None or options.until_found is not None
    ):
        source = library.recently_added()
    elif getattr(library, "all", None) is not None:
        source = library.all
    else:
        raise PhotosServiceException(
            f"Photo library '{options.library}' does not expose a default asset feed."
        )
    for asset in getattr(source, "photos"):
        if asset.id in seen:
            continue
        seen.add(asset.id)
        yield asset


def _select_resources(
    asset: Any, options: PhotoSyncOptions
) -> list[tuple[str, PhotoResource]]:
    resources = apply_align_raw_policy(
        getattr(asset, "resources", {}), options.align_raw
    )
    if asset.item_type == "movie":
        if options.skip_videos:
            return []
        primary = _resolve_resource(
            resources, [options.size, "original", "medium", "thumb"]
        )
        return [] if primary is None else [primary]

    if getattr(asset, "is_live_photo", False) and options.skip_live_photos:
        return []

    selected: list[tuple[str, PhotoResource]] = []
    primary = _resolve_resource(
        resources, [options.size, "original", "medium", "thumb"]
    )
    if primary is not None:
        selected.append(primary)

    if getattr(asset, "is_live_photo", False) and not options.skip_videos:
        live_key = {
            "original": "original_video",
            "medium": "medium_video",
            "thumb": "thumb_video",
        }[options.live_photo_size]
        live = _resolve_resource(
            resources, [live_key, "original_video", "medium_video", "thumb_video"]
        )
        if live is not None and live[0] not in {entry[0] for entry in selected}:
            selected.append(live)
    return selected


def _resolve_resource(
    resources: dict[str, PhotoResource],
    candidates: Iterable[str],
) -> tuple[str, PhotoResource] | None:
    for candidate in candidates:
        resource = resources.get(candidate)
        if resource is not None and resource.url:
            return getattr(resource, "key", None) or candidate, resource
    return None


def _render_relative_path(
    asset: Any, resource: PhotoResource, folder_structure: str
) -> str:
    filename = _safe_resource_filename(resource)
    if folder_structure == "none":
        return filename
    asset_date = _asset_datetime(asset, "asset_date") or datetime.fromtimestamp(
        0, timezone.utc
    )
    try:
        if "{" in folder_structure:
            folder = folder_structure.format(asset_date)
        else:
            folder = asset_date.strftime(folder_structure)
    except Exception as exc:  # pragma: no cover - defensive formatting guard
        raise PhotosServiceException(
            f"Invalid folder structure format '{folder_structure}'."
        ) from exc
    folder = _safe_relative_folder(folder)
    if not folder:
        return filename
    relative = PurePosixPath(folder) / filename
    return relative.as_posix()


def _unique_relative_path(
    *,
    candidate: str,
    asset_id: str,
    resource_key: str,
    reserved_paths: set[str],
    tracked_paths: dict[str, tuple[str, str]],
) -> str:
    owner = tracked_paths.get(candidate)
    if candidate not in reserved_paths and (
        owner is None or owner == (asset_id, resource_key)
    ):
        return candidate
    path = PurePosixPath(candidate)
    stem = path.stem
    suffix = path.suffix
    directory = path.parent
    discriminator = asset_id[:8]
    index = 1
    while True:
        suffix_bits = f"_{discriminator}" if index == 1 else f"_{discriminator}_{index}"
        next_path = (directory / f"{stem}{suffix_bits}{suffix}").as_posix()
        owner = tracked_paths.get(next_path)
        if next_path not in reserved_paths and (
            owner is None or owner == (asset_id, resource_key)
        ):
            return next_path
        index += 1


def _safe_resource_filename(resource: PhotoResource) -> str:
    filename = str(getattr(resource, "filename", "") or "photo")
    filename = PurePosixPath(filename.replace("\\", "/")).name
    return _sanitize_path_component(filename, fallback="photo")


def _safe_relative_folder(folder: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(folder.replace("\\", "/")).parts:
        if part in {"", ".", "..", "/"}:
            continue
        parts.append(_sanitize_path_component(part, fallback="folder"))
    if not parts:
        return ""
    return PurePosixPath(*parts).as_posix()


def _sanitize_path_component(value: str, *, fallback: str) -> str:
    sanitized = UNSAFE_PATH_COMPONENT_CHARS.sub("-", value).strip()
    if sanitized in {"", ".", ".."}:
        return fallback
    return sanitized


def _safe_target_path(directory: Path, relative_path: str) -> Path:
    base = directory.resolve(strict=False)
    target = (base / relative_path).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise PhotosServiceException(
            f"Refusing to access path outside sync directory: {relative_path}"
        ) from exc
    return target


def _is_current_file(
    path: Path,
    manifest: SyncedPhotoResource | None,
    resource: PhotoResource,
    relative_path: str,
) -> bool:
    if manifest is None:
        return False
    if manifest.relative_path != relative_path:
        return False
    if not path.exists():
        return False
    if resource.size is not None and path.stat().st_size != resource.size:
        return False
    checksum = getattr(resource, "checksum", None)
    if checksum and manifest.checksum and checksum != manifest.checksum:
        return False
    return True


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".pyicloud-sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _sanitize_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return sanitized or "target"


def _apply_local_metadata(
    *,
    asset: Any,
    resource: PhotoResource,
    resource_key: str,
    target_path: Path,
    options: PhotoSyncOptions,
) -> None:
    if options.dry_run or options.only_print_filenames:
        return
    taken_at = _asset_datetime(asset, "asset_date")
    if options.set_exif_datetime and taken_at is not None:
        set_exif_datetime_if_missing(target_path, taken_at)
    if options.xmp_sidecar and not resource_key.endswith("_video"):
        write_xmp_sidecar(
            path=target_path,
            asset_record=getattr(asset, "_asset_record", None),
            dry_run=options.dry_run,
        )


def _asset_datetime(asset: Any, *attrs: str) -> datetime | None:
    for attr in attrs:
        value = getattr(asset, attr, None)
        if not isinstance(value, datetime):
            continue
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def _should_delete_remote_asset(
    *,
    asset: Any,
    options: PhotoSyncOptions,
    asset_ready_for_delete: bool,
    asset_confirmed_local: bool,
    now_local: datetime,
) -> bool:
    if options.keep_icloud_recent_days is None:
        return False
    if options.only_print_filenames or options.dry_run:
        return False
    if not asset_ready_for_delete or not asset_confirmed_local:
        return False
    asset_date = _asset_datetime(asset, "asset_date")
    if asset_date is None:
        return False
    age_days = (now_local - asset_date.astimezone(now_local.tzinfo)).days
    return age_days >= options.keep_icloud_recent_days
