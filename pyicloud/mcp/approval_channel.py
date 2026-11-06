"""Private local operator mailbox, deliberately outside the MCP tool protocol.

Assumes a trusted OS/host and that agents cannot write this directory through other
tools. This is not protection against privileged code running as the operator.
"""

from __future__ import annotations

from contextlib import suppress
import json
import math
import os
from os import stat as entry_stat
import re
import stat
import sys
import time
from typing import Any, TextIO

from .authorization import ApprovalLedger, MutationPreview, WriteDenied

MAX_RECORD_BYTES = 262144
PENDING_SUFFIX = ".pending.json"
APPROVED_SUFFIX = ".approved.json"


def stale_deadline(expires: Any, now: float) -> bool:
    """Recognize elapsed deadlines or values impossible under the 600-second bound."""
    return (
        isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and math.isfinite(expires)
        and (expires <= now or expires > now + 600)
    )


def approval_base_directory() -> str:
    """Use the OS account's private application area, not agent workspace input."""
    import pwd  # pylint: disable=import-outside-toplevel

    home = pwd.getpwuid(os.geteuid()).pw_dir
    return os.path.realpath(
        os.path.join(home, ".local", "share", "pyicloud", "mcp-approvals")
    )


def checked_approval_directory(directory: str) -> str:
    """Canonicalize and constrain CLI paths before opening an operator mailbox."""
    if (
        not isinstance(directory, str)
        or not directory
        or len(directory.encode("utf-8")) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in directory)
    ):
        raise WriteDenied("invalid_approval_directory")
    resolved = os.path.realpath(directory)
    base = approval_base_directory()
    if resolved != base and not resolved.startswith(base + os.sep):
        raise WriteDenied("approval_directory_outside_private_root")
    if os.path.islink(directory):
        raise WriteDenied("approval_directory_unavailable")
    return resolved


