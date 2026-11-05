"""Private file-only Drive moves through verified source-etag moveItems."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.drive import DriveNode

from .authorization import MutationPreview, WriteDenied
from .drive_writes import DriveTarget, DriveWrites

DRIVE_MOVE_FILE = "drive.move_file"


class DriveMoves(DriveWrites):
    """Bind both discovered nodes; no caller IDs, folders or shared writes."""

    def __init__(self, writer: DriveWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._moves: dict[str, tuple[DriveTarget, DriveTarget]] = {}

    def _pair(
        self, client: Any, targets: tuple[DriveTarget, DriveTarget]
    ) -> tuple[DriveNode, DriveNode, str, str, str]:
        source, destination = [self._fresh(client, target) for target in targets]
        parent = source.data.get("parentId")
        if (
            source.type != "file"
            or destination.type != "folder"
            or source.data.get("restorePath")
            or destination.data.get("restorePath")
            or not isinstance(parent, str)
            or not parent
            or targets[0].zone != targets[1].zone
            or targets[0].identifier == targets[1].identifier
        ):
            raise WriteDenied("unsupported_target")
        self._collision(source, destination)
        account, source_resource, source_revision = self._bindings(
            client, targets[0], source
        )
        other_account, destination_resource, destination_revision = self._bindings(
            client, targets[1], destination
        )
        if other_account != account or not source_revision or not destination_revision:
            raise WriteDenied("revision_unavailable")
        resource = json.dumps(
            (source_resource, destination_resource, parent), separators=(",", ":")
        )
        revision = json.dumps(
            (source_revision, destination_revision), separators=(",", ":")
        )
        return source, destination, account, resource, revision

    async def prepare_move(
        self,
        handle: str,
        destination_handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Read-only exact source/destination snapshot, never overwrite by name."""
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if payload:
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve("drive-file", handle)
        folder = self.additional.resolve("drive-folder", destination_handle)
        targets = tuple(
            DriveTarget(node.data["drivewsid"], node.data.get("zone"))
            for node in (original, folder)
        )
        pair = (targets[0], targets[1])

        def prepare(client: Any) -> MutationPreview:
            source, destination, account, resource, revision = self._pair(client, pair)
            preview = self.ledger.preview(
                DRIVE_MOVE_FILE,
                f"{source.name} -> {destination.name}",
                {},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._moves) >= 256:
                del self._moves[next(iter(self._moves))]
            self._moves[preview.request_id] = pair
            if publish is not None:
                publish(preview)
            return preview

        return await self.additional.reads.owner.run(prepare)

    def _collision(self, source: DriveNode, destination: DriveNode) -> None:
        children = destination.get_children(force=True)
        if any(
            child.name.casefold() == source.name.casefold()
            and child.data.get("drivewsid") != source.data["drivewsid"]
            for child in children
        ):
            raise WriteDenied("destination_conflict")

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume approval before one move and independently verify membership."""
        targets = self._moves.get(request_id)
        if targets is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            source, destination, account, resource, revision = self._pair(
                client, targets
            )
            if accept is not None:
                accept(request_id)
            self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._moves[request_id]
            if source.data["parentId"] == targets[1].identifier:
                return {"action": DRIVE_MOVE_FILE, "changed": False}
            try:
                client.drive.move_nodes_to_node([source], destination)
                (
                    moved,
                    current_destination,
                    current_account,
                    current_resource,
                    _revision,
                ) = self._pair(client, targets)
                members = [
                    child
                    for child in current_destination.get_children(force=True)
                    if child.data.get("drivewsid") == targets[0].identifier
                ]
                if (
                    current_account != account
                    or json.loads(current_resource)[:2] != json.loads(resource)[:2]
                    or moved.data["parentId"] != targets[1].identifier
                    or moved.name != source.name
                    or len(members) != 1
                    or members[0].name != source.name
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": DRIVE_MOVE_FILE, "changed": True}

        return await self.additional.reads.owner.run(apply)
