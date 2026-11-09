"""Elevated existing Drive uploads, explicitly acknowledged not final-state verified."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from urllib.parse import urlsplit

from pyicloud.services.drive import CLOUD_DOCS_ZONE, DriveNode

from .authorization import MutationPreview, WriteDenied
from .context import ToolFailure
from .drive_upload_bytes import (
    DriveUploadBytes,
    snapshot_upload_credentials,
    validate_upload_session,
)
from .drive_writes import DriveTarget, DriveWrites
from .upload_staging import StagedUpload, UploadStaging
from .upload_transport import UploadNotDispatched, UploadTransport, checked_upload_url


def _verify_upload_context(client: Any, account: str, route: str, root: str) -> None:
    """An acknowledgement must retain both account identities and bound routes."""
    if (
        str(client.params.get("dsid")) != account
        or str(client.drive.params.get("dsid")) != account
        or client.drive.service_root != route
        or client.drive.document_root != root
    ):
        raise WriteDenied("mutation_unverified")


class DriveUploads(DriveWrites):
    """Exact private destination/byte/host binding and pre-dispatch retirement."""

    def __init__(
        self,
        writer: DriveWrites,
        staging: UploadStaging,
        hosts: frozenset[str],
        pipeline: DriveUploadBytes | None = None,
    ) -> None:
        super().__init__(writer.additional, writer.ledger)
        self.staging = staging
        self.additional.reads.owner.add_cleanup(staging.close)
        self.hosts = hosts
        self.pipeline = pipeline or DriveUploadBytes(
            UploadTransport(hosts), self.additional.reads.owner.config.timeout
        )
        self._requests: dict[str, tuple[str, DriveTarget, str]] = {}
        self._accepted_receipts: dict[str, bool] = {}

    def _retire_destination(self, handle: str) -> None:
        """A failed pre-dispatch retirement leaves staged bytes and approval intact."""
        try:
            self.additional.retire("drive-folder", handle)
        except ToolFailure:
            raise WriteDenied("target_unavailable") from None

    def _acquire_stage(
        self, token: str, handle: str, captured: list[StagedUpload]
    ) -> None:
        """Validate expiry before retiring the destination or spending approval."""
        captured.append(
            self.staging.take(
                token, before_take=lambda: self._retire_destination(handle)
            )
        )

    def _restore_pre_dispatch_stage(
        self, token: str, captured: list[StagedUpload]
    ) -> None:
        """Retain bytes only for a known policy rejection before any dispatch."""
        for stage in captured:
            self.staging._stages[token] = stage  # pylint: disable=protected-access

    def _read(
        self, client: Any, target: DriveTarget, token: str
    ) -> tuple[DriveNode, str, str, str]:
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if not self.hosts:
            raise WriteDenied("upload_host_policy_required")
        stage = self.staging.peek(token)
        if stage.size > 4 * 1024 * 1024:
            raise WriteDenied("invalid_upload_bytes")
        folder = self._fresh(client, target)
        children = folder.get_children(force=True) if folder.type == "folder" else []
        folder = self._validated_node(client, target, folder.data)
        if (
            folder.type != "folder"
            or folder.data.get("restorePath")
            or target.zone != CLOUD_DOCS_ZONE
            or not isinstance(folder.data.get("docwsid"), str)
            or not folder.data["docwsid"]
        ):
            raise WriteDenied("unsupported_target")
        if any(child.name.casefold() == stage.name.casefold() for child in children):
            raise WriteDenied("destination_conflict")
        account, resource, revision = self._bindings(client, target, folder)
        observed = urlsplit(client.drive.service_root)
        checked_upload_url(
            client.drive.service_root,
            frozenset({observed.hostname}) if observed.hostname else frozenset(),
        )
        if observed.query:
            raise WriteDenied("unsupported_upload_route")
        root = client.drive.document_root
        if not isinstance(root, str) or not root:
            raise WriteDenied("route_unavailable")
        try:
            parsed = urlsplit(root)
        except ValueError:
            raise WriteDenied("unsupported_upload_route") from None
        checked_upload_url(
            root, frozenset({parsed.hostname}) if parsed.hostname else frozenset()
        )
        if (
            parsed.query
            or "?" in root
            or str(client.drive.params.get("dsid")) != account
        ):
            raise WriteDenied("account_unavailable")
        for host in self.hosts:
            checked_upload_url("https://" + host + "/", self.hosts)
        resource = json.dumps(
            (resource, root, folder.data["docwsid"], token, sorted(self.hosts)),
            separators=(",", ":"),
        )
        revision = json.dumps(
            (revision, stage.name, stage.size, stage.sha256), separators=(",", ":")
        )
        return folder, account, resource, revision

    async def prepare_upload(
        self,
        handle: str,
        token: str,
        modified_ms: int,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Preview conflict policy and exact immutable metadata, not final ID."""
        if (
            isinstance(modified_ms, bool)
            or not isinstance(modified_ms, int)
            or modified_ms < 0
        ):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("drive-folder", handle)
        if not isinstance(original, DriveNode):
            raise WriteDenied("unsupported_target")
        if (
            not isinstance(original.data.get("drivewsid"), str)
            or not original.data["drivewsid"]
        ):
            raise WriteDenied("unsupported_target")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))

        def prepare(client: Any) -> MutationPreview:
            folder, account, resource, revision = self._read(client, target, token)
            stage = self.staging.peek(token)
            preview = self.ledger.preview(
                "drive.upload_file",
                folder.name,
                {
                    "name": stage.name,
                    "size": stage.size,
                    "sha256": stage.sha256,
                    "modified_ms": modified_ms,
                    "created_ms": modified_ms,
                    "allow_conflict": True,
                    "create_short_guid": True,
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
                self.ledger.discard(oldest)
                self._accepted_receipts.pop(oldest, None)
            self._requests[preview.request_id] = (handle, target, token)
            self._publish_preview(
                preview, publish, (self._requests, self._accepted_receipts)
            )
            return preview

        return await self.additional.reads.owner.run(prepare)

    def _accept_receipt(
        self, request_id: str, accept: Callable[[str], None] | None
    ) -> bool:
        """Accept once, retaining the decision only for a proven pre-dispatch retry."""
        accepted = self._accepted_receipts.get(request_id, False)
        if accept is not None and not accepted:
            accept(request_id)
            return True
        return accepted

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume stage/receipt before one flow; never mint a guessed handle."""
        selected = self._requests.get(request_id)
        if selected is None:
            raise WriteDenied("unknown_or_consumed_preview")
        handle, target, token = selected

        def apply(client: Any) -> dict[str, Any]:
            if self._requests.get(request_id) != selected:
                raise WriteDenied("unknown_or_consumed_preview")
            self.additional.resolve("drive-folder", handle)
            folder, account, resource, revision = self._read(client, target, token)
            credentials = snapshot_upload_credentials(client.drive)
            owner_deadline = validate_upload_session(
                client.drive, credentials, bind_adapter=self._bind_transport_adapter
            )
            route, root = client.drive.service_root, client.drive.document_root
            receipt_accepted = self._accept_receipt(request_id, accept)
            _verify_upload_context(client, account, route, root)
            captured: list[StagedUpload] = []
            handle_snapshot = self.additional.snapshot_drive_handles(
                "drive-folder", handle
            )
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
                before_consume=lambda: self._acquire_stage(token, handle, captured),
            )
            del self._requests[request_id]
            self._accepted_receipts.pop(request_id, None)
            if approved.risk != "high":
                # No dispatch occurred; retain bytes if a trusted policy is corrupted.
                self._restore_pre_dispatch_stage(token, captured)
                raise WriteDenied("high_risk_operator_approval_required")
            if len(captured) != 1:
                raise WriteDenied("invalid_upload_bytes")
            stage = captured[0]
            try:
                self.pipeline.upload(
                    client.drive,
                    folder.data["docwsid"],
                    stage,
                    modified_ms=approved.payload["modified_ms"],
                    credential_snapshot=credentials,
                    approved_hosts=self.hosts,
                    stage_acquired=True,
                    owner_deadline=owner_deadline,
                )
                _verify_upload_context(client, account, route, root)
            except UploadNotDispatched:
                self._restore_pre_dispatch_stage(token, captured)
                self.additional.restore_before_dispatch_handles(handle_snapshot)
                self.ledger.restore_before_dispatch(
                    approved,
                    account_binding=account,
                    resource_binding=resource,
                    revision=revision,
                )
                self._requests[request_id] = selected
                if receipt_accepted:
                    self._accepted_receipts[request_id] = True
                raise
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": "drive.upload_file",
                "acknowledged": True,
                "final_state_verified": False,
            }

        return await self.additional.reads.owner.run(apply)