class ApprovalMailbox:
    """POSIX descriptor-anchored files; no symlinks or implicit creation."""

    def __init__(self, directory: str) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise WriteDenied("operator_channel_unsupported")
        directory = checked_approval_directory(directory)
        try:
            self._fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise WriteDenied("approval_directory_unavailable") from None
        details = os.fstat(self._fd)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_mode & 0o077
        ):
            os.close(self._fd)
            raise WriteDenied("insecure_approval_directory")

    def close(self) -> None:
        """Release the directory descriptor once."""
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> ApprovalMailbox:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _name(self, request_id: str, suffix: str) -> str:
        if self._fd < 0:
            raise WriteDenied("operator_channel_closed")
        if re.fullmatch(r"[A-Za-z0-9_-]{40,64}", request_id) is None:
            raise WriteDenied("invalid_approval_id")
        return request_id + suffix

    def _write(self, request_id: str, suffix: str, record: dict[str, Any]) -> None:
        encoded = json.dumps(
            record, ensure_ascii=True, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise WriteDenied("approval_record_too_large")
        name = self._name(request_id, suffix)
        records = os.listdir(self._fd)
        opposite = APPROVED_SUFFIX if suffix == PENDING_SUFFIX else PENDING_SUFFIX
        if sum(not item.endswith(opposite) for item in records) >= 256:
            self._reclaim_expired()
            if sum(not item.endswith(opposite) for item in os.listdir(self._fd)) >= 256:
                raise WriteDenied("approval_mailbox_full")
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._fd,
            )
        except OSError:
            raise WriteDenied("approval_record_unavailable") from None
        created = os.fstat(fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self._remove_if_same(name, created)
            raise WriteDenied("approval_record_write_failed") from None

    def _private_record_details(self, name: str) -> os.stat_result | None:
        """Inspect a descriptor-relative entry without following unsafe replacements."""
        try:
            details = entry_stat(name, dir_fd=self._fd, follow_symlinks=False)
        except OSError:
            return None
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_mode & 0o077
            or details.st_nlink != 1
        ):
            return None
        return details

    def _remove_if_same(self, name: str, expected: os.stat_result) -> None:
        """Never delete an entry whose device/inode changed after it was inspected."""
        actual = self._private_record_details(name)
        if actual is not None and (actual.st_dev, actual.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        ):
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=self._fd)

    def _reclaim_expired(self) -> None:
        """Remove only validated private records with an established stale deadline."""
        now = time.time()
        for name in os.listdir(self._fd):
            matched = re.fullmatch(
                r"([A-Za-z0-9_-]{40,64})(\.pending\.json|\.approved\.json)", name
            )
            if matched is None:
                continue
            details = self._private_record_details(name)
            if details is None:
                continue
            try:
                record = self._read(matched[1], matched[2])
            except WriteDenied as error:
                if (
                    str(error)
                    in {"invalid_approval_record", "approval_record_too_large"}
                    and now - details.st_mtime > 600
                ):
                    self._remove_if_same(name, details)
                continue
            expires = record.get("expires_at")
            if stale_deadline(expires, now):
                self._remove_if_same(name, details)

    def _read(self, request_id: str, suffix: str) -> dict[str, Any]:
        try:
            fd = os.open(
                self._name(request_id, suffix),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._fd,
            )
        except OSError:
            raise WriteDenied("approval_record_unavailable") from None
        with os.fdopen(fd, "rb") as handle:
            details = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or details.st_mode & 0o077
                or details.st_nlink != 1
            ):
                raise WriteDenied("insecure_approval_record")
            data = handle.read(MAX_RECORD_BYTES + 1)
        if len(data) > MAX_RECORD_BYTES:
            raise WriteDenied("approval_record_too_large")
        try:
            record = json.loads(data)
        except ValueError:
            raise WriteDenied("invalid_approval_record") from None
        if not isinstance(record, dict) or record.get("request_id") != request_id:
            raise WriteDenied("invalid_approval_record")
        return record

    def publish(self, preview: MutationPreview) -> None:
        """Persist only the exact operator-visible preview, never private bindings."""
        self._write(
            preview.request_id,
            PENDING_SUFFIX,
            {
                "request_id": preview.request_id,
                "action": preview.action,
                "target": preview.target,
                "digest": preview.digest,
                "payload": preview.payload,
                "risk": preview.risk,
                "expires_at": preview.expires_at_wall,
            },
        )

    def decide(self, request_id: str, source: TextIO, display: TextIO) -> None:
        """Require a separate interactive terminal; no --yes or model boolean."""
        if not source.isatty():
            raise WriteDenied("operator_terminal_required")
        record = self._read(request_id, PENDING_SUFFIX)
        if stale_deadline(record.get("expires_at"), time.time()):
            raise WriteDenied("expired_preview")
        digest = record.get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise WriteDenied("invalid_approval_record")
        risk = record.get("risk", "standard")
        if not isinstance(risk, str) or risk not in {"standard", "high"}:
            raise WriteDenied("invalid_approval_record")
        # JSON escapes terminal controls and never renders embedded HTML/instructions.
        display.write(
            json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        )
        phrase = ("APPROVE HIGH-RISK " if risk == "high" else "APPROVE ") + request_id
        display.write(
            f"Type {phrase} to authorize, or DECLINE {request_id} to discard: "
        )
        display.flush()
        decision = source.readline().strip()
        if decision != phrase:
            if decision == "DECLINE " + request_id:
                with suppress(FileNotFoundError):
                    os.unlink(self._name(request_id, PENDING_SUFFIX), dir_fd=self._fd)
            raise WriteDenied("operator_declined")
        self._write(
            request_id,
            APPROVED_SUFFIX,
            record,
        )

    def accept(self, request_id: str, ledger: ApprovalLedger) -> None:
        """Consume a private receipt before forwarding the exact decision."""
        record = self._read(request_id, APPROVED_SUFFIX)
        digest = record.get("digest")
        if not isinstance(digest, str):
            raise WriteDenied("invalid_approval_record")
        risk = record.get("risk", "standard")
        if not isinstance(risk, str) or risk not in {"standard", "high"}:
            raise WriteDenied("invalid_approval_record")
        ledger.verify_display(request_id, record)
        os.unlink(self._name(request_id, APPROVED_SUFFIX), dir_fd=self._fd)
        with suppress(FileNotFoundError):
            os.unlink(self._name(request_id, PENDING_SUFFIX), dir_fd=self._fd)
        if risk == "high":
            ledger.approve_from_operator(request_id, digest, high_risk=True)
        else:
            ledger.approve_from_operator(request_id, digest)


def approve_interactively(directory: str, request_id: str) -> None:
    """Standalone operator path, never an MCP tool or protocol-stdin prompt."""
    with ApprovalMailbox(directory) as mailbox:
        mailbox.decide(request_id, sys.stdin, sys.stderr)
