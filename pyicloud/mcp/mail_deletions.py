"""Internal elevated single-Trash-message adapter; no bulk or body inference."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any

from pyicloud.services.mail.models import MailMessage, MailMessageDeletion

from .authorization import MutationPreview, WriteDenied
from .mail_writes import MailWrites


class MailDeletions:
    """Retire an exact discovered source before one irreversible SDK call."""

    def __init__(self, writer: MailWrites) -> None:
        self.writer = writer
        self.additional = writer.additional
        self.ledger = writer.ledger
        self._requests: dict[str, str] = {}

    def _read(self, client: Any, handle: str) -> tuple[MailMessage, str, str, str]:
        if "mail" not in self.writer.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        source = self.additional.resolve("mail-message", handle)
        if (
            not isinstance(source, MailMessage)
            or source.folder.name != "Deleted Messages"
        ):
            raise WriteDenied("unsupported_target")
        account = client.params.get("dsid")
        route = client.mail.service_root
        if (
            not isinstance(account, str)
            or not account.isascii()
            or not account.isdecimal()
            or not isinstance(route, str)
            or not route
        ):
            raise WriteDenied("account_unavailable")
        if client.mail.check_delete_permanently(source) != account:
            raise WriteDenied("account_unavailable")
        current = client.mail.message_by_uid(source.folder, source.uid)
        if not isinstance(current, MailMessage) or (
            current.id,
            current.uid,
            current.folder,
            current.mailbox,
        ) != (source.id, source.uid, source.folder, source.mailbox):
            raise WriteDenied("stale_target")
        resource = json.dumps(
            (route, current.folder.id, current.folder.name, current.id, current.uid),
            separators=(",", ":"),
        )
        # Opaque flags remain uninterpreted. Metadata is not a body revision.
        revision = hashlib.sha256(current.model_dump_json().encode()).hexdigest()
        return current, account, resource, revision

    async def prepare_delete(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """No caller confirmation, bulk selector, or content selector is accepted."""
        if payload:
            raise WriteDenied("invalid_payload")

        def prepare(client: Any) -> MutationPreview:
            message, account, resource, revision = self._read(client, handle)
            preview = self.ledger.preview(
                "mail.delete_forever_message",
                message.id,
                {
                    "permanent": True,
                    "message_id": message.id,
                    "uid": message.uid,
                    "folder_id": message.folder.id,
                },
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if preview.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            if len(self._requests) >= 256:
                del self._requests[next(iter(self._requests))]
            self._requests[preview.request_id] = handle
            if publish is not None:
                publish(preview)
            return preview

        return await self.writer.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume elevated approval and source before dispatch; never retry."""
        handle = self._requests.get(request_id)
        if handle is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            message, account, resource, revision = self._read(client, handle)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            self._requests.pop(request_id, None)
            if approved.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            self.additional.retire("mail-message", handle)
            try:
                outcome = client.mail.delete_permanently(message, confirm=True)
                if not isinstance(outcome, MailMessageDeletion) or (
                    outcome.message_id,
                    outcome.folder_id,
                    outcome.uid,
                    outcome.permanent,
                ) != (message.id, message.folder.id, message.uid, True):
                    raise WriteDenied("mutation_unverified")
                by_uid = client.mail.message_by_uid(message.folder, message.uid)
                by_id = client.mail.message(message.folder, message.id)
                if (
                    client.params.get("dsid") != account
                    or client.mail.service_root != json.loads(resource)[0]
                    or by_uid is not None
                    or by_id is not None
                ):
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "mail.delete_forever_message", "permanent": True}

        return await self.writer.reads.owner.run(apply)
