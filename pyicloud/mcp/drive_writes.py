"""Verified private Drive rename adapter; shared mutation contracts are not guessed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import time
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pyicloud.services.drive import DriveNode

from .additional import AdditionalReads
from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .context import BoundedAdapter, SessionOwner, ToolFailure
from .one_shot import (
    checked_adapter,
    mutation_session,
    prepared_url,
    transport_settings,
)


@dataclass(frozen=True)
class DriveTarget:
    """Immutable selector, not mutable caller metadata or a local path."""

    identifier: str
    zone: str | None


DRIVE_RENAME = "drive.rename"
DRIVE_TRASH_FILE = "drive.trash_file"
T = TypeVar("T")


class DriveWrites:
    """Rename only after exact independent approval and fresh etag checks."""

    def __init__(self, additional: AdditionalReads, ledger: ApprovalLedger) -> None:
        self.additional = additional
        self.ledger = ledger
        self._targets: dict[str, DriveTarget] = {}
        self._actions: dict[str, str] = {}
        self._sources: dict[str, tuple[str, str]] = {}
        self._injected_adapters: dict[BoundedAdapter, float | None] = {}

    async def _run(self, operation: Callable[[Any], T]) -> T:
        """Restore injected-owner transport budgets after a serialized operation."""
        owner = self.additional.reads.owner

        def invoke(client: Any) -> T:
            try:
                return operation(client)
            finally:
                for adapter, previous in self._injected_adapters.items():
                    adapter.deadline = previous
                self._injected_adapters.clear()

        return await owner.run(invoke)

    def _fresh(self, client: Any, target: DriveTarget) -> DriveNode:
        self._require_metadata_transport(client)
        data = client.drive.get_node_data(target.identifier)
        return self._validated_node(client, target, data)

    def _validated_node(
        self, client: Any, target: DriveTarget, data: dict[str, Any]
    ) -> DriveNode:
        """Recheck identity/scope even when a child refresh replaced node metadata."""
        node = DriveNode(client.drive, data)
        if target.identifier.split("::", 1)[0].lower() != node.type:
            raise WriteDenied("unsupported_target")
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
    ) -> tuple[str, str, str]:
        account = client.params.get("dsid")
        if account is None or isinstance(account, bool):
            raise WriteDenied("account_unavailable")
        drive_account = client.drive.params.get("dsid")
        if (
            drive_account is None
            or isinstance(drive_account, bool)
            or str(drive_account) != str(account)
        ):
            raise WriteDenied("account_unavailable")
        route = client.drive.service_root
        if not isinstance(route, str) or not route:
            raise WriteDenied("route_unavailable")
        self._require_retry_disabled(client, route)
        revision = node.data.get("etag")
        if (
            not isinstance(revision, str)
            or not revision.strip()
            or revision != revision.strip()
            or revision.casefold() == "null"
        ):
            raise WriteDenied("revision_required")
        return (
            str(account),
            json.dumps((route, target.identifier, target.zone), separators=(",", ":")),
            revision,
        )

    def _require_retry_disabled(self, client: Any, route: str) -> BoundedAdapter:
        """Refuse custom transports that could dispatch one mutation more than once."""
        root = client.drive.service_root
        if not isinstance(root, str) or not root:
            raise WriteDenied("route_unavailable")
        try:
            parsed_root = urlsplit(root)
        except ValueError:
            raise WriteDenied("route_unavailable") from None
        if parsed_root.query or parsed_root.fragment or "?" in root or "#" in root:
            raise WriteDenied("route_unavailable")
        adapter = checked_adapter(client.drive.session, route, bounded=True)
        transport_settings(client.drive.session, route)
        assert isinstance(adapter, BoundedAdapter)
        self._bind_transport_adapter(adapter)
        return adapter

    def _bind_transport_adapter(self, adapter: BoundedAdapter) -> None:
        """Bind the selected adapter itself, never repeat a dynamic selection."""
        owner = self.additional.reads.owner
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

    def _require_mutation_path(self, client: Any, path: str) -> BoundedAdapter:
        """Include query parameters in the longest-prefix adapter preflight."""
        url = prepared_url(
            client.drive.session, client.drive.service_root + path, client.drive.params
        )
        return self._require_retry_disabled(client, url or "")

    def _require_metadata_transport(self, client: Any) -> None:
        """Bind the actual detail/listing endpoint before any SDK metadata refresh."""
        self._require_mutation_path(client, "/retrieveItemDetailsInFolders")

    def _retire_source(self, kind: str, handle: str) -> None:
        """A known pre-dispatch retirement failure is not an uncertain mutation."""
        try:
            self.additional.retire(kind, handle)
        except ToolFailure:
            raise WriteDenied("target_unavailable") from None

    async def prepare(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
        *,
        action: str = DRIVE_RENAME,
    ) -> MutationPreview:
        """Refresh the discovered target and publish a non-mutating exact preview."""
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        name = payload.get("name")
        if action not in {DRIVE_RENAME, DRIVE_TRASH_FILE}:
            raise WriteDenied("unsupported_action")
        if action == DRIVE_TRASH_FILE and payload:
            raise WriteDenied("invalid_payload")
        if action == DRIVE_RENAME and self._invalid_rename(payload, name):
            raise WriteDenied("invalid_payload")
        original = self.additional.resolve_any(
            frozenset({"drive-file", "drive-folder"}), handle
        )
        if not isinstance(original, DriveNode):
            raise WriteDenied("unsupported_target")
        if (
            original.owner_record_name is not None
            or original.data.get("shareID")
            or original.data.get("share")
            or original.data["drivewsid"].split("::", 1)[0] not in {"FILE", "FOLDER"}
        ):
            raise WriteDenied("unsupported_shared_write")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))
        source = ("drive-file" if original.type == "file" else "drive-folder", handle)

        def prepare(client: Any) -> MutationPreview:
            node = self._fresh(client, target)
            self._require_action_target(action, node)
            account, resource, revision = self._bindings(client, target, node)
            endpoint = (
                "/moveItemsToTrash" if action == DRIVE_TRASH_FILE else "/renameItems"
            )
            self._require_mutation_path(client, endpoint)
            preview = self.ledger.preview(
                action,
                node.name,
                {"name": name} if action == DRIVE_RENAME else {},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            self._remember_preview(preview, target, source)
            self._publish_preview(
                preview, publish, (self._targets, self._actions, self._sources)
            )
            return preview

        return await self._run(prepare)

    def _publish_preview(
        self,
        preview: MutationPreview,
        publish: Callable[[MutationPreview], None] | None,
        registries: tuple[dict[str, Any], ...] = (),
    ) -> None:
        """Roll back unpublished approvals and their private selector metadata."""
        if publish is None:
            return
        try:
            publish(preview)
        except Exception:
            self.ledger.discard(preview.request_id)
            for registry in registries:
                registry.pop(preview.request_id, None)
            raise

    def _remember_preview(
        self, preview: MutationPreview, target: DriveTarget, source: tuple[str, str]
    ) -> None:
        """Keep bounded target, action and selected-handle metadata together."""
        if len(self._targets) >= 256:
            oldest = next(iter(self._targets))
            del self._targets[oldest]
            self._actions.pop(oldest, None)
            self._sources.pop(oldest, None)
        self._targets[preview.request_id] = target
        self._actions[preview.request_id] = preview.action
        self._sources[preview.request_id] = source

    @staticmethod
    def _invalid_rename(payload: dict[str, Any], name: Any) -> bool:
        """Preserve the exact bounded Drive rename leaf policy."""
        try:
            encoded_length = len(name.encode("utf-8")) if isinstance(name, str) else 0
        except UnicodeEncodeError:
            return True
        return (
            set(payload) != {"name"}
            or not isinstance(name, str)
            or not name.strip()
            or name in {".", ".."}
            or encoded_length > 255
            or any(char in name for char in ("/", "\\", "\x00", "\r", "\n"))
        )

    @staticmethod
    def _require_action_target(action: str, node: DriveNode) -> None:
        """Refuse restored nodes and non-file Trash targets before authorization."""
        if node.data.get("restorePath") or (
            action == DRIVE_TRASH_FILE and node.type != "file"
        ):
            raise WriteDenied("unsupported_target")

    def _check_accepted_context(
        self,
        client: Any,
        target: DriveTarget,
        node: DriveNode,
        account: str,
        resource: str,
        revision: str,
    ) -> None:
        """Receipt acceptance cannot replace the account, service route or target."""
        if self._bindings(client, target, node) != (account, resource, revision):
            raise WriteDenied("stale_preview")

    @staticmethod
    def _require_current_preview(current: Any, selected: Any) -> None:
        """Reject queued selections evicted before owner-lane admission."""
        if current != selected:
            raise WriteDenied("unknown_or_consumed_preview")

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Retire approval before dispatch; verify the actual post-write state."""
        target = self._targets.get(request_id)
        action = self._actions.get(request_id)
        selected_source = self._sources.get(request_id)
        if target is None or action is None or selected_source is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            self._require_current_preview(
                (
                    self._targets.get(request_id),
                    self._actions.get(request_id),
                    self._sources.get(request_id),
                ),
                (target, action, selected_source),
            )
            node = self._fresh(client, target)
            self._require_action_target(action, node)
            account, resource, revision = self._bindings(client, target, node)
            adapter = self._require_mutation_endpoint(client, action)
            self._require_live_source(*selected_source)
            if accept is not None:
                accept(request_id)
            self._check_accepted_context(
                client, target, node, account, resource, revision
            )
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._targets[request_id]
            del self._actions[request_id]
            source_kind, source_handle = self._sources.pop(request_id)
            if action == DRIVE_TRASH_FILE:
                self._retire_source(source_kind, source_handle)
                try:
                    with mutation_session(
                        client.drive, adapter=adapter
                    ) as dispatch_drive:
                        DriveNode(dispatch_drive, node.data).move_to_trash(
                            allow_redirects=False
                        )
                    self._verify_trash(client, target, account, resource)
                except Exception:
                    raise WriteDenied("mutation_uncertain") from None
                return {"action": action, "changed": True}
            name = approved.payload["name"]
            if node.name == name:
                return {"action": DRIVE_RENAME, "changed": False}
            self._retire_source(source_kind, source_handle)
            try:
                with mutation_session(client.drive, adapter=adapter) as dispatch_drive:
                    DriveNode(dispatch_drive, node.data).rename(
                        name, allow_redirects=False
                    )
                self._verify_rename(client, target, name, account, resource)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": DRIVE_RENAME, "changed": True}

        return await self._run(apply)

    def _verify_rename(
        self, client: Any, target: DriveTarget, name: str, account: str, resource: str
    ) -> None:
        """Require independent refreshed name and exact original account/route."""
        verified = self._fresh(client, target)
        verified_account, verified_resource, _revision = self._bindings(
            client, target, verified
        )
        if verified.name != name or (
            verified_account,
            verified_resource,
        ) != (account, resource):
            raise WriteDenied("mutation_unverified")

    def _require_mutation_endpoint(self, client: Any, action: str) -> BoundedAdapter:
        """Recheck longest-prefix policy on the actual mutation endpoint."""
        endpoint = "/moveItemsToTrash" if action == DRIVE_TRASH_FILE else "/renameItems"
        return self._require_mutation_path(client, endpoint)

    def _require_live_source(self, kind: str, handle: str) -> None:
        """A no-op still requires the originally selected live handle."""
        try:
            self.additional.resolve(kind, handle)
        except ToolFailure:
            raise WriteDenied("target_unavailable") from None

    def _verify_trash(
        self, client: Any, target: DriveTarget, account: str, resource: str
    ) -> None:
        """Require exact Recently Deleted membership, not generic OK or absence."""
        self._require_metadata_transport(client)
        matches = [
            item
            for item in client.drive.trash.get_children(force=True)
            if item.data.get("drivewsid") == target.identifier
        ]
        if len(matches) != 1:
            raise WriteDenied("mutation_unverified")
        listed = self._validated_node(client, target, matches[0].data)
        verified = self._fresh(client, target)
        if any(
            listed.data.get(field) != verified.data.get(field)
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
            raise WriteDenied("mutation_unverified")
        current_account, current_resource, revision = self._bindings(
            client, target, verified
        )
        if (
            (current_account, current_resource) != (account, resource)
            or not revision
            or not isinstance(verified.data.get("restorePath"), str)
            or not verified.data["restorePath"]
            or verified.data.get("parentId") != "TRASH_ROOT"
        ):
            raise WriteDenied("mutation_unverified")
