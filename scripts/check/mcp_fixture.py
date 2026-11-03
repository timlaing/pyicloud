"""Synthetic configured stdio server for installed-package integration checks.

Never opens an Apple account or saved-state directory; outbound sockets are denied.
"""

from __future__ import annotations

import argparse
from collections.abc import Generator, Iterator
from datetime import datetime
import re
import socket
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from requests import Response

from pyicloud.common.cloudkit import CKRecord
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.mcp.context import SUPPORTED_SERVICES, ServerConfig, SessionOwner
from pyicloud.mcp.server import build_server
from pyicloud.services.calendar import EventObject
from pyicloud.services.drive import DriveNode
from pyicloud.services.mail.models import (
    MailBody,
    MailBodyPart,
    MailDraft,
    MailFolder,
    MailMessage,
    MailSearchHit,
    MailSender,
)
from pyicloud.services.photos.constants import (
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
)
from pyicloud.services.photos.queries import parent_filter
from pyicloud.services.photos.service import (
    AlbumContainer,
    PhotoAlbum,
    PhotoAlbumRecordState,
    PhotoAsset,
    PhotoLibrary,
)
from pyicloud.services.reminders.models import Reminder, RemindersList


class SyntheticCalendar:
    """Only bounded deterministic calendar data, including fields to omit."""

    service_root = "https://calendar.example.test"

    def __init__(self) -> None:
        self.deleted = False
        self.event = EventObject(
            pguid="C",
            guid="E",
            title="Synthetic event",
            start_date=datetime(2026, 1, 1, 12),
            end_date=datetime(2026, 1, 1, 13),
            tz="UTC",
            etag="E1",
        )

    def get_event_detail(
        self, calendar_id: str, event_id: str, *, as_obj: bool
    ) -> EventObject:
        """Read the exact authoritative fake identity, never an acknowledgement."""
        assert (calendar_id, event_id, as_obj) == ("C", "E", True)
        if self.deleted:
            raise PyiCloudAPIResponseException("Synthetic absence", 404)
        return self.event

    def remove_event(self, event: EventObject) -> dict[str, str]:
        """Only a fake mutation; exact captured identity/revision must be supplied."""
        assert (event.pguid, event.guid, event.etag) == ("C", "E", "E1")
        assert not self.deleted
        self.deleted = True
        print("MCP_SYNTHETIC_CALENDAR_DELETED", file=sys.stderr, flush=True)
        return {"ignored": "Not independent proof"}

    def get_calendars(self) -> list[dict[str, Any]]:
        """Return one calendar with a deliberately non-public payload field."""
        return [{"guid": "C", "title": "Synthetic calendar", "private": "OMIT_ME"}]

    def get_events(self, *, from_dt: datetime, to_dt: datetime) -> list[dict[str, Any]]:
        """The adapter must use the library's inclusive date-bound semantics."""
        assert from_dt == to_dt == datetime(2026, 1, 1)
        return [
            {
                "guid": "E",
                "pGuid": "C",
                "title": "Synthetic event",
                "startDate": [20260101, 2026, 1, 1, 12, 0, 720],
                "description": "OMIT_ME",
            },
            {"guid": "OTHER", "pGuid": "OTHER", "title": "Do not expose"},
        ]


class SyntheticReminders:
    """Two colliding list IDs that require explicit private/shared model routes."""

    service_root = "https://reminders.example.test"

    def lists(self) -> Iterator[RemindersList]:
        """Only route handles, titles and scopes should survive serialization."""
        yield RemindersList(id="List/SAME", title="Private", record_change_tag="L1")
        yield RemindersList(
            id="List/SAME",
            title="Shared",
            scope="shared",
            zone_name="Shared",
            zone_owner="OMIT_ME",
            record_change_tag="L1",
        )

    def list_reminders(
        self, *, list_id: RemindersList, include_completed: bool, results_limit: int
    ) -> SimpleNamespace:
        """Reject string fallback and verify the complete explicit route."""
        assert isinstance(list_id, RemindersList)
        assert include_completed is False
        assert results_limit == 51
        if list_id.scope == "shared":
            assert (list_id.zone_name, list_id.zone_owner) == ("Shared", "OMIT_ME")
        return SimpleNamespace(
            reminders=[
                Reminder(
                    id="Reminder/SAME",
                    list_id=list_id.id,
                    title=list_id.title,
                    scope=list_id.scope,
                    zone_name=list_id.zone_name,
                    zone_owner=list_id.zone_owner,
                    desc="OMIT_ME",
                    due_date=datetime(2026, 1, 1),
                )
            ]
        )

    def create(
        self, context: RemindersList, title: str, *, desc: str, completed: bool
    ) -> Reminder:
        """Only synthetic mutation; marker permits exact-once verification."""
        assert isinstance(context, RemindersList)
        assert title == "Approved synthetic mutation"
        assert desc == ""
        assert completed is False
        print("MCP_SYNTHETIC_MUTATED", file=sys.stderr, flush=True)
        return Reminder(
            id="Reminder/CREATED",
            list_id=context.id,
            title=title,
            scope=context.scope,
            zone_name=context.zone_name,
            zone_owner=context.zone_owner,
            record_change_tag="R1",
        )


