"""Private date-search paging candidate; no live adapter or public API.

Boundary completeness is deliberately a source obligation, not inferred from a
short response. Only a closed-world test source currently supplies that evidence.
This module must not be wired to Mail HTTP responses until that is verified.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .models import MailFolder, MailMessage, MailMessageRecord
from .service import MailError, _bind_message

_BUCKET_LIMIT = 1000
_MAX_TIMESTAMP_MS = 2**53 - 2


@dataclass(frozen=True)
class _SearchWindow:
    """One date-ordered query retaining the bound search and folder predicates.

    A boundary query uses strict gt(timestamp - 1) and lt(timestamp + 1).
    Ordinary subsequent pages use strict lt(before_ms). Values are integer
    milliseconds; unknown precision must never be rounded into a boundary.
    """

    limit: int
    before_ms: int | None = None
    boundary_ms: int | None = None


@dataclass(frozen=True)
class _SearchPage:
    """Validated records plus explicit source evidence for an entire bucket.

    boundary_exhausted must not be derived from response length, a missing
    marker, or a repeated identical response. No live implementation supplies it.
    """

    records: tuple[MailMessageRecord, ...]
    boundary_exhausted: bool = False
    continuation_marker: str | None = None


class _SearchSource(Protocol):
    """Bound metadata-only source; real search HTTP support is intentionally absent."""

    def mailbox(self, folder_id: str) -> MailFolder | None:
        """Return the current folder identity, or None after disappearance."""
        ...

    def fetch(self, folder: MailFolder, window: _SearchWindow) -> _SearchPage:
        """Apply the same text, folder, non-deleted scope and date ordering."""
        ...


def _timestamp_ms(record: MailMessageRecord) -> int:
    """Require a finite, exactly representable millisecond boundary with headroom."""
    if record.internal_date is None:
        raise MailError("Search record has no date boundary")
    value = Decimal(str(record.internal_date)) * 1000
    if (
        not value.is_finite()
        or abs(value) > _MAX_TIMESTAMP_MS
        or value != value.to_integral_value()
    ):
        raise MailError("Search timestamp precision or range is unsupported")
    return int(value)


def _unique_records(
    records: tuple[MailMessageRecord, ...], folder: MailFolder
) -> list[MailMessageRecord]:
    """Reject conflicting identities or metadata before removing exact duplicates."""
    by_id: dict[str, MailMessageRecord] = {}
    by_uid: dict[int, str] = {}
    for record in records:
        if record.mailbox.id != folder.id:
            raise MailError("Search returned a message from another folder")
        _timestamp_ms(record)
        if record.id in by_id and by_id[record.id] != record:
            raise MailError("Search returned conflicting message metadata")
        if record.uid in by_uid and by_uid[record.uid] != record.id:
            raise MailError("Search returned conflicting UID identities")
        by_id[record.id] = record
        by_uid[record.uid] = record.id
    return list(by_id.values())


def _validate_scan_options(
    folder: MailFolder, page_size: int, max_requests: int
) -> None:
    """Reject unsafe limits and unbound namespaces before reading the source."""
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= _BUCKET_LIMIT
    ):
        raise ValueError("page_size must be an integer between 1 and 1000")
    if (
        isinstance(max_requests, bool)
        or not isinstance(max_requests, int)
        or max_requests < 1
    ):
        raise ValueError("max_requests must be a positive integer")
    if folder.uid_validity is None or folder.uid_validity < 1:
        raise ValueError("A positive mailbox UID validity is required")


def _ordinary_records(
    page: _SearchPage, folder: MailFolder, before: int | None
) -> list[MailMessageRecord]:
    """Validate complete raw page ordering and the strict prior-date boundary."""
    dates = [_timestamp_ms(record) for record in page.records]
    if dates != sorted(dates, reverse=True):
        raise MailError("Search did not return descending date order")
    if before is not None and any(date >= before for date in dates):
        raise MailError("Search did not honor the date boundary")
    return _unique_records(page.records, folder)


def _join_boundary(
    records: list[MailMessageRecord],
    bucket: _SearchPage,
    folder: MailFolder,
    boundary: int,
) -> list[MailMessageRecord]:
    """Require proof of a consistent, exact and unsaturated boundary bucket."""
    if len(bucket.records) == _BUCKET_LIMIT:
        raise MailError("Search boundary bucket reached the 1000-record ceiling")
    if any(_timestamp_ms(record) != boundary for record in bucket.records):
        raise MailError("Search returned a record outside the boundary bucket")
    if bucket.boundary_exhausted is not True:
        raise MailError("Search boundary completeness is unverified")
    expected = {record.id for record in records if _timestamp_ms(record) == boundary}
    if not expected <= {record.id for record in bucket.records}:
        raise MailError("Search boundary membership changed")
    return _unique_records((*records, *bucket.records), folder)


def _checked_folder(source: _SearchSource, folder: MailFolder) -> MailFolder:
    """Do not let a stale mailbox lend its UIDs to the current scan."""
    result = source.mailbox(folder.id)
    if (
        result is None
        or result.id != folder.id
        or result.uid_validity != folder.uid_validity
    ):
        raise MailError("Search mailbox identity changed")
    return result


def _iter_search_candidates(
    source: _SearchSource,
    folder: MailFolder,
    *,
    page_size: int = 50,
    max_requests: int = 10000,
) -> Iterator[MailMessage]:
    """Exercise date paging only with independently verified boundary buckets.

    Validate a page and its whole boundary bucket before yielding either. The
    request budget counts ordinary, boundary and terminal requests. Detectable
    mutations fail, but this is not an atomic snapshot: new matches above the
    cursor and disappearing older matches can remain invisible. Earlier yielded
    records cannot be retracted after a later failure. No bodies or writes occur.
    """
    _validate_scan_options(folder, page_size, max_requests)
    remaining = max_requests
    current = folder

    def fetch(window: _SearchWindow) -> _SearchPage:
        """Count every request and validate context before exposing its records."""
        nonlocal remaining, current
        if remaining == 0:
            raise MailError("Search request budget exhausted; scan is incomplete")
        current = _checked_folder(source, folder)
        remaining -= 1
        page = source.fetch(current, window)
        current = _checked_folder(source, folder)
        if len(page.records) > window.limit:
            raise MailError("Search exceeded the requested result limit")
        if page.continuation_marker is not None:
            raise MailError("Search requires unsupported continuation")
        return page

    seen_ids: set[str] = set()
    seen_uids: set[int] = set()
    before: int | None = None
    while True:
        page = fetch(_SearchWindow(limit=page_size, before_ms=before))
        records = _ordinary_records(page, current, before)
        if not records:
            return
        boundary = _timestamp_ms(records[-1])
        bucket = fetch(_SearchWindow(limit=_BUCKET_LIMIT, boundary_ms=boundary))
        combined = _join_boundary(records, bucket, current, boundary)
        if any(record.id in seen_ids or record.uid in seen_uids for record in combined):
            raise MailError("Search reused an identity across date pages")
        seen_ids.update(record.id for record in combined)
        seen_uids.update(record.uid for record in combined)
        before = boundary
        for record in combined:
            yield _bind_message(record, current)
