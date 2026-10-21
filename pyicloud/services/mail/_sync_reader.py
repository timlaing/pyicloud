"""Private read-only adapter for checked Mail inventories and change polling.

No automatic history reset, mutation, push subscription, or public CLI is added.
Each operation pins account, mailbox, path, and UID validity on both sides of
the read. Tokens are returned only after the response is validated in full.
"""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
import json
from typing import Annotated

from pydantic import Field, ValidationError

from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudException
from pyicloud.services.mail._sync import (
    ChangeBatch,
    Identity,
    Number,
    SyncCheckpoint,
    SyncHistoryExpired,
    SyncModel,
    SyncResetRequired,
    decode_events,
    validate_batch,
)
from pyicloud.services.mail._sync_state import InventoryMessage
from pyicloud.services.mail.models import MailFolder, MailMessage
from pyicloud.services.mail.service import DEFAULT_TIMEOUT, MailService

Capability = Annotated[int, Field(ge=1, le=1)]
EventString = Annotated[str, Field(max_length=8192)]


class _Headers(SyncModel):
    folder: Identity
    modseq: Number | None
    threadmodseq: Number | None
    condstore: Capability
    qresync: Capability
    threadmode: Capability


class _PollRequest(SyncModel):
    session_headers: _Headers = Field(alias="sessionHeaders")


class _PollResponse(_PollRequest):
    events: Annotated[list[EventString], Field(max_length=1024)]


def _expired(data: object) -> bool:
    return isinstance(data, dict) and data.get("errorCode") == "MODSEQ_TOO_OLD"


