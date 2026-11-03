"""Verified private Drive rename adapter; shared mutation contracts are not guessed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from pyicloud.services.drive import DriveNode

from .additional import AdditionalReads
from .authorization import ApprovalLedger, MutationPreview, WriteDenied


@dataclass(frozen=True)
class DriveTarget:
    """Immutable selector, not mutable caller metadata or a local path."""

    identifier: str
    zone: str | None


class DriveWrites:
    """Rename only after exact independent approval and fresh etag checks."""

    def __init__(self, additional: AdditionalReads, ledger: ApprovalLedger) -> None:
        self.additional = additional
        self.ledger = ledger
        self._targets: dict[str, DriveTarget] = {}

    def _fresh(self, client: Any, target: DriveTarget) -> DriveNode:
        data = client.drive.get_node_data(target.identifier)
        node = DriveNode(client.drive, data)
        if (
            data.get("drivewsid") != target.identifier
            or data.get("zone") != target.zone
        ):
            raise WriteDenied("target_unavailable")
        if (
            node.owner_record_name is not None
            or data.get("shareID")
            or data.get("share")
            or target.identifier.split("::", 1)[0] not in {"FILE", "FOLDER"}
        ):
            raise WriteDenied("unsupported_shared_write")
        if node.type not in {"file", "folder"} or target.identifier.endswith("::root"):
            raise WriteDenied("unsupported_target")
        return node

    def _bindings(
        self, client: Any, target: DriveTarget, node: DriveNode
    ) -> tuple[str, str, str | None]:
        account = client.params.get("dsid")
        if account is None or isinstance(account, bool):
            raise WriteDenied("account_unavailable")
        return (
            str(account),
            json.dumps((target.identifier, target.zone), separators=(",", ":")),
            node.data.get("etag"),
        )

    async def prepare(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Refresh the discovered target and publish a non-mutating exact preview."""
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        name = payload.get("name")
        if (
            set(payload) != {"name"}
            or not isinstance(name, str)
            or not name.strip()
            or name in {".", ".."}
            or len(name.encode("utf-8")) > 255
            or any(char in name for char in ("/", "\\", "\x00", "\r", "\n"))
        ):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve_any(
            frozenset({"drive-file", "drive-folder"}), handle
        )
        if (
            original.owner_record_name is not None
            or original.data.get("shareID")
            or original.data.get("share")
            or original.data["drivewsid"].split("::", 1)[0] not in {"FILE", "FOLDER"}
        ):
            raise WriteDenied("unsupported_shared_write")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))

        def prepare(client: Any) -> MutationPreview:
            node = self._fresh(client, target)
            account, resource, revision = self._bindings(client, target, node)
            preview = self.ledger.preview(
                "drive.rename",
                node.name,
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
        """Retire approval before dispatch; verify the actual post-write state."""
        target = self._targets.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            node = self._fresh(client, target)
            account, resource, revision = self._bindings(client, target, node)
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
            if node.data.get("name") == name:
                return {"action": "drive.rename", "changed": False}
            try:
                node.rename(name)
                verified = self._fresh(client, target)
                if verified.data.get("name") != name or not verified.data.get("etag"):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "drive.rename", "changed": True}

        return await self.additional.reads.owner.run(apply)
