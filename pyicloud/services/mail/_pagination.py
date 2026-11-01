"""Private Mail query construction and whole-page pagination validation.

Transport, mailbox epoch checks, iterator timing and session ownership stay on
the public facade. These helpers only prepare queries or validate fetched data.
"""

from __future__ import annotations

from typing import Any, Literal

from .exceptions import MailError
from .models import (
    MailExpression,
    MailFolder,
    MailMessageRecord,
    MailOrder,
    MailPredicate,
    MailQuery,
    MailThreadFilter,
    MailThreadQuery,
    ThreadQueryResponse,
)

MAX_RESULTS = 1000
_RESULT_LIMIT_EXCEEDED = "Mail exceeded the requested result limit"
_UID_VALIDITY_REQUIRED = "A positive mailbox UID validity is required; refresh folders"
SearchField = Literal["all", "subject", "from", "to", "cc", "body"]
_HEADER_PROPERTIES: list[str | dict[str, Any]] = [
    "uid",
    "identifier",
    "mboxRef",
    "appleThrid",
    "flags",
    "stateInternalDate",
    "rfc822Size",
    "hasAttachment",
    "parts",
    "previewId",
    *[
        {
            "type": "fieldOf",
            "property": "rfc822HeaderBytes",
            "value": field,
            "alias": field.lower(),
            "decoder": "encodedWords",
        }
        for field in ("From", "To", "Cc", "Subject")
    ],
]


def _limit(value: int) -> None:
    """Reject limits outside the supported bounded-query range."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_RESULTS
    ):
        raise ValueError(f"limit must be an integer between 1 and {MAX_RESULTS}")


def _folder_predicate(folder: MailFolder) -> MailPredicate:
    """Restrict a query to non-deleted messages in the requested folder."""
    return MailPredicate(
        type="eq",
        expression=MailExpression(property="mboxRef"),
        value=folder.id,
        and_=[
            MailPredicate(
                type="eq",
                value=False,
                expression=MailExpression(
                    type="fieldOf", property="flags", value="DELETED"
                ),
            )
        ],
    )


def _validate_scan(folder: MailFolder, page_size: int, max_pages: int) -> None:
    """Validate a scan before checking mailbox identity or reading metadata."""
    _limit(page_size)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if folder.uid_validity is None or folder.uid_validity < 1:
        raise ValueError(_UID_VALIDITY_REQUIRED)


def _filtered_thread_query(
    folder: MailFolder, filter_by: MailThreadFilter, page_size: int
) -> MailThreadQuery:
    """Use observed thread timestamps and projected boolean flag predicates."""
    timestamp = MailExpression(property="tsMap", type="mapValueOf", value=folder.id)
    return MailThreadQuery(
        predicate=MailPredicate(
            expression=timestamp,
            type="ge",
            value=0,
            and_=[
                MailPredicate(
                    expression=MailExpression(
                        property="flags",
                        type="fieldOf",
                        value="SEEN" if filter_by == "unread" else "FLAGGED",
                    ),
                    type="eq",
                    value=filter_by == "flagged",
                )
            ],
        ),
        orderby=MailOrder(expressions=[timestamp], ascending=False),
        limit=page_size,
    )


def _check_thread_page(
    response: ThreadQueryResponse,
    folder: MailFolder,
    page_size: int,
    seen: set[str],
    markers: set[str],
) -> None:
    """Validate a cursor page before exposing any of its group identities."""
    records = response.records
    if len(records) > page_size:
        raise MailError(_RESULT_LIMIT_EXCEEDED)
    if any(folder.id not in record.timestamps for record in records):
        raise MailError("Mail returned a thread from another folder")
    ids = {record.id for record in records}
    if len(ids) != len(records) or ids.intersection(seen):
        raise MailError("Mail repeated a thread identifier; enumeration is incomplete")
    marker = response.continuation_marker
    if marker is not None and (not records or marker in markers):
        raise MailError("Mail returned a non-progressing thread cursor")


def _uid_query(
    folder: MailFolder, page_size: int, before: int | None, match: MailPredicate | None
) -> MailQuery:
    """Retain selected scope while adding a strict descending UID boundary."""
    predicate = _folder_predicate(folder)
    clauses = list(predicate.and_ or [])
    if match is not None:
        clauses.append(match)
    if before is not None:
        clauses.append(
            MailPredicate(
                type="lt",
                expression=MailExpression(type="property", property="uid"),
                value=before,
            )
        )
    return MailQuery(
        domain="email",
        properties=_HEADER_PROPERTIES,
        predicate=predicate.model_copy(update={"and_": clauses}),
        limit=page_size,
        orderby=MailOrder(
            expressions=[MailExpression(type="property", property="uid")]
        ),
    )


def _search_fields(text: str, field: SearchField, limit: int) -> tuple[str, ...]:
    """Validate a bounded text query before any request is sent."""
    _limit(limit)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Search text must not be empty")
    if field not in ("all", "subject", "from", "to", "cc", "body"):
        raise ValueError("Unsupported search field")
    return ("subject", "to", "from", "cc", "body") if field == "all" else (field,)


def _unique_uid_records(records: list[MailMessageRecord]) -> list[MailMessageRecord]:
    """Deduplicate exact identities and reject contradictory UID mappings."""
    by_uid: dict[int, MailMessageRecord] = {}
    identifiers: set[str] = set()
    for record in records:
        existing = by_uid.get(record.uid)
        if existing is not None:
            if existing.id != record.id:
                raise MailError("Mail returned conflicting identities for a UID")
            if (existing.flags, existing.thread_id) != (record.flags, record.thread_id):
                raise MailError("Mail returned conflicting metadata for a UID")
            continue
        if record.id in identifiers:
            raise MailError("Mail returned one message identifier for multiple UIDs")
        by_uid[record.uid] = record
        identifiers.add(record.id)
    return list(by_uid.values())


def _uid_page(
    records: list[MailMessageRecord], folder: MailFolder, before: int | None, size: int
) -> list[MailMessageRecord]:
    """Validate a whole UID page before any of its records can be yielded."""
    if len(records) > size:
        raise MailError(_RESULT_LIMIT_EXCEEDED)
    if any(record.mailbox.id != folder.id for record in records):
        raise MailError("Mail returned a message from another folder")
    if before is not None and any(record.uid >= before for record in records):
        raise MailError("Mail did not honor the UID paging boundary")
    unique = _unique_uid_records(records)
    if any(
        left.uid <= right.uid for left, right in zip(unique, unique[1:], strict=False)
    ):
        raise MailError("Mail did not return descending UID order")
    return unique
