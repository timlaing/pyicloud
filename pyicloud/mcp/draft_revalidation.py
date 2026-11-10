"""Read-only exact revalidation of verified server-created draft contexts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from pyicloud.services.mail._draft_headers import parse_headers
from pyicloud.services.mail._drafts import checked_recipients, header_sender
from pyicloud.services.mail._threading import checked_threading
from pyicloud.services.mail.models import MailMessage

from .authorization import WriteDenied
from .mail_writes import MailWrites, verify_draft_body
from .saved_drafts import SavedDraftContext


@dataclass(frozen=True, repr=False)
class ValidatedDraftState:
    """Private state only: no untrusted fresh body/header data is returned to MCP."""

    context: SavedDraftContext
    message: MailMessage
    account_binding: str
    resource_binding: str
    revision: str


class DraftRevalidation:
    """No mutation path; future edits must use this on the serialized owner lane."""

    def __init__(self, writer: MailWrites) -> None:
        self.reads = writer.reads
        self.additional = writer.additional
        self._bindings = writer.binding_context

    def read(self, client: Any, handle: str) -> ValidatedDraftState:
        """Require content consent and exact last-approved state before replacing it."""
        if "mail" not in self.reads.owner.config.services:
            raise WriteDenied("write_disabled")
        if "mail" not in self.reads.owner.config.content_services:
            raise WriteDenied("content_disabled")
        context = self.additional.resolve("mail-draft", handle)
        if not isinstance(context, SavedDraftContext):
            raise WriteDenied("unsupported_target")
        draft, payload = context.draft, context.payload
        if draft.attachments or draft.threading or draft.retained_drafts:
            raise WriteDenied("unsupported_target")
        account, resource, revision, folder, sender = self._bindings(client, payload)
        if (account, resource, revision) != (
            context.account_binding,
            context.resource_binding,
            context.revision,
        ) or sender != draft.sender:
            raise WriteDenied("stale_saved_context")
        current = client.mail.check_draft(draft)
        if not isinstance(current, MailMessage) or (
            current.id,
            current.uid,
            current.folder.id,
            current.folder.name,
            current.folder.uid_validity,
            current.subject,
        ) != (
            draft.message.id,
            draft.message.uid,
            folder.id,
            folder.name,
            folder.uid_validity,
            payload["subject"],
        ):
            raise WriteDenied("stale_saved_context")
        body = client.mail.body(current)
        verify_draft_body(body, current.uid, payload["text"])
        checked_recipients(body, draft)
        checked_threading(body, None)
        headers = parse_headers(body.headers)
        senders = headers.get_all("From", [])
        if (
            len(senders) != 1
            or header_sender(str(senders[0])).casefold() != sender.address.casefold()
        ):
            raise WriteDenied("stale_saved_context")
        body_digest = hashlib.sha256(body.model_dump_json().encode("utf-8")).hexdigest()
        bound_resource = json.dumps(
            (resource, current.id, current.uid), separators=(",", ":")
        )
        payload_digest = hashlib.sha256(
            context.payload_json.encode("utf-8")
        ).hexdigest()
        bound_revision = json.dumps(
            (revision, payload_digest, body_digest), separators=(",", ":")
        )
        return ValidatedDraftState(
            context, current, account, bound_resource, bound_revision
        )
