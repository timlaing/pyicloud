"""Exact receipt-gated saved-draft edits with pre-dispatch handle retirement."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pyicloud.services.mail._draft_headers import parse_headers
from pyicloud.services.mail._drafts import checked_recipients, header_sender
from pyicloud.services.mail._threading import checked_threading
from pyicloud.services.mail.models import MailDraft

from .authorization import MutationPreview, WriteDenied
from .draft_revalidation import DraftRevalidation
from .mail_writes import MailWrites, validate_payload, verify_draft_body
from .saved_drafts import SavedDraftContext


class DraftEdits:
    """Exact receipts, single dispatch and rotating-handle proof; never send drafts."""

    def __init__(self, writer: MailWrites) -> None:
        self.writer = writer
        self.reader = DraftRevalidation(writer)
        self.additional = writer.additional
        self.ledger = writer.ledger
        self._edits: dict[str, str] = {}

    async def prepare_edit(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Propose bounded text/recipient replacement, never infer saved context."""
        config = self.writer.reads.owner.config
        if "mail" not in config.services or "mail" not in config.content_services:
            raise WriteDenied("content_disabled")
        if set(payload) != {"subject", "text", "to", "cc", "bcc"}:
            raise WriteDenied("invalid_payload")
        context = self.additional.resolve("mail-draft", handle)
        if not isinstance(context, SavedDraftContext):
            raise WriteDenied("unsupported_target")
        data = {"sender": context.payload["sender"], **payload}
        validate_payload(data)
        data = json.loads(json.dumps(data))

        def prepare(client: Any) -> MutationPreview:
            state = self.reader.read(client, handle)
            preview = self.ledger.preview(
                "mail.edit_draft",
                state.message.id,
                data,
                account_binding=state.account_binding,
                resource_binding=state.resource_binding,
                revision=state.revision,
            )
            if len(self._edits) >= 256:
                del self._edits[next(iter(self._edits))]
            self._edits[preview.request_id] = handle
            if publish is not None:
                publish(preview)
            return preview

        return await self.writer.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Consume and retire before edit; uncertain outcomes have no retry handle."""
        handle = self._edits.get(request_id)
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
            del self._edits[request_id]
            self.additional.retire("mail-draft", handle)
            payload = approved.payload
            try:
                saved = client.mail.edit_draft(
                    state.context.draft,
                    subject=payload["subject"],
                    text=payload["text"],
                    html="",
                    to=tuple(payload["to"]),
                    cc=tuple(payload["cc"]),
                    bcc=tuple(payload["bcc"]),
                    attachments=(),
                )
                account, resource, revision, folder, sender = (
                    self.writer.binding_context(client, payload)
                )
                if (
                    not isinstance(saved, MailDraft)
                    or (account, resource, revision)
                    != (
                        state.context.account_binding,
                        state.context.resource_binding,
                        state.context.revision,
                    )
                    or saved.message.uid == state.message.uid
                    or saved.message.id == state.message.id
                    or (
                        saved.message.folder.id,
                        saved.message.folder.name,
                        saved.message.folder.uid_validity,
                    )
                    != (folder.id, folder.name, folder.uid_validity)
                    or saved.sender != sender
                    or saved.message.subject != payload["subject"]
                    or saved.attachments
                    or saved.threading
                    or saved.retained_drafts
                    or (saved.to, saved.cc, saved.bcc)
                    != (
                        tuple(payload["to"]),
                        tuple(payload["cc"]),
                        tuple(payload["bcc"]),
                    )
                ):
                    raise WriteDenied("mutation_unverified")
                if (
                    client.mail.message_by_uid(state.message.folder, state.message.uid)
                    is not None
                ):
                    raise WriteDenied("mutation_unverified")
                body = client.mail.body(saved.message)
                verify_draft_body(body, saved.message.uid, payload["text"])
                checked_recipients(body, saved)
                checked_threading(body, None)
                senders = parse_headers(body.headers).get_all("From", [])
                if (
                    len(senders) != 1
                    or header_sender(str(senders[0])).casefold()
                    != sender.address.casefold()
                ):
                    raise WriteDenied("mutation_unverified")
                replacement = SavedDraftContext(
                    saved,
                    json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ),
                    account,
                    resource,
                    revision,
                )
                new_handle = self.additional.remember("mail-draft", replacement)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": "mail.edit_draft",
                "id": saved.message.id,
                "handle": new_handle,
            }

        return await self.writer.reads.owner.run(apply)
