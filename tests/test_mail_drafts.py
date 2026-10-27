"""Draft UID rotation, identity guards, and exactly-once write attempts."""

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
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
    MailError,
    MailFolder,
    MailMessage,
    MailSender,
    MailService,
    MailWriteRejected,
    MailWriteUncertain,
)
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
    save_result: dict[str, Any] | None = None
    next_uid: int = 101
    keep_previous: bool = False
    after_error: Exception | None = None
    after_record: dict[str, Any] | None = None
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


@pytest.fixture(name="tmp_path")
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
                "guid": case.body_guid or f"message:Drafts/{uid}",
                "longHeader": case.body_headers,
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
            case.records[case.next_uid] = current
            data = load_fixture("mail", "draft_save_response.json")
            data["uid"] = str(case.next_uid)
            response.json.return_value = (
                data if case.save_result is None else case.save_result
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


def save(case: DraftCase, edit: bool) -> MailDraft:
    """Both public save routes supply explicit full replacement content."""
    values = {"subject": "After", "text": "New text", "html": "<p>New text</p>"}
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
    with patch("pyicloud.services.mail._drafts.Parser") as parser:
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
    trash = MailFolder(**case.folders[1])
    if kind == "wrong-trash":
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
        if kind == "wrong-trash"
        else MailError
        if kind == "disabled-sender"
        else MailWriteRejected
        if kind == "rejected"
        else MailWriteUncertain
    )
    with pytest.raises(expected):
        case.service.discard_draft(draft, trash)
    assert case.session.request_raw.call_count == (
        0 if kind in {"wrong-trash", "disabled-sender"} else 1
    )


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