class MailSyncReader:
    """Pin a verified service/folder and expose only bounded read operations."""

    def __init__(
        self,
        service: MailService,
        folder: MailFolder,
        *,
        page_size: int = 50,
        max_pages: int = 10000,
    ) -> None:
        if (
            isinstance(folder.uid_validity, bool)
            or not isinstance(folder.uid_validity, int)
            or folder.uid_validity < 1
            or isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 1000
            or isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 1
        ):
            raise ValueError(
                "Mail sync requires a verified namespace and positive paging budgets"
            )
        assert folder.uid_validity is not None
        self.service, self.folder = service, folder
        self.uid_validity = folder.uid_validity
        self.page_size, self.max_pages = page_size, max_pages
        self.account = self._account_scope()

    def _account_scope(self) -> str:
        account = self.service.session.service.account_name
        dsid = self.service.params.get("dsid")
        if (
            not isinstance(account, str)
            or not account.strip()
            or type(dsid) not in (str, int)
            or not str(dsid).strip()
        ):
            raise SyncResetRequired("Mail sync has no verified account identity")
        return sha256(json.dumps([account.casefold(), str(dsid)]).encode()).hexdigest()

    def _guard(self) -> None:
        if self._account_scope() != self.account:
            raise SyncResetRequired("Mail sync account changed")
        matches = [item for item in self.service.folders() if item.id == self.folder.id]
        if (
            len(matches) != 1
            or matches[0].name != self.folder.name
            or matches[0].uid_validity != self.folder.uid_validity
        ):
            raise SyncResetRequired("Mail sync folder changed; refresh its identity")

    def _checkpoint(self, modseq: int, threadmodseq: int) -> SyncCheckpoint:
        return SyncCheckpoint(
            account=self.account,
            mailbox_id=self.folder.id,
            folder=self.folder.name,
            uid_validity=self.uid_validity,
            modseq=modseq,
            threadmodseq=threadmodseq,
        )

    def _poll(self, previous: SyncCheckpoint | None) -> _PollResponse:
        self._guard()
        if previous is not None and previous != self._checkpoint(
            previous.modseq, previous.threadmodseq
        ):
            raise SyncResetRequired("Mail poll checkpoint belongs to another scope")
        request = _PollRequest(
            sessionHeaders=_Headers(
                folder=self.folder.name,
                modseq=None if previous is None else previous.modseq,
                threadmodseq=None if previous is None else previous.threadmodseq,
                condstore=1,
                qresync=1,
                threadmode=1,
            )
        )
        try:
            response = self.service.session.post(
                f"{self.service.service_root}/mailws2/v1/event/list",
                params=self.service.params,
                json=request.model_dump(by_alias=True),
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except PyiCloudException as error:
            failed_response = getattr(error, "response", None)
            if failed_response is not None:
                try:
                    try:
                        expired = isinstance(
                            error, PyiCloudAPIResponseException
                        ) and _expired(failed_response.json())
                    except ValueError:
                        expired = False
                finally:
                    failed_response.close()
                if expired:
                    raise SyncHistoryExpired(
                        "Mail change history expired; rebuild explicitly"
                    ) from None
            raise
        try:
            try:
                data = response.json()
            except ValueError:
                raise SyncResetRequired("Mail poll returned invalid JSON") from None
            if _expired(data):
                raise SyncHistoryExpired(
                    "Mail change history expired; rebuild explicitly"
                )
            if response.status_code != 200:
                raise SyncResetRequired("Mail change poll failed; checkpoint retained")
            try:
                result = _PollResponse.model_validate(data)
            except ValidationError:
                raise SyncResetRequired(
                    "Mail poll returned an unsupported envelope"
                ) from None
            headers = result.session_headers
            if (
                headers.folder != self.folder.name
                or headers.modseq is None
                or headers.threadmodseq is None
            ):
                raise SyncResetRequired(
                    "Mail poll returned an invalid folder or counter"
                )
            self._guard()
            return result
        finally:
            response.close()

    def current_checkpoint(self) -> SyncCheckpoint:
        """Read a fresh observation for baseline bracketing, never for delta reset."""
        response = self._poll(None)
        decode_events(response.events)
        headers = response.session_headers
        assert headers.modseq is not None and headers.threadmodseq is not None
        return self._checkpoint(headers.modseq, headers.threadmodseq)

    def poll_changes(self, previous: SyncCheckpoint) -> ChangeBatch:
        """Replay exactly the supplied counters; expired history never retries null."""
        response = self._poll(previous)
        headers = response.session_headers
        assert headers.modseq is not None and headers.threadmodseq is not None
        return validate_batch(
            previous,
            self._checkpoint(headers.modseq, headers.threadmodseq),
            response.events,
        )

    def _record(self, message: MailMessage) -> InventoryMessage:
        if (
            message.mailbox.id != self.folder.id
            or message.folder.id != self.folder.id
            or message.folder.name != self.folder.name
            or message.folder.uid_validity != self.folder.uid_validity
        ):
            raise SyncResetRequired("Mail metadata belongs to another namespace")
        if message.flags is None:
            raise SyncResetRequired("Mail returned incomplete inventory metadata")
        try:
            return InventoryMessage(
                uid=message.uid,
                message_id=message.id,
                thread_id=message.thread_id,
                flags=message.flags,
            )
        except ValidationError:
            raise SyncResetRequired(
                "Mail returned invalid inventory metadata"
            ) from None

    def iter_inventory(self) -> Iterator[InventoryMessage]:
        """Exhaust checked, unfiltered UID metadata without bodies or attachments."""
        self._guard()
        for message in self.service.iter_messages(
            self.folder, page_size=self.page_size, max_pages=self.max_pages
        ):
            yield self._record(message)
        self._guard()

    def iter_thread_inventory(self, thread_id: str) -> Iterator[InventoryMessage]:
        """Exhaust a changed thread; never treat a bounded thread page as complete."""
        self._guard()
        for message in self.service.iter_thread_messages(
            self.folder, thread_id, page_size=self.page_size, max_pages=self.max_pages
        ):
            if message.thread_id != thread_id:
                raise SyncResetRequired("Mail returned another thread's metadata")
            yield self._record(message)
        self._guard()

    def message_by_uid(self, uid: int) -> InventoryMessage | None:
        """Refresh authoritative numeric flags and metadata for one exact UID."""
        self._guard()
        message = self.service.message_by_uid(self.folder, uid)
        self._guard()
        if message is None:
            return None
        record = self._record(message)
        if record.uid != uid:
            raise SyncResetRequired("Mail returned metadata for a different UID")
        return record
