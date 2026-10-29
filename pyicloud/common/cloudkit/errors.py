"""Helpers for mapping session-raised transport errors onto service errors.

``PyiCloudSession`` raises on any non-OK response before the CloudKit HTTP
layer can inspect it, so service clients translating shared CloudKit errors
also see raw ``PyiCloudAPIResponseException`` instances. These helpers read
the HTTP status and ``Retry-After`` header back off such an exception so each
service can map it the same way the HTTP layer would have.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math

from pyicloud.exceptions import PyiCloudAPIResponseException

UNAUTHORIZED_STATUSES = (401, 403)
RATE_LIMITED_STATUS = 429


def parse_retry_after(header: str | None) -> float | None:
    """Read a ``Retry-After`` value as seconds to wait, if it parses.

    RFC 9110 allows either a delay in seconds (``"120"``) or an HTTP-date
    (``"Wed, 21 Oct 2015 07:28:00 GMT"``). Apple sends the former, but the date
    form must not be dropped silently: the header is the only thing telling a
    rate-limited caller exactly when it may retry.
    """

    if not header:
        return None
    try:
        seconds = float(header)
    except (TypeError, ValueError):
        pass
    else:
        # RFC 9110 delay-seconds is 1*DIGIT, so a negative or non-finite value
        # is malformed. Surfacing it would hand callers a wait they cannot use.
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    try:
        retry_at = parsedate_to_datetime(header)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def retry_after_of(exc: PyiCloudAPIResponseException) -> float | None:
    """Return the ``Retry-After`` Apple sent with a rate limit, if any.

    The session attaches the response to the exception, so the header survives
    the trip. Dropping it would leave callers backing off on a guess when Apple
    has told them exactly how long to wait.
    """

    response = exc.response
    if response is None:
        return None
    return parse_retry_after(response.headers.get("Retry-After"))


def status_of(exc: PyiCloudAPIResponseException) -> int | None:
    """Return the exception's status as an int, whichever form it arrived in.

    ``PyiCloudAPIResponseException.code`` is typed ``int | str | None``, and a
    string ``"401"`` compared against the integer statuses would silently fall
    through to a generic error instead of an auth one.
    """

    try:
        return int(exc.code) if exc.code is not None else None
    except (TypeError, ValueError):
        return None
