"""Threaded draft creation, stored-part provenance, edits, and failure guards."""
# cspell:words cachedpart headersonly

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import (
    MailBody,
    MailDraft,
    MailDraftThreading,
    MailError,
    MailFolder,
    MailMessage,
    MailWriteUncertain,
)
from pyicloud.services.mail._draft_models import DraftStoredReference
from pyicloud.services.mail._draft_uploads import attachment_references
from pyicloud.services.mail._threading import checked_threading, source_threading
from tests.fixture_loader import load_fixture
from tests.test_mail_drafts import (
    DraftCase,
    build_draft_case,
    sandbox_directory,
    uploaded_attachment,
)

PARENT = "<parent@example.test>"
ANCESTOR = "<ancestor@example.test>"


@pytest.fixture(name="case")
def conversation_case() -> DraftCase:
    """Reuse the HTTP-only synthetic server without importing pytest fixtures."""
    return build_draft_case()


@pytest.fixture(name="tmp_path")
def conversation_directory() -> Iterator[Path]:
    """Use the same sanctioned file sandbox as the ordinary draft tests."""
    yield from sandbox_directory()


def source(case: DraftCase) -> MailMessage:
    """One synthetic Sent copy with an ordinary attachment, never real mail."""
    folder: dict[str, Any] = {
        "identifier": "sent",
        "name": "Sent Messages",
        "uidValidity": 99,
        "flags": 0,
    }
    case.folders.append(folder)
    current = case.records[100]
    current.update(mboxRef={"id": "sent"}, hasAttachment=True)
    current["parts"].append({
        "partId": "2.2",
        "contentType": "text/plain",
        "size": 106,
        "fileName": "original.txt",
        "attach": True,
    })
    case.body_headers_by_uid[100] = (
        f"From: primary@example.test\r\nMessage-ID: {PARENT}\r\n"
        f"References: {ANCESTOR}\r\n"
    )
    return MailMessage(**current, folder=MailFolder(**folder))


def create(
    case: DraftCase, message: MailMessage, mode: str, **options: Any
) -> MailDraft:
    """Drive the public service, mocking only HTTP through the shared case."""
    method = (
        case.service.create_reply_draft
        if mode == "reply"
        else case.service.create_forward_draft
    )
    return method(
        message,
        sender="primary@example.test",
        to=("recipient@example.test",),
        text="Explicit full body",
        **options,
    )


@pytest.mark.parametrize("mode", ["reply", "forward"])
def test_conversation_create_edit_send_preserves_context(
    case: DraftCase, mode: str
) -> None:
    """Requests and handles retain headers, attachment kind and rotating UIDs."""
    original = source(case)
    draft = create(case, original, mode)
    payload = case.session.request_raw.call_args.kwargs["json"]
    assert payload["mode"] == mode
    assert payload["headerReferences"] == [ANCESTOR, PARENT]
    assert payload.get("headerInReplyTo") == ([PARENT] if mode == "reply" else None)
    assert "draftGuid" not in payload
    assert payload["subject"] == ("Re: Before" if mode == "reply" else "Fwd: Before")
    assert payload["textBody"] == "Explicit full body"
    if mode == "reply":
        assert not draft.attachments
    else:
        assert payload["attachments"] == [
            load_fixture("mail", "draft_forward_stored_reference.json")
        ]
        assert draft.attachments[0].source_message == original
        assert isinstance(draft.attachments[0].reference, DraftStoredReference)
    restored = MailDraft.model_validate_json(draft.model_dump_json())
    assert restored == draft
    case.wrote = False
    case.next_uid = 102
    edited = case.service.edit_draft(
        restored, subject="Explicit revised subject", text="Revised full body", html=""
    )
    request = case.session.request_raw.call_args.kwargs["json"]
    assert request["draftGuid"] == "Drafts/101"
    assert request["mode"] == mode
    assert request["headerReferences"] == [ANCESTOR, PARENT]
    assert edited.threading == draft.threading
    assert edited.attachments == draft.attachments
    case.wrote = False
    case.session.request_raw.reset_mock()
    assert case.service.send_draft(edited, confirm=True).acknowledged
    case.session.request_raw.assert_called_once()
    assert case.session.request_raw.call_args.args[1].endswith("/draft/send")


@pytest.mark.parametrize("mode", ["reply", "forward"])
def test_conversation_adds_explicit_uploaded_parts(case: DraftCase, mode: str) -> None:
    """Cached parts require mappings even when stored source parts do not."""
    draft = create(case, source(case), mode, attachments=(uploaded_attachment(case),))
    assert len(draft.attachments) == (1 if mode == "reply" else 2)
    assert draft.attachments[-1].reference.guid.startswith("cachedpart:")
    case.session.request_raw.assert_called_once()


