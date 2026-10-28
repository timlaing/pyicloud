"""Keep lazy email-header parser failures inside Mail's error boundary."""
# cspell:words headersonly

from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import Parser

from .exceptions import MailError


def parse_headers(value: str) -> EmailMessage:
    """Force lazy parsing, including malformed-input bugs on older Python releases."""
    try:
        headers = Parser(policy=policy.default).parsestr(value, headersonly=True)
        tuple(headers.items())
    except (IndexError, ValueError, MessageError):
        raise MailError("Mail returned malformed draft headers") from None
    return headers
