"""Draft UID rotation, identity guards, and exactly-once write attempts."""
# cspell:words cachedpart

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from email.errors import MessageError
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import (
    MailDraft,
    MailDraftAttachment,
    MailError,
    MailFolder,
    MailMessage,
    MailSender,
    MailService,
    MailWriteRejected,
    MailWriteUncertain,
)
from pyicloud.services.mail._draft_headers import parse_headers
from pyicloud.services.mail._draft_models import DraftAttachmentReference
from pyicloud.services.mail._drafts import header_sender
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@dataclass
class DraftCase:
    """A minimal synthetic server; do not stub the public draft implementation."""

    service: MailService
    session: MagicMock
    response: MagicMock
    folders: list[dict[str, Any]]
    records: dict[int, dict[str, Any]] = field(default_factory=dict)
    body_headers: str = (
        "From: Synthetic User <primary@example.test>\r\nSubject: Before\r\n"
    )
    body_guid: str | None = None
    body_headers_by_uid: dict[int, str] = field(default_factory=dict)
    save_result: dict[str, Any] | None = None
    next_uid: int = 101
    keep_previous: bool = False
    after_error: Exception | None = None
    after_record: dict[str, Any] | None = None
    after_headers: str | None = None
    wrote: bool = False


def record(uid: int = 100) -> dict[str, Any]:
    """One individual message, not a thread or a globally stable UID."""
    return {
        "identifier": f"draft-{uid}",
        "uid": uid,
        "mboxRef": {"id": "drafts"},
        "from": "Synthetic User <primary@example.test>",
        "subject": "Before",
        "hasAttachment": False,
        "parts": [{"partId": "1", "contentType": "text/plain", "attach": False}],
    }