def test_forward_retained_versions_are_explicit_and_never_implicitly_sent(
    case: DraftCase,
) -> None:
    """Copy-on-save must not hide or automatically delete old drafts."""
    draft = create(case, source(case), "forward")
    case.keep_previous = True
    for uid in (102, 103):
        case.wrote = False
        case.next_uid = uid
        draft = case.service.edit_draft(
            draft, subject=f"Version {uid}", text="Body", html=""
        )
    assert [m.uid for m in draft.retained_drafts] == [101, 102]
    assert all(uid in case.records for uid in (101, 102, 103))
    assert MailDraft.model_validate_json(draft.model_dump_json()) == draft
    case.wrote = False
    case.keep_previous = False
    case.session.request_raw.reset_mock()
    result = case.service.send_draft(draft, confirm=True)
    assert result.retained_draft_ids == ("draft-101", "draft-102")
    case.session.request_raw.assert_called_once()
    assert all(uid in case.records for uid in (101, 102))
    assert 103 not in case.records


@pytest.mark.parametrize("changed_id", ["unexpected", "draft-102"])
def test_forward_retention_requires_exact_previous_identity(
    case: DraftCase, changed_id: str
) -> None:
    """Replaced old records or aliased current IDs are not verified retention."""
    draft = create(case, source(case), "forward")
    case.keep_previous = True
    case.next_uid = 102
    case.wrote = False
    original = case.service.message_by_uid

    def lookup(folder: MailFolder, uid: int) -> MailMessage | None:
        current = original(folder, uid)
        if case.wrote and uid == 101 and current is not None:
            return current.model_copy(update={"id": changed_id})
        return current

    with (
        patch.object(case.service, "message_by_uid", side_effect=lookup),
        pytest.raises(MailWriteUncertain),
    ):
        case.service.edit_draft(draft, subject="Edited", text="Body", html="")


@pytest.mark.parametrize("output", ["json", "text"])
def test_retained_forward_versions_visible_in_cli(
    case: DraftCase, tmp_path: Path, output: str
) -> None:
    """Text and JSON report retained old versions rather than hiding them."""
    draft = create(case, source(case), "forward")
    state, text, html = (
        tmp_path / name for name in ("draft.json", "text.txt", "html.txt")
    )
    state.write_text(draft.model_dump_json(), encoding="utf-8")
    text.write_text("Revised", encoding="utf-8")
    html.write_text("", encoding="utf-8")
    case.keep_previous = True
    case.next_uid = 102
    case.wrote = False
    api = MagicMock()
    api.mail = case.service
    api.account_name = "login@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "edit-draft",
                draft.message.id,
                "--folder-id",
                "drafts",
                "--sender",
                "primary@example.test",
                "--draft-state",
                str(state),
                "--subject",
                "Edited",
                "--text-file",
                str(text),
                "--html-file",
                str(html),
                "--format",
                output,
            ],
        )
    assert result.exit_code == 0, result.exception
    if output == "json":
        assert json.loads(result.stdout)["retained_drafts"][0]["id"] == "draft-101"
    else:
        assert "draft-101" in result.stdout
        assert "Retained" in result.stdout


def test_forward_explicitly_omits_source_attachments(case: DraftCase) -> None:
    """Omission is explicit, never a response to unknown part metadata."""
    draft = create(case, source(case), "forward", include_original_attachments=False)
    assert draft.attachments == ()
    assert case.session.request_raw.call_args.kwargs["json"]["attachments"] == []


@pytest.mark.parametrize("mode", ["reply", "forward"])
def test_subject_prefix_is_not_duplicated(case: DraftCase, mode: str) -> None:
    """Existing standard prefixes are retained, not repeatedly prepended."""
    message = source(case)
    case.records[100]["subject"] = "Re: Test" if mode == "reply" else "Fwd: Test"
    assert create(case, message, mode).message.subject == case.records[100]["subject"]


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "identity",
        "body",
        "headers",
        "size",
        "name",
        "incomplete",
        "part",
        "choice",
        "object",
    ],
)
def test_source_failure_prevents_save(case: DraftCase, failure: str) -> None:
    """A stale source or unknown attachment cannot produce a destructive forward."""
    message: Any = source(case)
    options: dict[str, Any] = {}
    if failure == "missing":
        case.records.pop(100)
    elif failure == "identity":
        case.records[100]["identifier"] = "changed"
    elif failure == "body":
        case.body_guid = "message:INBOX/100"
    elif failure == "headers":
        case.body_headers_by_uid[100] = "From: primary@example.test\r\n"
    elif failure in {"size", "name"}:
        case.records[100]["parts"][-1]["size" if failure == "size" else "fileName"] = (
            None
        )
    elif failure == "incomplete":
        case.records[100]["hasAttachment"] = False
    elif failure == "part":
        case.records[100]["parts"][-1]["partId"] = "malformed"
    elif failure == "choice":
        options["include_original_attachments"] = 1
    else:
        message = None
    with pytest.raises((MailError, ValueError)):
        create(case, message, "forward", **options)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("mode", ["reply", "forward"])
