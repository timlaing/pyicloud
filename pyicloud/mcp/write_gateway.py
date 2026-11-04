"""Narrow runtime write gateway with independent private operator receipts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel

from .additional import AdditionalReads
from .approval_channel import ApprovalMailbox
from .authorization import ApprovalLedger, MutationPreview, WriteDenied
from .calendar_writes import CalendarWrites
from .context import ToolFailure
from .draft_edits import DraftEdits
from .draft_sends import DraftSends
from .drive_acknowledged import DriveAcknowledged
from .drive_deletions import DriveDeletions
from .drive_moves import DriveMoves
from .drive_uploads import DriveUploads
from .drive_writes import DriveWrites
from .mail_acknowledged import MailAcknowledged
from .mail_deletions import MailDeletions
from .mail_folder_renames import MailFolderRenames
from .mail_folders import MailFolderWrites
from .mail_writes import MailWrites
from .photo_album_creates import PhotoAlbumCreates
from .photo_album_writes import PhotoAlbumWrites
from .photo_relations import PhotoRelations
from .photo_uploads import PhotoUploads
from .photo_writes import PhotoWrites
from .reads import ReadTools
from .reminder_writes import ReminderWrites
from .upload_staging import UploadStaging

PHOTOS_UPLOAD_FILE = "photos.upload_file"
DRIVE_UPLOAD_FILE = "drive.upload_file"
MAIL_CREATE_FOLDER = "mail.create_folder"


class TargetPreparer(Protocol):
    """Shared discovered-target preview contract."""

    async def __call__(
        self,
        handle: str,
        payload: dict[str, Any],
        publish: Callable[[MutationPreview], None] | None = None,
    ) -> MutationPreview:
        """Publish only a preview requiring independent approval."""
        ...


class WriteAdapter(Protocol):
    """Common receipt-gated execution contract shared by existing writers."""

    async def apply(
        self, request_id: str, accept: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Revalidate and consume authorization before dispatch."""
        ...


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
        self.deletions = DriveDeletions(self.drive)
        self.moves = DriveMoves(self.drive)
        self.drive_acknowledged = DriveAcknowledged(self.drive)
        self.calendar = CalendarWrites(reads, self.ledger)
        self.mail = MailWrites(reads, self.ledger, self.drive.additional)
        self.draft_edits = DraftEdits(self.mail)
        self.draft_sends = DraftSends(self.mail)
        self.mail_deletions = MailDeletions(self.mail)
        self.mail_acknowledged = MailAcknowledged(self.mail)
        self.mail_folders = MailFolderWrites(reads, self.ledger)
        self.mail_renames = MailFolderRenames(self.drive.additional, self.ledger)
        self.photos = PhotoWrites(self.drive.additional, self.ledger)
        self.photo_albums = PhotoAlbumWrites(self.drive.additional, self.ledger)
        self.photo_creates = PhotoAlbumCreates(self.photo_albums)
        self.upload_staging = (
            UploadStaging()
            if {PHOTOS_UPLOAD_FILE, DRIVE_UPLOAD_FILE} & set(self.config.write_actions)
            else None
        )
        if self.upload_staging is not None:
            reads.owner.add_cleanup(self.upload_staging.close)
        self.drive_uploads = (
            DriveUploads(
                self.drive,
                self.upload_staging,
                frozenset(self.config.upload_transfer_hosts),
            )
            if DRIVE_UPLOAD_FILE in self.config.write_actions
            and self.upload_staging is not None
            else None
        )
        self.photo_uploads = (
            PhotoUploads(
                self.photo_creates,
                self.upload_staging,
                frozenset(self.config.upload_transfer_hosts),
            )
            if PHOTOS_UPLOAD_FILE in self.config.write_actions
            and self.upload_staging is not None
            else None
        )
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
            handle = target_handle or list_handle
            if action in {PHOTOS_UPLOAD_FILE, DRIVE_UPLOAD_FILE}:
                preview = await self._prepare_upload(action, handle, payload)
            elif action in {"drive.create_folder", "drive.recover_item"}:
                preview = await self._prepare_drive_acknowledged(
                    action, handle, payload, destination_handle
                )
            elif action == "drive.move_file":
                preview = await self.moves.prepare_move(
                    handle,
                    destination_handle or "",
                    payload,
                    publish=self.mailbox.publish,
                )
            elif action == "photos.add_to_album":
                preview = await self.photo_relations.prepare_add(
                    handle,
                    destination_handle or "",
                    payload,
                    publish=self.mailbox.publish,
                )
            elif action in self._target_preparers():
                preview = await self._prepare_target(action, handle, payload)
            elif action in {
                MAIL_CREATE_FOLDER,
                "mail.set_read",
                "mail.set_flagged",
                "mail.move_message",
            }:
                preview = await self._prepare_mail_metadata(
                    action, handle, payload, destination_handle
                )
            else:
                preview = await self._prepare_basic(
                    action,
                    handle,
                    payload,
                    (list_handle, reminder_id, calendar_id, event_id),
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

    async def _prepare_drive_acknowledged(
        self, action: str, handle: str, payload: dict[str, Any], destination: str | None
    ) -> MutationPreview:
        """Reject destination overrides before Drive create/recover previews."""
        if destination is not None:
            raise WriteDenied("invalid_payload")
        return await self.drive_acknowledged.prepare_acknowledged(
            action, handle, payload, publish=self.mailbox.publish
        )

    async def _prepare_mail_metadata(
        self, action: str, handle: str, payload: dict[str, Any], destination: str | None
    ) -> MutationPreview:
        """Preserve folder creation and source/destination Mail metadata routing."""
        if action == MAIL_CREATE_FOLDER:
            return await self.mail_folders.prepare(
                payload, publish=self.mailbox.publish
            )
        return await self.mail_acknowledged.prepare(
            action, handle, payload, destination, publish=self.mailbox.publish
        )

    async def _prepare_basic(
        self,
        action: str,
        handle: str,
        payload: dict[str, Any],
        selectors: tuple[str, str | None, str | None, str | None],
    ) -> MutationPreview:
        """Keep original Mail/Calendar/Drive routing and Reminders fallback."""
        list_handle, reminder_id, calendar_id, event_id = selectors
        if action == "mail.create_draft":
            return await self.mail.prepare(payload, publish=self.mailbox.publish)
        if action.startswith("calendar."):
            return await self.calendar.prepare(
                action,
                calendar_id or "",
                payload,
                event_id,
                publish=self.mailbox.publish,
            )
        if action in {"drive.rename", "drive.trash_file"}:
            return await self.drive.prepare(
                handle, payload, publish=self.mailbox.publish, action=action
            )
        return await self.writer.prepare(
            action, list_handle, payload, reminder_id, publish=self.mailbox.publish
        )

    async def _prepare_upload(
        self, action: str, handle: str, payload: dict[str, Any]
    ) -> MutationPreview:
        """Validate the selected upload adapter and exact immutable-stage inputs."""
        upload = (
            self.photo_uploads if action == PHOTOS_UPLOAD_FILE else self.drive_uploads
        )
        if upload is None:
            raise WriteDenied("write_disabled")
        if set(payload) != {"stage_handle", "modified_ms"} or not isinstance(
            payload["stage_handle"], str
        ):
            raise WriteDenied("invalid_payload")
        return await upload.prepare_upload(
            handle,
            payload["stage_handle"],
            payload["modified_ms"],
            publish=self.mailbox.publish,
        )

    def _target_preparers(self) -> dict[str, TargetPreparer]:
        """Exact existing actions sharing a discovered-target preparation signature."""
        return {
            "drive.delete_forever_file": self.deletions.prepare_delete,
            "photos.create_album": self.photo_creates.prepare_create,
            "photos.rename_album": self.photo_albums.prepare,
            "photos.set_favorite": self.photos.prepare,
            "mail.rename_folder": self.mail_renames.prepare_rename,
            "mail.delete_forever_message": self.mail_deletions.prepare_delete,
            "mail.send_draft": self.draft_sends.prepare_send,
            "mail.edit_draft": self.draft_edits.prepare_edit,
        }

    async def _prepare_target(
        self, action: str, handle: str, payload: dict[str, Any]
    ) -> MutationPreview:
        """Delegate exact targets without caller-selected methods or approval inputs."""
        result: MutationPreview = await self._target_preparers()[action](
            handle, payload, publish=self.mailbox.publish
        )
        return result

    async def stage(self, operation: str, payload: dict[str, Any]) -> WriteResult:
        """Serialize bounded local staging; no URL, path or approval input accepted."""
        store = self.upload_staging
        if store is None:
            return WriteResult(error="write_disabled")

        def stage(_client: Any) -> dict[str, Any]:
            if operation == "begin" and set(payload) == {"name", "size", "sha256"}:
                token = store.begin(payload["name"], payload["size"], payload["sha256"])
                return {"stage_handle": token, **store.describe(token)}
            if operation == "append" and set(payload) == {
                "stage_handle",
                "offset",
                "base64",
            }:
                return {
                    "offset": store.append(
                        payload["stage_handle"], payload["offset"], payload["base64"]
                    )
                }
            if set(payload) == {"stage_handle"} and isinstance(
                payload["stage_handle"], str
            ):
                token = payload["stage_handle"]
                return self._stage_token(store, operation, token)
            raise WriteDenied("invalid_payload")

        try:
            return WriteResult(
                result=await self.drive.additional.reads.owner.run(stage)
            )
        except (WriteDenied, ToolFailure) as error:
            return WriteResult(error=str(error))
        except Exception:
            return WriteResult(error="staging_failed")

    @staticmethod
    def _stage_token(
        store: UploadStaging, operation: str, token: str
    ) -> dict[str, Any]:
        """Operate only on the already validated single opaque stage selector."""
        if operation == "seal":
            store.seal(token)
            return store.describe(token)
        if operation == "describe":
            return store.describe(token)
        if operation == "discard":
            store.discard(token)
            return {"discarded": True}
        raise WriteDenied("invalid_payload")

    async def apply(self, request_id: str) -> WriteResult:
        """Revalidate and consume the trusted receipt/ledger on the serialized lane."""

        def accept(identifier: str) -> None:
            self.mailbox.accept(identifier, self.ledger)

        try:
            action = self._actions.get(request_id, "")
            adapter: WriteAdapter
            if action in {PHOTOS_UPLOAD_FILE, DRIVE_UPLOAD_FILE}:
                upload = (
                    self.photo_uploads
                    if action == PHOTOS_UPLOAD_FILE
                    else self.drive_uploads
                )
                if upload is None:
                    raise WriteDenied("write_disabled")
                adapter = upload
            else:
                adapter = self._apply_adapter(action)
            result = await adapter.apply(request_id, accept=accept)
            self._actions.pop(request_id, None)
            return WriteResult(result=result)
        except (WriteDenied, ToolFailure) as error:
            return WriteResult(error=str(error))
        except Exception:
            return WriteResult(error="write_failed")

    def _apply_adapter(self, action: str) -> WriteAdapter:
        """Keep exact action routing; unknown actions retain the Reminders fallback."""
        adapters: dict[str, WriteAdapter] = {
            "drive.create_folder": self.drive_acknowledged,
            "drive.recover_item": self.drive_acknowledged,
            "drive.delete_forever_file": self.deletions,
            "drive.move_file": self.moves,
            "photos.add_to_album": self.photo_relations,
            "photos.create_album": self.photo_creates,
            "photos.rename_album": self.photo_albums,
            "photos.set_favorite": self.photos,
            "mail.rename_folder": self.mail_renames,
            MAIL_CREATE_FOLDER: self.mail_folders,
            "mail.set_read": self.mail_acknowledged,
            "mail.set_flagged": self.mail_acknowledged,
            "mail.move_message": self.mail_acknowledged,
            "mail.delete_forever_message": self.mail_deletions,
            "mail.send_draft": self.draft_sends,
            "mail.edit_draft": self.draft_edits,
            "mail.create_draft": self.mail,
            "drive.rename": self.drive,
            "drive.trash_file": self.drive,
        }
        if action in adapters:
            return adapters[action]
        return self.calendar if action.startswith("calendar.") else self.writer
