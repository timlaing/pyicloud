"""Internal private add-to-album adapter; no runtime relation action enabled yet."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.photos.mappers import record_change_tag, record_zone
from pyicloud.services.photos.service import PhotoAlbum, PhotoAsset

from .authorization import MutationPreview, WriteDenied
from .photo_album_writes import PhotoAlbumWrites


class PhotoRelations(PhotoAlbumWrites):
    """Fresh two-target revisions and membership observations, never blind retries."""

    def __init__(self, writer: PhotoAlbumWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._relations: dict[str, tuple[str, str, str, str]] = {}

    def _pair(
        self, client: Any, target: tuple[str, str, str, str]
    ) -> tuple[PhotoAlbum, PhotoAsset, str, str, str, bool]:
        album_id, asset_id, master_id, zone = target
        album, account, album_resource, album_revision = self._fresh(
            client, (album_id, zone)
        )
        libraries = [
            library
            for library in client.photos.libraries.values()
            if library.scope == "private"
            and json.dumps(library.zone_id, sort_keys=True, separators=(",", ":"))
            == zone
        ]
        if len(libraries) != 1:
            raise WriteDenied("target_unavailable")
        asset = libraries[0].all.get(asset_id)
        if (
            not isinstance(asset, PhotoAsset)
            or (asset.id, asset.master_id) != (asset_id, master_id)
            or json.dumps(
                record_zone(asset.asset_record), sort_keys=True, separators=(",", ":")
            )
            != zone
        ):
            raise WriteDenied("target_unavailable")
        tag = record_change_tag(asset.asset_record)
        if (
            not isinstance(tag, str)
            or not tag.strip()
            or tag != tag.strip()
            or tag.casefold() == "null"
        ):
            raise WriteDenied("revision_unavailable")
        if not album.can_add_photo_correlated(asset):
            raise WriteDenied("relation_unavailable")
        present = album.contains_photo_scoped(asset)
        if not isinstance(present, bool):
            raise WriteDenied("membership_unavailable")
        resource = json.dumps(
            (album_resource, asset_id, master_id, zone), separators=(",", ":")
        )
        revision = json.dumps((album_revision, tag, present), separators=(",", ":"))
        return album, asset, account, resource, revision, present

    async def prepare_add(
        self,
        source_handle: str,
        album_handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Only discovered source/album selectors and an exact empty payload."""
        if "photos" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if payload:
            raise WriteDenied("invalid_payload")
        album = self.additional.resolve("photo-album", album_handle)
        album_id, zone = self._selector(album)
        source = self.additional.resolve("photo-asset", source_handle)
        if not isinstance(source, PhotoAsset):
            raise WriteDenied("unsupported_target")
        target = (album_id, source.id, source.master_id, zone)

        def prepare(client: Any) -> MutationPreview:
            current, asset, account, resource, revision, _present = self._pair(
                client, target
            )
            preview = self.ledger.preview(
                "photos.add_to_album",
                f"{asset.id} -> {current.name}",
                {},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._relations) >= 256:
                del self._relations[next(iter(self._relations))]
            self._relations[preview.request_id] = target
            if publish is not None:
                publish(preview)
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume approval before one add, then independently observe membership."""
        target = self._relations.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            album, asset, account, resource, revision, present = self._pair(
                client, target
            )
            if accept is not None:
                accept(request_id)
            self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._relations[request_id]
            if present:
                return {"action": "photos.add_to_album", "changed": False}
            try:
                if album.add_photo_correlated(asset) is not True:
                    raise WriteDenied("mutation_unverified")
                (
                    _album,
                    _asset,
                    current_account,
                    current_resource,
                    _revision,
                    verified,
                ) = self._pair(client, target)
                if not verified or (current_account, current_resource) != (
                    account,
                    resource,
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "photos.add_to_album", "changed": True}

        return await self.additional.reads.owner.run(apply)
