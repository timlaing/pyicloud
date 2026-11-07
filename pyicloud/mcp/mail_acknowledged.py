"""Receipt-gated existing Mail flags/moves; acknowledgements are not final state."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any

from pyicloud.services.mail.models import (
    MailFlagUpdate,
    MailFolder,
    MailMessage,
    MailMoveUpdate,
)

from .authorization import MutationPreview, WriteDenied
from .mail_writes import MailWrites

MAIL_SET_READ = "mail.set_read"
MAIL_MOVE_MESSAGE = "mail.move_message"


def _verify_move(outcome: Any, message: MailMessage, folder: MailFolder | None) -> None:
    """Require exact typed source and destination acknowledgement identity."""
    if (
        not isinstance(outcome, MailMoveUpdate)
        or folder is None
        or (
            outcome.source_message_id,
            outcome.source_folder_id,
            outcome.destination_folder_id,
        )
        != (message.id, message.folder.id, folder.id)
    ):
        raise WriteDenied("mutation_unverified")


def _verify_flag(
    outcome: Any, message: MailMessage, action: str, enabled: bool
) -> None:
    """Require the exact typed requested flag acknowledgement, not inferred state."""
    if not isinstance(outcome, MailFlagUpdate) or (
        outcome.message_id,
        outcome.flag,
        outcome.enabled,
    ) != (message.id, "seen" if action == MAIL_SET_READ else "flagged", enabled):
        raise WriteDenied("mutation_unverified")


class MailAcknowledged:
    """One typed source, one receipt and one SDK call; retire before dispatch."""

    def __init__(self, writer: MailWrites) -> None:
        self.writer = writer
        self.additional = writer.additional
        self.ledger = writer.ledger
        self._requests: dict[str, tuple[str, str, str | None]] = {}

    def _read(
        self, client: Any, handle: str, destination: str | None
    ) -> tuple[MailMessage, MailFolder | None, str, str, str]:
        if "mail" not in self.writer.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        source = self.additional.resolve("mail-message", handle)
        if not isinstance(source, MailMessage):
            raise WriteDenied("unsupported_target")
        if (
            isinstance(source.folder.uid_validity, bool)
            or not isinstance(source.folder.uid_validity, int)
            or source.folder.uid_validity < 1
            or source.mailbox.id != source.folder.id
        ):
            raise WriteDenied("unsupported_target")
        account, route = client.params.get("dsid"), client.mail.service_root
        if (
            not isinstance(account, str)
            or not account.isascii()
            or not account.isdecimal()
            or not isinstance(route, str)
            or not route
        ):
            raise WriteDenied("account_unavailable")
        current = client.mail.message_by_uid(source.folder, source.uid)
        if not isinstance(current, MailMessage) or (
            current.id,
            current.uid,
            current.folder,
            current.mailbox,
        ) != (source.id, source.uid, source.folder, source.mailbox):
            raise WriteDenied("stale_target")
        folder = None
        if destination is not None:
            folder = self.additional.resolve("mail-folder", destination)
            if not isinstance(folder, MailFolder):
                raise WriteDenied("unsupported_target")
            if client.mail.check_move(current, folder) != account:
                raise WriteDenied("account_unavailable")
        resource = json.dumps(
            (
                route,
                current.id,
                current.uid,
                current.folder.id,
                folder.model_dump(mode="json") if folder is not None else None,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        revision = hashlib.sha256(current.model_dump_json().encode()).hexdigest()
        return current, folder, account, resource, revision

    async def prepare(
        self,
        action: str,
        handle: str,
        payload: dict[str, Any],
        destination: str | None = None,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """No ID selectors, flag masks, caller approval or inferred flag state."""
        if action not in {MAIL_SET_READ, "mail.set_flagged", MAIL_MOVE_MESSAGE}:
            raise WriteDenied("unsupported_action")
        if action == MAIL_MOVE_MESSAGE:
            if payload or not destination:
                raise WriteDenied("invalid_payload")
        elif (
            set(payload) != {"enabled"}
            or not isinstance(payload["enabled"], bool)
            or destination is not None
        ):
            raise WriteDenied("invalid_payload")
        data = dict(payload)

        def prepare(client: Any) -> MutationPreview:
            message, folder, account, resource, revision = self._read(
                client, handle, destination
            )
            approved_payload = (
                data
                if folder is None
                else {
                    "destination_folder_id": folder.id,
                    "destination_name": folder.name,
                }
            )
            preview = self.ledger.preview(
                action,
                message.id,
                approved_payload,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._requests) >= 256:
                del self._requests[next(iter(self._requests))]
            self._requests[preview.request_id] = (action, handle, destination)
            if publish is not None:
                publish(preview)
            return preview

        return await self.writer.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Retire before one call, then return only exact typed acknowledgement."""
        target = self._requests.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")
        action, handle, destination = target

        def apply(client: Any) -> dict[str, Any]:
            message, folder, account, resource, revision = self._read(
                client, handle, destination
            )
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            self._requests.pop(request_id, None)
            self.additional.retire("mail-message", handle)
            try:
                if action == MAIL_MOVE_MESSAGE:
                    outcome = client.mail.move(message, folder)
                    _verify_move(outcome, message, folder)
                else:
                    enabled = approved.payload["enabled"]
                    outcome = (
                        client.mail.set_read(message, read=enabled)
                        if action == MAIL_SET_READ
                        else client.mail.set_flagged(message, flagged=enabled)
                    )
                    _verify_flag(outcome, message, action, enabled)
                if (
                    client.params.get("dsid") != account
                    or client.mail.service_root != json.loads(resource)[0]
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": action,
                "acknowledged": True,
                "final_state_verified": False,
            }

        return await self.writer.reads.owner.run(apply)
