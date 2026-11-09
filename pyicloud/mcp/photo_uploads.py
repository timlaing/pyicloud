"""Internal elevated staged Photos uploads with independent private asset proof."""

# Reuse the existing validated internal library/asset selectors, not raw SDK data.
# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Callable
import json
import time
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pyicloud.services.photos.mappers import record_change_tag
from pyicloud.services.photos.service import (
    PhotoAsset,
    PhotoLibrary,
    private_photo_zone,
)

from .authorization import MutationPreview, WriteDenied
from .context import BoundedAdapter, SessionOwner
from .photo_album_creates import PhotoAlbumCreates
from .photo_upload_bytes import PhotoUploadBytes, validate_photo_upload_session
from .photo_writes import PhotoWrites
from .upload_staging import StagedUpload, UploadStaging
from .upload_transport import UploadNotDispatched, UploadTransport, checked_upload_url

T = TypeVar("T")


class PhotoUploads:
    """No runtime surface until separately selected operator transfer-host policy."""

    def __init__(
        self,
        writer: PhotoAlbumCreates,
        staging: UploadStaging,
        hosts: frozenset[str],
        pipeline: PhotoUploadBytes | None = None,
    ) -> None:
        self.writer = writer
        self.additional = writer.additional
        self.ledger = writer.ledger
        self.staging = staging
        self.hosts = hosts
        self.pipeline = pipeline or PhotoUploadBytes(
            UploadTransport(hosts), self.additional.reads.owner.config.timeout
        )
        self._requests: dict[str, tuple[str, str, int]] = {}
        self._accepted_receipts: dict[str, bool] = {}
        self._injected_adapters: dict[BoundedAdapter, float | None] = {}
        self.additional.reads.owner.add_cleanup(staging.close)

    async def _run(self, operation: Callable[[Any], T]) -> T:
        """Restore injected Photos adapter deadlines after the complete operation."""

        def invoke(client: Any) -> T:
            try:
                return operation(client)
            finally:
                for adapter, previous in self._injected_adapters.items():
                    adapter.deadline = previous
                self._injected_adapters.clear()

        return await self.additional.reads.owner.run(invoke)

    def _read(
        self, client: Any, zone: str, token: str
    ) -> tuple[PhotoLibrary, str, str, str]:
        if "photos" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if not self.hosts:
            raise WriteDenied("upload_host_policy_required")
        library, account, resource, revision, _albums = self.writer._library(
            client, zone
        )  # pylint: disable=protected-access
        if private_photo_zone(client.photos, library.zone_id) != {
            "zoneName": "PrimarySync"
        }:
            raise WriteDenied("unsupported_shared_write")
        root = client.photos.upload_root
        if not isinstance(root, str) or not root:
            raise WriteDenied("route_unavailable")
        parsed = urlsplit(root)
        checked_upload_url(
            root, frozenset({parsed.hostname}) if parsed.hostname else frozenset()
        )
        if parsed.query or parsed.fragment:
            raise WriteDenied("unsupported_upload_route")
        if not account.isascii() or not account.isdecimal():
            raise WriteDenied("account_unavailable")
        if client.photos.params.get("dsid") != account:
            raise WriteDenied("account_unavailable")
        # Host policy is independent of the server-provided transfer URL.
        for host in self.hosts:
            checked_upload_url("https://" + host + "/", self.hosts)
        stage = self.staging.peek(token)
        observed_primary = getattr(client.photos, "private_primary_zone", None)
        primary = observed_primary if isinstance(observed_primary, dict) else None
        resource = json.dumps(
            (resource, root, token, sorted(self.hosts), primary), separators=(",", ":")
        )
        revision = json.dumps(
            (revision, stage.name, stage.size, stage.sha256), separators=(",", ":")
        )
        return library, account, resource, revision

    async def prepare_upload(
        self,
        handle: str,
        token: str,
        modified_ms: int,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Bind staged bytes, timestamp, library revision and both routes exactly."""
        if (
            isinstance(modified_ms, bool)
            or not isinstance(modified_ms, int)
            or modified_ms < 0
        ):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("photo-library", handle)
        if not isinstance(original, PhotoLibrary) or original.scope != "private":
            raise WriteDenied("unsupported_target")
        zone = json.dumps(
            private_photo_zone(original.service, original.zone_id),
            sort_keys=True,
            separators=(",", ":"),
        )

        def prepare(client: Any) -> MutationPreview:
            _library, account, resource, revision = self._read(client, zone, token)
            stage = self.staging.peek(token)
            preview = self.ledger.preview(
                "photos.upload_file",
                "private Photos library",
                {
                    "name": stage.name,
                    "size": stage.size,
                    "sha256": stage.sha256,
                    "modified_ms": modified_ms,
                    "timezone": "UTC",
                },
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if preview.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            if len(self._requests) >= 256:
                oldest = next(iter(self._requests))
                del self._requests[oldest]
                self._accepted_receipts.pop(oldest, None)
                self.ledger.discard(oldest)
            self._requests[preview.request_id] = (zone, token, modified_ms)
            if publish is not None:
                publish(preview)
            return preview

        return await self._run(prepare)

    def _verify_after_binding(
        self, client: Any, zone: str, account: str, resource: str
    ) -> None:
        """Recheck account, CloudKit route and private zone without stage reuse."""
        _library, after_account, after_resource, _rev, _albums = self.writer._library(
            client, zone
        )
        if (
            after_account != account
            or after_resource != json.loads(resource)[0]
            or (
                client.photos.private_primary_zone
                if isinstance(
                    getattr(client.photos, "private_primary_zone", None), dict
                )
                else None
            )
            != json.loads(resource)[4]
        ):
            raise WriteDenied("mutation_unverified")

    def _validate_session_policy(self, client: Any) -> float | None:
        """Check and bind both registration transports before resource consumption."""
        adapters = validate_photo_upload_session(
            client.photos.session, client.photos.upload_root, dict(client.photos.params)
        )
        owner = self.additional.reads.owner
        for adapter in adapters:
            if isinstance(owner, SessionOwner):
                owner.bind_adapter(adapter)
            else:
                if adapter not in self._injected_adapters:
                    self._injected_adapters[adapter] = adapter.deadline
                    adapter.requests_left = 32
                deadline = time.monotonic() + owner.config.timeout
                adapter.deadline = (
                    min(adapter.deadline, deadline)
                    if adapter.deadline is not None
                    else deadline
                )
        deadlines = [
            adapter.deadline for adapter in adapters if adapter.deadline is not None
        ]
        return min(deadlines) if deadlines else None

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Retire sealed bytes before one upload; missing indexing is uncertain."""
        target = self._requests.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")
        zone, token, modified_ms = target

        def apply(client: Any) -> dict[str, Any]:
            library, account, resource, revision = self._read(client, zone, token)
            owner_deadline = self._validate_session_policy(client)
            root = client.photos.upload_root
            accepted = self._accepted_receipts.get(request_id, False)
            if accept is not None and not accepted:
                accept(request_id)
                accepted = True
            self._verify_after_binding(client, zone, account, resource)
            if client.photos.upload_root != root:
                raise WriteDenied("stale_preview")
            captured: list[StagedUpload] = []
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
                before_consume=lambda: captured.append(self.staging.take(token)),
            )
            del self._requests[request_id]
            self._accepted_receipts.pop(request_id, None)
            if approved.risk != "high":
                self.staging._stages[token] = captured[0]
                raise WriteDenied("high_risk_operator_approval_required")
            stage = captured[0]
            try:
                result = self.pipeline.upload(
                    client.photos.session,
                    root,
                    dict(client.photos.params),
                    "PrimarySync",
                    stage,
                    modified_ms=modified_ms,
                    stage_acquired=True,
                    owner_deadline=owner_deadline,
                )
                proof = PhotoWrites(self.additional, self.ledger)
                if not result.cplAsset or not result.cplMaster:
                    raise WriteDenied("mutation_unverified")
                asset = library.all.get(result.cplAsset)
                if (
                    not isinstance(asset, PhotoAsset)
                    or proof._selector(asset)
                    != (result.cplAsset, result.cplMaster, zone)  # pylint: disable=protected-access
                    or not record_change_tag(asset.asset_record)
                    or client.photos.upload_root != root
                ):
                    raise WriteDenied("mutation_unverified")
                # Recheck the full library/account/CloudKit route without reusing
                # a registration cache or pretending the consumed stage still exists.
                self._verify_after_binding(client, zone, account, resource)
                return {
                    "action": "photos.upload_file",
                    "id": asset.id,
                    "duplicate": result.is_duplicate,
                    "verified": True,
                }
            except UploadNotDispatched:
                self.staging._stages[token] = stage
                self.ledger.restore_before_dispatch(
                    approved,
                    account_binding=account,
                    resource_binding=resource,
                    revision=revision,
                )
                self._requests[request_id] = target
                if accepted:
                    self._accepted_receipts[request_id] = True
                raise
            except Exception:
                raise WriteDenied("mutation_uncertain") from None

        return await self._run(apply)