def test_bad_inputs_precede_reads(case: DraftCase, mode: str) -> None:
    """Invalid bodies are not an excuse to fetch private source messages."""
    message = source(case)
    with pytest.raises(ValueError):
        method = (
            case.service.create_reply_draft
            if mode == "reply"
            else case.service.create_forward_draft
        )
        method(
            message,
            sender="primary@example.test",
            to=("bad\r\nBcc: hidden@example.test",),
            text="Body",
        )
    assert case.session.mock_calls == []


@pytest.mark.parametrize("change", ["missing", "identity", "parts"])
def test_stored_part_source_is_rechecked_before_edit(
    case: DraftCase, change: str
) -> None:
    """Saved handles do not authorize following moved or replaced attachments."""
    draft = create(case, source(case), "forward")
    case.session.request_raw.reset_mock()
    case.wrote = False
    if change == "missing":
        case.records.pop(100)
    elif change == "identity":
        case.records[100]["identifier"] = "replacement"
    else:
        case.records[100]["parts"][-1]["size"] = 1000
    with pytest.raises(MailError, match="source changed"):
        case.service.edit_draft(draft, subject="Edited", text="Text", html="")
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("change", ["references", "reply", "missing-state"])
def test_threading_change_prevents_send(case: DraftCase, change: str) -> None:
    """Neither a changed header nor an old context-free handle may erase context."""
    draft = create(case, source(case), "reply")
    case.wrote = False
    case.session.request_raw.reset_mock()
    if change == "references":
        case.body_headers = case.body_headers.replace(ANCESTOR, "<other@example.test>")
    elif change == "reply":
        case.body_headers = case.body_headers.replace(
            "In-Reply-To: " + PARENT, "In-Reply-To: <other@example.test>"
        )
    else:
        draft = draft.model_copy(update={"threading": None})
    with pytest.raises(MailError, match="threading"):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_not_called()


def test_missing_saved_threading_is_uncertain(case: DraftCase) -> None:
    """A save that stripped references may have happened; never retry it."""
    message = source(case)
    case.after_headers = "From: primary@example.test\r\nTo: recipient@example.test\r\n"
    with pytest.raises(MailWriteUncertain):
        create(case, message, "reply")
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "change", ["source", "guid", "url", "cached-source", "object", "mapping"]
)
def test_stored_reference_validation(case: DraftCase, change: str) -> None:
    """Persisted handles cannot change attachment identity, source or destination."""
    draft = create(case, source(case), "forward")
    attachment = draft.attachments[0]
    case.session.request_raw.reset_mock()
    if change == "source":
        attachment = attachment.model_copy(update={"source_message": None})
    elif change in {"guid", "url"}:
        reference = attachment.reference.model_copy(
            update={
                "guid"
                if change == "guid"
                else "url": "messagepart:Sent Messages/100-99"
                if change == "guid"
                else "https://attacker.example.test/part"
            }
        )
        attachment = attachment.model_copy(update={"reference": reference})
    elif change == "cached-source":
        attachment = uploaded_attachment(case).model_copy(
            update={"source_message": attachment.source_message}
        )
    elif change == "object":
        attachment = attachment.model_copy(update={"reference": object()})
    else:
        case.save_result = {
            "uid": "102",
            "attachments": [
                {
                    "requestGuid": attachment.reference.guid,
                    "newGuid": attachment.reference.guid,
                }
            ],
        }
        case.next_uid = 102
        with pytest.raises(MailWriteUncertain):
            case.service.edit_draft(draft, subject="Edited", text="Text", html="")
        case.session.request_raw.assert_called_once()
        return
    with pytest.raises((ValueError, MailError)):
        attachment_references((attachment,), case.service.service_root, "12345")
    case.session.request_raw.assert_not_called()


