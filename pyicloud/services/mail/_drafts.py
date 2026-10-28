"""Guarded draft saves with rotating UIDs and no replay after submission."""
# cspell:words headersonly

from collections.abc import Callable
from dataclasses import dataclass
from email.headerregistry import Address
from email.utils import formatdate
from html import escape

from pyicloud.exceptions import PyiCloudException

from ._draft_headers import parse_headers
from ._draft_models import DraftCreateRequest, DraftEditRequest, DraftSaveResponse
from .exceptions import MailError, MailWriteUncertain
from .models import MailBody, MailDraft, MailFolder, MailMessage, MailSender

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


@dataclass(frozen=True)
class DraftWriter:
    """Keep identity/body validation separate from authenticated HTTP transport."""

    senders: Callable[[], list[MailSender]]
    folder: Callable[[MailFolder | None], MailFolder]
    lookup: Callable[[MailFolder, int], MailMessage | None]
    body: Callable[[MailMessage], MailBody]
    submit: Callable[[DraftCreateRequest], DraftSaveResponse]
    client_build: str

    def checked(self, draft: MailDraft) -> MailMessage:
        """Verify the exact current draft and refuse destructive full replacements."""
        if not isinstance(draft, MailDraft):
            raise ValueError("A saved MailDraft handle is required")
        folder = self.folder(draft.message.folder)
        current = self.lookup(folder, draft.message.uid)
        if current is None or current.id != draft.message.id:
            raise MailError("Draft identity changed; refresh before writing")
        if current.has_attachment is not False or any(
            p.is_attachment for p in current.parts
        ):
            raise MailError(
                "Drafts with attachments are not supported by this operation"
            )
        body = self.body(current)
        if body.guid != f"message:Drafts/{current.uid}":
            raise MailError("Draft body belongs to a different identity")
        headers = parse_headers(body.headers)
        # The replacement carries only From/To/Cc/Bcc/subject/bodies, so refuse
        # every address-bearing header it would otherwise silently erase.
        if headers.defects or any(
            value.strip()
            for name in (
                "To",
                "Cc",
                "Bcc",
                "Reply-To",
                "Sender",
                "Return-Receipt-To",
                "Disposition-Notification-To",
                "Resent-To",
                "Resent-Cc",
                "Resent-Bcc",
                "Resent-From",
                "Resent-Sender",
                "Resent-Reply-To",
            )
            for value in headers.get_all(name, [])
        ):
            raise MailError(
                "Only recipient-free drafts are supported by this operation"
            )
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
        self, sender: str, subject: str, text: str, html: str, draft: MailDraft | None
    ) -> MailDraft:
        """Save a full recipient-free replacement and return the new UID handle."""
        self._inputs(sender, subject, text, html)
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
        return self._saved_draft(response, previous, folder, subject, verified)

    def _saved_draft(
        self,
        response: DraftSaveResponse,
        previous: MailMessage | None,
        folder: MailFolder,
        subject: str,
        verified: MailSender,
    ) -> MailDraft:
        """Keep every post-dispatch verification failure explicitly uncertain."""
        try:
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
            return MailDraft(message=current, sender=verified)
        except (PyiCloudException, OSError, ValueError) as exc:
            raise MailWriteUncertain(DRAFT_UNCERTAIN) from exc

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