def range_response(headers: dict[str, str]) -> Response:
    """Synthetic bytes with stable version identity; never open an outbound socket."""
    matched = re.fullmatch(r"bytes=(\d+)-(\d+)", headers.get("Range", ""))
    assert matched is not None
    first, last = map(int, matched.groups())
    assert 0 <= first <= last < 8
    if "If-Match" in headers:
        assert headers["If-Match"] == "synthetic-v1"
    response = Response()
    response.status_code = 206
    response.headers.update({
        "Content-Range": f"bytes {first}-{last}/8",
        "ETag": "synthetic-v1",
    })
    response._content = b"abcdefgh"[first : last + 1]  # pylint: disable=protected-access
    vars(response)["_content_consumed"] = True
    return response


class SyntheticFile:
    """Only a discovered synthetic remote file, never a local output path."""

    name = "Synthetic.txt"
    type = "file"
    size = 8
    date_modified = None

    def open(
        self, *, stream: bool, headers: dict[str, str], allow_redirects: bool
    ) -> Response:
        """The adapter must request a bounded range without following redirects."""
        assert stream is True
        assert allow_redirects is False
        return range_response(headers)


class SyntheticFolder:
    """Drive metadata only; no local paths or file-opening method."""

    type = "folder"

    def get_children(self, *, force: bool) -> list[SyntheticFile]:
        """Ensure the adapter refreshes metadata without requesting content."""
        assert force is True
        return [SyntheticFile()]


class SyntheticTrash:
    """Metadata only; no recovery/delete/content methods are present."""

    def get_children(self, *, force: bool) -> list[SimpleNamespace]:
        """Keep restoration paths private even when listing a discovered trash item."""
        assert force is True
        return [
            SimpleNamespace(
                name="Deleted.txt",
                type="file",
                date_modified=None,
                restorePath="OMIT_ME",
            )
        ]


