"""Narrow runtime write gateway with independent private operator receipts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .additional import AdditionalReads
from .approval_channel import ApprovalMailbox
from .authorization import ApprovalLedger, WriteDenied
from .calendar_writes import CalendarWrites
from .context import ToolFailure
from .drive_writes import DriveWrites
from .mail_writes import MailWrites
from .reads import ReadTools
from .reminder_writes import ReminderWrites


class WriteResult(BaseModel):
    """Fixed error categories and safe previews, never raw mutation acknowledgements."""

    preview: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class WriteGateway:
    """No model-callable approval path; every apply requires a private receipt."""

    def __init__(
        self, reads: ReadTools, additional: AdditionalReads | None = None
    ) -> None:
        self.config = reads.owner.config
        self.mailbox = ApprovalMailbox(self.config.approval_dir or "")
        reads.owner.add_cleanup(self.mailbox.close)
        self.ledger = ApprovalLedger(frozenset(self.config.write_actions))
        self.writer = ReminderWrites(reads, self.ledger)
        self.drive = DriveWrites(additional or AdditionalReads(reads), self.ledger)
        self.calendar = CalendarWrites(reads, self.ledger)
        self.mail = MailWrites(reads, self.ledger)
        self._actions: dict[str, str] = {}

    async def prepare(
        self,
        action: str,
        list_handle: str,
        payload: dict[str, Any],
        reminder_id: str | None,
        target_handle: str | None = None,
        calendar_id: str | None = None,
        event_id: str | None = None,
    ) -> WriteResult:
        """Preparation publishes an exact preview but performs no mutation."""
        if action not in self.config.write_actions:
            return WriteResult(error="write_disabled")
        try:
            if action == "mail.create_draft":
                preview = await self.mail.prepare(payload, publish=self.mailbox.publish)
            elif action.startswith("calendar."):
                preview = await self.calendar.prepare(
                    action,
                    calendar_id or "",
                    payload,
                    event_id,
                    publish=self.mailbox.publish,
                )
            elif action == "drive.rename":
                preview = await self.drive.prepare(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            else:
                preview = await self.writer.prepare(
                    action,
                    list_handle,
                    payload,
                    reminder_id,
                    publish=self.mailbox.publish,
                )
            if len(self._actions) >= 256:
                del self._actions[next(iter(self._actions))]
            self._actions[preview.request_id] = action
            return WriteResult(
                preview={
                    "request_id": preview.request_id,
                    "action": preview.action,
                    "target": preview.target,
                    "digest": preview.digest,
                    "payload": preview.payload,
                    "approval": (
                        "Use the separate operator terminal; "
                        "no MCP confirmation grants approval."
                    ),
                }
            )
        except (WriteDenied, ToolFailure) as error:
            return WriteResult(error=str(error))
        except Exception:
            return WriteResult(error="preview_failed")

    async def apply(self, request_id: str) -> WriteResult:
        """Revalidate and consume the trusted receipt/ledger on the serialized lane."""

        def accept(identifier: str) -> None:
            self.mailbox.accept(identifier, self.ledger)

        try:
            action = self._actions.get(request_id, "")
            adapter: CalendarWrites | DriveWrites | ReminderWrites | MailWrites
            if action == "mail.create_draft":
                adapter = self.mail
            elif action.startswith("calendar."):
                adapter = self.calendar
            elif action == "drive.rename":
                adapter = self.drive
            else:
                adapter = self.writer
            result = await adapter.apply(request_id, accept=accept)
            self._actions.pop(request_id, None)
            return WriteResult(result=result)
        except (WriteDenied, ToolFailure) as error:
            return WriteResult(error=str(error))
        except Exception:
            return WriteResult(error="write_failed")
