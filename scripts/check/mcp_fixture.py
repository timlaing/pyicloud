"""Synthetic configured stdio server for installed-package integration checks.

Never opens an Apple account or saved-state directory; outbound sockets are denied.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import datetime
import socket
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pyicloud.mcp.context import SUPPORTED_SERVICES, ServerConfig, SessionOwner
from pyicloud.mcp.server import build_server
from pyicloud.services.mail.models import MailFolder, MailMessage, MailSearchHit
from pyicloud.services.reminders.models import Reminder, RemindersList


class SyntheticCalendar:
    """Only bounded deterministic calendar data, including fields to omit."""

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

    def lists(self) -> Iterator[RemindersList]:
        """Only route handles, titles and scopes should survive serialization."""
        yield RemindersList(id="List/SAME", title="Private")
        yield RemindersList(
            id="List/SAME",
            title="Shared",
            scope="shared",
            zone_name="Shared",
            zone_owner="OMIT_ME",
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


class SyntheticFolder:
    """Drive metadata only; no local paths or file-opening method."""

    type = "folder"

    def get_children(self, *, force: bool) -> list[SimpleNamespace]:
        """Ensure the adapter refreshes metadata without requesting content."""
        assert force is True
        return [SimpleNamespace(name="Synthetic.txt", type="file", date_modified=None)]


class SyntheticAlbum:
    """Photo metadata iterator with a signed URL that must never be exposed."""

    title = "Synthetic album"

    def __iter__(self) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(
            id="P",
            filename="Synthetic.jpg",
            created=datetime(2026, 1, 1),
            download_url="OMIT_ME",
            latitude="OMIT_ME",
        )


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


class SyntheticMail:
    """Typed mailbox/metadata APIs only, deliberately without body methods."""

    def __init__(self) -> None:
        self.folder = MailFolder(identifier="F", name="Inbox", uidValidity=1)

    def folders(self) -> list[MailFolder]:
        """Expose the one explicitly selectable mailbox."""
        return [self.folder]

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


class SyntheticClient:
    """Saved-session state only; there is deliberately no credential-login method."""

    def __init__(self, challenge: bool) -> None:
        self.challenge = challenge
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
        self.drive = SimpleNamespace(root=SyntheticFolder())
        self.notes = SyntheticNotes()
        album = SyntheticAlbum()
        self.photos = SimpleNamespace(
            libraries={"synthetic": SimpleNamespace(all=album, albums=[album])}
        )
        self.mail = SyntheticMail()
        self.session = SimpleNamespace(close=self.close)

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
    )
    owner = SessionOwner(config, lambda: SyntheticClient(args.challenge))
    with patch("socket.socket", side_effect=local_socket):
        build_server(owner=owner).run(transport="stdio")


if __name__ == "__main__":
    main()
