"""Synthetic exact-content checks, not evidence of live server transformations."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.additional import AdditionalReads
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.context import ToolFailure
from pyicloud.mcp.mail_writes import MailWrites, canonical_draft_html, verify_draft_body
from pyicloud.mcp.reads import ReadTools
from pyicloud.mcp.saved_drafts import SavedDraftContext
from pyicloud.services.mail.models import (
    MailBody,
    MailBodyPart,
    MailboxReference,
    MailDraft,
    MailFolder,
    MailMessage,
    MailSender,
)
from tests.test_mcp_additional import owner_for


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("a\r\nb\rc\nd", "<div>a<br>b<br>c<br>d</div>"),
        ('<script>&"', '<div>&lt;script&gt;&amp;"</div>'),
        (" ", "<div> </div>"),
    ],
)
def test_canonical_body_matches_plain_only_save(text: str, expected: str) -> None:
    """Escape markup exactly while preserving ordinary text and whitespace."""
    assert canonical_draft_html(text) == expected


@pytest.mark.parametrize(
    "case", ["exact", "identity", "part", "mime", "multiple", "empty", "content"]
)
def test_postsave_body_requires_exact_identity_and_content(case: str) -> None:
    """Generic acknowledgements and equivalent-looking HTML are not proof."""
    body = MailBody(
        guid="message:Drafts/101",
        longHeader="From: user@example.test\r\nSubject: Approved\r\n",
        contentType="text/html",
        parts=[
            MailBodyPart(guid="messagepart:Drafts/101-1", content="<div>Approved</div>")
        ],
    )
    if case == "identity":
        body = body.model_copy(update={"guid": "message:Drafts/102"})
    elif case == "part":
        body = body.model_copy(
            update={
                "parts": [
                    body.parts[0].model_copy(
                        update={"guid": "messagepart:Drafts/102-1"}
                    )
                ]
            }
        )
    elif case == "mime":
        body = body.model_copy(update={"content_type": "text/plain"})
    elif case == "multiple":
        body.parts.append(body.parts[0])
    elif case == "empty":
        body = body.model_copy(update={"parts": []})
    elif case == "content":
        body = body.model_copy(
            update={
                "parts": [
                    body.parts[0].model_copy(
                        update={"content": "<div>Unapproved</div>"}
                    )
                ]
            }
        )
    if case == "exact":
        verify_draft_body(body, 101, "Approved")
    else:
        with pytest.raises(WriteDenied, match="mutation_unverified"):
            verify_draft_body(body, 101, "Approved")


def setup_mail() -> tuple[MailWrites, MagicMock, dict[str, Any]]:
    """No account or transport; only verified typed facade results."""
    client = MagicMock()
    client.mail = MagicMock()
    client.params = {"dsid": "123"}
    client.mail.service_root = "https://example.test"
    folder = MailFolder(identifier="drafts", name="Drafts", uidValidity=1)
    sender = MailSender(address="user@example.test", display_name="Synthetic")
    client.mail.draft_folder.return_value = folder
    client.mail.senders.return_value = [sender]
    client.mail.create_draft.return_value = MailDraft(
        message=MailMessage(
            identifier="draft-101",
            uid=101,
            mboxRef=MailboxReference(id="drafts"),
            subject="Approved",
            folder=folder,
        ),
        sender=sender,
    )
    client.mail.body.return_value = MailBody(
        guid="message:Drafts/101",
        longHeader="From: user@example.test\r\nSubject: Approved\r\n",
        contentType="text/html",
        parts=[
            MailBodyPart(guid="messagepart:Drafts/101-1", content="<div>Approved</div>")
        ],
    )
    data = {
        "sender": sender.address,
        "subject": "Approved",
        "text": "Approved",
        "to": [],
        "cc": [],
        "bcc": [],
    }
    return (
        MailWrites(
            ReadTools(owner_for(client)),
            ApprovalLedger(frozenset({"mail.create_draft"})),
        ),
        client,
        data,
    )


@pytest.mark.asyncio
async def test_draft_requires_receipt_then_saves_once() -> None:
    """Preview is read-only; one exact receipt permits one non-sending save."""
    writes, client, data = setup_mail()
    publish = MagicMock()
    preview = await writes.prepare(data, publish)
    publish.assert_called_once_with(preview)
    client.mail.create_draft.assert_not_called()
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        await writes.apply(preview.request_id)

    def accept(identifier: str) -> None:
        writes.ledger.approve_from_operator(identifier, preview.digest)

    result = await writes.apply(preview.request_id, accept)
    assert result["action"] == "mail.create_draft"
    assert result["id"] == "draft-101"
    assert isinstance(result["handle"], str)
    context = writes.additional.resolve("mail-draft", result["handle"])
    assert isinstance(context, SavedDraftContext)
    assert context.draft == client.mail.create_draft.return_value
    assert context.payload == data
    assert context.account_binding == "123"
    detached = context.payload
    detached["text"] = "Changed"
    detached["to"].append("other@example.test")
    assert context.payload == data
    assert "Approved" not in repr(context)
    with pytest.raises(ToolFailure):
        writes.additional.resolve("mail-message", result["handle"])
    other_registry = AdditionalReads(writes.reads)
    with pytest.raises(ToolFailure):
        other_registry.resolve("mail-draft", result["handle"])
    with (
        patch("pyicloud.mcp.additional.time.monotonic", return_value=10**30),
        pytest.raises(ToolFailure),
    ):
        writes.additional.resolve("mail-draft", result["handle"])
    client.mail.create_draft.assert_called_once_with(
        sender="user@example.test",
        subject="Approved",
        text="Approved",
        to=(),
        cc=(),
        bcc=(),
    )
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
async def test_disabled_mail_denies_before_reads_or_approval_consumption() -> None:
    """Service removal is effective before any fresh read or draft save."""
    writes, client, data = setup_mail()
    preview = await writes.prepare(data)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    owner = cast(Any, writes.reads.owner)
    owner.config = replace(owner.config, services=("drive",))
    client.mail.draft_folder.reset_mock()
    with pytest.raises(WriteDenied, match="write_disabled"):
        await writes.apply(preview.request_id)
    client.mail.draft_folder.assert_not_called()
    client.mail.create_draft.assert_not_called()
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)


@pytest.mark.asyncio
async def test_evicted_payload_mapping_does_not_abort_approved_apply() -> None:
    """A queued preparation cannot cause KeyError after an application captures data."""
    writes, client, data = setup_mail()
    preview = await writes.prepare(data)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)

    async def queued_run(operation: Callable[[Any], Any]) -> Any:
        writes._payloads.pop(preview.request_id)  # pylint: disable=protected-access
        return operation(client)

    cast(Any, writes.reads.owner).run = queued_run
    assert (await writes.apply(preview.request_id))["id"] == "draft-101"
    client.mail.create_draft.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        "",
        "From: other@example.test\r\n",
        "From: user@example.test\r\nFrom: user@example.test\r\n",
    ],
)
async def test_saved_body_sender_mismatch_has_no_handle_or_replay(headers: str) -> None:
    """Created drafts need one independently read approved From address."""
    writes, client, data = setup_mail()
    preview = await writes.prepare(data)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    body = client.mail.body.return_value
    client.mail.body.return_value = body.model_copy(update={"headers": headers})
    with pytest.raises(WriteDenied, match="mutation_uncertain"):
        await writes.apply(preview.request_id)
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.mail.create_draft.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["account", "route", "folder", "sender", "missing", "invalid"]
)
async def test_mail_fresh_bindings_prevent_changed_target(case: str) -> None:
    """Disabled sender, mailbox incarnation or route changes cannot dispatch."""
    writes, client, data = setup_mail()
    preview = await writes.prepare(data)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "account":
        client.params["dsid"] = "124"
    elif case == "route":
        client.mail.service_root = "https://other.example.test"
    elif case == "folder":
        client.mail.draft_folder.return_value = (
            client.mail.draft_folder.return_value.model_copy(update={"uid_validity": 2})
        )
    elif case == "sender":
        client.mail.senders.return_value = [
            MailSender(address="user@example.test", display_name="Changed")
        ]
    elif case == "missing":
        client.mail.senders.return_value = []
    else:
        client.params = {}
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    client.mail.create_draft.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["body", "identity", "transport"])
async def test_mail_uncertainty_never_retries(case: str) -> None:
    """All post-dispatch failures retire approval regardless of acknowledgements."""
    writes, client, data = setup_mail()
    preview = await writes.prepare(data)
    writes.ledger.approve_from_operator(preview.request_id, preview.digest)
    if case == "body":
        client.mail.body.return_value = client.mail.body.return_value.model_copy(
            update={"parts": []}
        )
    elif case == "identity":
        client.mail.create_draft.return_value = None
    else:
        client.mail.create_draft.side_effect = OSError("private")
    with patch.object(writes.additional, "remember") as publish:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
        publish.assert_not_called()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)
    client.mail.create_draft.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"html": "markup"},
        {"text": "x" * 32769},
        {"subject": "x" * 4097},
        {"to": "user@example.test"},
        {"to": ["not an address"]},
        {"cc": ["user@example.test"] * 51},
        {"subject": "bad\nheader"},
    ],
)
async def test_mail_payload_invalid_before_discovery(change: dict[str, Any]) -> None:
    """No implicit recipient conversions, HTML, attachments or unbounded content."""
    writes, client, data = setup_mail()
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await writes.prepare({**data, **change})
    client.mail.draft_folder.assert_not_called()