def sandbox_directory() -> Iterator[Path]:
    """Keep file inputs inside the repository's sanctioned test sandbox."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mail-draft-", dir=root) as directory:
        try:
            yield Path(directory)
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


@pytest.fixture(name="case")
def draft_case() -> DraftCase:
    """Bind the synthetic server factory to the local pytest fixture."""
    return build_draft_case()


@pytest.fixture(name="tmp_path")
def draft_directory() -> Iterator[Path]:
    """Use the same sandbox factory as the conversation-draft tests."""
    yield from sandbox_directory()


def build_draft_case() -> DraftCase:
    """Only HTTP is mocked; request models, metadata lookups and guards are real."""
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    service = MailService(
        "https://gateway.example.test",
        session,
        {
            "dsid": "12345",
            "clientMasteringNumber": "synthetic-build",
            "clientIntent": "stale",
        },
        settings_root="https://settings.example.test",
    )
    case = DraftCase(
        service,
        session,
        response,
        [
            {"identifier": "drafts", "name": "Drafts", "uidValidity": 77, "flags": 0},
            {
                "identifier": "trash",
                "name": "Deleted Messages",
                "uidValidity": 88,
                "flags": 0,
            },
            {"identifier": "testing", "name": "Testing", "uidValidity": 99, "flags": 0},
        ],
        {100: record()},
    )
    preferences = MagicMock(spec=Response)
    preferences.status_code = 200
    preferences.json.side_effect = lambda: load_fixture(
        "mail", "sender_preferences_response.json"
    )
    session.get.return_value = preferences

    def read(url: str, **kwargs: Any) -> MagicMock:
        if case.wrote and case.after_error is not None:
            raise case.after_error
        result = MagicMock(spec=Response)
        result.status_code = 200
        query = kwargs["json"]
        if url.endswith("message/get"):
            uid = int(query["uid"])
            result.json.return_value = {
                "guid": case.body_guid
                or f"message:{query['sessionHeaders']['folder']}/{uid}",
                "longHeader": case.after_headers
                if case.wrote and case.after_headers is not None
                else case.body_headers_by_uid.get(uid, case.body_headers),
                "contentType": "text/plain",
                "parts": [{"guid": f"messagepart:Drafts/{uid}-1", "content": "Before"}],
            }
        elif query["domain"] == "mailbox":
            result.json.return_value = {"domainObjects": deepcopy(case.folders)}
            predicate = query["predicate"]
            if predicate["expression"]["property"] == "identifier":
                result.json.return_value["domainObjects"] = [
                    f for f in case.folders if f["identifier"] == predicate["value"]
                ]
        else:
            clauses = [query["predicate"], *query["predicate"].get("and", [])]
            identity = next(
                c
                for c in clauses
                if c["expression"]["property"] in {"uid", "identifier"}
            )
            data = (
                case.records.get(identity["value"])
                if identity["expression"]["property"] == "uid"
                else next(
                    (
                        r
                        for r in case.records.values()
                        if r["identifier"] == identity["value"]
                    ),
                    None,
                )
            )
            if case.wrote and case.after_record is not None:
                data = case.after_record
            result.json.return_value = {
                "domainObjects": [] if data is None else [deepcopy(data)]
            }
        return result

    def write(_method: str, url: str, **kwargs: Any) -> MagicMock:
        case.wrote = True
        payload = kwargs["json"]
        if url.endswith("savedraft"):
            if "draftGuid" in payload and not case.keep_previous:
                case.records.pop(int(payload["draftGuid"].split("/")[-1]), None)
            current = record(case.next_uid)
            current["subject"] = payload["subject"]
            current["hasAttachment"] = bool(payload["attachments"])
            current["parts"] += [
                {
                    "partId": str(index + 2),
                    "contentType": reference["datatype"],
                    "attach": True,
                    "fileName": reference["name"],
                    "size": reference["size"],
                }
                for index, reference in enumerate(payload["attachments"])
            ]
            case.body_headers = (
                "From: "
                + payload["from"]
                + "\r\n"
                + "".join(
                    name + ": " + ", ".join(payload[name.lower()]) + "\r\n"
                    for name in ("To", "Cc", "Bcc")
                    if payload[name.lower()]
                )
            )
            case.records[case.next_uid] = current
            for header, key in (
                ("In-Reply-To", "headerInReplyTo"),
                ("References", "headerReferences"),
            ):
                if key in payload:
                    case.body_headers += header + ": " + " ".join(payload[key]) + "\r\n"
            data = load_fixture("mail", "draft_save_response.json")
            data["uid"] = str(case.next_uid)
            cached_parts = [
                r for r in payload["attachments"] if r["guid"].startswith("cachedpart:")
            ]
            if cached_parts:
                data["attachments"] = [
                    {"requestGuid": r["guid"], "newGuid": r["guid"]}
                    for r in cached_parts
                ]
            response.json.return_value = (
                data if case.save_result is None else case.save_result
            )
        elif url.endswith("/draft/send"):
            if not case.keep_previous:
                case.records.pop(int(payload["messageGuid"].split("/")[-1]), None)
            response.json.return_value = (
                load_fixture("mail", "draft_send_response.json")
                if case.save_result is None
                else case.save_result
            )
        else:
            case.records.clear()
            response.json.return_value = (
                {
                    "accountId": "12345",
                    "created": None,
                    "destroyed": None,
                    "newState": None,
                    "oldState": None,
                    "notCreated": None,
                    "notDestroyed": None,
                    "notUpdated": None,
                    "updated": {"draft-100": {}},
                }
                if case.save_result is None
                else case.save_result
            )
        return response

    session.post.side_effect = read
    session.request_raw.side_effect = write
    return case


def handle(case: DraftCase) -> MailDraft:
    """Construct a previously discovered handle; preflight must still re-read it."""
    return MailDraft(
        message=MailMessage(**case.records[100], folder=MailFolder(**case.folders[0])),
        sender=MailSender(
            address="primary@example.test", display_name="Synthetic User"
        ),
    )


def addressed_draft(case: DraftCase) -> MailDraft:
    """Save first, then count only the subsequent send attempt."""
    draft = case.service.create_draft(
        sender="primary@example.test", to=("recipient@example.test",), subject="Before"
    )
    case.session.reset_mock()
    case.wrote = False
    return draft


@pytest.mark.parametrize("confirmation", [False, None, 1, "yes"])
def test_send_requires_explicit_confirmation(
    case: DraftCase, confirmation: Any
) -> None:
    """Truthy values are not authorization and must not even start reads."""
    with pytest.raises(ValueError, match="confirm=True"):
        case.service.send_draft(handle(case), confirm=confirmation)
    assert case.session.mock_calls == []


def test_send_requires_handle_and_recipients(case: DraftCase) -> None:
    """A recipient-free draft or malformed handle cannot accidentally send."""
    with pytest.raises(ValueError, match="MailDraft"):
        case.service.send_draft(None, confirm=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="recipient"):
        case.service.send_draft(handle(case), confirm=True)
    invalid = handle(case).model_copy(update={"to": ("bad\r\nBcc: x@y.test",)})
    with pytest.raises(ValueError):
        case.service.send_draft(invalid, confirm=True)
    assert case.session.mock_calls == []


def test_send_exact_wire_and_source_acknowledgement(case: DraftCase) -> None:
    """No save, schedule, global-ID inference, or delivery claim is implicit."""
    draft = addressed_draft(case)
    result = case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_called_once()
    args, kwargs = case.session.request_raw.call_args
    assert args == ("POST", "https://gateway.example.test/mailws2/v1/draft/send")
    assert kwargs["json"] == load_fixture("mail", "draft_send_request.json")
    assert kwargs["timeout"] == (10.0, 60.0)
    assert kwargs["allow_redirects"] is False
    assert "clientIntent" not in kwargs["params"]
    assert result.model_dump() == {
        "source_message_id": "draft-101",
        "source_folder_id": "drafts",
        "source_uid": 101,
        "acknowledged": True,
        "delivery_confirmed": False,
        "retained_draft_ids": (),
    }
    assert 101 not in case.records


@pytest.mark.parametrize("change", ["subject", "identity", "sender", "recipient"])
def test_send_rejects_stale_draft(case: DraftCase, change: str) -> None:
    """Fresh checks precede irreversible submission."""
    draft = addressed_draft(case)
    if change == "subject":
        case.records[101]["subject"] = "Changed elsewhere"
    elif change == "identity":
        case.records[101]["identifier"] = "different-message"
    elif change == "sender":
        case.session.get.return_value.json.return_value = {"unexpected": True}
        case.session.get.return_value.json.side_effect = None
    else:
        case.body_headers = "From: primary@example.test\r\nTo: other@example.test\r\n"
    with pytest.raises(MailError):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_not_called()


def test_send_rejects_disabled_sender(case: DraftCase) -> None:
    """Previously verified handles do not keep disabled senders authorized."""
    draft = addressed_draft(case)
    with (
        patch.object(case.service, "senders", return_value=[]),
        pytest.raises(MailError, match="no longer enabled"),
    ):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("events", []),
        ("events", ["* 3 EXPUNGE", "* 4 EXPUNGE"]),
        ("events", ["* 101 EXISTS"]),
        ("events", ["* 0 EXPUNGE"]),
        ("unexpected", True),
        ("sessionHeaders", {"folder": "Inbox"}),
    ],
)
def test_send_malformed_acknowledgement_is_uncertain(
    case: DraftCase, key: str, value: Any
) -> None:
    """A submitted send is never classified as safely retryable."""
    draft = addressed_draft(case)
    case.save_result = load_fixture("mail", "draft_send_response.json")
    case.save_result[key] = value
    with pytest.raises(MailWriteUncertain, match="may already have been sent"):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("counter", [0, -1, True, "1001"])
def test_send_counter_validation(case: DraftCase, counter: Any) -> None:
    """Mutation response counters are positive integers, not coercible values."""
    draft = addressed_draft(case)
    case.save_result = load_fixture("mail", "draft_send_response.json")
    case.save_result["sessionHeaders"]["modseq"] = counter
    with pytest.raises(MailWriteUncertain):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["transport", "http", "json", "close", "read", "present"]
)
def test_send_failure_is_not_retried(case: DraftCase, failure: str) -> None:
    """Include errors after the request might already have taken effect."""
    draft = addressed_draft(case)
    if failure == "transport":
        case.session.request_raw.side_effect = ReadTimeout("synthetic")
    elif failure == "http":
        case.response.status_code = 429
    elif failure == "json":
        case.response.json.side_effect = ValueError("synthetic")
    elif failure == "close":
        case.response.close.side_effect = OSError("synthetic")
    elif failure == "read":
        case.after_error = MailError("synthetic")
    else:
        case.keep_previous = True
    with pytest.raises(MailWriteUncertain, match="may already have been sent"):
        case.service.send_draft(draft, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("mode", ["json", "text"])
def test_mounted_send_cli(case: DraftCase, tmp_path: Path, mode: str) -> None:
    """A persisted rich handle reaches the real service, without body output."""
    draft = addressed_draft(case)
    path = tmp_path / "draft.json"
    path.write_text(draft.model_dump_json(), encoding="utf-8")
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
                "send-draft",
                "draft-101",
                "--folder-id",
                "drafts",
                "--sender",
                "primary@example.test",
                "--draft-state",
                str(path),
                "--confirm",
                "--format",
                mode,
            ],
        )
    assert result.exit_code == 0, result.exception
    case.session.request_raw.assert_called_once()
    assert "recipient@example.test" not in result.stdout
    if mode == "json":
        assert json.loads(result.stdout)["delivery_confirmed"] is False
    else:
        assert "Delivery Not Confirmed" in " ".join(result.stdout.split())


@pytest.mark.parametrize("sender", ["", "   "])
def test_send_cli_empty_sender_precedes_state_and_auth(
    case: DraftCase, tmp_path: Path, sender: str
) -> None:
    """A bad selector cannot cause local state reads, authentication or sending."""
    with (
        patch("pyicloud.cli.commands.mail._saved_draft") as read_state,
        patch.object(CLIState, "get_api") as auth,
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "send-draft",
                "draft-100",
                "--folder-id",
                "drafts",
                "--sender",
                sender,
                "--draft-state",
                str(tmp_path / "missing.json"),
                "--confirm",
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    assert "--sender" in str(result.exception)
    read_state.assert_not_called()
    auth.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("failure", ["confirmation", "state", "missing-handle"])
def test_send_cli_refuses_before_auth(
    case: DraftCase, tmp_path: Path, failure: str
) -> None:
    """No remote work follows absent authorization or mismatched state."""
    path = tmp_path / "draft.json"
    path.write_text("{}", encoding="utf-8")
    args = [
        "mail",
        "send-draft",
        "draft-100",
        "--folder-id",
        "drafts",
        "--sender",
        "primary@example.test",
        "--draft-state",
        str(path),
    ]
    if failure != "confirmation":
        args.append("--confirm")
    state_patch = (
        patch("pyicloud.cli.commands.mail._saved_draft", return_value=None)
        if failure == "missing-handle"
        else patch.object(CLIState, "open")
    )
    auth = MagicMock()
    with (
        patch.object(CLIState, "get_api", auth),
        state_patch,
    ):
        result = CliRunner().invoke(app, args)
    assert isinstance(result.exception, CLIAbort)
    auth.assert_not_called()
    case.session.request_raw.assert_not_called()


def save(case: DraftCase, edit: bool) -> MailDraft:
    """Both public save routes supply explicit full replacement content."""
    values: dict[str, Any] = {
        "subject": "After",
        "text": "New text",
        "html": "<p>New text</p>",
    }
    if edit:
        return case.service.edit_draft(handle(case), **values)
    return case.service.create_draft(sender="primary@example.test", **values)


@pytest.mark.parametrize("edit", [False, True])
def test_save_exact_wire_and_replacement_handle(case: DraftCase, edit: bool) -> None:
    """Null session counters and omitted create reference must survive serialization."""
    original = handle(case)
    result = save(case, edit)
    assert result.message.uid == 101
    assert result.message.id == "draft-101"
    assert result.message.folder.name == "Drafts"
    assert original.message.uid == 100
    assert result.sender.address == "primary@example.test"
    case.session.request_raw.assert_called_once()
    call = case.session.request_raw.call_args
    assert call.args == (
        "POST",
        "https://gateway.example.test/mailws2/v1/message/savedraft",
    )
    payload = call.kwargs["json"]
    assert payload["from"] == "Synthetic User <primary@example.test>"
    assert (
        payload["to"] == payload["cc"] == payload["bcc"] == payload["attachments"] == []
    )
    assert payload["sessionHeaders"] == {
        "folder": None,
        "modseq": None,
        "threadmodseq": None,
        "condstore": 1,
        "qresync": 1,
        "threadmode": 1,
    }
    assert payload["subject"] == "After"
    assert payload["textBody"] == "New text"
    assert payload["webmailClientBuild"] == "synthetic-build"
    assert payload["isHME"] is False
    assert payload["date"].endswith("-0000")
    if edit:
        assert payload["draftGuid"] == "Drafts/100"
    else:
        assert "draftGuid" not in payload
    assert "clientIntent" not in call.kwargs["params"]
    assert case.service.params["clientIntent"] == "stale"
    assert call.kwargs["timeout"] == (10.0, 60.0)
    assert call.kwargs["allow_redirects"] is False
    case.response.close.assert_called_once()


def test_recipient_free_save_refuses_unexpected_attachment_mappings(
    case: DraftCase,
) -> None:
    """Parsing a richer envelope must not accept unrequested attachment effects."""
    case.save_result = load_fixture("mail", "draft_attachment_save_response.json")
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        save(case, False)
    case.session.request_raw.assert_called_once()


def uploaded_attachment(case: DraftCase) -> MailDraftAttachment:
    """A synthetic handle with the same account and gateway as the test service."""
    return MailDraftAttachment(
        account_id="12345",
        service_root=case.service.service_root,
        reference=load_fixture("mail", "draft_attachment_reference.json"),
    )


@pytest.mark.parametrize("role", ["to", "cc", "bcc"])
@pytest.mark.parametrize("stale_part", [False, True])
def test_send_preserves_address_roles_and_saved_attachments(
    case: DraftCase, role: str, stale_part: bool
) -> None:
    """Sending uses the saved draft, never reuploads or reconstructs its parts."""
    options: dict[str, Any] = {role: ("recipient@example.test",)}
    draft = case.service.create_draft(
        sender="primary@example.test",
        attachments=(uploaded_attachment(case),),
        **options,
    )
    case.session.reset_mock()
    case.wrote = False
    if stale_part:
        case.records[101]["parts"][-1]["fileName"] = "unapproved.txt"
        with pytest.raises(MailError):
            case.service.send_draft(draft, confirm=True)
        case.session.request_raw.assert_not_called()
    else:
        assert case.service.send_draft(draft, confirm=True).acknowledged
        case.session.request_raw.assert_called_once()
        assert case.session.request_raw.call_args.kwargs["json"] == load_fixture(
            "mail", "draft_send_request.json"
        )


@pytest.mark.parametrize("role", ["to", "cc", "bcc"])
def test_create_verifies_explicit_recipients(case: DraftCase, role: str) -> None:
    """Each role is serialized separately and verified in fresh headers after save."""
    values = ("Synthetic Recipient <recipient@example.test>", "other@example.test")
    options: dict[str, Any] = {role: values}
    result = case.service.create_draft(sender="primary@example.test", **options)
    assert getattr(result, role) == values
    assert case.session.request_raw.call_args.kwargs["json"][role] == list(values)
    assert case.session.request_raw.call_count == 1


@pytest.mark.parametrize("clear", [False, True])
def test_edit_preserves_or_explicitly_clears_content(
    case: DraftCase, clear: bool
) -> None:
    """Omission preserves all recipients/parts; explicit empty tuples remove them."""
    initial = case.service.create_draft(
        sender="primary@example.test",
        to=("recipient@example.test",),
        bcc=("hidden@example.test",),
        attachments=(uploaded_attachment(case),),
    )
    case.next_uid = 102
    changes = {"to": (), "cc": (), "bcc": (), "attachments": ()} if clear else {}
    result = case.service.edit_draft(
        initial, subject="Edited", text="New", html="", **changes
    )
    assert result.message.uid == 102
    assert result.to == (() if clear else initial.to)
    assert result.bcc == (() if clear else initial.bcc)
    assert len(result.attachments) == (0 if clear else 1)
    assert case.session.request_raw.call_count == 2
    assert initial.message.uid == 101
    assert initial.attachments[0].reference.name == "synthetic.txt"


@pytest.mark.parametrize(
    "values",
    [
        "one@example.test",
        ["one@example.test"],
        (None,),
        ("",),
        ("no-domain",),
        ("a@",),
        ("@example.test",),
        ("a@example.test, b@example.test",),
        ("a@example.test\r\nBcc: hidden@example.test",),
        ("\ud800",),
        ("Group: a@example.test;",),
    ],
)
def test_bad_recipients_fail_before_reads(case: DraftCase, values: Any) -> None:
    """Neither address coercion nor header injection can reach the write endpoint."""
    with pytest.raises(ValueError):
        case.service.create_draft(sender="primary@example.test", to=values)
    case.session.request_raw.assert_not_called()
    case.session.post.assert_not_called()
    case.session.get.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["account", "gateway", "identity", "url", "duplicate", "list", "object"]
)
def test_attachment_handles_are_rechecked_before_reads(
    case: DraftCase, kind: str
) -> None:
    """A persisted or manually copied handle cannot bypass account/URL guards."""
    attachment = uploaded_attachment(case)
    if kind in {"account", "gateway"}:
        attachment = attachment.model_copy(
            update={"account_id" if kind == "account" else "service_root": "other"}
        )
    elif kind in {"identity", "url"}:
        reference = DraftAttachmentReference.model_validate(
            load_fixture("mail", "draft_attachment_reference.json")
        ).model_copy(
            update={
                "content_id"
                if kind == "identity"
                else "url": "cachedpart:11111111-2222-4333-8444-555555555555"
                if kind == "identity"
                else "https://attacker.example.test/part"
            }
        )
        attachment = attachment.model_copy(update={"reference": reference})
    values: Any = (
        [attachment]
        if kind == "list"
        else (None,)
        if kind == "object"
        else (attachment, attachment)
        if kind == "duplicate"
        else (attachment,)
    )
    with pytest.raises((ValueError, MailError)):
        case.service.create_draft(sender="primary@example.test", attachments=values)
    case.session.request_raw.assert_not_called()
    case.session.get.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    [
        ("name", "../file.txt"),
        ("name", "bad\r\nname"),
        ("datatype", "text/plain; charset=utf-8"),
        ("size", 26 * 1024 * 1024),
    ],
)
def test_attachment_state_metadata_cannot_bypass_upload_guards(
    case: DraftCase, key: str, value: Any
) -> None:
    """Persisted JSON handles must obey the same filename, type and size limits."""
    data = load_fixture("mail", "draft_attachment_reference.json")
    data[key] = value
    attachment = uploaded_attachment(case).model_copy(
        update={"reference": DraftAttachmentReference.model_validate(data)}
    )
    with pytest.raises(ValueError):
        case.service.create_draft(
            sender="primary@example.test", attachments=(attachment,)
        )
    case.session.request_raw.assert_not_called()
    case.session.post.assert_not_called()
    case.session.get.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["absent", "duplicate", "wrong", "no-new", "bad-new", "content-id"]
)
def test_attachment_mapping_failures_are_uncertain(case: DraftCase, kind: str) -> None:
    """Never retry a save or return stale parts after an ambiguous reply."""
    data = load_fixture("mail", "draft_attachment_save_response.json")
    mapping = data["attachments"][0]
    if kind == "absent":
        data.pop("attachments")
    elif kind == "duplicate":
        data["attachments"] *= 2
    elif kind == "wrong":
        mapping["requestGuid"] = "unknown"
    elif kind == "no-new":
        mapping.pop("newGuid")
    elif kind == "bad-new":
        mapping["newGuid"] = "messagepart:Drafts/102-2"
    else:
        mapping["contentId"] = "cachedpart:11111111-2222-4333-8444-555555555555"
    case.save_result = data
    with pytest.raises(MailWriteUncertain):
        case.service.create_draft(
            sender="primary@example.test", attachments=(uploaded_attachment(case),)
        )
    case.session.request_raw.assert_called_once()


def test_attachment_mapping_can_rotate_cached_identity(case: DraftCase) -> None:
    """A valid new cached ID replaces both the part URL and content identity."""
    data = load_fixture("mail", "draft_attachment_save_response.json")
    new = "cachedpart:11111111-2222-4333-8444-555555555555"
    data["uid"] = "101"
    data["attachments"][0]["newGuid"] = new
    case.save_result = data
    result = case.service.create_draft(
        sender="primary@example.test", attachments=(uploaded_attachment(case),)
    )
    assert result.attachments[0].reference.guid == new
    assert result.attachments[0].reference.content_id == new
    assert "11111111-2222-4333-8444-555555555555" in result.attachments[0].reference.url


@pytest.mark.parametrize(
    "headers",
    [
        "From: primary@example.test\r\n",  # missing expected To
        "To: a@example.test\r\nTo: a@example.test\r\n",  # duplicate
        "To: Group: a@example.test;\r\n",  # unsupported group
        "To: broken <\r\n",  # malformed address
        "Resent-To: a@example.test\r\n",  # unexpected redirect recipient
        "From: primary@example.test\r\nTo: a@example.test\r\n"
        "Reply-To: other@example.test\r\n",
        "From: primary@example.test\r\nTo: a@example.test\r\n"
        "Sender: other@example.test\r\n",
        "From: primary@example.test\r\nTo: a@example.test\r\n"
        "Return-Receipt-To: other@example.test\r\n",
        "From: primary@example.test\r\nTo: a@example.test\r\n"
        "Disposition-Notification-To: other@example.test\r\n",
    ],
)
def test_post_save_recipient_mismatch_is_uncertain(
    case: DraftCase, headers: str
) -> None:
    """Verify recipient expectations rather than trusting HTTP 200."""
    case.after_headers = headers
    with pytest.raises(MailWriteUncertain):
        case.service.create_draft(sender="primary@example.test", to=("a@example.test",))
    case.session.request_raw.assert_called_once()


def test_post_save_recipient_body_wrong_identity(case: DraftCase) -> None:
    """A different body cannot verify the saved recipient-bearing draft."""
    case.body_guid = "message:Other/101"
    with pytest.raises(MailWriteUncertain):
        case.service.create_draft(sender="primary@example.test", to=("a@example.test",))


@pytest.mark.parametrize(
    "address,accepted", [("a@EXAMPLE.test", True), ("A@example.test", False)]
)
def test_recipient_domain_only_case_insensitivity(
    case: DraftCase, address: str, accepted: bool
) -> None:
    """Ignore domain case, but preserve potentially case-sensitive local parts."""
    case.after_headers = "From: primary@example.test\r\nTo: " + address + "\r\n"
    if accepted:
        assert case.service.create_draft(
            sender="primary@example.test", to=("a@example.test",)
        ).to == ("a@example.test",)
    else:
        with pytest.raises(MailWriteUncertain):
            case.service.create_draft(
                sender="primary@example.test", to=("a@example.test",)
            )


@pytest.mark.parametrize("kind", ["metadata", "parts"])
def test_edit_refuses_changed_attachment_state(case: DraftCase, kind: str) -> None:
    """Do not overwrite a draft whose attachments changed after its saved handle."""
    initial = case.service.create_draft(
        sender="primary@example.test", attachments=(uploaded_attachment(case),)
    )
    current = case.records[101]["parts"][1]
    current["fileName" if kind == "metadata" else "partId"] = "changed"
    case.session.request_raw.reset_mock()
    with pytest.raises(MailError):
        case.service.edit_draft(initial, subject="", text="", html="")
    case.session.request_raw.assert_not_called()


def test_discard_addressed_attachment_draft(case: DraftCase) -> None:
    """Recoverable discard verifies the rich handle without rewriting its content."""
    initial = case.service.create_draft(
        sender="primary@example.test",
        to=("a@example.test",),
        attachments=(uploaded_attachment(case),),
    )
    case.save_result = {
        "accountId": "12345",
        "created": None,
        "destroyed": None,
        "newState": None,
        "oldState": None,
        "notCreated": None,
        "notDestroyed": None,
        "notUpdated": None,
        "updated": {"draft-101": {}},
    }
    result = case.service.discard_draft(initial, MailFolder(**case.folders[1]))
    assert result.source_message_id == "draft-101"
    assert "attachments" not in case.session.request_raw.call_args.kwargs["json"]


@pytest.mark.parametrize("mode", ["json", "text"])
def test_create_cli_recipients_and_attachment_states(
    case: DraftCase, tmp_path: Path, mode: str
) -> None:
    """The mounted CLI loads uploaded state and sends the same explicit wire shape."""
    state = tmp_path / "attachment.json"
    state.write_text(uploaded_attachment(case).model_dump_json(), encoding="utf-8")
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "create-draft",
                "--sender",
                "primary@example.test",
                "--to",
                "recipient@example.test",
                "--cc",
                "copy@example.test",
                "--bcc",
                "hidden@example.test",
                "--attachment-state",
                str(state),
                "--format",
                mode,
            ],
        )
    assert result.exit_code == 0, result.exception
    payload = case.session.request_raw.call_args.kwargs["json"]
    assert payload["to"] == ["recipient@example.test"]
    assert payload["bcc"] == ["hidden@example.test"]
    assert payload["attachments"][0]["name"] == "synthetic.txt"
    if mode == "json":
        assert json.loads(result.stdout)["attachments"][0]["account_id"] == "12345"


@pytest.mark.parametrize("operation", ["preserve", "replace", "clear", "discard"])
def test_rich_draft_cli_uses_saved_handle(
    case: DraftCase, tmp_path: Path, operation: str
) -> None:
    """An explicit saved state carries rich expectations across CLI invocations."""
    initial = case.service.create_draft(
        sender="primary@example.test",
        to=("recipient@example.test",),
        attachments=(uploaded_attachment(case),),
    )
    state, text, html, attachment_state = [
        tmp_path / n for n in ("draft.json", "text.txt", "html.txt", "attachment.json")
    ]
    state.write_text(initial.model_dump_json(), encoding="utf-8")
    text.write_text("Updated private body", encoding="utf-8")
    html.write_text("", encoding="utf-8")
    attachment_state.write_text(
        uploaded_attachment(case).model_dump_json(), encoding="utf-8"
    )
    case.next_uid = 102
    args = [
        "mail",
        "discard-draft" if operation == "discard" else "edit-draft",
        "draft-101",
        "--folder-id",
        "drafts",
        "--sender",
        "primary@example.test",
        "--draft-state",
        str(state),
        "--format",
        "json",
    ]
    if operation == "discard":
        args += ["--trash-id", "trash"]
        case.save_result = {
            "accountId": "12345",
            "created": None,
            "destroyed": None,
            "newState": None,
            "oldState": None,
            "notCreated": None,
            "notDestroyed": None,
            "notUpdated": None,
            "updated": {"draft-101": {}},
        }
    else:
        args += [
            "--subject",
            "Edited",
            "--text-file",
            str(text),
            "--html-file",
            str(html),
        ]
        if operation == "clear":
            args += ["--clear-recipients", "--clear-attachments"]
        elif operation == "replace":
            args += [
                "--to",
                "other@example.test",
                "--attachment-state",
                str(attachment_state),
            ]
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.exception
    assert "Updated private body" not in result.stdout
    payload = json.loads(result.stdout)
    if operation != "discard":
        assert payload["to"] == (
            []
            if operation == "clear"
            else ["other@example.test"]
            if operation == "replace"
            else ["recipient@example.test"]
        )
        assert len(payload["attachments"]) == (0 if operation == "clear" else 1)
    else:
        assert payload["source_message_id"] == "draft-101"


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "large",
        "malformed",
        "selectors",
        "recipient",
        "clear-recipients",
        "clear-attachments",
        "attachment-json",
    ],
)
def test_rich_cli_input_failures_precede_auth(
    case: DraftCase, tmp_path: Path, kind: str
) -> None:
    """Invalid persisted state and conflicting options must never start a write."""
    state, text, html = [tmp_path / n for n in ("state.json", "text.txt", "html.txt")]
    text.write_text("", encoding="utf-8")
    html.write_text("", encoding="utf-8")
    state.write_text(handle(case).model_dump_json(), encoding="utf-8")
    args = [
        "mail",
        "edit-draft",
        "draft-100",
        "--folder-id",
        "drafts",
        "--sender",
        "primary@example.test",
        "--subject",
        "Edited",
        "--text-file",
        str(text),
        "--html-file",
        str(html),
    ]
    if kind == "missing":
        state.unlink()
    elif kind == "large":
        state.write_bytes(b" " * 131073)
    elif kind in {"malformed", "attachment-json"}:
        state.write_text("{invalid", encoding="utf-8")
    elif kind == "selectors":
        state.write_text(
            handle(case)
            .model_copy(update={"sender": MailSender(address="other@example.test")})
            .model_dump_json(),
            encoding="utf-8",
        )
    if kind in {"missing", "large", "malformed", "selectors"}:
        args += ["--draft-state", str(state)]
    elif kind == "recipient":
        args += ["--to", "bad\r\nBcc: hidden@example.test"]
    elif kind == "clear-recipients":
        args += ["--to", "recipient@example.test", "--clear-recipients"]
    elif kind == "clear-attachments":
        args += ["--attachment-state", str(state), "--clear-attachments"]
    else:
        args += ["--attachment-state", str(state)]
    with patch.object(CLIState, "get_api") as get_api, patch.object(CLIState, "open"):
        result = CliRunner().invoke(app, args)
    assert isinstance(result.exception, CLIAbort), result.exception
    get_api.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("sender", ""),
        ("sender", None),
        ("sender", "a\nBcc: b"),
        ("subject", True),
        ("subject", "bad\r\nheader"),
        ("text", b"binary"),
        ("html", "\ud800"),
    ],
)
def test_invalid_inputs_never_read_or_write(
    case: DraftCase, field_name: str, value: Any
) -> None:
    """Validate before preferences or draft mailbox lookups."""
    values: dict[str, Any] = {
        "sender": "primary@example.test",
        "subject": "New",
        "text": "",
        "html": "",
    }
    values[field_name] = value
    with pytest.raises(ValueError):
        case.service.create_draft(**values)
    case.session.get.assert_not_called()
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("operation", ["edit", "discard"])
@pytest.mark.parametrize(
    "header",
    [
        "Reply-To",
        "Sender",
        "Return-Receipt-To",
        "Disposition-Notification-To",
        "Resent-From",
        "Resent-Sender",
        "Resent-Reply-To",
    ],
)
def test_address_headers_are_not_erased(
    case: DraftCase, operation: str, header: str
) -> None:
    """Retain the published refusal policy for routing and receipt headers."""
    case.body_headers += f"{header}: synthetic@example.test\r\n"
    draft = handle(case)
    trash = MailFolder(**case.folders[1])
    mutation = (
        partial(case.service.edit_draft, draft, subject="After", text="", html="")
        if operation == "edit"
        else partial(case.service.discard_draft, draft, trash)
    )
    with pytest.raises(MailError):
        mutation()
    case.session.request_raw.assert_not_called()
    assert case.records[100]["subject"] == "Before"


def test_known_regenerated_headers_are_supported(case: DraftCase) -> None:
    """Routine MIME/date metadata is supported, without permitting arbitrary fields."""
    case.body_headers += (
        "Date: Tue, 27 Oct 2026 12:00:00 +0000\r\n"
        "Message-ID: <synthetic@example.test>\r\nMIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: 7bit\r\n"
    )
    assert save(case, True).message.uid == 101


@pytest.mark.parametrize("failure", [IndexError, ValueError, MessageError])
@pytest.mark.parametrize("lazy", [False, True])
def test_header_parser_failures_are_mail_errors(
    failure: type[Exception], lazy: bool
) -> None:
    """Older Python parser bugs and lazy header failures stay inside the boundary."""
    with patch("pyicloud.services.mail._draft_headers.Parser") as parser:
        target = (
            parser.return_value.parsestr.return_value.items
            if lazy
            else parser.return_value.parsestr
        )
        target.side_effect = failure("private header details")
        with pytest.raises(MailError, match="malformed draft headers") as caught:
            parse_headers("From: synthetic@example.test\r\n")
    assert caught.value.__suppress_context__ is True
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("command", ["edit-draft", "discard-draft"])
def test_draft_cli_missing_namespace_has_actionable_hint(
    case: DraftCase, tmp_path: Path, command: str
) -> None:
    """These commands cannot recommend a nonexistent bounded-lookup option."""
    case.folders[0].pop("uidValidity")
    body = tmp_path / "empty-body.txt"
    body.write_text("", encoding="utf-8")
    args = [
        "mail",
        command,
        "draft-100",
        "--folder-id",
        "drafts",
        "--sender",
        "primary@example.test",
    ]
    if command == "edit-draft":
        args += [
            "--subject",
            "After",
            "--text-file",
            str(body),
            "--html-file",
            str(body),
        ]
    else:
        args += ["--trash-id", "trash"]
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, args)
    assert isinstance(result.exception, CLIAbort)
    assert "icloud mail folders" in str(result.exception)
    assert "--limit" not in str(result.exception)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("build", [None, 123, "", " "])
def test_missing_client_build_is_not_copied_from_capture(
    case: DraftCase, build: Any
) -> None:
    """Do not invent a build or replay a captured browser constant."""
    case.service.params["clientMasteringNumber"] = build
    with pytest.raises(ValueError, match="mastering number"):
        save(case, False)
    case.session.request_raw.assert_not_called()


def test_sender_must_be_freshly_enabled(case: DraftCase) -> None:
    """No arbitrary From value is forwarded to the draft endpoint."""
    with pytest.raises(MailError, match="not enabled"):
        case.service.create_draft(sender="other@example.test")
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("edit", [False, True])
def test_plain_only_save_supplies_escaped_canonical_html(
    case: DraftCase, edit: bool
) -> None:
    """Live evidence: HTML-empty saves lose nonempty textBody on Apple's server."""
    content = "<script>& plain\r\nsecond\rthird\nfourth"
    if edit:
        case.service.edit_draft(handle(case), subject="After", text=content, html="")
    else:
        case.service.create_draft(
            sender="primary@example.test", subject="After", text=content
        )
    payload = case.session.request_raw.call_args.kwargs["json"]
    assert payload["textBody"] == content
    assert (
        payload["htmlBody"]
        == "<div>&lt;script&gt;&amp; plain<br>second<br>third<br>fourth</div>"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "flags",
        "renamed",
        "uid-validity",
        "wrong-message",
        "attachment",
        "unknown-attachment",
        "part-attachment",
        "body-guid",
        "recipients",
        "bcc",
        "reply-to",
        "sender-header",
        "resent-from",
        "headers",
        "from-missing",
        "from-duplicate",
        "from-wrong",
        "metadata-from",
    ],
)
def test_edit_preflight_never_overwrites_unverified_draft(
    case: DraftCase, kind: str
) -> None:
    """Refuse stale handles and unsupported fields rather than silently erasing them."""
    draft = handle(case)
    if kind == "missing":
        case.records.clear()
    elif kind == "flags":
        case.folders[0]["flags"] = 4
    elif kind == "renamed":
        case.folders[0]["name"] = "Other"
    elif kind == "uid-validity":
        case.folders[0]["uidValidity"] = 999
    elif kind == "wrong-message":
        case.records[100]["identifier"] = "other"
    elif kind == "attachment":
        case.records[100]["hasAttachment"] = True
    elif kind == "unknown-attachment":
        del case.records[100]["hasAttachment"]
    elif kind == "part-attachment":
        case.records[100]["parts"][0]["attach"] = True
    elif kind == "body-guid":
        case.body_guid = "message:Other/100"
    elif kind == "recipients":
        case.body_headers += "To: other@example.test\r\n"
    elif kind == "bcc":
        case.body_headers += "Bcc: hidden@example.test\r\n"
    elif kind == "reply-to":
        case.body_headers += "Reply-To: other@example.test\r\n"
    elif kind == "sender-header":
        case.body_headers += "Sender: other@example.test\r\n"
    elif kind == "resent-from":
        case.body_headers += "Resent-From: other@example.test\r\n"
    elif kind == "headers":
        case.body_headers = "not a header\r\n"
    elif kind == "from-missing":
        case.body_headers = "Subject: Before\r\n"
    elif kind == "from-duplicate":
        case.body_headers += "From: other@example.test\r\n"
    elif kind == "from-wrong":
        case.body_headers = "From: other@example.test\r\n"
    else:
        case.records[100]["from"] = "other@example.test"
    with pytest.raises(MailError):
        case.service.edit_draft(draft, subject="After", text="", html="")
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("operation", ["edit", "discard"])
def test_handle_type_is_not_guessed(case: DraftCase, operation: str) -> None:
    """A string or message ID cannot implicitly select a draft."""
    invalid: Any = "100"
    mutation = (
        partial(case.service.edit_draft, invalid, subject="", text="", html="")
        if operation == "edit"
        else partial(case.service.discard_draft, invalid, MailFolder(**case.folders[1]))
    )
    with pytest.raises(ValueError, match="MailDraft"):
        mutation()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "value",
    ["", "a\nb@example.test", "a@example.test, b@example.test", "not an address"],
)
def test_malformed_from_headers_are_not_verified(value: str) -> None:
    """Do not accept header injection or choose one address from a group."""
    with pytest.raises(MailError):
        header_sender(value)


