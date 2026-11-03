"""Receipt-gated private top-level Photos album rename with independent proof."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.photos.constants import ObjectTypeEnum
from pyicloud.services.photos.service import PhotoAlbum

from .additional import AdditionalReads
from .authorization import ApprovalLedger, MutationPreview, WriteDenied


class PhotoAlbumWrites:
    """Fresh private album snapshots with exact independent single-use approval."""

    def __init__(self, additional: AdditionalReads, ledger: ApprovalLedger) -> None:
        self.additional = additional
        self.ledger = ledger
        self._targets: dict[str, tuple[str, str]] = {}

    def _selector(self, album: Any) -> tuple[str, str]:
        if (
            not isinstance(album, PhotoAlbum)
            or album.object_type != ObjectTypeEnum.CONTAINER
            or album.parent_id is not None
        ):
            raise WriteDenied("unsupported_target")
        zone = album.zone_id
        if not zone.get("zoneName") or zone.get("ownerRecordName"):
            raise WriteDenied("unsupported_shared_write")
        return album.id, json.dumps(zone, sort_keys=True, separators=(",", ":"))

    def _fresh(
        self, client: Any, target: tuple[str, str]
    ) -> tuple[PhotoAlbum, str, str, str]:
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
            if library.scope == "private"
            and json.dumps(library.zone_id, sort_keys=True, separators=(",", ":"))
            == target[1]
        ]
        if len(libraries) != 1:
            raise WriteDenied("target_unavailable")
        albums = [
            album for album in libraries[0].server_albums() if album.id == target[0]
        ]
        if len(albums) != 1 or self._selector(albums[0]) != target:
            raise WriteDenied("target_unavailable")
        album = albums[0]
        if not isinstance(album.record_change_tag, str) or not album.record_change_tag:
            raise WriteDenied("revision_unavailable")
        resource = json.dumps(
            (endpoint, target, album.parent_id), separators=(",", ":")
        )
        revision = json.dumps(
            (album.record_change_tag, album.name), separators=(",", ":")
        )
        return album, str(account), resource, revision

    async def prepare(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Only discovery and fresh reads; never infer a missing CloudKit tag."""
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
        target = self._selector(self.additional.resolve("photo-album", handle))

        def prepare(client: Any) -> MutationPreview:
            album, account, resource, revision = self._fresh(client, target)
            preview = self.ledger.preview(
                "photos.rename_album",
                album.name,
                {"name": name},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._targets) >= 256:
                del self._targets[next(iter(self._targets))]
            self._targets[preview.request_id] = target
            if publish is not None:
                publish(preview)
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume before one verified facade call, then independently refresh."""
        target = self._targets.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            album, account, resource, revision = self._fresh(client, target)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._targets[request_id]
            name = approved.payload["name"]
            if album.name == name:
                return {"action": "photos.rename_album", "changed": False}
            try:
                album.rename(name)
                verified, current_account, current_resource, _revision = self._fresh(
                    client, target
                )
                if (
                    verified is album
                    or (current_account, current_resource)
                    != (
                        account,
                        resource,
                    )
                    or verified.name != name
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "photos.rename_album", "changed": True}

        return await self.additional.reads.owner.run(apply)
