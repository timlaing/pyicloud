"""Fail-closed, single-use mutation approval primitives.

This module registers no MCP tools and executes no mutations. Operator approval
must be wired through a separately trusted local channel, never an MCP boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from secrets import token_bytes, token_urlsafe
import time
from typing import Any, cast


class WriteDenied(Exception):
    """Safe denial category, without account or payload details."""


@dataclass(frozen=True)
class MutationPreview:
    """An immutable public summary; payload access returns an independent copy."""

    request_id: str
    action: str
    target: str
    digest: str
    expires_at: float
    _payload_json: str

    @property
    def payload(self) -> dict[str, Any]:
        """Caller edits cannot change the approved operation's stored payload."""
        return cast(dict[str, Any], json.loads(self._payload_json))


@dataclass
class PendingMutation:
    """Private account/resource bindings are never part of the public preview."""

    preview: MutationPreview
    account_binding: str
    resource_binding: str
    revision: str | None
    approved: bool = False


class ApprovalLedger:
    """Single owner-lane ledger; operator decisions cannot be supplied by tools."""

    def __init__(
        self,
        allowed_actions: frozenset[str] = frozenset(),
        *,
        ttl: float = 300,
        capacity: int = 64,
    ) -> None:
        if not 0 < ttl <= 600 or not 1 <= capacity <= 256:
            raise ValueError("Invalid approval limits")
        self._allowed_actions = frozenset(allowed_actions)
        self._ttl = ttl
        self._capacity = capacity
        self._pending: dict[str, PendingMutation] = {}
        self._secret = token_bytes(32)

    def preview(
        self,
        action: str,
        target: str,
        payload: dict[str, Any],
        *,
        account_binding: str,
        resource_binding: str,
        revision: str | None,
    ) -> MutationPreview:
        """Bind a canonical snapshot to the exact action, account, route and version."""
        if action not in self._allowed_actions:
            raise WriteDenied("write_disabled")
        if not account_binding or not resource_binding or not target:
            raise WriteDenied("invalid_target")
        if not revision:
            raise WriteDenied("revision_required")
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise WriteDenied("invalid_payload") from None
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise WriteDenied("payload_too_large")
        now = time.monotonic()
        self._pending = {
            key: item
            for key, item in self._pending.items()
            if item.preview.expires_at > now
        }
        if len(self._pending) >= self._capacity:
            raise WriteDenied("approval_queue_full")
        request_id = token_urlsafe(32)
        fingerprint = json.dumps(
            {
                "request_id": request_id,
                "action": action,
                "target": target,
                "payload": encoded,
                "account": account_binding,
                "resource": resource_binding,
                "revision": revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        preview = MutationPreview(
            request_id,
            action,
            target,
            hmac.new(
                self._secret, fingerprint.encode("utf-8"), hashlib.sha256
            ).hexdigest(),
            now + self._ttl,
            encoded,
        )
        self._pending[request_id] = PendingMutation(
            preview, account_binding, resource_binding, revision
        )
        return preview

    def approve_from_operator(self, request_id: str, digest: str) -> None:
        """Trusted local operator hook; MUST NOT be exposed as an MCP tool."""
        item = self._lookup(request_id)
        if not hmac.compare_digest(item.preview.digest, digest):
            raise WriteDenied("preview_mismatch")
        item.approved = True

    def consume(
        self,
        request_id: str,
        *,
        account_binding: str,
        resource_binding: str,
        revision: str | None,
    ) -> MutationPreview:
        """Consume before mutation dispatch so ambiguous failures cannot be retried."""
        item = self._lookup(request_id)
        if not item.approved:
            raise WriteDenied("operator_approval_required")
        # Even a stale approved request is retired; it needs a new preview/decision.
        del self._pending[request_id]
        if (item.account_binding, item.resource_binding, item.revision) != (
            account_binding,
            resource_binding,
            revision,
        ):
            raise WriteDenied("stale_preview")
        return item.preview

    def _lookup(self, request_id: str) -> PendingMutation:
        item = self._pending.get(request_id)
        if item is None:
            raise WriteDenied("unknown_or_consumed_preview")
        if item.preview.expires_at <= time.monotonic():
            del self._pending[request_id]
            raise WriteDenied("expired_preview")
        return item