class SyntheticWritableDrive:
    """Private synthetic metadata and mutation methods, no permanent deletion."""

    service_root = "https://drive.example.test"

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {
            identifier: {
                "drivewsid": identifier,
                "zone": "com.apple.CloudDocs",
                "type": "FILE",
                "name": name,
                "extension": "txt",
                "etag": "R1",
                "size": 8,
                "parentId": "FOLDER::com.apple.CloudDocs::root",
            }
            for identifier, name in (
                ("FILE::com.apple.CloudDocs::FIRST", "Synthetic"),
                ("FILE::com.apple.CloudDocs::SECOND", "Second"),
            )
        }
        for name in ("Destination", "Other"):
            identifier = f"FOLDER::com.apple.CloudDocs::{name}"
            self.records[identifier] = {
                "drivewsid": identifier,
                "zone": "com.apple.CloudDocs",
                "type": "FOLDER",
                "name": name,
                "etag": "D1",
                "parentId": "FOLDER::com.apple.CloudDocs::root",
            }
        self.move_dispatches = 0
        self.trash_dispatches = 0
        self.root = SimpleNamespace(type="folder", get_children=self.children)
        self.trash = SimpleNamespace(get_children=self.trashed)

    def children(self, *, force: bool) -> list[DriveNode]:
        """Fresh typed records discovered through ordinary metadata tools."""
        assert force is True
        return [
            DriveNode(cast(Any, self), dict(data))
            for data in self.records.values()
            if not data.get("restorePath")
            and data["parentId"] == "FOLDER::com.apple.CloudDocs::root"
        ]

    def trashed(self, *, force: bool) -> list[DriveNode]:
        """Only authoritative fake Recently Deleted membership proves trashing."""
        assert force is True
        return [
            DriveNode(cast(Any, self), dict(data))
            for data in self.records.values()
            if data.get("restorePath")
        ]

    def get_node_data(
        self, identifier: str, _share_id: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Do not reuse mutable local optimistic node data."""
        data = dict(self.records[identifier])
        if data["type"] == "FOLDER":
            data["items"] = [
                dict(child)
                for child in self.records.values()
                if child["parentId"] == identifier and not child.get("restorePath")
            ]
        return data

    def move_nodes_to_node(
        self, nodes: list[DriveNode], destination: DriveNode
    ) -> dict[str, str]:
        """Only first dispatch changes parent and authoritative destination state."""
        assert len(nodes) == 1
        identifier = nodes[0].data["drivewsid"]
        target = destination.data["drivewsid"]
        assert self.records[identifier]["etag"] == nodes[0].data["etag"]
        assert self.records[target]["etag"] == destination.data["etag"]
        self.move_dispatches += 1
        if self.move_dispatches == 1:
            self.records[identifier].update(parentId=target, etag="M2")
            self.records[target]["etag"] = "D2"
        print("MCP_SYNTHETIC_DRIVE_MOVED", file=sys.stderr, flush=True)
        return {"status": "OK"}

    def rename_items(self, identifier: str, etag: str, name: str) -> dict[str, str]:
        """Exactly one synthetic rename, with the freshly approved etag."""
        assert self.records[identifier]["etag"] == etag
        assert name == "Renamed"
        self.records[identifier].update(name=name, etag="R2")
        print("MCP_SYNTHETIC_DRIVE_RENAMED", file=sys.stderr, flush=True)
        return {"status": "OK"}

    def move_items_to_trash(self, identifier: str, etag: str) -> dict[str, str]:
        """Second dispatch acknowledges but does not change authoritative state."""
        assert self.records[identifier]["etag"] == etag
        self.trash_dispatches += 1
        if self.trash_dispatches == 1:
            self.records[identifier].update(restorePath="Renamed.txt", etag="R3")
        print("MCP_SYNTHETIC_DRIVE_TRASHED", file=sys.stderr, flush=True)
        return {"status": "OK"}


class SyntheticAsset:
    """Signed-resource metadata never leaves the server in tool output."""

    id = "P"
    filename = "Synthetic.jpg"
    created = datetime(2026, 1, 1)
    latitude = "OMIT_ME"

    def download_url(self, version: str) -> str:
        """Only the discovered original variant is part of this fixture."""
        assert version == "original"
        return "https://cvws.icloud-content.com/synthetic?signature=OMIT_ME"


class SyntheticAlbum:
    """Photo metadata iterator with a signed URL that must never be exposed."""

    title = "Synthetic album"

    def __iter__(self) -> Iterator[SyntheticAsset]:
        yield SyntheticAsset()


class SyntheticWritableAsset(PhotoAsset):
    """Typed private asset; mutation updates only synthetic authoritative records."""

    def __init__(self, album: SyntheticWritableAlbum) -> None:
        self.album = album
        master = CKRecord.model_validate({
            "recordName": "M",
            "recordType": "CPLMaster",
            "fields": {},
        })
        record = CKRecord.model_validate({
            "recordName": "P",
            "recordType": "CPLAsset",
            "recordChangeTag": "R1",
            "zoneID": {"zoneName": "Z"},
            "fields": {"isFavorite": {"type": "INT64", "value": album.favorite}},
        })
        super().__init__(cast(Any, SimpleNamespace()), master, record)

    @property
    def filename(self) -> str:
        """Keep read-protocol projections stable."""
        return "Synthetic.jpg"

    @property
    def created(self) -> datetime:
        """No filesystem or account timestamps."""
        return datetime(2026, 1, 1)

    def set_favorite(self, value: bool) -> bool:
        """Second dispatch deliberately acknowledges without changing state."""
        self.album.dispatches += 1
        if self.album.dispatches == 1:
            self.album.favorite = int(value)
        print("MCP_SYNTHETIC_PHOTO_MUTATED", file=sys.stderr, flush=True)
        return True


class SyntheticWritableAlbum:
    """Fresh instances prevent local optimistic state from masquerading as reads."""

    title = "Synthetic album"

    def __init__(self) -> None:
        self.favorite = 0
        self.dispatches = 0

    def __iter__(self) -> Iterator[SyntheticWritableAsset]:
        yield SyntheticWritableAsset(self)

    def get(self, identifier: str) -> SyntheticWritableAsset:
        """Fresh authoritative fake lookup, not the previously mutated object."""
        assert identifier == "P"
        return SyntheticWritableAsset(self)


class SyntheticNamedAlbum(PhotoAlbum):
    """Fresh typed album snapshot; no CloudKit method or endpoint is called."""

    def __init__(
        self,
        library: SyntheticPrivateLibrary,
        pending_name: str | None = None,
        *,
        identifier: str = "ALBUM",
        revision: str | None = None,
    ) -> None:
        self.fake_library = library
        super().__init__(
            cast(Any, library),
            name=pending_name or library.album_name,
            record_id=identifier,
            obj_type=ObjectTypeEnum.CONTAINER,
            list_type=ListTypeEnum.CONTAINER,
            direction=DirectionEnum.ASCENDING,
            zone_id=library.zone_id,
            query_filters=[parent_filter(identifier)],
            record=PhotoAlbumRecordState(
                record_change_tag=revision or library.album_tag
            ),
        )

    def __iter__(self) -> Generator[PhotoAsset]:
        """Preserve existing bounded metadata fixtures without transport."""
        yield SyntheticWritableAsset(
            cast(SyntheticWritableAlbum, self.fake_library.all)
        )

    def rename(self, value: str) -> None:
        """Second fake rename acknowledges without changing authoritative name."""
        assert value in {"Renamed album", "Uncertain album"}
        if value == "Renamed album":
            self.fake_library.album_name = value
            self.fake_library.album_tag = "A2"
        self._name = value
        self.fake_library.pending_name = value
        print("MCP_SYNTHETIC_ALBUM_RENAMED", file=sys.stderr, flush=True)

    def can_add_photo_correlated(self, photo: PhotoAsset) -> bool:
        """Synthetic capability only; real typed transport guards are unit qualified."""
        return (
            photo.id == "P"
            and photo.master_id == "M"
            and photo.asset_record.zoneID is not None
            and photo.asset_record.zoneID.zoneName == self.zone_id["zoneName"]
        )

    def contains_photo_scoped(self, photo: PhotoAsset) -> bool:
        """Fresh fake authoritative membership, never mutation acknowledgement data."""
        assert self.can_add_photo_correlated(photo)
        return (self.id, photo.id) in self.fake_library.relations

    def add_photo_correlated(self, photo: PhotoAsset) -> bool:
        """Only the first fake relation dispatch changes authoritative membership."""
        assert self.can_add_photo_correlated(photo)
        self.fake_library.relation_dispatches += 1
        if self.fake_library.relation_dispatches == 1:
            self.fake_library.relations.add((self.id, photo.id))
        print("MCP_SYNTHETIC_RELATION_ADDED", file=sys.stderr, flush=True)
        return True


class SyntheticPrivateLibrary(PhotoLibrary):
    """Fresh typed album-list reads distinct from optimistic mutation objects."""

    scope = "private"
    zone_id = {"zoneName": "Z"}

    def __init__(self, service: Any, all_photos: SyntheticWritableAlbum) -> None:
        super().__init__(
            service,
            zone_id={"zoneName": "Z"},
            client=cast(Any, SimpleNamespace()),
            check_indexing=False,
        )
        self.fake_all = all_photos
        self.album_name = "Synthetic album"
        self.album_tag = "A1"
        self.pending_name: str | None = None
        self.created_records: list[tuple[str, str]] = []
        self.pending_created: list[PhotoAlbum] = []
        self.relations: set[tuple[str, str]] = set()
        self.relation_dispatches = 0

    @property
    def all(self) -> PhotoAlbum:
        """Preserve the existing synthetic asset lookup without transport."""
        return cast(PhotoAlbum, self.fake_all)

    @property
    def correlated_album_creation_available(self) -> bool:
        """Only qualify fake dispatch; this is not a real transport capability claim."""
        return True

    def _created_snapshot(self, name: str, identifier: str) -> PhotoAlbum:
        """Construct a new typed snapshot, not a mutable pending cache reference."""
        return SyntheticNamedAlbum(self, name, identifier=identifier, revision="C1")

    def create_album_correlated(self, name: str) -> PhotoAlbum:
        """Second fake creation only populates pending local acknowledgement state."""
        assert name in {"Created album", "Uncertain creation"}
        identifier = "NEW1" if name == "Created album" else "NEW2"
        result = self._created_snapshot(name, identifier)
        self.pending_created.append(result)
        if name == "Created album":
            self.created_records.append((name, identifier))
        print("MCP_SYNTHETIC_ALBUM_CREATED", file=sys.stderr, flush=True)
        return result

    @property
    def albums(self) -> AlbumContainer:
        """Read-only discovered synthetic album handles."""
        return self.refresh_albums()

    def refresh_albums(self) -> AlbumContainer:
        """Include optimistic local names, which must never prove server visibility."""
        return AlbumContainer([
            SyntheticNamedAlbum(self, self.pending_name),
            *self.pending_created,
        ])

    def server_albums(self) -> AlbumContainer:
        """Authoritative fake observations, never a locally pending creation cache."""
        return AlbumContainer([
            SyntheticNamedAlbum(self),
            *[
                self._created_snapshot(name, identifier)
                for name, identifier in self.created_records
            ],
        ])


class SyntheticNotes:
    """Only recent summaries; no method can retrieve bodies or attachments."""

    def recents(self, *, limit: int) -> Iterator[SimpleNamespace]:
        """Bound the scan window and include a snippet to test minimization."""
        assert limit == 200
        yield SimpleNamespace(
            id="N",
            title="Synthetic note",
            modified_at=None,
            is_locked=False,
            is_deleted=False,
            snippet="OMIT_ME",
        )

    def get(self, identifier: str, *, with_attachments: bool) -> SimpleNamespace:
        """Only explicit plaintext; attachment retrieval remains disabled."""
        assert identifier == "N"
        assert with_attachments is False
        return SimpleNamespace(
            title="Synthetic note", text="Synthetic note body", is_locked=False
        )


class SyntheticMail:
    """Typed synthetic reads and draft saves; no sending or upload methods."""

    def __init__(self) -> None:
        self.folder = MailFolder(identifier="F", name="Inbox", uidValidity=1)
        self.service_root = "https://gateway.example.test"
        self.drafts = MailFolder(identifier="D", name="Drafts", uidValidity=2)
        self.sender = MailSender(address="synthetic@example.invalid")
        self.saved: list[MailDraft] = []
        self.created_folders: list[MailFolder] = []

    def create_folder(self, name: str) -> MailFolder:
        """Only the first fake create appears in authoritative namespace metadata."""
        assert name in {"Verified Folder", "Uncertain Folder"}
        result = MailFolder(identifier=name, name=name, uidValidity=3)
        if name == "Verified Folder":
            self.created_folders.append(result)
        print("MCP_SYNTHETIC_MAIL_FOLDER_CREATED", file=sys.stderr, flush=True)
        return result

    def rename_folder(self, folder: MailFolder, name: str) -> MailFolder:
        """Only the first synthetic rename changes authoritative namespace data."""
        assert folder == self.created_folders[0]
        assert name in {"Renamed Folder", "Uncertain Rename"}
        result = folder.model_copy(update={"name": name})
        if name == "Renamed Folder":
            self.created_folders[0] = result
        print("MCP_SYNTHETIC_MAIL_FOLDER_RENAMED", file=sys.stderr, flush=True)
        return result

    def draft_folder(self) -> MailFolder:
        """Return only the synthetic canonical selectable mailbox."""
        return self.drafts

    def senders(self) -> list[MailSender]:
        """Explicit discovered identity, never inferred from login."""
        return [self.sender]

    def create_draft(
        self,
        *,
        sender: str,
        subject: str,
        text: str,
        to: tuple[str, ...],
        cc: tuple[str, ...],
        bcc: tuple[str, ...],
    ) -> MailDraft:
        """Count synthetic dispatches without logging sender, recipients or body."""
        assert sender == self.sender.address
        assert subject in {"Approved", "Uncertain"}
        assert text == "Approved <text>\nsecond line"
        assert (to, cc, bcc) == (("recipient@example.invalid",), (), ())
        uid = 101 + len(self.saved)
        message = MailMessage.model_validate({
            "identifier": f"draft-{uid}",
            "uid": uid,
            "mboxRef": {"id": "D"},
            "folder": self.drafts,
            "subject": subject,
        })
        draft = MailDraft(message=message, sender=self.sender, to=to, cc=cc, bcc=bcc)
        self.saved.append(draft)
        print("MCP_SYNTHETIC_MAIL_SAVED", file=sys.stderr, flush=True)
        return draft

    def folders(self) -> list[MailFolder]:
        """Expose the one explicitly selectable mailbox."""
        return [self.folder, *self.created_folders]

    def check_draft(self, draft: MailDraft) -> MailMessage:
        """Read-only exact fake saved identity, never an ID-only reconstruction."""
        matches = [
            saved for saved in self.saved if saved.message.uid == draft.message.uid
        ]
        assert len(matches) == 1 and matches[0] == draft
        return matches[0].message

    def message_by_uid(self, folder: MailFolder, uid: int) -> MailMessage | None:
        """Independent old-UID absence comes from authoritative fake draft records."""
        assert folder == self.drafts
        return next(
            (saved.message for saved in self.saved if saved.message.uid == uid), None
        )

    def edit_draft(
        self,
        draft: MailDraft,
        *,
        subject: str,
        text: str,
        html: str,
        to: tuple[str, ...],
        cc: tuple[str, ...],
        bcc: tuple[str, ...],
        attachments: tuple[Any, ...],
    ) -> MailDraft:
        """Rotate one fake identity; second edit has unverified post-save body."""
        self.check_draft(draft)
        assert subject in {"Edited", "Uncertain edit"}
        assert text == "Edited <text>\nsecond line"
        assert html == "" and attachments == ()
        assert (to, cc, bcc) == (draft.to, draft.cc, draft.bcc)
        uid = max(saved.message.uid for saved in self.saved) + 1
        message = draft.message.model_copy(
            update={"id": f"draft-{uid}", "uid": uid, "subject": subject}
        )
        saved = draft.model_copy(update={"message": message})
        self.saved = [
            current
            for current in self.saved
            if current.message.uid != draft.message.uid
        ]
        self.saved.append(saved)
        print("MCP_SYNTHETIC_DRAFT_EDITED", file=sys.stderr, flush=True)
        return saved

    def messages(self, folder: MailFolder, *, limit: int) -> SimpleNamespace:
        """Use the discovered typed route, not caller-supplied identifiers."""
        assert folder is self.folder
        assert limit == 50
        message = MailMessage.model_validate({
            "identifier": "M",
            "uid": 1,
            "mboxRef": {"id": "F"},
            "folder": folder,
            "subject": "Synthetic message",
            "flags": 1,
            "from": "OMIT_ME",
        })
        return SimpleNamespace(messages=[message])

    def search(
        self, folder: MailFolder, query: str, *, field: str, limit: int
    ) -> SimpleNamespace:
        """Search subject headers only and never expose returned previews."""
        assert folder is self.folder
        assert (query, field, limit) == ("Synthetic", "subject", 50)
        return SimpleNamespace(
            results=[
                MailSearchHit(
                    identifier="T", subject="Synthetic message", preview="OMIT_ME"
                )
            ]
        )

    def message(self, folder: MailFolder, identifier: str) -> MailMessage:
        """Refresh one typed message scope before content retrieval."""
        assert identifier == "M"
        return cast(MailMessage, self.messages(folder, limit=50).messages[0])

    def body(self, message: MailMessage) -> SimpleNamespace | MailBody:
        """Headers remain private and HTML is returned only as untrusted data."""
        if message.folder == self.drafts:
            assert any(draft.message == message for draft in self.saved)
            # Literal expected wire content, not the verifier under test.
            content = "<div>Approved &lt;text&gt;<br>second line</div>"
            if message.subject == "Uncertain":
                content = "<div>Different content</div>"
            elif message.subject == "Edited":
                content = "<div>Edited &lt;text&gt;<br>second line</div>"
            elif message.subject == "Uncertain edit":
                content = "<div>Unverified edit content</div>"
            return MailBody(
                guid=f"message:Drafts/{message.uid}",
                longHeader=(
                    "From: synthetic@example.invalid\r\n"
                    "To: recipient@example.invalid\r\n"
                ),
                contentType="text/html",
                parts=[
                    MailBodyPart(
                        guid=f"messagepart:Drafts/{message.uid}-1", content=content
                    )
                ],
            )
        assert message.folder is self.folder
        return SimpleNamespace(
            content_type="text/plain",
            headers="OMIT_ME",
            parts=[SimpleNamespace(content="Synthetic mail body")],
        )


class SyntheticClient:
    """Saved-session state only; there is deliberately no credential-login method."""

    def __init__(self, challenge: bool, writes: bool = False) -> None:
        self.challenge = challenge
        self.params = {"dsid": "12345"}
        self.calendar = SyntheticCalendar()
        self.reminders = SyntheticReminders()
        self.contacts = SimpleNamespace(
            all=[
                {
                    "firstName": "Synthetic",
                    "lastName": "Contact",
                    "photo": "OMIT_ME",
                    "emails": [{"field": "synthetic@example.invalid"}],
                    "phones": [{"field": "000"}],
                }
            ]
        )
        self.drive = (
            SyntheticWritableDrive()
            if writes
            else SimpleNamespace(root=SyntheticFolder(), trash=SyntheticTrash())
        )
        self.notes = SyntheticNotes()
        album = SyntheticWritableAlbum() if writes else SyntheticAlbum()
        self.photos = SimpleNamespace(
            service_endpoint="https://photos.example.test",
            libraries={
                "synthetic": SimpleNamespace(
                    all=album,
                    albums=[album],
                    scope="private",
                    zone_id={"zoneName": "Z"},
                )
            },
        )
        if writes:
            self.photos.params = {}
            self.photos.libraries = {
                "synthetic": SyntheticPrivateLibrary(
                    self.photos, cast(SyntheticWritableAlbum, album)
                )
            }
        self.mail = SyntheticMail()
        self.session = SimpleNamespace(close=self.close, get=self.get_resource)

    def get_resource(
        self, url: str, *, stream: bool, headers: dict[str, str], allow_redirects: bool
    ) -> Response:
        """No network: verify selected variant, range and redirect policy directly."""
        assert url == "https://cvws.icloud-content.com/synthetic?signature=OMIT_ME"
        assert stream is True
        assert allow_redirects is False
        return range_response(headers)

    def close(self) -> None:
        """Emit only a deterministic synthetic cleanup marker on stderr."""
        print("MCP_SYNTHETIC_CLOSED", file=sys.stderr, flush=True)

    def get_auth_status(self) -> dict[str, bool]:
        """A challenge must remain a structured failure, not a login fallback."""
        return {"authenticated": not self.challenge, "requires_2fa": self.challenge}


def main() -> None:
    """Launch only a synthetic configured server with outbound networking denied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", action="store_true")
    parser.add_argument("--content", action="store_true")
    parser.add_argument("--approval-dir")
    args = parser.parse_args()
    original_socket = socket.socket

    def local_socket(family: int, *values: Any, **kwargs: Any) -> socket.socket:
        """Allow event-loop local sockets, never an outbound IPv4/IPv6 socket."""
        if family != socket.AF_UNIX:
            raise AssertionError("Synthetic MCP check attempted outbound networking")
        return original_socket(family, *values, **kwargs)

    config = ServerConfig(
        username="synthetic@example.invalid",
        session_dir="not-read",
        services=SUPPORTED_SERVICES,
        content_services=("drive", "notes", "photos", "mail")
        if args.content
        else ("mail",)
        if args.approval_dir
        else (),
        write_actions=(
            "calendar.delete_event",
            "reminders.create",
            "mail.create_draft",
            "mail.edit_draft",
            "mail.create_folder",
            "mail.rename_folder",
            "photos.set_favorite",
            "photos.rename_album",
            "photos.create_album",
            "photos.add_to_album",
            "drive.rename",
            "drive.trash_file",
            "drive.move_file",
        )
        if args.approval_dir
        else (),
        approval_dir=args.approval_dir,
        high_risk_actions=("calendar.delete_event",) if args.approval_dir else (),
    )
    owner = SessionOwner(
        config, lambda: SyntheticClient(args.challenge, bool(args.approval_dir))
    )
    with patch("socket.socket", side_effect=local_socket):
        build_server(owner=owner).run(transport="stdio")


if __name__ == "__main__":
    main()
