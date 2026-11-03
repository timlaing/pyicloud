"""Narrow runtime write gateway with independent private operator receipts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .additional import AdditionalReads
from .approval_channel import ApprovalMailbox
from .authorization import ApprovalLedger, WriteDenied
from .calendar_writes import CalendarWrites
from .context import ToolFailure
from .draft_edits import DraftEdits
from .draft_sends import DraftSends
from .drive_moves import DriveMoves
from .drive_writes import DriveWrites
from .mail_folder_renames import MailFolderRenames
from .mail_folders import MailFolderWrites
from .mail_writes import MailWrites
from .photo_album_creates import PhotoAlbumCreates
from .photo_album_writes import PhotoAlbumWrites
from .photo_relations import PhotoRelations
from .photo_writes import PhotoWrites
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
        self.ledger = ApprovalLedger(
            frozenset(self.config.write_actions),
            high_risk_actions=frozenset(self.config.high_risk_actions),
        )
        self.writer = ReminderWrites(reads, self.ledger)
        self.drive = DriveWrites(additional or AdditionalReads(reads), self.ledger)
        self.moves = DriveMoves(self.drive)
        self.calendar = CalendarWrites(reads, self.ledger)
        self.mail = MailWrites(reads, self.ledger, self.drive.additional)
        self.draft_edits = DraftEdits(self.mail)
        self.draft_sends = DraftSends(self.mail)
        self.mail_folders = MailFolderWrites(reads, self.ledger)
        self.mail_renames = MailFolderRenames(self.drive.additional, self.ledger)
        self.photos = PhotoWrites(self.drive.additional, self.ledger)
        self.photo_albums = PhotoAlbumWrites(self.drive.additional, self.ledger)
        self.photo_creates = PhotoAlbumCreates(self.photo_albums)
        self.photo_relations = PhotoRelations(self.photo_albums)
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
        destination_handle: str | None = None,
    ) -> WriteResult:
        """Preparation publishes an exact preview but performs no mutation."""
        if action not in self.config.write_actions:
            return WriteResult(error="write_disabled")
        try:
            if action == "drive.move_file":
                preview = await self.moves.prepare_move(
                    target_handle or list_handle,
                    destination_handle or "",
                    payload,
                    publish=self.mailbox.publish,
                )
            elif action == "photos.add_to_album":
                preview = await self.photo_relations.prepare_add(
                    target_handle or list_handle,
                    destination_handle or "",
                    payload,
                    publish=self.mailbox.publish,
                )
            elif action == "photos.create_album":
                preview = await self.photo_creates.prepare_create(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "photos.rename_album":
                preview = await self.photo_albums.prepare(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "photos.set_favorite":
                preview = await self.photos.prepare(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "mail.rename_folder":
                preview = await self.mail_renames.prepare_rename(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "mail.create_folder":
                preview = await self.mail_folders.prepare(
                    payload, publish=self.mailbox.publish
                )
            elif action == "mail.send_draft":
                preview = await self.draft_sends.prepare_send(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "mail.edit_draft":
                preview = await self.draft_edits.prepare_edit(
                    target_handle or list_handle, payload, publish=self.mailbox.publish
                )
            elif action == "mail.create_draft":
                preview = await self.mail.prepare(payload, publish=self.mailbox.publish)
            elif action.startswith("calendar."):
                preview = await self.calendar.prepare(
                    action,
                    calendar_id or "",
                    payload,
                    event_id,
                    publish=self.mailbox.publish,
                )
            elif action in {"drive.rename", "drive.trash_file"}:
                preview = await self.drive.prepare(
                    target_handle or list_handle,
                    payload,
                    publish=self.mailbox.publish,
                    action=action,
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
                    "risk": preview.risk,
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
            adapter: (
                CalendarWrites
                | DriveWrites
                | ReminderWrites
                | MailWrites
                | DraftEdits
                | DraftSends
                | PhotoWrites
                | PhotoAlbumWrites
                | MailFolderWrites
            )
            if action == "drive.move_file":
                adapter = self.moves
            elif action == "photos.add_to_album":
                adapter = self.photo_relations
            elif action == "photos.create_album":
                adapter = self.photo_creates
            elif action == "photos.rename_album":
                adapter = self.photo_albums
            elif action == "photos.set_favorite":
                adapter = self.photos
            elif action == "mail.rename_folder":
                adapter = self.mail_renames
            elif action == "mail.create_folder":
                adapter = self.mail_folders
            elif action == "mail.send_draft":
                adapter = self.draft_sends
            elif action == "mail.edit_draft":
                adapter = self.draft_edits
            elif action == "mail.create_draft":
                adapter = self.mail
            elif action.startswith("calendar."):
                adapter = self.calendar
            elif action in {"drive.rename", "drive.trash_file"}:
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
