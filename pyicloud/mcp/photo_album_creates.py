"""Receipt-gated typed-only private album creation with independent new-ID proof."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.photos.service import (
    PhotoAlbum,
    PhotoLibrary,
    SmartPhotoAlbum,
    private_photo_zone,
)

from .authorization import MutationPreview, WriteDenied
from .photo_album_writes import PhotoAlbumWrites


class PhotoAlbumCreates(PhotoAlbumWrites):
    """Fresh private library snapshot and independently observed new identity."""

    def __init__(self, writer: PhotoAlbumWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._libraries: dict[str, str] = {}

    def _library(
        self, client: Any, zone: str
    ) -> tuple[PhotoLibrary, str, str, str, list[PhotoAlbum]]:
        account, endpoint = client.params.get("dsid"), client.photos.service_endpoint
        if (
            account is None
            or isinstance(account, bool)
            or not isinstance(endpoint, str)
            or not endpoint
        ):
            raise WriteDenied("route_unavailable")
        libraries = [
            library
            for library in client.photos.libraries.values()
            if isinstance(library, PhotoLibrary)
            and library.scope == "private"
            and json.dumps(
                private_photo_zone(client.photos, library.zone_id),
                sort_keys=True,
                separators=(",", ":"),
            )
            == zone
        ]
        if len(libraries) != 1 or not libraries[0].correlated_album_creation_available:
            raise WriteDenied("creation_unavailable")
        library = libraries[0]
        observed = list(library.server_albums())
        if len(observed) > 1000:
            raise WriteDenied("inventory_unavailable")
        albums = self._validated_albums(observed, zone)
        observed_primary = getattr(client.photos, "private_primary_zone", None)
        resource = json.dumps(
            (
                endpoint,
                zone,
                observed_primary if isinstance(observed_primary, dict) else None,
            ),
            separators=(",", ":"),
        )
        revision = json.dumps(
            sorted((album.id, album.name, album.record_change_tag) for album in albums),
            separators=(",", ":"),
        )
        return library, str(account), resource, revision, albums

    def _validated_albums(self, observed: list[Any], zone: str) -> list[PhotoAlbum]:
        """Preserve inventory identity/revision checks before creating a snapshot."""
        albums: list[PhotoAlbum] = []
        for album in observed:
            if isinstance(album, SmartPhotoAlbum):
                continue
            if not isinstance(album, PhotoAlbum):
                raise WriteDenied("unsupported_target")
            identifier, actual_zone = self._selector(album)
            tag = album.record_change_tag
            if (
                actual_zone != zone
                or not isinstance(tag, str)
                or not tag.strip()
                or tag != tag.strip()
                or tag.casefold() == "null"
                or not identifier
            ):
                raise WriteDenied("revision_unavailable")
            albums.append(album)
        if len({album.id for album in albums}) != len(albums):
            raise WriteDenied("inventory_unavailable")
        return albums

    async def prepare_create(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """No caller IDs or REST fallback; bind the exact new name to library state."""
        if "photos" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        name = payload.get("name")
        if (
            set(payload) != {"name"}
            or not isinstance(name, str)
            or not name.strip()
            or len(name.encode("utf-8")) > 4096
            or any(ord(char) < 32 or char in {"/", "\\", "\x7f"} for char in name)
        ):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("photo-library", handle)
        if (
            not isinstance(original, PhotoLibrary)
            or original.scope != "private"
            or private_photo_zone(original.service, original.zone_id) is None
        ):
            raise WriteDenied("unsupported_target")
        zone = json.dumps(
            private_photo_zone(original.service, original.zone_id),
            sort_keys=True,
            separators=(",", ":"),
        )

        def prepare(client: Any) -> MutationPreview:
            _library, account, resource, revision, albums = self._library(client, zone)
            if any(album.name.casefold() == name.casefold() for album in albums):
                raise WriteDenied("destination_conflict")
            preview = self.ledger.preview(
                "photos.create_album",
                "private Photos library",
                {"name": name},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._libraries) >= 256:
                del self._libraries[next(iter(self._libraries))]
            self._libraries[preview.request_id] = zone
            if publish is not None:
                publish(preview)
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume before one create and never retry absent server visibility."""
        zone = self._libraries.get(request_id)
        if zone is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            library, account, resource, revision, before = self._library(client, zone)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._libraries[request_id]
            name = approved.payload["name"]
            try:
                created = library.create_album_correlated(name)
                _library, current_account, current_resource, _revision, after = (
                    self._library(client, zone)
                )
                if not isinstance(created, PhotoAlbum) or (
                    current_account,
                    current_resource,
                ) != (account, resource):
                    raise WriteDenied("mutation_unverified")
                matches = [album for album in after if album.id == created.id]
                named = [
                    album for album in after if album.name.casefold() == name.casefold()
                ]
                if (
                    created.id in {album.id for album in before}
                    or created.name != name
                    or self._selector(created)[1] != zone
                    or len(matches) != 1
                    or len(named) != 1
                    or matches[0].id != named[0].id
                    or matches[0].name != name
                    or matches[0] is created
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "photos.create_album", "id": created.id}

        return await self.additional.reads.owner.run(apply)
