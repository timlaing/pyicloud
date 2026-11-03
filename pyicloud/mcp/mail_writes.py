"""Exact plain-only Mail draft creation, without sending or attachment uploads."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
import json
from typing import Any

from pyicloud.services.mail._drafts import (
    DraftWriter,
    checked_recipients,
    recipient_addresses,
)
from pyicloud.services.mail._threading import checked_threading
from pyicloud.services.mail.models import MailBody, MailDraft, MailFolder, MailSender

from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .reads import ReadTools


def canonical_draft_html(text: str) -> str:
    """Match the verified library plain-only save conversion, without rendering."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    return "<div>" + escape(normalized, quote=False).replace("\n", "<br>") + "</div>"


def verify_draft_body(body: MailBody, uid: int, text: str) -> None:
    """Require exact canonical HTML and identity, not semantic HTML equivalence.

    This deliberately rejects alternate MIME layouts or server transformations.
    A rejection after dispatch is uncertain, not proof that saving failed. Never
    use it to retry a mutation or render the returned untrusted body.
    """
    if (
        body.guid != f"message:Drafts/{uid}"
        or body.content_type != "text/html"
        or len(body.parts) != 1
        or body.parts[0].guid != f"messagepart:Drafts/{uid}-1"
        or body.parts[0].content != canonical_draft_html(text)
    ):
        raise WriteDenied("mutation_unverified")


def validate_payload(payload: dict[str, Any]) -> None:
    """Explicit bounded strings/recipient arrays only; no HTML or attachment input."""
    if set(payload) != {"sender", "subject", "text", "to", "cc", "bcc"}:
        raise WriteDenied("invalid_payload")
    try:
        DraftWriter.validate_inputs(
            payload["sender"], payload["subject"], payload["text"], ""
        )
        if len(payload["sender"].encode("utf-8")) > 254:
            raise ValueError("Too large")
        if (
            len(payload["subject"].encode("utf-8")) > 4096
            or len(payload["text"].encode("utf-8")) > 32768
        ):
            raise ValueError("Too large")
        for name in ("to", "cc", "bcc"):
            values = payload[name]
            if not isinstance(values, list) or len(values) > 50:
                raise ValueError("Explicit bounded recipient array required")
            if any(
                not isinstance(value, str) or len(value.encode("utf-8")) > 512
                for value in values
            ):
                raise ValueError("Recipient too large")
            recipient_addresses(tuple(values))
    except (ValueError, TypeError):
        raise WriteDenied("invalid_payload") from None


class MailWrites:
    """Serialize revalidation, consume approval before saving, never retry a save."""

    def __init__(self, reads: ReadTools, ledger: ApprovalLedger) -> None:
        self.reads = reads
        self.ledger = ledger
        self._payloads: dict[str, dict[str, Any]] = {}

    def _fresh(
        self, client: Any, payload: dict[str, Any]
    ) -> tuple[str, str, str, MailFolder, MailSender]:
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
        folder = client.mail.draft_folder()
        senders = [
            s
            for s in client.mail.senders()
            if s.address.casefold() == payload["sender"].casefold()
        ]
        if (
            not isinstance(folder, MailFolder)
            or folder.name != "Drafts"
            or not folder.uid_validity
            or len(senders) != 1
            or not isinstance(senders[0], MailSender)
        ):
            raise WriteDenied("target_unavailable")
        sender = senders[0]
        resource = json.dumps((route, folder.id, folder.name), separators=(",", ":"))
        revision = json.dumps(
            (folder.uid_validity, sender.address, sender.display_name),
            separators=(",", ":"),
        )
        return account, resource, revision, folder, sender

    async def prepare(
        self,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Read-only discovery binds the selected sender and Drafts incarnation."""
        if "mail" not in self.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        validate_payload(payload)
        # Detach caller-owned arrays before queueing on the serialized worker.
        data = json.loads(json.dumps(payload))

        def prepare(client: Any) -> MutationPreview:
            account, resource, revision, _folder, _sender = self._fresh(client, data)
            preview = self.ledger.preview(
                "mail.create_draft",
                "Drafts",
                data,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._payloads) >= 256:
                del self._payloads[next(iter(self._payloads))]
            self._payloads[preview.request_id] = preview.payload
            if publish is not None:
                publish(preview)
            return preview

        return await self.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Return only identity after exact verified save; retire uncertain requests."""
        data = self._payloads.get(request_id)
        if data is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            account, resource, revision, folder, sender = self._fresh(client, data)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._payloads[request_id]
            payload = approved.payload
            try:
                saved = client.mail.create_draft(
                    sender=sender.address,
                    subject=payload["subject"],
                    text=payload["text"],
                    to=tuple(payload["to"]),
                    cc=tuple(payload["cc"]),
                    bcc=tuple(payload["bcc"]),
                )
                if (
                    not isinstance(saved, MailDraft)
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
                body = client.mail.body(saved.message)
                verify_draft_body(body, saved.message.uid, payload["text"])
                checked_recipients(body, saved)
                checked_threading(body, None)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {"action": "mail.create_draft", "id": saved.message.id}

        return await self.reads.owner.run(apply)
