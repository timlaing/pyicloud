"""Opt-in Contacts, Drive, Notes, Photos and Mail metadata adapters."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from itertools import islice
from secrets import token_urlsafe
import time
from typing import Any

from requests import Session

from pyicloud.services.drive import DriveNode, DriveService

from .authorization import WriteDenied
from .context import BoundedAdapter, SessionOwner, ToolFailure
from .one_shot import checked_adapter, prepared_url
from .reads import ReadResult, ReadTools, bounded_result


def text(value: Any) -> str:
    """Expose only strings, never stringified nested upstream private fields."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolFailure("upstream_failure")
    return value[:4096]


def timestamp(value: Any) -> str | None:
    """Datetime metadata keeps its existing zone; no invented UTC conversion."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ToolFailure("upstream_failure")
    return value.isoformat()


@contextmanager
def window(
    items: Iterable[Any], limit: int, *, live_only: bool = False
) -> Iterator[Iterator[Any]]:
    """Stop and close lazy service generators before loading further pages."""
    iterator = iter(items)
    try:
        selected = (
            (item for item in iterator if not item.deleted) if live_only else iterator
        )
        yield islice(selected, limit)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


class AdditionalReads:
    """Single-account, expiring handles bound to service and discovered objects."""

    def __init__(self, reads: ReadTools) -> None:
        self.reads = reads
        self._handles: dict[str, tuple[str, Any, float]] = {}

    def _store(self, kind: str, value: Any) -> str:
        if len(self._handles) >= 1000:
            del self._handles[next(iter(self._handles))]
        handle = token_urlsafe(24)
        self._handles[handle] = (kind, value, time.monotonic() + 600)
        return handle

    def _get(self, kind: str, handle: str) -> Any:
        route = self._handles.get(handle)
        if route is None or route[0] != kind or route[2] <= time.monotonic():
            raise ToolFailure("expired_or_unknown_handle")
        return route[1]

    def remember(self, kind: str, resource: Any) -> str:
        """Share scoped resources with separately authorized content/write adapters."""
        return self._store(kind, resource)

    def resolve(self, kind: str, handle: str) -> Any:
        """Resolve only an existing, expiring resource of the requested kind."""
        return self._get(kind, handle)

    def retire(self, kind: str, handle: str) -> None:
        """Retire only an exact live scoped selector before a version-changing write."""
        self.retire_many(((kind, handle),))

    def retire_many(self, selectors: tuple[tuple[str, str], ...]) -> None:
        """Admit all selected handles atomically, then retire their Drive aliases."""
        resources = [self._get(kind, handle) for kind, handle in selectors]
        identifiers = [
            (node.data.get("drivewsid"), node.data.get("zone"))
            for node in resources
            if isinstance(node, DriveNode)
        ]
        keys = {handle for _kind, handle in selectors}
        keys.update(
            handle
            for handle, (kind, node, _expires) in self._handles.items()
            if kind.startswith("drive-")
            and isinstance(node, DriveNode)
            and (node.data.get("drivewsid"), node.data.get("zone")) in identifiers
        )
        for key in keys:
            self._handles.pop(key, None)

    def resolve_any(self, kinds: frozenset[str], handle: str) -> Any:
        """Resolve only a permitted discovered kind, never an arbitrary identifier."""
        route = self._handles.get(handle)
        if route is None or route[0] not in kinds:
            raise ToolFailure("expired_or_unknown_handle")
        return self._get(route[0], handle)

    def snapshot_drive_handles(
        self, kind: str, handle: str
    ) -> dict[str, tuple[str, Any, float]]:
        """Retain only aliases affected by a known pre-dispatch transaction."""
        selected = self._get(kind, handle)
        return {
            key: entry
            for key, entry in self._handles.items()
            if entry[0].startswith("drive-")
            and isinstance(entry[1], DriveNode)
            and (entry[1].data.get("drivewsid"), entry[1].data.get("zone"))
            == (selected.data.get("drivewsid"), selected.data.get("zone"))
        }

    def restore_before_dispatch_handles(
        self, entries: dict[str, tuple[str, Any, float]]
    ) -> None:
        """Restore original TTLs only after proof that dispatch never began."""
        for handle, entry in entries.items():
            self._handles.setdefault(handle, entry)

    async def _run(
        self,
        service: str,
        query: str,
        limit: int,
        operation: Callable[[Any], ReadResult],
    ) -> ReadResult:
        if len(query) > 256:
            return ReadResult(error="invalid_argument")

        def guarded(client: Any) -> ReadResult:
            if service != "drive" or not isinstance(client.drive, DriveService):
                return operation(client)
            session = client.drive.session
            if (
                not isinstance(session, Session)
                or getattr(session.get_adapter, "__func__", None)
                is not Session.get_adapter
            ):
                raise ToolFailure("unsupported_drive_transport")
            url = prepared_url(
                session,
                client.drive.service_root + "/retrieveItemDetailsInFolders",
                client.drive.params,
            )
            try:
                adapter = checked_adapter(session, url, bounded=True)
            except WriteDenied:
                raise ToolFailure("unsupported_drive_transport") from None
            assert isinstance(adapter, BoundedAdapter)
            owner = self.reads.owner
            previous = adapter.deadline
            if isinstance(owner, SessionOwner):
                owner.bind_adapter(adapter)
                return operation(client)
            adapter.requests_left = 32
            deadline = time.monotonic() + owner.config.timeout
            adapter.deadline = (
                min(previous, deadline) if previous is not None else deadline
            )
            try:
                return operation(client)
            finally:
                adapter.deadline = previous

        return await self.reads.execute(service, limit, guarded)

    async def contacts(self, query: str, limit: int) -> ReadResult:
        """Search names; addresses/phones require a separate explicit call."""

        def read(client: Any) -> ReadResult:
            rows = client.contacts.all
            if rows is None:
                raise ToolFailure("upstream_failure")
            records = []
            for row in rows:
                first, last = text(row.get("firstName")), text(row.get("lastName"))
                company = text(row.get("companyName"))
                if query.casefold() not in f"{first} {last} {company}".casefold():
                    continue
                handle = self._store(
                    "contact",
                    {
                        "first_name": first,
                        "last_name": last,
                        "company_name": company,
                        "emails": row.get("emails") or [],
                        "phones": row.get("phones") or [],
                    },
                )
                records.append({
                    "handle": handle,
                    "first_name": first,
                    "last_name": last,
                    "company_name": company,
                })
                if len(records) >= limit:
                    break
            return bounded_result(records, limit, complete=False)

        return await self._run("contacts", query, limit, read)

    async def contact_details(
        self,
        handle: str,
        include_emails: bool,
        include_phones: bool,
        offset: int = 0,
        limit: int = 20,
    ) -> ReadResult:
        """Return explicitly requested details; no photos or raw payload."""
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= 2**31 - 1
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            return ReadResult(error="invalid_argument")

        def read(_client: Any) -> ReadResult:
            contact = self._get("contact", handle)
            record: dict[str, Any] = {
                "first_name": contact["first_name"],
                "last_name": contact["last_name"],
            }
            complete = True
            for enabled, key in [
                (include_emails, "emails"),
                (include_phones, "phones"),
            ]:
                if enabled:
                    record[key] = [
                        text(item.get("field"))
                        for item in contact[key][offset : offset + limit]
                    ]
                    complete = complete and offset + limit >= len(contact[key])
            if not complete:
                record["next_offset"] = offset + limit
            return bounded_result([record], 1, complete=complete)

        return await self.reads.execute("contacts", 1, read)

    async def drive(
        self, folder_handle: str, query: str, limit: int, scan_limit: int = 500
    ) -> ReadResult:
        """Browse one folder; filter names locally, never local paths."""
        if (
            isinstance(scan_limit, bool)
            or not isinstance(scan_limit, int)
            or not 1 <= scan_limit <= 2000
        ):
            return ReadResult(error="invalid_argument")

        def read(client: Any) -> ReadResult:
            node = (
                self._get("drive-folder", folder_handle)
                if folder_handle
                else client.drive.root
            )
            if node.type not in {"folder", "app_library"}:
                raise ToolFailure("invalid_argument")
            records = []
            with self._bounded_drive_children(
                node, scan_limit, client.drive
            ) as children:
                for child in children:
                    name, kind = text(child.name), text(child.type)
                    if query.casefold() not in name.casefold():
                        continue
                    record = {
                        "name": name,
                        "type": kind,
                        "modified": timestamp(child.date_modified),
                    }
                    self._drive_record_handle(record, kind, child)
                    records.append(record)
                    if len(records) >= limit:
                        break
            return bounded_result(records, limit, complete=False)

        return await self._run("drive", query, limit, read)

    def _drive_record_handle(
        self, record: dict[str, Any], kind: str, child: Any
    ) -> None:
        """Attach only the established folder or file discovery handle kinds."""
        if kind in {"folder", "app_library"}:
            record["handle"] = self._store("drive-folder", child)
        elif kind == "file":
            record["handle"] = self._store("drive-file", child)

    async def drive_trash(
        self, query: str, limit: int, scan_limit: int = 500
    ) -> ReadResult:
        """Bounded Recently Deleted metadata with separate opaque trash selectors."""
        if (
            isinstance(scan_limit, bool)
            or not isinstance(scan_limit, int)
            or not 1 <= scan_limit <= 2000
        ):
            return ReadResult(error="invalid_argument")

        def read(client: Any) -> ReadResult:
            records = []
            with self._bounded_drive_children(
                client.drive.trash, scan_limit, client.drive
            ) as children:
                for child in children:
                    name, kind = text(child.name), text(child.type)
                    if (
                        kind not in {"file", "folder"}
                        or query.casefold() not in name.casefold()
                    ):
                        continue
                    records.append({
                        "handle": self._store("drive-trash-" + kind, child),
                        "name": name,
                        "type": kind,
                        "modified": timestamp(child.date_modified),
                    })
                    if len(records) >= limit:
                        break
            return bounded_result(records, limit, complete=False)

        return await self._run("drive", query, limit, read)

    async def notes(self, query: str, limit: int) -> ReadResult:
        """Search 200 recent titles; omit snippets and bodies."""

        def read(client: Any) -> ReadResult:
            records = []
            with window(client.notes.recents(limit=200), 200) as items:
                for item in items:
                    title = text(item.title)
                    if item.is_deleted or query.casefold() not in title.casefold():
                        continue
                    records.append({
                        "handle": self._store("note", item.id),
                        "id": text(item.id),
                        "title": title,
                        "modified": timestamp(item.modified_at),
                        "locked": item.is_locked,
                    })
                    if len(records) >= limit:
                        break
            return bounded_result(records, limit, complete=False)

        return await self._run("notes", query, limit, read)

    async def photo_libraries(self, limit: int) -> ReadResult:
        """Discover library handles without exposing zone-owner identifiers."""

        def read(client: Any) -> ReadResult:
            records = []
            for index, library in enumerate(client.photos.libraries.values()):
                records.append({
                    "handle": self._store("photo-library", library),
                    "label": f"Library {index + 1}",
                })
                if len(records) >= limit:
                    break
            return bounded_result(records, limit, complete=False)

        return await self.reads.execute("photos", limit, read)

    async def photo_albums(self, library_handle: str, limit: int) -> ReadResult:
        """Album handles retain the discovered library, including shared ownership."""

        def read(_client: Any) -> ReadResult:
            library = self._get("photo-library", library_handle)
            records = [
                {
                    "handle": self._store("photo-album", library.all),
                    "title": "All photos",
                }
            ]
            with window(library.albums, limit) as albums:
                for album in albums:
                    if len(records) >= limit:
                        break
                    records.append({
                        "handle": self._store("photo-album", album),
                        "title": text(album.title),
                    })
            return bounded_result(records, limit, complete=False)

        return await self.reads.execute("photos", limit, read)

    async def photos(self, album_handle: str, limit: int) -> ReadResult:
        """Project asset filename/capture metadata only, never URLs or downloads."""

        def read(_client: Any) -> ReadResult:
            album = self._get("photo-album", album_handle)
            with window(album, limit) as assets:
                return bounded_result(
                    (
                        {
                            "id": text(asset.id),
                            "handle": self._store("photo-asset", asset),
                            "filename": text(asset.filename),
                            "created": timestamp(asset.created),
                        }
                        for asset in assets
                    ),
                    limit,
                    complete=False,
                )

        return await self.reads.execute("photos", limit, read)

    async def mail_folders(self, limit: int) -> ReadResult:
        """Discover typed mailbox contexts without UID-validity or account internals."""

        def read(client: Any) -> ReadResult:
            return bounded_result(
                (
                    {
                        "handle": self._store("mail-folder", folder),
                        "name": text(folder.name),
                    }
                    for folder in client.mail.folders()
                ),
                limit,
            )

        return await self.reads.execute("mail", limit, read)

    async def mail_messages(self, folder_handle: str, limit: int) -> ReadResult:
        """Read bounded subjects and flags, without bodies, addresses or attachments."""

        def read(client: Any) -> ReadResult:
            folder = self._get("mail-folder", folder_handle)
            page = client.mail.messages(folder, limit=limit)
            return bounded_result(
                (
                    {
                        "id": text(item.id),
                        "handle": self._store("mail-message", item),
                        "subject": text(item.subject),
                        "flags": item.flags,
                    }
                    for item in page.messages
                ),
                limit,
                complete=False,
            )

        return await self.reads.execute("mail", limit, read)

    async def mail_search(
        self, folder_handle: str, query: str, limit: int, field: str = "subject"
    ) -> ReadResult:
        """Search subject/from/to/body/all; body/all require Mail content opt-in.

        Results contain thread IDs and subjects only; no previews or bodies.
        """
        if not query.strip():
            return ReadResult(error="invalid_argument")
        if field not in {"subject", "from", "to", "body", "all"}:
            return ReadResult(error="invalid_argument")
        if (
            field in {"body", "all"}
            and "mail" not in self.reads.owner.config.content_services
        ):
            return ReadResult(error="content_disabled")

        def read(client: Any) -> ReadResult:
            folder = self._get("mail-folder", folder_handle)
            page = client.mail.search(folder, query, field=field, limit=limit)
            return bounded_result(
                (
                    {"thread_id": text(item.id), "subject": text(item.subject)}
                    for item in page.results
                ),
                limit,
                complete=False,
            )

        return await self._run("mail", query, limit, read)

    async def search(
        self, service: str, handle: str, query: str, limit: int, scan_limit: int
    ) -> ReadResult:
        """Bounded metadata searches, not whole-account completeness claims."""
        if (
            not query.strip()
            or not 1 <= scan_limit <= 2000
            or service not in {"notes", "photos", "drive"}
        ):
            return ReadResult(error="invalid_argument")

        def read(client: Any) -> ReadResult:
            source: Iterable[Any]
            if service == "notes":
                source = client.notes.iter_all()
            elif service == "photos":
                source = self._get("photo-album", handle)
            else:
                source = self._drive_search_nodes(client, handle)
            records = []
            with window(source, scan_limit) as items:
                for item in items:
                    record = self._search_record(service, item, query)
                    if record is not None:
                        records.append(record)
                        if len(records) >= limit:
                            break
            return bounded_result(records, limit, complete=False)

        return await self._run(service, query, limit, read)

    def _search_record(
        self, service: str, item: Any, query: str
    ) -> dict[str, Any] | None:
        if service == "notes":
            name = text(item.title)
            if item.is_deleted:
                return None
            record = {
                "id": text(item.id),
                "title": name,
                "modified": timestamp(item.modified_at),
                "locked": item.is_locked,
            }
            kind, resource = "note", item.id
        elif service == "photos":
            name = text(item.filename)
            record = {
                "id": text(item.id),
                "filename": name,
                "created": timestamp(item.created),
            }
            kind, resource = "photo-asset", item
        else:
            if item.type not in {"file", "folder", "app_library"}:
                return None
            name = text(item.name)
            record = {
                "name": name,
                "type": text(item.type),
                "modified": timestamp(item.date_modified),
            }
            kind, resource = (
                "drive-file" if item.type == "file" else "drive-folder",
                item,
            )
        if query.casefold() not in name.casefold():
            return None
        record["handle"] = self._store(kind, resource)
        return record

    def _drive_search_nodes(self, client: Any, handle: str) -> Iterator[Any]:
        """Remote folder BFS only; at most sixteen folder fetches and bounded rows."""
        pending = deque([
            self._get("drive-folder", handle) if handle else client.drive.root
        ])
        fetched = 0
        while pending and fetched < 16:
            folder = pending.popleft()
            if folder.type not in {"folder", "app_library"}:
                raise ToolFailure("invalid_argument")
            fetched += 1
            with self._bounded_drive_children(folder, 2000, client.drive) as children:
                for item in children:
                    yield item
                    if item.type in {"folder", "app_library"}:
                        pending.append(item)

    @staticmethod
    @contextmanager
    def _bounded_drive_children(
        node: Any, maximum: int, connection: Any = None
    ) -> Generator[Iterator[Any], None, None]:
        """Use the library's lazy bounded producer and cap custom facade iteration."""
        if isinstance(node, DriveNode):
            if connection is not None and node.connection is not connection:
                raise ToolFailure("unsupported_drive_connection")
            items = node.iter_children(force=True, max_children=maximum)
        else:
            producer = getattr(node, "iter_children", None)
            if not callable(producer):
                raise ToolFailure("unsupported_drive_inventory")
            items = producer(force=True, max_children=maximum)
        with window(items, maximum) as children:
            yield children
