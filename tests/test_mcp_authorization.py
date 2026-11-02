"""Approval tests never touch accounts, storage, transport or actual mutations."""

from typing import Any
from unittest.mock import patch

import pytest

from pyicloud.mcp.authorization import ApprovalLedger, MutationPreview, WriteDenied


def preview(authority: ApprovalLedger) -> MutationPreview:
    """Synthetic account and revision values are private server-side bindings."""
    return authority.preview(
        "reminders.update",
        "Synthetic target",
        {"title": "Before"},
        account_binding="account",
        resource_binding="route",
        revision="v1",
    )


def ledger() -> ApprovalLedger:
    """Only one specific operator-authorized action is allowed."""
    return ApprovalLedger(frozenset({"reminders.update"}))


def test_default_denies_writes() -> None:
    """Read-only remains the default without explicit operator configuration."""
    with pytest.raises(WriteDenied, match="write_disabled"):
        preview(ApprovalLedger())


def test_exact_operator_approval_is_single_use_and_payload_is_frozen() -> None:
    """Neither a caller payload change nor replay can alter an approved operation."""
    authority = ledger()
    with pytest.raises(WriteDenied, match="revision_required"):
        authority.preview(
            "reminders.update",
            "target",
            {},
            account_binding="account",
            resource_binding="route",
            revision=None,
        )
    item = preview(authority)
    item.payload["title"] = "Edited by model"
    assert item.payload == {"title": "Before"}
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        authority.consume(
            item.request_id,
            account_binding="account",
            resource_binding="route",
            revision="v1",
        )
    with pytest.raises(WriteDenied, match="preview_mismatch"):
        authority.approve_from_operator(item.request_id, "forged")
    authority.approve_from_operator(item.request_id, item.digest)
    accepted = authority.consume(
        item.request_id,
        account_binding="account",
        resource_binding="route",
        revision="v1",
    )
    assert accepted.payload == {"title": "Before"}
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        authority.consume(
            item.request_id,
            account_binding="account",
            resource_binding="route",
            revision="v1",
        )


@pytest.mark.parametrize("binding", ["account", "resource", "revision"])
def test_approved_preview_cannot_cross_accounts_routes_or_revisions(
    binding: str,
) -> None:
    """State is revalidated in the owner lane immediately before dispatch."""
    authority = ledger()
    item = preview(authority)
    authority.approve_from_operator(item.request_id, item.digest)
    state = {
        "account_binding": "account",
        "resource_binding": "route",
        "revision": "v1",
    }
    state[
        {
            "account": "account_binding",
            "resource": "resource_binding",
            "revision": "revision",
        }[binding]
    ] = "changed"
    with pytest.raises(WriteDenied, match="stale_preview"):
        authority.consume(item.request_id, **state)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        authority.approve_from_operator(item.request_id, item.digest)


def test_expiration_and_capacity_are_fail_closed() -> None:
    """An expired approval frees space but never authorizes a subsequent write."""
    authority = ApprovalLedger(frozenset({"reminders.update"}), ttl=1, capacity=1)
    with patch("pyicloud.mcp.authorization.time.monotonic", return_value=1):
        item = preview(authority)
        with pytest.raises(WriteDenied, match="approval_queue_full"):
            preview(authority)
    with patch("pyicloud.mcp.authorization.time.monotonic", return_value=3):
        with pytest.raises(WriteDenied, match="expired_preview"):
            authority.approve_from_operator(item.request_id, item.digest)
        assert preview(authority).request_id != item.request_id
    with patch("pyicloud.mcp.authorization.time.monotonic", return_value=5):
        assert preview(authority).request_id != item.request_id


@pytest.mark.parametrize(
    "kwargs", [{"ttl": 0}, {"ttl": 601}, {"capacity": 0}, {"capacity": 257}]
)
def test_invalid_approval_limits(kwargs: dict[str, Any]) -> None:
    """No unbounded approval queue or indefinite validity is permitted."""
    with pytest.raises(ValueError):
        ApprovalLedger(**kwargs)


def test_invalid_target_and_oversized_payload_are_rejected() -> None:
    """Reject invalid bindings or giant content before requesting operator approval."""
    authority = ledger()
    invalid_payload = {"value": float("nan")}
    with pytest.raises(WriteDenied, match="invalid_payload"):
        authority.preview(
            "reminders.update",
            "target",
            invalid_payload,
            account_binding="account",
            resource_binding="route",
            revision="v1",
        )
    with pytest.raises(WriteDenied, match="invalid_target"):
        authority.preview(
            "reminders.update",
            "",
            {},
            account_binding="account",
            resource_binding="route",
            revision="v1",
        )
    with pytest.raises(WriteDenied, match="payload_too_large"):
        authority.preview(
            "reminders.update",
            "target",
            {"text": "x" * 65536},
            account_binding="account",
            resource_binding="route",
            revision="v1",
        )
