"""Guarded draft saves with rotating UIDs and no replay after submission."""
# cspell:words headersonly

from collections.abc import Callable
from dataclasses import dataclass
from email.headerregistry import Address
from email.utils import formatdate
from html import escape
from urllib.parse import urlencode

from pyicloud.exceptions import PyiCloudException

from ._draft_headers import parse_headers
from ._draft_models import DraftCreateRequest, DraftEditRequest, DraftSaveResponse
from ._draft_uploads import attachment_references
from .exceptions import MailError, MailWriteUncertain
from .models import (
    MailBody,
    MailDraft,
    MailDraftAttachment,
    MailFolder,
    MailMessage,
    MailSender,
)

DRAFT_UNCERTAIN = (
    "Mail draft save outcome is uncertain; inspect Drafts before retrying. "
    "The UID may have changed. No automatic retry or cleanup was performed."
)


def header_sender(value: str) -> str:
    """Require one well-formed From address without accepting injected headers."""
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MailError("Draft sender metadata is invalid")
    header = parse_headers("From: " + value + "\r\n\r\n")["From"]
    # Header factories retain class references across module reloads. Validate
    # their structured result, not the identity of a dynamically created class.
    addresses: object = getattr(header, "addresses", None)
    defects: object = getattr(header, "defects", None)
    if (
        not isinstance(addresses, tuple)
        or not isinstance(defects, tuple)
        or defects
        or len(addresses) != 1
    ):
        raise MailError("Draft sender metadata is ambiguous or malformed")
    address: object = getattr(addresses[0], "addr_spec", None)
    if not isinstance(address, str) or not address:
        raise MailError("Draft sender metadata has no address")
    return address


def recipient_addresses(values: tuple[str, ...]) -> tuple[str, ...]:
    """Validate one mailbox per explicit recipient, without groups or injection."""
    if not isinstance(values, tuple):
        raise ValueError("Recipients must be a tuple of address strings")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Recipients must be address strings")
        try:
            value.encode("utf-8")
            address = header_sender(value)
            parsed = Address(addr_spec=address)
        except (MailError, UnicodeError, ValueError) as exc:
            raise ValueError("Invalid draft recipient") from exc
        if not parsed.domain or not parsed.username or ":" in value:
            raise ValueError("Draft recipients require individual mailbox addresses")
        result.append(parsed.username + "@" + parsed.domain.casefold())
    return tuple(result)


def checked_recipients(body: MailBody, draft: MailDraft) -> None:
    """Compare fresh headers with the handle, including Bcc and Resent guards."""
    headers = parse_headers(body.headers)
    if headers.defects or any(
        value.strip()
        for name in (
            "Reply-To",
            "Sender",
            "Return-Receipt-To",
            "Disposition-Notification-To",
            "Resent-To",
            "Resent-Cc",
            "Resent-Bcc",
        )
        for value in headers.get_all(name, [])
    ):
        raise MailError(
            "Draft headers are malformed or contain unsupported address headers"
        )
    for name, expected in (("To", draft.to), ("Cc", draft.cc), ("Bcc", draft.bcc)):
        values = headers.get_all(name, [])
        if len(values) > 1:
            raise MailError("Duplicate draft recipient headers")
        actual = values[0] if values else None
        if actual is not None and (
            actual.defects or any(g.display_name is not None for g in actual.groups)
        ):
            raise MailError("Draft recipient headers are malformed or grouped")
        addresses = (
            ()
            if actual is None
            else tuple(a.username + "@" + a.domain.casefold() for a in actual.addresses)
        )
        if addresses != recipient_addresses(expected):
            raise MailError("Draft recipient state changed; refresh before writing")