def test_valid_sender_header_uses_structured_parser_fields() -> None:
    """Dynamically generated header classes need not share nominal identity."""
    assert (
        header_sender("Synthetic User <primary@example.test>") == "primary@example.test"
    )


@pytest.mark.parametrize(
    "header",
    [
        None,
        SimpleNamespace(addresses=[], defects=()),
        SimpleNamespace(addresses=(SimpleNamespace(addr_spec=None),), defects=()),
        SimpleNamespace(addresses=(SimpleNamespace(addr_spec=""),), defects=()),
    ],
)
def test_unknown_parser_shapes_fail_closed(header: object) -> None:
    """Defensive parser-interface failures cannot become a verified sender."""
    with patch("pyicloud.services.mail._draft_headers.Parser") as parser:
        parser.return_value.parsestr.return_value = {"From": header}
        with pytest.raises(MailError):
            header_sender("Synthetic User <primary@example.test>")


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"uid": 101},
        {"uid": True},
        {"uid": "0"},
        {"uid": "-1"},
        {"uid": "001"},
        {"uid": "101", "error": "private"},
    ],
)
def test_bad_save_acknowledgement_is_uncertain(
    case: DraftCase, data: dict[str, Any]
) -> None:
    """Never retry an ambiguous create; it may already have saved a draft."""
    case.save_result = data
    with pytest.raises(MailWriteUncertain):
        save(case, False)
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    [
        "read-error",
        "same-uid",
        "old-remains",
        "wrong-subject",
        "wrong-sender",
        "missing",
    ],
)
def test_post_save_failure_is_uncertain_not_rejected(
    case: DraftCase, kind: str
) -> None:
    """UID rotation and post-save identity are necessary, not optional best effort."""
    if kind == "read-error":
        case.after_error = ReadTimeout("private")
    elif kind == "same-uid":
        case.next_uid = 100
    elif kind == "old-remains":
        case.keep_previous = True
    elif kind == "wrong-subject":
        case.after_record = record(101)
    elif kind == "wrong-sender":
        case.after_record = {
            **record(101),
            "subject": "After",
            "from": "other@example.test",
        }
    else:
        case.save_result = {"uid": "999"}
    with pytest.raises(MailWriteUncertain):
        save(case, True)
    case.session.request_raw.assert_called_once()