def headers(value: str) -> MailBody:
    """A synthetic body used only for field-level header edge cases."""
    return MailBody(
        guid="message:Sent Messages/100",
        longHeader=value,
        contentType="text/plain",
        parts=[],
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Message-ID: bad\r\n",
        f"Message-ID: {PARENT}\r\nMessage-ID: {PARENT}\r\n",
        "Malformed header\r\n",
        "X-Long: " + "a" * 100_001,
        f"Message-ID: {PARENT}\r\nReferences: <invalid>\r\n",
        f"Message-ID: {PARENT}\r\nReferences: "
        + " ".join(f"<a{i}@example.test>" for i in range(101)),
        "Message-ID: <" + "a" * 999 + "@example.test>\r\n",
    ],
)
def test_source_header_failures(value: str) -> None:
    """Malformed/large chains fail explicitly, never truncating references."""
    body = headers(value)
    with pytest.raises(MailError):
        source_threading(body, "reply")


def test_reference_boundary_and_deduplication() -> None:
    """Preserve order, append the parent once, and refuse a 101st reference."""
    body = headers(
        f"Message-ID: {PARENT}\r\nReferences: {ANCESTOR} {PARENT} {ANCESTOR}\r\n"
    )
    assert source_threading(body, "reply").references == (ANCESTOR, PARENT)
    references = " ".join(f"<a{i}@example.test>" for i in range(100))
    oversized = headers(f"Message-ID: {PARENT}\r\nReferences: {references}\r\n")
    with pytest.raises(MailError, match="cannot be preserved"):
        source_threading(oversized, "reply")
    checked_threading(headers("From: a@example.test\r\n"), None)
    checked_threading(headers("References: \r\nIn-Reply-To: \r\n"), None)
    with pytest.raises(ValueError):
        MailDraftThreading(mode="forward", references=(PARENT,), in_reply_to=(PARENT,))


@pytest.mark.parametrize("mode", ["reply", "forward"])
@pytest.mark.parametrize("output", ["json", "text"])
def test_mounted_conversation_cli(
    case: DraftCase, tmp_path: Path, mode: str, output: str
) -> None:
    """Explicit selectors, recipients and body file reach real service guards."""
    message = source(case)
    path = tmp_path / "body.txt"
    path.write_text("Explicit full body", encoding="utf-8")
    api = MagicMock()
    api.mail = case.service
    api.account_name = "login@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                mode + "-draft",
                message.id,
                "--folder-id",
                "sent",
                "--sender",
                "primary@example.test",
                "--to",
                "recipient@example.test",
                "--text-file",
                str(path),
                "--format",
                output,
            ],
        )
    assert result.exit_code == 0, result.exception
    assert "Explicit full body" not in result.stdout
    case.session.request_raw.assert_called_once()
    if output == "json":
        assert json.loads(result.stdout)["threading"]["mode"] == mode


@pytest.mark.parametrize("mode", ["reply", "forward"])
@pytest.mark.parametrize("sender", ["", "   "])
def test_conversation_cli_empty_sender_precedes_io(
    case: DraftCase, tmp_path: Path, mode: str, sender: str
) -> None:
    """Invalid draft selectors are rejected before files or remote state are read."""
    with (
        patch("pyicloud.cli.commands.mail._draft_input") as read_body,
        patch.object(CLIState, "get_api") as auth,
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                mode + "-draft",
                "id",
                "--folder-id",
                "sent",
                "--sender",
                sender,
                "--to",
                "recipient@example.test",
                "--text-file",
                str(tmp_path / "missing.txt"),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    assert "--sender" in str(result.exception)
    read_body.assert_not_called()
    auth.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("mode", ["reply", "forward"])
def test_conversation_cli_namespace_hint_has_no_limit(
    case: DraftCase, tmp_path: Path, mode: str
) -> None:
    """Reply/forward commands also cannot suggest an unavailable lookup flag."""
    message = source(case)
    case.folders[-1].pop("uidValidity")
    path = tmp_path / "body.txt"
    path.write_text("Text", encoding="utf-8")
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                mode + "-draft",
                message.id,
                "--folder-id",
                "sent",
                "--sender",
                "primary@example.test",
                "--to",
                "recipient@example.test",
                "--text-file",
                str(path),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    assert "icloud mail folders" in str(result.exception)
    assert "--limit" not in str(result.exception)
    case.session.request_raw.assert_not_called()


def test_reply_cli_rejects_forward_only_option(tmp_path: Path) -> None:
    """An irrelevant flag must not be silently accepted."""
    path = tmp_path / "body.txt"
    path.write_text("Text", encoding="utf-8")
    with patch.object(CLIState, "get_api") as auth:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "reply-draft",
                "id",
                "--folder-id",
                "sent",
                "--sender",
                "primary@example.test",
                "--to",
                "recipient@example.test",
                "--text-file",
                str(path),
                "--omit-original-attachments",
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    auth.assert_not_called()
