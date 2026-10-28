"""Confirmed single-message Trash destruction with no replay or bulk fallback."""

from dataclasses import dataclass
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.services.mail import (
    MailError,
    MailMessage,
    MailService,
    MailWriteRejected,
    MailWriteUncertain,
)
from pyicloud.services.mail._message_deletion import MessageDeleteRequest
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@dataclass
class DeleteCase:
    """A synthetic exact target; no mailbox scan, body, or attachment is needed."""

    service: MailService
    session: MagicMock
    response: MagicMock
    message: MailMessage
    folder: dict[str, Any]
    record: dict[str, Any]
    present: bool = True
    wrote: bool = False
    keep_message: bool = False
    after_error: Exception | None = None
    after_uid: bool = False


@pytest.fixture(name="case")
def delete_case() -> DeleteCase:
    """Mock transport only; run real input, identity, capability and outcome checks."""
    folder = {
        "identifier": "trash",
        "name": "Deleted Messages",
        "uidValidity": 88,
        "flags": 0,
    }
    record = {
        "identifier": "message-to-delete",
        "uid": 7,
        "mboxRef": {"id": "trash"},
        "subject": "Synthetic disposable",
    }
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "message_delete_response.json")
    service = MailService(
        "https://gateway.example.test", session, {"dsid": "synthetic-account"}
    )
    message = MailMessage.model_validate({**record, "folder": folder})
    case = DeleteCase(service, session, response, message, folder, record)

    def read(_url: str, **kwargs: Any) -> MagicMock:
        if case.wrote and case.after_error is not None:
            raise case.after_error
        result = MagicMock(spec=Response)
        result.status_code = 200
        query = kwargs["json"]
        if query["domain"] == "mailbox":
            result.json.return_value = {"domainObjects": [case.folder]}
        else:
            ids = [query["predicate"], *query["predicate"].get("and", [])]
            exact = next(
                c for c in ids if c["expression"]["property"] in {"uid", "identifier"}
            )
            assert exact["value"] in {7, "message-to-delete"}
            available = case.present
            if case.wrote and case.after_uid:
                available = exact["expression"]["property"] == "identifier"
            result.json.return_value = {
                "domainObjects": [case.record] if available else []
            }
        return result

    def write(*_args: Any, **_kwargs: Any) -> MagicMock:
        case.wrote = True
        case.present = case.keep_message
        return response

    session.post.side_effect = read
    session.request_raw.side_effect = write
    return case


def test_delete_exact_wire_and_typed_result(case: DeleteCase) -> None:
    """The one captured ID, intent and endpoint cannot broaden into empty Trash."""
    before = case.message.model_dump()
    result = case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_called_once_with(
        "POST",
        "https://gateway.example.test/mailws2/v1/email/set",
        params={"dsid": "synthetic-account", "clientIntent": "deleteMessagesGems"},
        json=load_fixture("mail", "message_delete_request.json"),
        timeout=(10.0, 60.0),
        allow_redirects=False,
    )
    assert result.model_dump() == {
        "message_id": "message-to-delete",
        "folder_id": "trash",
        "uid": 7,
        "permanent": True,
    }
    assert case.message.model_dump() == before
    case.session.get.assert_not_called()
    case.response.close.assert_called_once()


@pytest.mark.parametrize("confirm", [False, None, 1, "yes"])
def test_confirmation_precedes_all_io(case: DeleteCase, confirm: Any) -> None:
    """Even truthy values are not irreversible-deletion authorization."""
    with pytest.raises(ValueError, match="confirm=True"):
        case.service.delete_permanently(case.message, confirm=confirm)
    assert case.session.mock_calls == []


@pytest.mark.parametrize("kind", ["object", "folder", "scope", "validity", "account"])
def test_invalid_target_precedes_all_io(case: DeleteCase, kind: str) -> None:
    """No implicit destination, guessed namespace, missing account or wildcard."""
    message: Any = case.message
    if kind == "object":
        message = None
    elif kind == "folder":
        message = message.model_copy(
            update={"folder": message.folder.model_copy(update={"name": "INBOX"})}
        )
    elif kind == "scope":
        message = message.model_copy(
            update={"folder": message.folder.model_copy(update={"id": "other"})}
        )
    elif kind == "validity":
        message = message.model_copy(
            update={"folder": message.folder.model_copy(update={"uid_validity": None})}
        )
    else:
        case.service.params["dsid"] = ""
    with pytest.raises(ValueError):
        case.service.delete_permanently(message, confirm=True)
    assert case.session.mock_calls == []