def test_write_transport_failure_is_not_replayed(case: DraftCase) -> None:
    """A timeout may follow a successful remote save."""
    case.session.request_raw.side_effect = ReadTimeout("private")
    with pytest.raises(MailWriteUncertain):
        save(case, False)
    case.session.request_raw.assert_called_once()


def test_discard_exact_payload_and_acknowledgement(case: DraftCase) -> None:
    """Discard uses the browser's Trash action, not an unsupported ordinary move."""
    draft = handle(case)
    result = case.service.discard_draft(draft, MailFolder(**case.folders[1]))
    assert result.source_message_id == "draft-100"
    assert result.destination_folder_id == "trash"
    call = case.session.request_raw.call_args
    assert call.kwargs["params"]["clientIntent"] == "deleteMessagesGems"
    assert call.kwargs["json"] == {
        "domain": "email",
        "batchUpdate": [
            {
                "ids": ["draft-100"],
                "patch": {
                    "flags": {"set": ["seen"]},
                    "mboxRef": {"replace": ["trash"]},
                },
            }
        ],
    }
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    [
        "not-folder",
        "wrong-trash",
        "disabled-sender",
        "conflicting",
        "rejected",
        "read-error",
        "still-present",
    ],
)
def test_discard_guards_and_uncertain_outcomes(case: DraftCase, kind: str) -> None:
    """Contradictory outcomes stay uncertain; they never authorize a retry."""
    draft = handle(case)
    trash: Any = MailFolder(**case.folders[1])
    if kind == "not-folder":
        trash = "trash"
    elif kind == "wrong-trash":
        trash = MailFolder(**case.folders[2])
    elif kind == "disabled-sender":
        draft = draft.model_copy(
            update={"sender": MailSender(address="other@example.test")}
        )
        case.body_headers = "From: other@example.test\r\n"
        case.records[100]["from"] = "other@example.test"
    elif kind in {"conflicting", "rejected"}:
        case.save_result = {
            "accountId": "12345",
            "created": None,
            "destroyed": None,
            "newState": None,
            "oldState": None,
            "notCreated": None,
            "notDestroyed": None,
            "notUpdated": {"draft-100": {"type": "unknown"}},
            "updated": {"draft-100": {}} if kind == "conflicting" else None,
        }
    elif kind == "read-error":
        case.after_error = ReadTimeout("private")
    else:
        case.after_record = record()
    expected = (
        ValueError
        if kind in {"not-folder", "wrong-trash"}
        else MailError
        if kind == "disabled-sender"
        else MailWriteRejected
        if kind == "rejected"
        else MailWriteUncertain
    )
    with pytest.raises(expected):
        case.service.discard_draft(draft, trash)
    assert case.session.request_raw.call_count == (
        0 if kind in {"not-folder", "wrong-trash", "disabled-sender"} else 1
    )
    if kind in {"not-folder", "wrong-trash"}:
        case.session.get.assert_not_called()
        case.session.post.assert_not_called()


