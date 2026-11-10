"""Receipt-gated custom Mail folder rename with exact namespace identity proof."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.mail.models import MailFolder

from .additional import AdditionalReads
from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .mail_folders import MailFolderWrites, folder_name


class MailFolderRenames(MailFolderWrites):
    """Exact discovered top-level identities, no system or descendant tree writes."""

    def __init__(self, additional: AdditionalReads, ledger: ApprovalLedger) -> None:
        super().__init__(additional.reads, ledger)
        self.additional = additional
        self._renames: dict[str, tuple[MailFolder, str]] = {}

    def _current(
        self, folders: list[MailFolder], target: MailFolder, name: str
    ) -> None:
        matches = [item for item in folders if item.id == target.id]
        if len(matches) != 1 or (matches[0].name, matches[0].uid_validity) != (
            target.name,
            target.uid_validity,
        ):
            raise WriteDenied("target_unavailable")
        if any(
            item.name.casefold().startswith(target.name.casefold() + "/")
            for item in folders
        ):
            raise WriteDenied("unsupported_tree_write")
        self._collision(folders, name)

    async def prepare_rename(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Only metadata reads until a separate operator approves exact bindings."""
        if "mail" not in self.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        name = folder_name(payload)
        target = self.additional.resolve("mail-folder", handle)
        if (
            not isinstance(target, MailFolder)
            or not target.uid_validity
            or target.uid_validity < 1
        ):
            raise WriteDenied("unsupported_target")
        folder_name({"name": target.name})
        if name == target.name:
            raise WriteDenied("unchanged_name")

        def prepare(client: Any) -> MutationPreview:
            account, route, revision, folders = self._fresh(client)
            self._current(folders, target, name)
            resource = json.dumps(
                (route, target.id, target.name, target.uid_validity),
                separators=(",", ":"),
            )
            preview = self.ledger.preview(
                "mail.rename_folder",
                target.name,
                {"name": name},
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._renames) >= 256:
                del self._renames[next(iter(self._renames))]
            self._renames[preview.request_id] = (target, name)
            if publish is not None:
                publish(preview)
            return preview

        return await self.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume before one verified public rename; post failure is uncertain."""
        selected = self._renames.get(request_id)
        if selected is None:
            raise WriteDenied("unknown_or_consumed_preview")
        target, name = selected

        def apply(client: Any) -> dict[str, Any]:
            account, route, revision, folders = self._fresh(client)
            self._current(folders, target, name)
            resource = json.dumps(
                (route, target.id, target.name, target.uid_validity),
                separators=(",", ":"),
            )
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            self._renames.pop(request_id, None)
            try:
                result = client.mail.rename_folder(target, approved.payload["name"])
                current_account, current_route, _revision, current = self._fresh(client)
                matches = [item for item in current if item.id == target.id]
                named = [
                    item for item in current if item.name.casefold() == name.casefold()
                ]
                if (
                    not isinstance(result, MailFolder)
                    or (current_account, current_route) != (account, route)
                    or (result.id, result.name, result.uid_validity)
                    != (target.id, name, target.uid_validity)
                    or len(matches) != 1
                    or len(named) != 1
                    or (matches[0].id, matches[0].name, matches[0].uid_validity)
                    != (result.id, result.name, result.uid_validity)
                    or (named[0].id, named[0].name, named[0].uid_validity)
                    != (result.id, result.name, result.uid_validity)
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "mail.rename_folder", "id": target.id}

        return await self.reads.owner.run(apply)
