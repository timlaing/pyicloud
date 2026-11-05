"""Verified Reminders write adapters; not registered as live MCP tools yet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from pyicloud.services.reminders.models import Reminder, RemindersList

from .additional import window
from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .reads import ReadTools

REMINDERS_CREATE = "reminders.create"
REMINDERS_DELETE = "reminders.delete"

REMINDER_ACTIONS = frozenset({
    REMINDERS_CREATE,
    "reminders.update",
    REMINDERS_DELETE,
})


def valid_payload_text(value: Any) -> bool:
    """Reject malformed Unicode and enforce the existing UTF-8 byte budget."""
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode("utf-8")) <= 32768
    except UnicodeEncodeError:
        return False


@dataclass(frozen=True)
class ReminderTarget:
    """Private route snapshot; changing a public preview cannot retarget a write."""

    context: RemindersList
    reminder_id: str | None


def route_identity(record: Any) -> tuple[str, str, str | None]:
    """Scope and both zone fields are needed to distinguish colliding IDs."""
    return record.scope, record.zone_name, record.zone_owner


class ReminderWrites:
    """Prepare only reads; execute requires a trusted approval and fresh revision."""

    def __init__(self, reads: ReadTools, approvals: ApprovalLedger) -> None:
        self.reads = reads
        self.approvals = approvals
        self._targets: dict[str, ReminderTarget] = {}

    def _fresh(
        self, client: Any, target: ReminderTarget
    ) -> tuple[RemindersList, Reminder | None]:
        context = None
        with window(client.reminders.lists(), 1000, live_only=True) as items:
            for candidate in items:
                if (
                    candidate.id == target.context.id
                    and not candidate.deleted
                    and route_identity(candidate) == route_identity(target.context)
                ):
                    context = candidate
                    break
        if context is None:
            raise WriteDenied("target_unavailable")
        if target.reminder_id is None:
            return context, None
        reminder = client.reminders.get(target.reminder_id, list_context=context)
        if (
            reminder.list_id != context.id
            or reminder.deleted
            or route_identity(reminder) != route_identity(context)
        ):
            raise WriteDenied("target_unavailable")
        return context, reminder

    def _bindings(
        self,
        client: Any,
        target: ReminderTarget,
        context: RemindersList,
        reminder: Reminder | None,
    ) -> tuple[str, str, str | None]:
        account = client.params.get("dsid")
        if account is None or isinstance(account, bool):
            raise WriteDenied("account_unavailable")
        route = client.reminders.service_root
        if not isinstance(route, str) or not route:
            raise WriteDenied("route_unavailable")
        resource = json.dumps(
            (route, context.id, *route_identity(context), target.reminder_id),
            separators=(",", ":"),
        )
        revision = (
            reminder.record_change_tag
            if reminder is not None
            else context.record_change_tag
        )
        return str(account), resource, revision

    def _payload(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            {"title", "description", "completed"}
            if action != REMINDERS_DELETE
            else set()
        )
        if set(payload) - allowed:
            raise WriteDenied("invalid_payload")
        if action == REMINDERS_CREATE and not payload.get("title"):
            raise WriteDenied("invalid_payload")
        if action == "reminders.update" and not payload:
            raise WriteDenied("invalid_payload")
        for key, value in payload.items():
            if key == "completed":
                if not isinstance(value, bool):
                    raise WriteDenied("invalid_payload")
            elif not valid_payload_text(value):
                raise WriteDenied("invalid_payload")
        return dict(payload)

    async def prepare(
        self,
        action: str,
        list_handle: str,
        payload: dict[str, Any],
        reminder_id: str | None = None,
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Capture exact route and revision; never call create/update/delete here."""
        if (
            action not in REMINDER_ACTIONS
            or "reminders" not in self.reads.owner.config.services
        ):
            raise WriteDenied("unsupported_action")
        if (action == REMINDERS_CREATE) != (reminder_id is None):
            raise WriteDenied("invalid_target")
        data = self._payload(action, payload)
        context = self.reads.resolve_list(list_handle).model_copy(deep=True)
        target = ReminderTarget(context, reminder_id)

        def prepare(client: Any) -> MutationPreview:
            fresh, reminder = self._fresh(client, target)
            account, resource, revision = self._bindings(
                client, target, fresh, reminder
            )
            result = self.approvals.preview(
                action,
                fresh.title,
                data,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            if len(self._targets) >= 256:
                del self._targets[next(iter(self._targets))]
            self._targets[result.request_id] = target
            if publish is not None:
                publish(result)
            return result

        return await self.reads.owner.run(prepare)

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Revalidate state, consume approval, then dispatch exactly once."""
        target = self._targets.get(request_id)
        if target is None:
            raise WriteDenied("unknown_or_consumed_preview")

        def apply(client: Any) -> dict[str, Any]:
            fresh, reminder = self._fresh(client, target)
            account, resource, revision = self._bindings(
                client, target, fresh, reminder
            )
            if accept is not None:
                accept(request_id)
            approved = self.approvals.consume(
                request_id,
                account_binding=account,
                resource_binding=resource,
                revision=revision,
            )
            del self._targets[request_id]
            try:
                return self._dispatch(client, fresh, reminder, approved)
            except Exception:
                raise WriteDenied("mutation_uncertain") from None

        return await self.reads.owner.run(apply)

    def _dispatch(
        self,
        client: Any,
        fresh: RemindersList,
        reminder: Reminder | None,
        approved: MutationPreview,
    ) -> dict[str, Any]:
        """Only dispatch performs writes, after approval has been consumed."""
        data = approved.payload
        if approved.action == REMINDERS_CREATE:
            created = client.reminders.create(
                fresh,
                data["title"],
                desc=data.get("description", ""),
                completed=data.get("completed", False),
            )
            return {"id": created.id, "action": approved.action}
        if reminder is None:
            raise WriteDenied("target_unavailable")
        if approved.action == REMINDERS_DELETE:
            client.reminders.delete(reminder)
        else:
            if "title" in data:
                reminder.title = data["title"]
            if "description" in data:
                reminder.desc = data["description"]
            if "completed" in data:
                reminder.completed = data["completed"]
            client.reminders.update(reminder)
        return {"id": reminder.id, "action": approved.action}