@pytest.mark.parametrize(
    "command", ["senders", "create-draft", "edit-draft", "discard-draft"]
)
@pytest.mark.parametrize("mode", ["json", "text"])
def test_mounted_draft_cli(
    case: DraftCase, tmp_path: Path, command: str, mode: str
) -> None:
    """CLI commands exercise real service validation and raw transport."""
    text = tmp_path / "text.txt"
    html = tmp_path / "html.txt"
    text.write_text("Private replacement text", encoding="utf-8")
    html.write_text("<p>Private replacement text</p>", encoding="utf-8")
    args = ["mail", command]
    if command != "senders":
        if command != "create-draft":
            args += ["draft-100", "--folder-id", "drafts"]
        args += ["--sender", "primary@example.test"]
        if command == "discard-draft":
            args += ["--trash-id", "trash"]
        else:
            args += [
                "--subject",
                "After",
                "--text-file",
                str(text),
                "--html-file",
                str(html),
            ]
    api = MagicMock()
    api.mail = case.service
    api.account_name = "login@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, [*args, "--format", mode])
    assert result.exit_code == 0, result.exception
    assert "Private replacement text" not in result.stdout
    assert case.session.request_raw.call_count == (0 if command == "senders" else 1)
    if mode == "json":
        payload = json.loads(result.stdout)
        if command == "senders":
            assert payload[0]["address"] == "primary@example.test"
        elif command == "discard-draft":
            assert payload["source_message_id"] == "draft-100"
        else:
            assert payload["message"]["uid"] == 101
    else:
        assert "Mail" in result.stdout


