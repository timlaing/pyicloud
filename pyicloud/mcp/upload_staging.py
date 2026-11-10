"""Bounded in-memory byte staging only; no filesystem or upload transport access."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import re
from secrets import token_urlsafe
import time

from .authorization import WriteDenied

MAX_CHUNK_BYTES = 65536


@dataclass(frozen=True, repr=False)
class StagedUpload:
    """Immutable bytes and independently computed digest, never an upload approval."""

    name: str
    size: int
    sha256: str
    expires_at: float
    data: bytes


@dataclass(repr=False)
class PendingUpload:
    """Private bounded buffer and declared reservation; incomplete data cannot apply."""

    name: str
    size: int
    sha256: str
    expires_at: float
    data: bytearray = field(default_factory=bytearray)


class UploadStaging:
    """Single serialized owner lane; independent elevated approval remains required."""

    def __init__(
        self,
        *,
        max_file_bytes: int = 4194304,
        max_total_bytes: int = 8388608,
        capacity: int = 8,
        ttl: float = 300,
    ) -> None:
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (max_file_bytes, max_total_bytes, capacity)
            )
            or isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or (
                not 0 < max_file_bytes <= 16777216
                or not max_file_bytes <= max_total_bytes <= 33554432
                or not 1 <= capacity <= 32
                or not 0 < ttl <= 600
            )
        ):
            raise ValueError("Invalid upload staging limits")
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.capacity = capacity
        self.ttl = ttl
        self._stages: dict[str, PendingUpload | StagedUpload] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        self._stages = {
            token: stage
            for token, stage in self._stages.items()
            if stage.expires_at > now
        }

    def _get(self, token: str) -> PendingUpload | StagedUpload:
        self._purge()
        stage = self._stages.get(token)
        if stage is None:
            raise WriteDenied("expired_or_unknown_upload")
        return stage

    def begin(self, name: str, size: int, sha256: str) -> str:
        """Reserve bounded bytes under an opaque token, never accept a path or URL."""
        try:
            valid_name = (
                isinstance(name, str)
                and bool(name.strip())
                and name == name.strip()
                and name not in {".", ".."}
                and len(name.encode("utf-8")) <= 255
                and not any(
                    ord(char) < 32 or char in {"/", "\\", "\x7f"} for char in name
                )
            )
        except UnicodeError:
            valid_name = False
        if (
            not valid_name
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= self.max_file_bytes
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise WriteDenied("invalid_upload_declaration")
        self._purge()
        if (
            len(self._stages) >= self.capacity
            or sum(stage.size for stage in self._stages.values()) + size
            > self.max_total_bytes
        ):
            raise WriteDenied("upload_staging_full")
        token = token_urlsafe(32)
        self._stages[token] = PendingUpload(
            name, size, sha256, time.monotonic() + self.ttl
        )
        return token

    def append(self, token: str, offset: int, encoded: str) -> int:
        """Append one strict bounded Base64 chunk at the exact next byte offset."""
        stage = self._get(token)
        if not isinstance(stage, PendingUpload):
            raise WriteDenied("upload_already_sealed")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset != len(stage.data)
            or not isinstance(encoded, str)
            or not encoded
            or len(encoded) > ((MAX_CHUNK_BYTES + 2) // 3) * 4
        ):
            raise WriteDenied("invalid_upload_chunk")
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise WriteDenied("invalid_upload_chunk") from None
        if (
            not data
            or len(data) > MAX_CHUNK_BYTES
            or len(stage.data) + len(data) > stage.size
        ):
            raise WriteDenied("invalid_upload_chunk")
        stage.data.extend(data)
        return len(stage.data)

    def seal(self, token: str) -> StagedUpload:
        """Only exact size and computed digest can produce immutable sealed bytes."""
        stage = self._get(token)
        if isinstance(stage, StagedUpload):
            return stage
        if len(stage.data) != stage.size:
            raise WriteDenied("incomplete_upload")
        digest = hashlib.sha256(stage.data).hexdigest()
        if digest != stage.sha256:
            del self._stages[token]
            raise WriteDenied("upload_digest_mismatch")
        sealed = StagedUpload(
            stage.name, stage.size, digest, stage.expires_at, bytes(stage.data)
        )
        self._stages[token] = sealed
        return sealed

    def take(
        self, token: str, *, before_take: Callable[[], None] | None = None
    ) -> StagedUpload:
        """Consume sealed bytes once before a future separately approved upload."""
        stage = self._get(token)
        if not isinstance(stage, StagedUpload):
            raise WriteDenied("upload_not_sealed")
        if before_take is not None:
            before_take()
        del self._stages[token]
        return stage

    def peek(self, token: str) -> StagedUpload:
        """Read sealed immutable bytes for exact preview binding, never approval."""
        stage = self._get(token)
        if not isinstance(stage, StagedUpload):
            raise WriteDenied("upload_not_sealed")
        return stage

    def describe(self, token: str) -> dict[str, str | int | bool]:
        """Bounded metadata only; no byte content, local path or transfer URL."""
        stage = self._get(token)
        return {
            "name": stage.name,
            "size": stage.size,
            "sha256": stage.sha256,
            "sealed": isinstance(stage, StagedUpload),
        }

    def discard(self, token: str) -> None:
        """Discard only a selected stage; does not touch user files or remote data."""
        self._get(token)
        del self._stages[token]

    def close(self) -> None:
        """Release references, not a promise of cryptographic memory erasure."""
        self._stages.clear()
