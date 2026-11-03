"""High-risk approval tests execute no account operations or actual mutations."""

from collections.abc import Iterator
from dataclasses import replace
import io
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from pyicloud.mcp.approval_channel import ApprovalMailbox
from pyicloud.mcp.authorization import ApprovalLedger, MutationPreview, WriteDenied
from pyicloud.mcp.context import ServerConfig
from tests.test_mcp_approval_channel import (
    sandbox_directory as directory_fixture,
)
from tests.test_mcp_approval_channel import (
    terminal,
)


@pytest.fixture(name="directory")
def approval_directory() -> Iterator[Path]:
    """Use the established sandbox descriptor shim, never live approval storage."""
    yield from cast(Any, directory_fixture).__wrapped__()


def high_preview(
    ledger: ApprovalLedger, action: str = "mail.send_draft"
) -> MutationPreview:
    """Only a prospective action label, never a dispatch path or live target."""
    return ledger.preview(
        action,
        "Synthetic",
        {"risk": "standard", "confirm": True},
        account_binding="A",
        resource_binding="R",
        revision="V",
    )


@pytest.mark.parametrize(
    "action",
    [
        "mail.send_draft",
        "mail.delete_permanently",
        "drive.upload",
        "unknown.new_action",
    ],
)
def test_high_risk_needs_separate_startup_selection(action: str) -> None:
    """An ordinary allowlist or caller risk field cannot grant elevated permission."""
    with pytest.raises(WriteDenied, match="high_risk_disabled"):
        high_preview(ApprovalLedger(frozenset({action})), action)
    ledger = ApprovalLedger(frozenset({action}), high_risk_actions=frozenset({action}))
    preview = high_preview(ledger, action)
    assert preview.risk == "high"
    with pytest.raises(WriteDenied, match="high_risk_operator_approval_required"):
        ledger.approve_from_operator(preview.request_id, preview.digest)
    ledger.approve_from_operator(preview.request_id, preview.digest, high_risk=True)
    assert (
        ledger.consume(
            preview.request_id, account_binding="A", resource_binding="R", revision="V"
        ).risk
        == "high"
    )
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        ledger.consume(
            preview.request_id, account_binding="A", resource_binding="R", revision="V"
        )


def test_invalid_high_risk_selections_and_standard_tier_mismatch() -> None:
    """Elevated authorization cannot be attached to unrelated ordinary actions."""
    with pytest.raises(ValueError):
        ApprovalLedger(high_risk_actions=frozenset({"mail.send_draft"}))
    with pytest.raises(ValueError):
        ApprovalLedger(
            frozenset({"mail.create_draft"}),
            high_risk_actions=frozenset({"mail.create_draft"}),
        )
    ledger = ApprovalLedger(frozenset({"mail.create_draft"}))
    preview = ledger.preview(
        "mail.create_draft",
        "Synthetic",
        {},
        account_binding="A",
        resource_binding="R",
        revision="V",
    )
    with pytest.raises(WriteDenied, match="risk_mismatch"):
        ledger.approve_from_operator(preview.request_id, preview.digest, high_risk=True)


def test_high_risk_terminal_receipt_requires_distinct_phrase(directory: Path) -> None:
    """Only the separate private terminal can produce the elevated exact receipt."""
    ledger = ApprovalLedger(
        frozenset({"mail.send_draft"}), high_risk_actions=frozenset({"mail.send_draft"})
    )
    preview = high_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
        with pytest.raises(WriteDenied, match="operator_declined"):
            mailbox.decide(
                preview.request_id,
                terminal("APPROVE " + preview.request_id),
                io.StringIO(),
            )
        display = io.StringIO()
        mailbox.decide(
            preview.request_id,
            terminal("APPROVE HIGH-RISK " + preview.request_id),
            display,
        )
        assert "HIGH-RISK" in display.getvalue()
        mailbox.accept(preview.request_id, ledger)
    ledger.consume(
        preview.request_id, account_binding="A", resource_binding="R", revision="V"
    )


def test_downgraded_operator_record_cannot_approve_high_risk(directory: Path) -> None:
    """Displayed or caller-copied metadata cannot change the ledger's bound tier."""
    ledger = ApprovalLedger(
        frozenset({"mail.send_draft"}), high_risk_actions=frozenset({"mail.send_draft"})
    )
    preview = high_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(replace(preview, risk="standard"))
        mailbox.decide(
            preview.request_id, terminal("APPROVE " + preview.request_id), io.StringIO()
        )
        with pytest.raises(WriteDenied, match="high_risk_operator_approval_required"):
            mailbox.accept(preview.request_id, ledger)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        ledger.consume(
            preview.request_id, account_binding="A", resource_binding="R", revision="V"
        )


def test_unimplemented_high_risk_actions_remain_unavailable() -> None:
    """Risk primitives cannot enable an unsupported deletion or upload adapter."""
    with pytest.raises(ValueError, match="Unsupported write action"):
        ServerConfig(
            username="synthetic",
            session_dir="unused",
            services=("mail",),
            write_actions=("mail.delete_permanently",),
            approval_dir="unused",
        )


@pytest.mark.parametrize("risk", ["unknown", [], True])
def test_invalid_private_risk_records_are_refused(directory: Path, risk: Any) -> None:
    """Malformed metadata cannot bypass terminal or ledger tier checks."""
    ledger = ApprovalLedger(
        frozenset({"mail.send_draft"}), high_risk_actions=frozenset({"mail.send_draft"})
    )
    preview = high_preview(ledger)
    record = {"request_id": preview.request_id, "digest": preview.digest, "risk": risk}
    with (
        ApprovalMailbox(str(directory)) as mailbox,
        patch.object(mailbox, "_read", return_value=record),
    ):
        with pytest.raises(WriteDenied, match="invalid_approval_record"):
            mailbox.decide(
                preview.request_id,
                terminal("APPROVE " + preview.request_id),
                io.StringIO(),
            )
        with pytest.raises(WriteDenied, match="invalid_approval_record"):
            mailbox.accept(preview.request_id, ledger)