def test_create_cli_default_empty_bodies(case: DraftCase) -> None:
    """Omitted body files mean empty bodies only for a new draft."""
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "create-draft",
                "--sender",
                "primary@example.test",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.exception
    payload = case.session.request_raw.call_args.kwargs["json"]
    assert payload["textBody"] == payload["htmlBody"] == payload["subject"] == ""


@pytest.mark.parametrize("kind", ["missing", "encoding"])
def test_draft_cli_input_errors_do_not_authenticate(
    case: DraftCase, tmp_path: Path, kind: str
) -> None:
    """No reads or saves occur when a local UTF-8 body cannot be loaded."""
    path = tmp_path / "body.txt"
    if kind == "encoding":
        path.write_bytes(b"\xff")
    with patch.object(CLIState, "get_api") as get_api, patch.object(CLIState, "open"):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "create-draft",
                "--sender",
                "primary@example.test",
                "--text-file",
                str(path),
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    get_api.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("command", ["create-draft", "edit-draft", "discard-draft"])
def test_draft_cli_required_selection_is_not_implicit(
    case: DraftCase, command: str
) -> None:
    """Never choose a sender, newest draft, or Trash destination implicitly."""
    with patch.object(CLIState, "get_api") as get_api, patch.object(CLIState, "open"):
        result = CliRunner().invoke(app, ["mail", command])
    assert result.exit_code != 0
    get_api.assert_not_called()
    case.session.request_raw.assert_not_called()


