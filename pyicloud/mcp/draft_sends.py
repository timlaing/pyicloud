"""Internal elevated saved-draft send adapter; no runtime send action enabled yet."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyicloud.services.mail.models import MailSendUpdate

from .authorization import MutationPreview, WriteDenied
from .draft_revalidation import DraftRevalidation
from .mail_writes import MailWrites


class DraftSends:
    """One exact elevated receipt, one submission, never a delivery or retry claim."""

    def __init__(self, writer: MailWrites) -> None:
        self.writer = writer
        self.reader = DraftRevalidation(writer)
        self.ledger = writer.ledger
        self.additional = writer.additional
        self._sends: dict[str, str] = {}

    async def prepare_send(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Only an empty caller payload; exact verified saved content is previewed."""
        if payload:
            raise WriteDenied("invalid_payload")

        def prepare(client: Any) -> MutationPreview:
            state = self.reader.read(client, handle)
            data = state.context.payload
            if not any(data[name] for name in ("to", "cc", "bcc")):
                raise WriteDenied("recipients_required")
            preview = self.ledger.preview(
                "mail.send_draft",
                state.message.id,
                data,
                account_binding=state.account_binding,
                resource_binding=state.resource_binding,
                revision=state.revision,
            )
            if preview.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            if len(self._sends) >= 256:
                del self._sends[next(iter(self._sends))]
            self._sends[preview.request_id] = handle
            if publish is not None:
                publish(preview)
            return preview

        return await self.writer.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Retire before submission; no delivery inference or blind retry."""
        handle = self._sends.get(request_id)
        if handle is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            state = self.reader.read(client, handle)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=state.account_binding,
                resource_binding=state.resource_binding,
                revision=state.revision,
            )
            del self._sends[request_id]
            if approved.risk != "high":
                raise WriteDenied("high_risk_operator_approval_required")
            self.additional.retire("mail-draft", handle)
            try:
                outcome = client.mail.send_draft(state.context.draft, confirm=True)
                if (
                    not isinstance(outcome, MailSendUpdate)
                    or (
                        outcome.source_message_id,
                        outcome.source_folder_id,
                        outcome.source_uid,
                    )
                    != (state.message.id, state.message.folder.id, state.message.uid)
                    or not outcome.acknowledged
                    or outcome.delivery_confirmed
                    or outcome.retained_draft_ids
                ):
                    raise WriteDenied("mutation_unverified")
                account, resource, revision, _folder, _sender = (
                    self.writer.binding_context(client, state.context.payload)
                )
                if (account, resource, revision) != (
                    state.context.account_binding,
                    state.context.resource_binding,
                    state.context.revision,
                ) or client.mail.message_by_uid(
                    state.message.folder, state.message.uid
                ) is not None:
                    raise WriteDenied("mutation_unverified")
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": "mail.send_draft",
                "acknowledged": True,
                "delivery_confirmed": False,
            }

        return await self.writer.reads.owner.run(apply)
