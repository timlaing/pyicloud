"""Receipt-gated top-level Mail folder creation with fresh namespace proof."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.mail._folder_names import SYSTEM_FOLDERS
from pyicloud.services.mail.models import MailFolder

from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .reads import ReadTools


def folder_name(payload: dict[str, Any]) -> str:
    """Conservative unencoded custom leaf names, never protected system names."""
    name = payload.get("name")
    if (
        set(payload) != {"name"}
        or not isinstance(name, str)
        or not name.strip()
        or not name.isascii()
        or name != name.strip()
        or len(name) > 255
        or any(not (char.isalnum() or char in {" ", "_", "-"}) for char in name)
        or name.casefold() in SYSTEM_FOLDERS | {"sent", "trash"}
    ):
        raise WriteDenied("invalid_payload")
    return name


class MailFolderWrites:
    """Single-use exact previews and fresh namespace proof, never folder deletion."""

    def __init__(self, reads: ReadTools, ledger: ApprovalLedger) -> None:
        self.reads = reads
        self.ledger = ledger
        self._names: dict[str, str] = {}

    def _fresh(self, client: Any) -> tuple[str, str, str, list[MailFolder]]:
        account, route = client.params.get("dsid"), client.mail.service_root
        if (
            not isinstance(account, str)
            or not account
            or not isinstance(route, str)
            or not route
        ):
            raise WriteDenied("route_unavailable")
        folders = client.mail.folders()
        if (
            not isinstance(folders, list)
            or not all(isinstance(item, MailFolder) for item in folders)
            or len(folders) > 1000
            or len({item.id for item in folders}) != len(folders)
        ):
            raise WriteDenied("namespace_unavailable")
        snapshot = sorted((item.id, item.name, item.uid_validity) for item in folders)
        return account, route, json.dumps(snapshot, separators=(",", ":")), folders

    def _collision(self, folders: list[MailFolder], name: str) -> None:
        if any(item.name.casefold() == name.casefold() for item in folders):
            raise WriteDenied("destination_conflict")

    async def prepare(
        self,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Capture namespace metadata only; this is not server-side atomicity."""
        if "mail" not in self.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        name = folder_name(payload)

        def prepare(client: Any) -> MutationPreview:
            account, route, revision, folders = self._fresh(client)
            self._collision(folders, name)
            preview = self.ledger.preview(
                "mail.create_folder",
                "top-level Mail namespace",
                {"name": name},
                account_binding=account,
                resource_binding=route,
                revision=revision,
            )
            if len(self._names) >= 256:
                del self._names[next(iter(self._names))]
            self._names[preview.request_id] = name
            if publish is not None:
                publish(preview)
            return preview

        return await self.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume before one verified-facade create; post failures never retry."""
        name = self._names.get(request_id)
        if name is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            account, route, revision, folders = self._fresh(client)
            self._collision(folders, name)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=route,
                revision=revision,
            )
            del self._names[request_id]
            try:
                result = client.mail.create_folder(approved.payload["name"])
                current_account, current_route, _revision, current = self._fresh(client)
                matches = [item for item in current if item.name == name]
                if (
                    not isinstance(result, MailFolder)
                    or (current_account, current_route) != (account, route)
                    or result.name != name
                    or result.id in {item.id for item in folders}
                    or len(matches) != 1
                    or matches[0] != result
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "mail.create_folder", "id": result.id}

        return await self.reads.owner.run(apply)
