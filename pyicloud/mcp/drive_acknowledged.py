"""Guarded existing Drive create/recover requests with acknowledgement-only results."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.services.drive import DriveNode

from .authorization import MutationPreview, WriteDenied
from .drive_writes import DriveTarget, DriveWrites
from .upload_transport import UploadTransport, checked_upload_url


class DriveAcknowledged(DriveWrites):
    """Exact private metadata/etag binding; no invented created or recovered handles."""

    def __init__(self, writer: DriveWrites) -> None:
        super().__init__(writer.additional, writer.ledger)
        self._requests: dict[str, tuple[str, str, DriveTarget, str | None]] = {}

    def _read(
        self, client: Any, action: str, target: DriveTarget, name: str | None
    ) -> tuple[DriveNode, str, str, str]:
        if "drive" not in self.additional.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        node = self._fresh(client, target)
        account, resource, revision = self._bindings(client, target, node)
        route = client.drive.service_root
        hostname = urlsplit(route).hostname
        checked_upload_url(route, frozenset({hostname}) if hostname else frozenset())
        if urlsplit(route).query or client.drive.params.get("dsid") != account:
            raise WriteDenied("route_unavailable")
        if action == "drive.create_folder":
            if node.type != "folder" or node.data.get("restorePath"):
                raise WriteDenied("unsupported_target")
            children = node.get_children(force=True)
            node = self._validated_node(client, target, node.data)
            if node.type != "folder" or node.data.get("restorePath"):
                raise WriteDenied("unsupported_target")
            if any(child.name.casefold() == str(name).casefold() for child in children):
                raise WriteDenied("destination_conflict")
            account, resource, revision = self._bindings(client, target, node)
        else:
            path = node.data.get("restorePath")
            if (
                node.data.get("parentId") != "TRASH_ROOT"
                or not isinstance(path, str)
                or not path
            ):
                raise WriteDenied("unsupported_target")
            members = [
                item
                for item in client.drive.trash.get_children(force=True)
                if item.data.get("drivewsid") == target.identifier
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
            # The existing API restores to its recorded location; it accepts no
            # destination selector. Bind that observed path without interpreting
            # it as a local filesystem path or fabricating a parent identifier.
            resource = json.dumps((resource, node.name, path), separators=(",", ":"))
        return node, account, resource, revision

    async def prepare_acknowledged(
        self,
        action: str,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """A discovered private destination or Trash node, never caller IDs/paths."""
        if action not in {"drive.create_folder", "drive.recover_item"}:
            raise WriteDenied("unsupported_action")
        name = payload.get("name")
        if action == "drive.create_folder":
            if (
                set(payload) != {"name"}
                or not isinstance(name, str)
                or not name.strip()
                or name in {".", ".."}
                or len(name.encode()) > 255
                or any(ord(char) < 32 or char in {"/", "\\", "\x7f"} for char in name)
            ):
                raise WriteDenied("invalid_payload")
            original = self.additional.resolve("drive-folder", handle)
        else:
            if payload:
                raise WriteDenied("invalid_payload")
            original = self.additional.resolve_any(
                frozenset({"drive-trash-file", "drive-trash-folder"}), handle
            )
        if not isinstance(original, DriveNode):
            raise WriteDenied("unsupported_target")
        target = DriveTarget(original.data["drivewsid"], original.data.get("zone"))

        def prepare(client: Any) -> MutationPreview:
            node, account, resource, revision = self._read(client, action, target, name)
            data = (
                {"name": name}
                if action == "drive.create_folder"
                else {
                    "restore_path": node.data["restorePath"],
                    "destination": "server_recorded_location",
                    "conflict_behavior": "existing_api_not_independently_verified",
                }
            )
            preview = self.ledger.preview(
                action,
                node.name,
                data,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._requests) >= 256:
                del self._requests[next(iter(self._requests))]
            self._requests[preview.request_id] = (action, handle, target, name)
            if publish is not None:
                publish(preview)
            return preview

        return await self.additional.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """One raw request; exact acknowledgement shape, never final identity."""
        target = self._requests.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")
        action, handle, selector, pending_name = target

        def apply(client: Any) -> dict[str, Any]:
            # Approved name is detached in the ledger; prepare registries retain
            # no caller-controlled mutable payload.
            self.additional.resolve_any(
                frozenset({"drive-folder", "drive-trash-file", "drive-trash-folder"}),
                handle,
            )
            node, account, resource, revision = self._read(
                client, action, selector, pending_name
            )
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._requests[request_id]
            self.additional.retire(
                "drive-folder"
                if action == "drive.create_folder"
                else "drive-trash-" + node.type,
                handle,
            )
            try:
                route = client.drive.service_root
                if action == "drive.create_folder":
                    body = {
                        "destinationDrivewsId": selector.identifier,
                        "folders": [
                            {
                                "clientId": "FOLDER::UNKNOWN_ZONE::TempId-"
                                + str(uuid4()),
                                "name": approved.payload["name"],
                            }
                        ],
                    }
                    path = "/createFolders"
                else:
                    body = {
                        "items": [
                            {
                                "drivewsid": selector.identifier,
                                "etag": node.data["etag"],
                            }
                        ]
                    }
                    path = "/putBackItemsFromTrash"
                response = UploadTransport.register(
                    client.drive.session,
                    route + path + "?" + urlencode(dict(client.drive.params)),
                    frozenset({str(urlsplit(route).hostname)}),
                    body,
                    headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
                    if action == "drive.create_folder"
                    else None,
                )
                if action == "drive.create_folder":
                    records = (
                        response.get("folders") if isinstance(response, dict) else None
                    )
                    if (
                        not isinstance(records, list)
                        or len(records) != 1
                        or not isinstance(records[0], dict)
                        or records[0].get("name") != approved.payload["name"]
                        or records[0].get("status", "OK") != "OK"
                    ):
                        raise WriteDenied("mutation_unverified")
                else:
                    records = (
                        response.get("items") if isinstance(response, dict) else None
                    )
                    if (
                        not isinstance(records, list)
                        or len(records) != 1
                        or not isinstance(records[0], dict)
                        or records[0].get("drivewsid") != selector.identifier
                        or records[0].get("status") != "OK"
                    ):
                        raise WriteDenied("mutation_unverified")
                if (
                    str(client.params.get("dsid")) != account
                    or client.drive.service_root != route
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": action,
                "acknowledged": True,
                "final_state_verified": False,
            }

        return await self.additional.reads.owner.run(apply)