def draft_cli_args(command: str, tmp_path: Path, sender: str) -> list[str]:
    """One explicit draft-command invocation with real sandbox body files."""
    args = ["mail", command, "draft-100", "--folder-id", "drafts", "--sender", sender]
    if command == "discard-draft":
        return args + ["--trash-id", "trash"]
    text = tmp_path / "text.txt"
    html = tmp_path / "html.txt"
    text.write_text("Private replacement text", encoding="utf-8")
    html.write_text("<p>Private replacement text</p>", encoding="utf-8")
    return args + [
        "--subject",
        "After",
        "--text-file",
        str(text),
        "--html-file",
        str(html),
    ]


@pytest.mark.parametrize("command", ["edit-draft", "discard-draft"])
@pytest.mark.parametrize("sender", ["", "   "])
def test_draft_cli_empty_sender_fails_before_authentication(
    case: DraftCase, tmp_path: Path, command: str, sender: str
) -> None:
    """A nonempty --sender guard precedes body reads, authentication and lookups."""
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api) as get_api,
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, draft_cli_args(command, tmp_path, sender))
    assert isinstance(result.exception, CLIAbort)
    assert "--sender" in str(result.exception)
    get_api.assert_not_called()
    case.session.get.assert_not_called()
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("command", ["edit-draft", "discard-draft"])
def test_draft_cli_uid_validity_hint_needs_no_limit(
    case: DraftCase, tmp_path: Path, command: str
) -> None:
    """Draft commands offer no --limit, so their hint must name the real fix."""
    case.folders[0]["uidValidity"] = None
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app, draft_cli_args(command, tmp_path, "primary@example.test")
        )
    assert isinstance(result.exception, CLIAbort)
    assert "icloud mail folders" in str(result.exception)
    assert "--limit" not in str(result.exception)
    case.session.request_raw.assert_not_called()
