"""Fail-closed body verification foundation; no Mail write tools enabled yet."""

from __future__ import annotations

from html import escape

from pyicloud.services.mail.models import MailBody

from .authorization import WriteDenied


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
