"""Verified non-recurring Calendar create/delete, not guessed update/upsert APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import uuid4

from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.calendar import EventObject

from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .reads import ReadTools

CALENDAR_ACTIONS = frozenset({"calendar.create_event", "calendar.delete_event"})


@dataclass(frozen=True)
class CalendarTarget:
    """Pair calendar and event identities; creation uses a fixed server GUID."""

    calendar_id: str
    event_id: str
    action: str


def event_from_payload(target: CalendarTarget, payload: dict[str, Any]) -> EventObject:
    """Only explicit wall-clock datetimes and timezone; no invitees or attachments."""
    if set(payload) != {"title", "start", "end", "timezone"}:
        raise WriteDenied("invalid_payload")
    if not all(isinstance(value, str) for value in payload.values()):
        raise WriteDenied("invalid_payload")
    if (
        not payload["title"].strip()
        or len(payload["title"].encode("utf-8")) > 4096
        or not payload["timezone"].strip()
    ):
        raise WriteDenied("invalid_payload")
    try:
        first, last = (
            datetime.fromisoformat(payload["start"]),
            datetime.fromisoformat(payload["end"]),
        )
        if first.tzinfo is not None or last.tzinfo is not None:
            raise WriteDenied("invalid_payload")
        return EventObject(
            pguid=target.calendar_id,
            guid=target.event_id,
            title=payload["title"],
            start_date=first,
            end_date=last,
            tz=payload["timezone"],
        )
    except ValueError:
        raise WriteDenied("invalid_payload") from None


class CalendarWrites:
    """All previews are read-only and execution requires exact single-use approval."""

    def __init__(self, reads: ReadTools, ledger: ApprovalLedger) -> None:
        self.reads, self.ledger = reads, ledger
        self._targets: dict[str, CalendarTarget] = {}

    def _fresh(
        self, client: Any, target: CalendarTarget
    ) -> tuple[str, EventObject | None]:
        calendars = client.calendar.get_calendars()
        if not any(item.get("guid") == target.calendar_id for item in calendars):
            raise WriteDenied("target_unavailable")
        if target.action == "calendar.create_event":
            return client.calendar.get_ctag(target.calendar_id), None
        event = client.calendar.get_event_detail(
            target.calendar_id, target.event_id, as_obj=True
        )
        if not isinstance(event, EventObject) or (event.pguid, event.guid) != (
            target.calendar_id,
            target.event_id,
        ):
            raise WriteDenied("target_unavailable")
        if (
            event.invitees
            or event.recurrence_master
            or event.recurrence_exception
            or event.change_recurring
            or event.has_attachments
        ):
            raise WriteDenied("unsupported_event")
        return event.etag or "", event

    def _bindings(
        self, client: Any, target: CalendarTarget, revision: str
    ) -> tuple[str, str, str]:
        account = client.params.get("dsid")
        if account is None or isinstance(account, bool):
            raise WriteDenied("account_unavailable")
        return (
            str(account),
            json.dumps((target.calendar_id, target.event_id), separators=(",", ":")),
            revision,
        )

    async def prepare(
        self,
        action: str,
        calendar_id: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Capture exact event identity, fields and current calendar/event version."""
        if (
            action not in CALENDAR_ACTIONS
            or "calendar" not in self.reads.owner.config.services
        ):
            raise WriteDenied("unsupported_action")
        if (
            not calendar_id
            or len(calendar_id) > 512
            or (action == "calendar.delete_event" and not event_id)
        ):
            raise WriteDenied("invalid_target")
        target = CalendarTarget(calendar_id, event_id or str(uuid4()).upper(), action)
        if action == "calendar.create_event":
            if event_id is not None:
                raise WriteDenied("invalid_target")
            event_from_payload(target, payload)
        elif payload:
            raise WriteDenied("invalid_payload")

        def prepare(client: Any) -> MutationPreview:
            revision, event = self._fresh(client, target)
            account, resource, version = self._bindings(client, target, revision)
            data = dict(payload)
            data["event_id"] = target.event_id
            preview = self.ledger.preview(
                action,
                event.title if event else calendar_id,
                data,
                account_binding=account,
                resource_binding=resource,
                revision=version,
            )
            if len(self._targets) >= 256:
                del self._targets[next(iter(self._targets))]
            self._targets[preview.request_id] = target
            if publish is not None:
                publish(preview)
            return preview

        return await self.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Retire approval before dispatch; never retry uncertain outcomes."""
        target = self._targets.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            revision, event = self._fresh(client, target)
            account, resource, version = self._bindings(client, target, revision)
            if accept is not None:
                accept(request_id)
            approved = self.ledger.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=version,
            )
            del self._targets[request_id]
            try:
                if target.action == "calendar.create_event":
                    data = approved.payload
                    data.pop("event_id")
                    requested = event_from_payload(target, data)
                    client.calendar.add_event(requested)
                    result = client.calendar.get_event_detail(
                        target.calendar_id, target.event_id, as_obj=True
                    )
                    if not isinstance(result, EventObject) or (
                        result.pguid,
                        result.guid,
                        result.title,
                        result.start_date,
                        result.end_date,
                        result.tz,
                    ) != (
                        requested.pguid,
                        requested.guid,
                        requested.title,
                        requested.start_date,
                        requested.end_date,
                        requested.tz,
                    ):
                        raise WriteDenied("mutation_unverified")
                else:
                    client.calendar.remove_event(event)
                    self._verify_deleted(client, target)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None
            return {
                "action": target.action,
                "calendar_id": target.calendar_id,
                "event_id": target.event_id,
            }

        return await self.reads.owner.run(apply)

    def _verify_deleted(self, client: Any, target: CalendarTarget) -> None:
        try:
            client.calendar.get_event_detail(
                target.calendar_id, target.event_id, as_obj=True
            )
        except PyiCloudAPIResponseException as error:
            if str(error.code) in {"404", "410"}:
                return
            raise
        raise WriteDenied("mutation_unverified")
