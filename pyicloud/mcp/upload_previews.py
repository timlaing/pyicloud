"""Exact staged-byte upload previews only; no upload execution method or endpoint."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.drive import DriveNode

from .authorization import MutationPreview, WriteDenied
from .drive_writes import DriveTarget, DriveWrites
from .upload_staging import UploadStaging


def _require_upload_folder(folder: DriveNode) -> None:
    """Only a current folder outside Trash can receive staged bytes."""
    if folder.type != "folder" or folder.data.get("restorePath"):
        raise WriteDenied("unsupported_target")


class UploadPreviews(DriveWrites):
    """Bind immutable byte identity to a fresh private destination without uploading."""

    def __init__(self, writer: DriveWrites, staging: UploadStaging) -> None:
        super().__init__(writer.additional, writer.ledger)
        self.staging = staging
        self.additional.reads.owner.add_cleanup(staging.close)

    async def prepare_upload(
        self,
        destination_handle: str,
        upload_handle: str,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Read-only preview with exact bytes/name/digest and both service routes."""
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        original = self.additional.resolve("drive-folder", destination_handle)
        if not isinstance(original, DriveNode):
            raise WriteDenied("unsupported_target")
        if (
            not isinstance(original.data.get("drivewsid"), str)
            or not original.data["drivewsid"]
        ):
            raise WriteDenied("unsupported_target")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))

        def prepare(client: Any) -> MutationPreview:
            staged = self.staging.peek(upload_handle)
            folder = self._fresh(client, target)
            _require_upload_folder(folder)
            document_root = client.drive.document_root
            if not isinstance(document_root, str) or not document_root:
                raise WriteDenied("route_unavailable")
            children = folder.get_children(force=True)
            folder = self._validated_node(client, target, folder.data)
            _require_upload_folder(folder)
            if any(
                child.name.casefold() == staged.name.casefold() for child in children
            ):
                raise WriteDenied("destination_conflict")
            account, resource, revision = self._bindings(client, target, folder)
            resource = json.dumps(
                (resource, document_root, upload_handle), separators=(",", ":")
            )
            payload = {
                "name": staged.name,
                "size": staged.size,
                "sha256": staged.sha256,
            }
            preview = self.ledger.preview(
                "drive.upload_file",
                folder.name,
                payload,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if preview.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            self._publish_preview(preview, publish)
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """No mutation fallback: execution awaits a verified upload contract."""
        raise WriteDenied("upload_execution_unavailable")
