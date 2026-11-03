"""Narrow runtime write gateway with independent private operator receipts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .approval_channel import ApprovalMailbox
from .authorization import ApprovalLedger, WriteDenied
from .context import ToolFailure
from .reads import ReadTools
from .reminder_writes import ReminderWrites


class WriteResult(BaseModel):
    """Fixed error categories and safe previews, never raw mutation acknowledgements."""

    preview: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class WriteGateway:
    """No model-callable approval path; every apply requires a private receipt."""

    def __init__(self, reads: ReadTools) -> None:
        self.config = reads.owner.config
        self.mailbox = ApprovalMailbox(self.config.approval_dir or "")
        reads.owner.add_cleanup(self.mailbox.close)
        self.ledger = ApprovalLedger(frozenset(self.config.write_actions))
        self.writer = ReminderWrites(reads, self.ledger)

    async def prepare(
        self,
        action: str,
        list_handle: str,
        payload: dict[str, Any],
        reminder_id: str | None,
    ) -> WriteResult:
        """Preparation publishes an exact preview but performs no mutation."""
        if action not in self.config.write_actions:
            return WriteResult(error="write_disabled")
        try:
            preview = await self.writer.prepare(
                action, list_handle, payload, reminder_id, publish=self.mailbox.publish
            )
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
            result = await self.writer.apply(request_id, accept=accept)
            return WriteResult(result=result)
        except (WriteDenied, ToolFailure) as error:
            return WriteResult(error=str(error))
        except Exception:
            return WriteResult(error="write_failed")
