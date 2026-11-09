"""Private file-only Drive moves through verified source-etag moveItems."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.drive import DriveNode

from .authorization import MutationPreview, WriteDenied
from .context import ToolFailure
from .drive_writes import DriveTarget, DriveWrites
from .one_shot import mutation_session

DRIVE_MOVE_FILE = "drive.move_file"


class DriveMoves(DriveWrites):
    """Bind both discovered nodes; no caller IDs, folders or shared writes."""

    def __init__(self, writer: DriveWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._moves: dict[str, tuple[DriveTarget, DriveTarget]] = {}
        self._destinations: dict[str, str] = {}
        self._source_handles: dict[str, str] = {}

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
        destination = self._checked_destination(client, targets[1], destination)
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
                oldest = next(iter(self._moves))
                del self._moves[oldest]
                self._destinations.pop(oldest, None)
                self._source_handles.pop(oldest, None)
            self._moves[preview.request_id] = pair
            self._destinations[preview.request_id] = destination_handle
            self._source_handles[preview.request_id] = handle
            self._publish_preview(
                preview,
                publish,
                (self._moves, self._destinations, self._source_handles),
            )
            return preview

        return await self.additional.reads.owner.run(prepare)

    def _checked_destination(
        self, client: Any, target: DriveTarget, destination: DriveNode
    ) -> DriveNode:
        """Apply the same complete destination guard after every child refresh."""
        current = self._validated_node(client, target, destination.data)
        if current.type != "folder" or current.data.get("restorePath"):
            raise WriteDenied("unsupported_target")
        return current

    def _collision(self, source: DriveNode, destination: DriveNode) -> None:
        children = destination.get_children(force=True)
        if any(
            child.name.casefold() == source.name.casefold()
            and child.data.get("drivewsid") != source.data["drivewsid"]
            for child in children
        ):
            raise WriteDenied("destination_conflict")

    def _retire_move_handles(
        self,
        source: DriveNode,
        destination: DriveTarget,
        source_handle: str,
        destination_handle: str,
    ) -> None:
        """Validate both handles before retiring either or spending approval."""
        try:
            self.additional.resolve("drive-file", source_handle)
            self.additional.resolve("drive-folder", destination_handle)
            if source.data["parentId"] == destination.identifier:
                return
            self.additional.retire_many((
                ("drive-file", source_handle),
                ("drive-folder", destination_handle),
            ))
        except ToolFailure:
            raise WriteDenied("target_unavailable") from None

    def _verify_member(
        self,
        client: Any,
        target: DriveTarget,
        moved: DriveNode,
        members: list[DriveNode],
    ) -> None:
        """Require one private listed child consistent with the refreshed source."""
        if len(members) != 1:
            raise WriteDenied("mutation_unverified")
        listed = self._validated_node(client, target, members[0].data)
        fields = (
            "drivewsid",
            "zone",
            "etag",
            "parentId",
            "name",
            "extension",
            "type",
            "restorePath",
            "docwsid",
        )
        if any(listed.data.get(field) != moved.data.get(field) for field in fields):
            raise WriteDenied("mutation_unverified")

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume approval before one move and independently verify membership."""
        targets = self._moves.get(request_id)
        destination_handle = self._destinations.get(request_id)
        source_handle = self._source_handles.get(request_id)
        if targets is None or destination_handle is None or source_handle is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            if (
                self._moves.get(request_id) != targets
                or self._destinations.get(request_id) != destination_handle
                or self._source_handles.get(request_id) != source_handle
            ):
                raise WriteDenied("unknown_or_consumed_preview")
            source, destination, account, resource, revision = self._pair(
                client, targets
            )
            self._require_mutation_path(client, "/moveItems")
            if accept is not None:
                accept(request_id)
            self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
                before_consume=lambda: self._retire_move_handles(
                    source, targets[1], source_handle, destination_handle
                ),
            )
            self._moves.pop(request_id, None)
            self._destinations.pop(request_id, None)
            self._source_handles.pop(request_id, None)
            if source.data["parentId"] == targets[1].identifier:
                return {"action": DRIVE_MOVE_FILE, "changed": False}
            try:
                with mutation_session(client.drive) as dispatch_drive:
                    dispatch_drive.move_nodes_to_node(
                        [source], destination, allow_redirects=False
                    )
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
                current_destination = self._checked_destination(
                    client, targets[1], current_destination
                )
                self._bindings(client, targets[1], current_destination)
                self._verify_member(client, targets[0], moved, members)
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
