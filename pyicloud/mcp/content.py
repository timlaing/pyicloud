"""Explicitly opted-in bounded content and server-discovered binary downloads."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlsplit

from .additional import AdditionalReads, text
from .context import ToolFailure
from .reads import ReadResult, bounded_result


@dataclass
class DownloadState:
    """Bind resumed chunks to a strong ETag from the selected resource."""

    resource: Any
    etag: str | None = None


def clipped_text(value: str, maximum: int) -> tuple[str, bool]:
    """Bound UTF-8 bytes, never split a code point or silently claim completeness."""
    raw = value.encode("utf-8")
    return raw[:maximum].decode("utf-8", errors="ignore"), len(raw) <= maximum


def resource_url(value: str | None) -> str:
    """Only discovered HTTPS Apple resources; no caller URLs or credentials."""
    try:
        parsed = urlsplit(value or "")
        port = parsed.port
    except ValueError:
        raise ToolFailure("invalid_resource_url") from None
    host = parsed.hostname or ""
    suffixes = (
        "icloud.com",
        "icloud-content.com",
        "apple.com",
        "icloud.com.cn",
        "icloud-content.com.cn",
    )
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)
    ):
        raise ToolFailure("invalid_resource_url")
    return value or ""


def download_chunk(
    state: DownloadState, response: Any, offset: int, maximum: int, filename: str
) -> ReadResult:
    """Validate byte ranges and version identity before returning an assembly chunk."""
    try:
        data = response.content
        if not isinstance(data, bytes) or len(data) > maximum:
            raise ToolFailure("content_too_large")
        if response.status_code == 200 and offset == 0:
            complete, next_offset = True, None
        elif response.status_code == 206:
            matched = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
            )
            if matched is None:
                raise ToolFailure("invalid_download_range")
            first, last, total = map(int, matched.groups())
            if (
                first != offset
                or last - first + 1 != len(data)
                or last >= total
                or not data
            ):
                raise ToolFailure("invalid_download_range")
            complete = last + 1 == total
            next_offset = None if complete else last + 1
        else:
            raise ToolFailure("download_rejected")
        etag = response.headers.get("ETag")
        if state.etag is not None and etag != state.etag:
            raise ToolFailure("stale_download")
        if not complete:
            if not etag or etag.startswith("W/"):
                raise ToolFailure("download_requires_strong_etag")
            state.etag = etag
        return bounded_result(
            [
                {
                    "filename": filename,
                    "offset": offset,
                    "data_base64": base64.b64encode(data).decode("ascii"),
                    "next_offset": next_offset,
                }
            ],
            1,
            complete=complete,
        )
    finally:
        response.close()


class ContentReads:
    """No content tools are authorized by metadata opt-in alone."""

    def __init__(self, additional: AdditionalReads) -> None:
        self.additional = additional
        self._downloads: dict[str, DownloadState] = {}

    async def _run(
        self,
        service: str,
        maximum: int,
        ceiling: int,
        operation: Callable[[Any], ReadResult],
    ) -> ReadResult:
        if service not in self.additional.reads.owner.config.content_services:
            return ReadResult(error="content_disabled")
        if not 1 <= maximum <= ceiling:
            return ReadResult(error="invalid_argument")
        return await self.additional.reads.execute(service, 1, operation)

    async def note(self, handle: str, maximum: int) -> ReadResult:
        """Return untrusted plaintext without attachments or HTML rendering."""

        def read(client: Any) -> ReadResult:
            identifier = self.additional.resolve("note", handle)
            note = client.notes.get(identifier, with_attachments=False)
            if note.is_locked or note.text is None:
                raise ToolFailure("content_unavailable")
            body, complete = clipped_text(note.text, maximum)
            return bounded_result(
                [{"title": text(note.title), "text": body, "untrusted": True}],
                1,
                complete=complete,
            )

        return await self._run("notes", maximum, 32768, read)

    async def mail_body(self, handle: str, maximum: int) -> ReadResult:
        """Fetch fresh typed message context; HTML is data and headers are omitted."""

        def read(client: Any) -> ReadResult:
            previous = self.additional.resolve("mail-message", handle)
            message = client.mail.message(previous.folder, previous.id)
            if message is None:
                raise ToolFailure("content_unavailable")
            body = client.mail.body(message)
            remaining, complete = maximum, True
            parts = []
            for index, part in enumerate(body.parts):
                content, whole = clipped_text(part.content, remaining)
                parts.append({"content": content})
                remaining -= len(content.encode("utf-8"))
                complete = complete and whole
                if remaining <= 0:
                    complete = complete and index == len(body.parts) - 1
                    break
            return bounded_result(
                [
                    {
                        "subject": text(message.subject),
                        "parts": parts,
                        "content_type": text(body.content_type),
                        "untrusted": True,
                    }
                ],
                1,
                complete=complete,
            )

        return await self._run("mail", maximum, 32768, read)

    async def file(
        self,
        service: str,
        handle: str,
        offset: int,
        maximum: int,
        version: str = "original",
    ) -> ReadResult:
        """Read range-checked chunks without local paths or caller URLs."""
        if (
            service not in {"drive", "photos"}
            or offset < 0
            or offset > 2**63 - 1
            or len(version) > 64
        ):
            return ReadResult(error="invalid_argument")

        def read(client: Any) -> ReadResult:
            resource = self.additional.resolve(
                "drive-file" if service == "drive" else "photo-asset", handle
            )
            size = (
                getattr(resource, "data", {}).get("size")
                if service == "drive"
                else None
            )
            if service == "drive" and (
                (isinstance(size, int) and not isinstance(size, bool) and size == 0)
                or size == "0"
            ):
                if offset:
                    raise ToolFailure("invalid_download_range")
                return bounded_result(
                    [
                        {
                            "filename": text(resource.name),
                            "offset": 0,
                            "data_base64": "",
                            "next_offset": None,
                        }
                    ],
                    1,
                )
            key = f"{service}:{handle}:{version}"
            if key not in self._downloads:
                if len(self._downloads) >= 1000:
                    del self._downloads[next(iter(self._downloads))]
                self._downloads[key] = DownloadState(resource)
            state = self._downloads[key]
            if offset and state.etag is None:
                raise ToolFailure("download_requires_strong_etag")
            headers = {"Range": f"bytes={offset}-{offset + maximum - 1}"}
            if state.etag:
                headers["If-Match"] = state.etag
            if service == "drive":
                filename = text(resource.name)
                response = resource.open(
                    stream=True, headers=headers, allow_redirects=False
                )
            else:
                filename = text(resource.filename)
                url = resource_url(resource.download_url(version))
                response = client.session.get(
                    url, stream=True, headers=headers, allow_redirects=False
                )
            return download_chunk(state, response, offset, maximum, filename)

        return await self._run(service, maximum, 131072, read)