def checked_attachments(message: MailMessage, draft: MailDraft) -> None:
    """Do not clear unrecognized attachments during a full replacement save."""
    parts = [p for p in message.parts if p.is_attachment]
    if message.has_attachment is not bool(draft.attachments) or len(parts) != len(
        draft.attachments
    ):
        raise MailError("Draft attachment state does not match the saved handle")
    expected = sorted(
        (a.reference.name, a.reference.datatype) for a in draft.attachments
    )
    actual = sorted((p.filename or "", p.content_type) for p in parts)
    if actual != expected:
        raise MailError("Draft attachment metadata changed; refresh before writing")


@dataclass(frozen=True)
class DraftWriter:
    """Keep identity/body validation separate from authenticated HTTP transport."""

    senders: Callable[[], list[MailSender]]
    folder: Callable[[MailFolder | None], MailFolder]
    lookup: Callable[[MailFolder, int], MailMessage | None]
    body: Callable[[MailMessage], MailBody]
    submit: Callable[[DraftCreateRequest], DraftSaveResponse]
    client_build: str
    service_root: str
    account_id: str

    def checked(self, draft: MailDraft) -> MailMessage:
        """Verify the exact current draft and refuse destructive full replacements."""
        if not isinstance(draft, MailDraft):
            raise ValueError("A saved MailDraft handle is required")
        folder = self.folder(draft.message.folder)
        current = self.lookup(folder, draft.message.uid)
        if current is None or current.id != draft.message.id:
            raise MailError("Draft identity changed; refresh before writing")
        attachment_references(draft.attachments, self.service_root, self.account_id)
        checked_attachments(current, draft)
        if [p for p in current.parts if p.is_attachment] != [
            p for p in draft.message.parts if p.is_attachment
        ]:
            raise MailError("Draft attachment parts changed; refresh before writing")
        body = self.body(current)
        if body.guid != f"message:Drafts/{current.uid}":
            raise MailError("Draft body belongs to a different identity")
        headers = parse_headers(body.headers)
        checked_recipients(body, draft)
        sender_headers = headers.get_all("From", [])
        if (
            len(sender_headers) != 1
            or header_sender(str(sender_headers[0])).casefold()
            != draft.sender.address.casefold()
        ):
            raise MailError("Draft sender changed; refresh before writing")
        if (
            header_sender(current.sender or "").casefold()
            != draft.sender.address.casefold()
        ):
            raise MailError("Draft sender metadata does not match")
        return current

    def save(
        self,
        sender: str,
        subject: str,
        text: str,
        html: str,
        draft: MailDraft | None,
        *,
        to: tuple[str, ...] = (),
        cc: tuple[str, ...] = (),
        bcc: tuple[str, ...] = (),
        attachments: tuple[MailDraftAttachment, ...] = (),
    ) -> MailDraft:
        """Save explicit content and return verified replacement identities."""
        self._inputs(sender, subject, text, html)
        for values in (to, cc, bcc):
            recipient_addresses(values)
        references = attachment_references(
            attachments, self.service_root, self.account_id
        )
        # Apple treats HTML as canonical and discards textBody when HTML is
        # empty. Preserve plain-only input as escaped text, never raw markup.
        if text and not html:
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            html = (
                "<div>"
                + escape(normalized, quote=False).replace("\n", "<br>")
                + "</div>"
            )
        if not isinstance(self.client_build, str) or not self.client_build.strip():
            raise ValueError("A discovered client mastering number is required")
        enabled = [
            s for s in self.senders() if s.address.casefold() == sender.casefold()
        ]
        if len(enabled) != 1:
            raise MailError("The selected sender is not enabled for this account")
        verified = enabled[0]
        previous = None if draft is None else self.checked(draft)
        folder = self.folder(None if draft is None else draft.message.folder)
        common = {
            "date": formatdate(localtime=False),
            "from": str(
                Address(display_name=verified.display_name, addr_spec=verified.address)
            ),
            "subject": subject,
            "textBody": text,
            "htmlBody": html,
            "webmailClientBuild": self.client_build,
            "to": list(to),
            "cc": list(cc),
            "bcc": list(bcc),
            "attachments": [r.model_dump(by_alias=True) for r in references],
        }
        request = (
            DraftCreateRequest.model_validate(common)
            if previous is None
            else DraftEditRequest.model_validate({
                **common,
                "draftGuid": f"Drafts/{previous.uid}",
            })
        )
        response = self.submit(request)
        return self._saved_draft(
            response, previous, folder, subject, verified, to, cc, bcc, attachments
        )

    def _saved_draft(
        self,
        response: DraftSaveResponse,
        previous: MailMessage | None,
        folder: MailFolder,
        subject: str,
        verified: MailSender,
        to: tuple[str, ...],
        cc: tuple[str, ...],
        bcc: tuple[str, ...],
        attachments: tuple[MailDraftAttachment, ...],
    ) -> MailDraft:
        """Keep every post-dispatch verification failure explicitly uncertain."""
        try:
            remapped = self._remap_attachments(attachments, response)
            uid = int(response.uid)
            if previous is not None and uid == previous.uid:
                raise MailError("Draft save did not rotate the UID")
            current_folder = self.folder(folder)
            current = self.lookup(current_folder, uid)
            if current is None or (current.subject or "") != subject:
                raise MailError("Saved draft could not be identified")
            if (
                header_sender(current.sender or "").casefold()
                != verified.address.casefold()
            ):
                raise MailError("Saved draft sender does not match")
            if (
                previous is not None
                and self.lookup(current_folder, previous.uid) is not None
            ):
                raise MailError("The previous draft UID is still present")
            saved = MailDraft(
                message=current,
                sender=verified,
                to=to,
                cc=cc,
                bcc=bcc,
                attachments=remapped,
            )
            checked_attachments(current, saved)
            if to or cc or bcc:
                body = self.body(current)
                if body.guid != f"message:Drafts/{current.uid}":
                    raise MailError("Saved draft body belongs to a different identity")
                checked_recipients(body, saved)
            return saved
        except (PyiCloudException, OSError, ValueError) as exc:
            raise MailWriteUncertain(DRAFT_UNCERTAIN) from exc

    def _remap_attachments(
        self, attachments: tuple[MailDraftAttachment, ...], response: DraftSaveResponse
    ) -> tuple[MailDraftAttachment, ...]:
        """Require a complete one-to-one mapping before returning reusable handles."""
        mappings = response.attachments or []
        if len(mappings) != len(attachments) or len({
            m.request_guid for m in mappings
        }) != len(mappings):
            raise MailError("Draft attachment mappings are incomplete or unexpected")
        result = []
        for attachment in attachments:
            reference = attachment.reference
            matches = [m for m in mappings if m.request_guid == reference.guid]
            if len(matches) != 1 or not matches[0].new_guid:
                raise MailError("Draft attachment mapping has no verified new identity")
            mapping = matches[0]
            fields = reference.model_dump(by_alias=True)
            fields.update({
                "guid": mapping.new_guid,
                "contentID": mapping.content_id or mapping.new_guid,
                "url": self.service_root.rstrip("/")
                + "/mailws2/v1/message/part?"
                + urlencode({
                    "guid": mapping.new_guid,
                    "code": "base64",
                    "name": reference.name,
                    "type": reference.datatype,
                }),
            })
            result.append(
                MailDraftAttachment(
                    account_id=self.account_id,
                    service_root=self.service_root,
                    reference=type(reference).model_validate(fields),
                )
            )
        attachment_references(tuple(result), self.service_root, self.account_id)
        return tuple(result)

    @staticmethod
    def _inputs(sender: str, subject: str, text: str, html: str) -> None:
        """Reject malformed caller data before reads or any write dispatch."""
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError("An explicit enabled sender address is required")
        for value in (sender, subject, text, html):
            if not isinstance(value, str):
                raise ValueError("Draft sender, subject and bodies must be strings")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Draft data must be valid Unicode") from exc
        if any(ord(c) < 32 or ord(c) == 127 for c in sender + subject):
            raise ValueError("Draft sender and subject cannot contain controls")
