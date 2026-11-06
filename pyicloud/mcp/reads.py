"""Bounded, minimized read adapters using only public PyiCloud service APIs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, time
import json
from secrets import token_urlsafe
import time as clock
from typing import Any, Literal

from pydantic import BaseModel, Field

from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailable,
)
from pyicloud.services.notes.exceptions import (
    NotesAuthError,
    NotesRateLimited,
    NotesZoneNotFoundError,
)
from pyicloud.services.photos.exceptions import (
    PhotosAuthError,
    PhotosRateLimited,
    PhotosZoneNotFoundError,
)
from pyicloud.services.reminders.exceptions import (
    RemindersAuthError,
    RemindersZoneNotFoundError,
)
from pyicloud.services.reminders.models import RemindersList

from .context import SessionOwner, ToolFailure


class ReadResult(BaseModel):
    """Explicit result envelope; errors never contain upstream data or tracebacks."""

    records: list[dict[str, Any]] = Field(default_factory=list)
    complete: bool = False
    error: str | None = None


def bounded_result(
    items: Iterable[dict[str, Any]], limit: int, *, complete: bool = True
) -> ReadResult:
    """Cap response records and serialized bytes without inventing cursors."""
    rows: list[dict[str, Any]] = []
    for item in items:
        if len(rows) >= limit:
            return ReadResult(records=rows, complete=False)
        if len(json.dumps(rows + [item], ensure_ascii=True).encode("utf-8")) > 250_000:
            return ReadResult(records=rows, complete=False)
        rows.append(item)
    return ReadResult(records=rows, complete=complete)


def calendar_text(value: Any) -> str:
    """Do not stringify nested or unknown Calendar fields into metadata output."""
    if not isinstance(value, str):
        raise ToolFailure("upstream_failure")
    return value


def calendar_time(value: Any) -> str | None:
    """Expose only a valid Apple wall-clock timestamp, never arbitrary nested data."""
    if not isinstance(value, (list, tuple)) or len(value) != 7:
        return None
    try:
        year, month, day, hour, minute = (int(part) for part in value[1:6])
        return datetime(year, month, day, hour, minute).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class ReadTools:
    """Session-local handles hide Apple zone owners and reject ambiguous IDs."""

    def __init__(self, owner: SessionOwner) -> None:
        self.owner = owner
        self._routes: dict[str, tuple[RemindersList, float]] = {}
        self.availability: dict[str, Literal["unknown", "available", "unavailable"]] = (
            dict.fromkeys(owner.config.services, "unknown")
        )

    def resolve_list(self, handle: str) -> RemindersList:
        """Resolve one existing route without accepting a caller-supplied owner/zone."""
        route = self._routes.get(handle)
        if route is None or route[1] <= clock.monotonic():
            raise ToolFailure("expired_or_unknown_handle")
        return route[0]

    async def execute(
        self, service: str, limit: int, operation: Callable[[Any], ReadResult]
    ) -> ReadResult:
        """Validate authorization before work; sanitize every upstream failure."""
        if service not in self.owner.config.services:
            return ReadResult(error="service_disabled")
        if not 1 <= limit <= 200:
            return ReadResult(error="invalid_argument")
        result = await self._read(operation)
        if result.error is None:
            self.availability[service] = "available"
        elif result.error == "service_unavailable":
            self.availability[service] = "unavailable"
        else:
            self.availability[service] = "unknown"
        return result

    async def _read(self, operation: Callable[[Any], ReadResult]) -> ReadResult:
        """Map service failures without leaking private upstream details."""
        try:
            return await self.owner.run(operation)
        except ToolFailure as error:
            return ReadResult(error=str(error))
        except (RemindersAuthError, NotesAuthError, PhotosAuthError):
            self.owner.authenticated = False
            return ReadResult(error="authentication_required")
        except (
            PyiCloudServiceNotActivatedException,
            PyiCloudServiceUnavailable,
            RemindersZoneNotFoundError,
            NotesZoneNotFoundError,
            PhotosZoneNotFoundError,
        ):
            return ReadResult(error="service_unavailable")
        except (NotesRateLimited, PhotosRateLimited):
            return ReadResult(error="rate_limited")
        except PyiCloudAPIResponseException as error:
            if str(error.code) in {"401", "403"}:
                self.owner.authenticated = False
                return ReadResult(error="authentication_required")
            if str(error.code) == "429":
                return ReadResult(error="rate_limited")
            return ReadResult(error="upstream_failure")
        except Exception:  # No untrusted Apple exceptions escape into SDK logs/results.
            return ReadResult(error="upstream_failure")

    async def calendars(self, limit: int) -> ReadResult:
        """List minimal calendar identity/title metadata, not sharing details."""

        def read(client: Any) -> ReadResult:
            return bounded_result(
                (
                    {
                        "id": calendar_text(row["guid"]),
                        "title": calendar_text(row.get("title", ""))[:4096],
                    }
                    for row in client.calendar.get_calendars()
                ),
                limit,
            )

        return await self.execute("calendar", limit, read)

    async def events(
        self, calendar_id: str, start: str, end: str, limit: int
    ) -> ReadResult:
        """Read a selected calendar in an explicit inclusive wall-date interval."""
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if (
                not calendar_id
                or len(calendar_id) > 512
                or not 0 <= (last - first).days < 31
                or last == date.max
            ):
                return ReadResult(error="invalid_argument")
        except ValueError:
            return ReadResult(error="invalid_argument")

        def read(client: Any) -> ReadResult:
            rows = client.calendar.get_events(
                from_dt=datetime.combine(first, time()),
                # The public Calendar API treats the end date as inclusive.
                to_dt=datetime.combine(last, time()),
            )
            return bounded_result(
                (
                    {
                        "id": calendar_text(row["guid"]),
                        "calendar_id": calendar_id,
                        "title": calendar_text(row.get("title", ""))[:4096],
                        "start": calendar_time(row.get("startDate")),
                        "end": calendar_time(row.get("endDate")),
                    }
                    for row in rows
                    if row.get("pGuid") == calendar_id
                ),
                limit,
            )

        return await self.execute("calendar", limit, read)

    async def lists(self, limit: int) -> ReadResult:
        """Return opaque, expiring list contexts; refresh invalidates old handles."""

        def read(client: Any) -> ReadResult:
            self._routes.clear()
            records: list[dict[str, Any]] = []
            iterator = iter(client.reminders.lists())
            try:
                for item in iterator:
                    if item.deleted:
                        continue
                    handle = token_urlsafe(24)
                    self._routes[handle] = (item, clock.monotonic() + 600)
                    records.append({
                        "handle": handle,
                        "title": item.title[:4096],
                        "scope": item.scope,
                    })
                    if len(records) >= limit:
                        break
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
            # The service deliberately suppresses zone-local availability errors.
            # Public list enumeration cannot prove all shares were readable.
            return bounded_result(records, limit, complete=False)

        return await self.execute("reminders", limit, read)

    async def reminders(
        self, handle: str, include_completed: bool, limit: int
    ) -> ReadResult:
        """Read one precise private/shared list model, never a guessed string route."""

        def read(client: Any) -> ReadResult:
            route = self._routes.get(handle)
            if route is None or route[1] <= clock.monotonic():
                raise ToolFailure("expired_or_unknown_handle")
            snapshot = client.reminders.list_reminders(
                list_id=route[0],
                include_completed=include_completed,
                results_limit=limit + 1,
            )
            return bounded_result(
                (
                    {
                        "id": item.id,
                        "title": item.title[:4096],
                        "completed": item.completed,
                        "due_date": item.due_date.isoformat()
                        if item.due_date
                        else None,
                    }
                    for item in snapshot.reminders
                    if not item.deleted
                ),
                limit,
            )

        return await self.execute("reminders", limit, read)
