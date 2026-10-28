"""Strict source-header extraction and saved-draft threading checks."""
# cspell:words headersonly

from email import policy
from email.message import Message
from email.parser import Parser
import re
from typing import Literal

from pydantic import TypeAdapter

from ._thread_models import (
    MESSAGE_IDENTIFIER_PATTERN,
    MailDraftThreading,
    MessageIdentifier,
)
from .exceptions import MailError
from .models import MailBody

_IDENTIFIER = re.compile(MESSAGE_IDENTIFIER_PATTERN)


def _headers(body: MailBody) -> Message:
    """Treat source and draft headers as untrusted data, not transport commands."""
    if len(body.headers) > 100_000:
        raise MailError("Mail threading headers exceed the supported size")
    result = Parser(policy=policy.default).parsestr(body.headers, headersonly=True)
    if result.defects:
        raise MailError("Mail threading headers are malformed")
    return result


def _identifiers(headers: Message, name: str) -> tuple[str, ...]:
    """Accept one bounded standard header; never silently truncate a chain."""
    values = headers.get_all(name, [])
    if not values:
        return ()
    if len(values) != 1:
        raise MailError("Duplicate Mail threading headers")
    value = str(values[0])
    if not value.strip():
        return ()
    ids = tuple(_IDENTIFIER.findall(value))
    if not ids or _IDENTIFIER.sub("", value).strip() or len(ids) > 100:
        raise MailError("Unsupported or excessive Mail threading identifiers")
    try:
        return tuple(TypeAdapter(MessageIdentifier).validate_python(v) for v in ids)
    except ValueError as exc:
        raise MailError("Invalid Mail threading identifier") from exc


def source_threading(
    body: MailBody, mode: Literal["reply", "forward"]
) -> MailDraftThreading:
    """Append the exact source Message-ID without inferring any recipient."""
    headers = _headers(body)
    parent = _identifiers(headers, "Message-ID")
    if len(parent) != 1:
        raise MailError("A single source Message-ID is required")
    references = _identifiers(headers, "References")
    chain = tuple(dict.fromkeys(v for v in references if v != parent[0])) + parent
    try:
        return MailDraftThreading(
            mode=mode,
            references=chain,
            in_reply_to=parent if mode == "reply" else (),
        )
    except ValueError as exc:
        raise MailError("Mail threading chain cannot be preserved") from exc


def checked_threading(body: MailBody, expected: MailDraftThreading | None) -> None:
    """An edit/send must not silently drop or replace reply/forward headers."""
    if expected is not None:
        expected = MailDraftThreading.model_validate(expected.model_dump())
    headers = _headers(body)
    references = _identifiers(headers, "References")
    reply = _identifiers(headers, "In-Reply-To")
    if references != (() if expected is None else expected.references) or reply != (
        () if expected is None else expected.in_reply_to
    ):
        raise MailError("Draft threading changed; refresh before writing")
