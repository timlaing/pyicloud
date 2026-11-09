"""Elevated permanent trash-file deletion with exact receipts and absence proof."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.drive import DriveNode

from .authorization import MutationPreview, WriteDenied
from .drive_writes import DriveTarget, DriveWrites
from .one_shot import mutation_session


class DriveDeletions(DriveWrites):
    """Exact private trash identity, elevated receipt and independent absence proof."""

    def __init__(self, writer: DriveWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._deletions: dict[str, tuple[str, DriveTarget]] = {}

    def _trash_file(
        self, client: Any, target: DriveTarget
    ) -> tuple[DriveNode, str, str, str]:
        node = self._fresh(client, target)
        path = node.data.get("restorePath")
        if (
            node.type != "file"
            or node.data.get("parentId") != "TRASH_ROOT"
            or not isinstance(path, str)
            or not path
        ):
            raise WriteDenied("unsupported_target")
        members = [
            child
            for child in client.drive.trash.get_children(force=True)
            if child.data.get("drivewsid") == target.identifier
        ]
        if len(members) != 1 or any(
            members[0].data.get(field) != node.data.get(field)
            for field in (
                "drivewsid",
                "etag",
                "restorePath",
                "name",
                "zone",
                "parentId",
                "type",
            )
        ):
            raise WriteDenied("target_unavailable")
        account, resource, revision = self._bindings(client, target, node)
        self._validated_node(client, target, members[0].data)
        resource = json.dumps(
            (resource, node.name, path, "TRASH_ROOT"), separators=(",", ":")
        )
        return node, account, resource, revision

    async def prepare_delete(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Use a discovered trash-file handle, never IDs or recursive targets."""
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if payload:
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("drive-trash-file", handle)
        if not isinstance(original, DriveNode):
            raise WriteDenied("unsupported_target")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))

        def prepare(client: Any) -> MutationPreview:
            node, account, resource, revision = self._trash_file(client, target)
            self._require_mutation_path(client, "/deleteItems")
            preview = self.ledger.preview(
                "drive.delete_forever_file",
                node.name,
                {},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if preview.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            if len(self._deletions) >= 256:
                del self._deletions[next(iter(self._deletions))]
            self._deletions[preview.request_id] = (handle, target)
            self._publish_preview(preview, publish, (self._deletions,))
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume/retire before deletion; no compensation or blind mutation retry."""
        selected = self._deletions.get(request_id)
        if selected is None:
            raise WriteDenied("unknown_or_consumed_preview")
        handle, target = selected

        def apply(client: Any) -> dict[str, Any]:
            if self._deletions.get(request_id) != selected:
                raise WriteDenied("unknown_or_consumed_preview")
            self.additional.resolve("drive-trash-file", handle)
            node, account, resource, revision = self._trash_file(client, target)
            self._require_mutation_path(client, "/deleteItems")
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._deletions[request_id]
            if approved.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            self._retire_source("drive-trash-file", handle)
            try:
                with mutation_session(client.drive):
                    node.delete_forever(allow_redirects=False)
                current_account, current_resource, _revision = self._bindings(
                    client, target, node
                )
                # The current route/account must remain those bound before dispatch.
                prior_base = json.loads(resource)[0]
                if current_account != account or current_resource != prior_base:
                    raise WriteDenied("mutation_unverified")
                if any(
                    child.data.get("drivewsid") == target.identifier
                    for child in client.drive.trash.get_children(force=True)
                ):
                    raise WriteDenied("mutation_unverified")
                self._absent(client, target)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "drive.delete_forever_file", "deleted": True}

        return await self.additional.reads.owner.run(apply)

    def _absent(self, client: Any, target: DriveTarget) -> None:
        try:
            client.drive.get_node_data(target.identifier)
        except PyiCloudAPIResponseException as error:
            if str(error.code) in {"404", "410"}:
                return
            raise
        raise WriteDenied("mutation_unverified")
