"""Private local operator mailbox, deliberately outside the MCP tool protocol.

Assumes a trusted OS/host and that agents cannot write this directory through other
tools. This is not protection against privileged code running as the operator.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from typing import Any, TextIO

from .authorization import ApprovalLedger, MutationPreview, WriteDenied

MAX_RECORD_BYTES = 262144


class ApprovalMailbox:
    """POSIX descriptor-anchored files; no symlinks or implicit creation."""

    def __init__(self, directory: str) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise WriteDenied("operator_channel_unsupported")
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
        if len(os.listdir(self._fd)) >= 256:
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
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def _read(self, request_id: str, suffix: str) -> dict[str, Any]:
        try:
            fd = os.open(
                self._name(request_id, suffix),
                os.O_RDONLY | os.O_NOFOLLOW,
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
        except (ValueError, UnicodeError):
            raise WriteDenied("invalid_approval_record") from None
        if not isinstance(record, dict) or record.get("request_id") != request_id:
            raise WriteDenied("invalid_approval_record")
        return record

    def publish(self, preview: MutationPreview) -> None:
        """Persist only the exact operator-visible preview, never private bindings."""
        self._write(
            preview.request_id,
            ".pending.json",
            {
                "request_id": preview.request_id,
                "action": preview.action,
                "target": preview.target,
                "digest": preview.digest,
                "payload": preview.payload,
                "risk": preview.risk,
            },
        )

    def decide(self, request_id: str, source: TextIO, display: TextIO) -> None:
        """Require a separate interactive terminal; no --yes or model boolean."""
        if not source.isatty():
            raise WriteDenied("operator_terminal_required")
        record = self._read(request_id, ".pending.json")
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
        display.write(f"Type {phrase} to authorize this exact mutation: ")
        display.flush()
        if source.readline().strip() != phrase:
            raise WriteDenied("operator_declined")
        self._write(
            request_id,
            ".approved.json",
            {"request_id": request_id, "digest": digest, "risk": risk},
        )

    def accept(self, request_id: str, ledger: ApprovalLedger) -> None:
        """Consume a private receipt before forwarding the exact decision."""
        record = self._read(request_id, ".approved.json")
        digest = record.get("digest")
        if not isinstance(digest, str):
            raise WriteDenied("invalid_approval_record")
        risk = record.get("risk", "standard")
        if not isinstance(risk, str) or risk not in {"standard", "high"}:
            raise WriteDenied("invalid_approval_record")
        os.unlink(self._name(request_id, ".approved.json"), dir_fd=self._fd)
        if risk == "high":
            ledger.approve_from_operator(request_id, digest, high_risk=True)
        else:
            ledger.approve_from_operator(request_id, digest)


def approve_interactively(directory: str, request_id: str) -> None:
    """Standalone operator path, never an MCP tool or protocol-stdin prompt."""
    with ApprovalMailbox(directory) as mailbox:
        mailbox.decide(request_id, sys.stdin, sys.stderr)