@pytest.mark.parametrize("kind", ["missing", "identity", "validity", "name", "flags"])
def test_stale_target_or_unsupported_mailbox_prevents_write(
    case: DeleteCase, kind: str
) -> None:
    """Fresh exact metadata and zero-flag canonical Trash semantics are required."""
    if kind == "missing":
        case.present = False
    elif kind == "identity":
        case.record["identifier"] = "replacement"
    elif kind == "validity":
        case.folder["uidValidity"] = 99
    elif kind == "name":
        case.folder["name"] = "Other"
    else:
        case.folder["flags"] = 2
    with pytest.raises(MailError):
        case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accountId", "other-account"),
        ("destroyed", []),
        ("destroyed", ["other-message"]),
        ("destroyed", ["message-to-delete", "other-message"]),
        ("destroyed", ["message-to-delete", "message-to-delete"]),
        ("created", {"other": None}),
        ("updated", {"other": None}),
        ("notCreated", {"other": {"type": "error"}}),
        ("notUpdated", {"other": {"type": "error"}}),
        ("notDestroyed", {"message-to-delete": {"type": "error"}}),
        ("unknown", True),
    ],
)
def test_conflicting_or_unrelated_outcome_is_uncertain(
    case: DeleteCase, field: str, value: Any
) -> None:
    """Reject partial destruction, another account and mixed effects."""
    payload = load_fixture("mail", "message_delete_response.json")
    payload[field] = value
    case.response.json.return_value = payload
    with pytest.raises(MailWriteUncertain):
        case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("kind", ["exact", "unknown", "malformed"])
def test_structured_rejection_requires_exact_unambiguous_target(
    case: DeleteCase, kind: str
) -> None:
    """Only the captured target's explicit no-success error can establish rejection."""
    payload = load_fixture("mail", "message_delete_response.json")
    payload["destroyed"] = None
    payload["notDestroyed"] = {
        "other" if kind == "unknown" else "message-to-delete": {}
        if kind == "malformed"
        else {"type": "notFound"}
    }
    case.response.json.return_value = payload
    expected = MailWriteRejected if kind == "exact" else MailWriteUncertain
    with pytest.raises(expected):
        case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    ["transport", "http", "json", "close", "post-read", "present-uid", "present-id"],
)
def test_submitted_delete_is_never_retried(case: DeleteCase, kind: str) -> None:
    """Include failures after the irreversible effect might already have happened."""
    if kind == "transport":
        case.session.request_raw.side_effect = ReadTimeout("synthetic")
    elif kind == "http":
        case.response.status_code = 429
    elif kind == "json":
        case.response.json.side_effect = ValueError("synthetic")
    elif kind == "close":
        case.response.close.side_effect = OSError("synthetic")
    elif kind == "post-read":
        case.after_error = MailError("synthetic")
    elif kind == "present-uid":
        case.keep_message = True
    else:
        case.after_uid = True
    with pytest.raises(MailWriteUncertain, match="already have been deleted"):
        case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("error", [ReadTimeout, OSError, MailError, ValueError])
def test_delete_post_read_errors_remain_uncertain(
    case: DeleteCase, error: type[Exception]
) -> None:
    """Post-submission verification failures cannot make deletion safe to retry."""
    case.after_error = error("synthetic failure")
    with pytest.raises(MailWriteUncertain, match="already have been deleted"):
        case.service.delete_permanently(case.message, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("values", [(), ("one", "two"), ("",), (1,)])
def test_request_model_forbids_empty_and_bulk_destruction(values: Any) -> None:
    """A request cannot be repurposed as empty Trash or a multi-message delete."""
    with pytest.raises(ValueError):
        MessageDeleteRequest(destroy=values)


@pytest.mark.parametrize("output", ["json", "text"])
def test_mounted_delete_cli(case: DeleteCase, output: str) -> None:
    """The CLI delegates exact lookup and confirmation, never a newest-message scan."""
    api = MagicMock()
    api.mail = case.service
    api.account_name = "login@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
        patch.object(case.service, "folders", return_value=[case.message.folder]),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "delete-forever",
                case.message.id,
                "--folder-id",
                "trash",
                "--confirm",
                "--format",
                output,
            ],
        )
    assert result.exit_code == 0, result.exception
    case.session.request_raw.assert_called_once()
    assert "Synthetic disposable" not in result.stdout
    if output == "json":
        assert json.loads(result.stdout)["permanent"] is True
    else:
        assert "Permanently Deleted" in result.stdout


@pytest.mark.parametrize("validity", [None, 0, -1])
@pytest.mark.parametrize("output", ["json", "text"])
def test_delete_cli_missing_namespace_has_actionable_hint(
    case: DeleteCase, validity: int | None, output: str
) -> None:
    """An unverified namespace cannot suggest bounded lookup or submit deletion."""
    folder = case.message.folder.model_copy(update={"uid_validity": validity})
    api = MagicMock(mail=case.service, account_name="login@example.test")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
        patch.object(case.service, "folders", return_value=[folder]),
        patch.object(case.service, "message") as exact_lookup,
        patch.object(case.service, "messages") as bounded_lookup,
        patch.object(case.service, "delete_permanently") as delete,
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "delete-forever",
                case.message.id,
                "--folder-id",
                "trash",
                "--confirm",
                "--format",
                output,
            ],
        )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIAbort)
    assert "icloud mail folders" in str(result.exception)
    assert "--limit" not in str(result.exception)
    exact_lookup.assert_not_called()
    bounded_lookup.assert_not_called()
    delete.assert_not_called()
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


def test_cli_confirmation_required_before_authentication() -> None:
    """Missing irreversible-action confirmation never starts authentication."""
    with patch.object(CLIState, "get_api") as auth:
        result = CliRunner().invoke(
            app, ["mail", "delete-forever", "message", "--folder-id", "trash"]
        )
    assert isinstance(result.exception, CLIAbort)
    auth.assert_not_called()
