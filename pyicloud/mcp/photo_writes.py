"""Private-library favorite changes through the verified Photos facade."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.photos.mappers import (
    record_change_tag,
    record_field_value,
    record_zone,
)
from pyicloud.services.photos.service import PhotoAsset, private_photo_zone

from .additional import AdditionalReads
from .authorization import ApprovalLedger, MutationPreview, WriteDenied


class PhotoWrites:
    """Fresh private-zone revisions and exact independent single-use approval."""

    def __init__(self, additional: AdditionalReads, ledger: ApprovalLedger) -> None:
        self.additional = additional
        self.ledger = ledger
        self._targets: dict[str, tuple[str, str, str]] = {}

    def _selector(self, asset: PhotoAsset) -> tuple[str, str, str]:
        zone = private_photo_zone(asset._service, record_zone(asset.asset_record))  # pylint: disable=protected-access
        if zone is None:
            raise WriteDenied("unsupported_shared_write")
        return (
            asset.id,
            asset.master_id,
            json.dumps(zone, sort_keys=True, separators=(",", ":")),
        )

    def _fresh(
        self, client: Any, target: tuple[str, str, str]
    ) -> tuple[PhotoAsset, str, str, str]:
        account = client.params.get("dsid")
        route = client.photos.service_endpoint
        if (
            account is None
            or isinstance(account, bool)
            or not isinstance(route, str)
            or not route
        ):
            raise WriteDenied("account_unavailable")
        libraries = [
            library
            for library in client.photos.libraries.values()
            if library.scope == "private"
            and json.dumps(
                private_photo_zone(client.photos, library.zone_id),
                sort_keys=True,
                separators=(",", ":"),
            )
            == target[2]
        ]
        if len(libraries) != 1:
            raise WriteDenied("target_unavailable")
        asset = libraries[0].all.get(target[0])
        if not isinstance(asset, PhotoAsset) or self._selector(asset) != target:
            raise WriteDenied("target_unavailable")
        revision = record_change_tag(asset.asset_record)
        if not revision:
            raise WriteDenied("revision_unavailable")
        favorite = record_field_value(asset.asset_record, "isFavorite")
        if (
            not isinstance(favorite, int)
            or isinstance(favorite, bool)
            or favorite not in {0, 1}
        ):
            raise WriteDenied("target_unavailable")
        return (
            asset,
            str(account),
            json.dumps(
                (
                    route,
                    target,
                    record_zone(asset.asset_record),
                    client.photos.private_primary_zone
                    if isinstance(
                        getattr(client.photos, "private_primary_zone", None), dict
                    )
                    else None,
                ),
                separators=(",", ":"),
            ),
            revision,
        )

    async def prepare(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Resolve a discovered asset and capture only a strict boolean change."""
        if "photos" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if set(payload) != {"favorite"} or not isinstance(payload["favorite"], bool):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("photo-asset", handle)
        if not isinstance(original, PhotoAsset):
            raise WriteDenied("unsupported_target")
        target = self._selector(original)
        value = payload["favorite"]

        def prepare(client: Any) -> MutationPreview:
            _asset, account, resource, revision = self._fresh(client, target)
            preview = self.ledger.preview(
                "photos.set_favorite",
                target[0],
                {"favorite": value},
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
        """Consume approval before one mutation and verify an independent read."""
        target = self._targets.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            asset, account, resource, revision = self._fresh(client, target)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._targets[request_id]
            value = approved.payload["favorite"]
            if record_field_value(asset.asset_record, "isFavorite") == int(value):
                return {"action": "photos.set_favorite", "changed": False}
            try:
                asset.set_favorite(value)
                verified, _account, _resource, _revision = self._fresh(client, target)
                if (_account, _resource) != (account, resource) or record_field_value(
                    verified.asset_record, "isFavorite"
                ) != int(value):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "photos.set_favorite", "changed": True}

        return await self.additional.reads.owner.run(apply)
