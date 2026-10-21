"""Offline single-message flag writes, exact outcomes, and mounted CLI guards."""

from collections.abc import Iterator
from dataclasses import dataclass
import json
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from requests import ReadTimeout, Response, Session
from typer.testing import CliRunner, Result

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIState
from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import (
    MailError,
    MailFolder,
    MailMessage,
    MailService,
    MailWriteRejected,
    MailWriteUncertain,
)
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@dataclass
class FlagCase:
    """Real service with independent read and write responses; no network."""

    service: MailService
    session: MagicMock
    response: MagicMock
    message: MailMessage
    folders: MagicMock


@pytest.fixture(name="case")
def flag_case() -> Iterator[FlagCase]:
    """Every call starts with independent synthetic metadata and acknowledgements."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    records = load_fixture("mail", "message_lookup_response.json")
    message = MailMessage.model_validate({
        **records["domainObjects"][0],
        "folder": folder,
    })
    session = MagicMock(spec=PyiCloudSession)
    read_response = MagicMock(spec=Response)
    read_response.status_code = 200
    read_response.json.return_value = records
    session.post.return_value = read_response
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "flag_update_response.json")
    session.request_raw.return_value = response
    service = MailService(
        "https://example.test", session, {"dsid": "synthetic-account"}
    )
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield FlagCase(service, session, response, message, folders)


def change(case: FlagCase, flag: str = "seen", enabled: Any = True) -> Any:
    """Exercise both public APIs without using the private submission helper."""
    if flag == "seen":
        return case.service.set_read(case.message, read=enabled)
    return case.service.set_flagged(case.message, flagged=enabled)


def invoke(case: FlagCase, action: str, mode: str = "json") -> Result:
    """Run the mounted CLI and real write implementation with synthetic HTTP only."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = case.service
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        return CliRunner().invoke(
            app,
            [
                "mail",
                "mark",
                case.message.id,
                "--folder-id",
                "10",
                "--action",
                action,
                "--format",
                mode,
            ],
        )


@pytest.mark.parametrize("flag", ["seen", "flagged"])
@pytest.mark.parametrize("enabled", [True, False])
def test_single_named_flag_patch(case: FlagCase, flag: str, enabled: bool) -> None:
    """Preserve all other bits, require exact UID identity, and do not mutate input."""
    result = change(case, flag, enabled)
    assert result.message_id == case.message.id
    assert result.flag == flag
    assert result.enabled is enabled
    assert result.modseq == 101
    assert case.message.flags == 0
    call = case.session.request_raw.call_args
    case.session.request_raw.assert_called_once()
    assert call.args == ("POST", "https://example.test/mailws2/v1/email/set")
    expected = load_fixture("mail", "flag_update_request.json")
    expected["batchUpdate"][0]["patch"]["flags"] = {
        "set" if enabled else "clear": [flag]
    }
    assert call.kwargs == {
        "params": {
            "dsid": "synthetic-account",
            "clientIntent": "setMessageFlag"
            if enabled == (flag == "flagged")
            else "clearMessageFlag",
        },
        "json": expected,
        "timeout": (10.0, 60.0),
        "allow_redirects": False,
    }
    query = case.session.post.call_args.kwargs["json"]
    assert query["predicate"]["and"][1]["expression"]["property"] == "uid"
    assert query["predicate"]["and"][1]["value"] == 7
    assert case.service.params == {"dsid": "synthetic-account"}
    case.response.close.assert_called_once()


@pytest.mark.parametrize("flag", ["seen", "flagged"])
@pytest.mark.parametrize("enabled", [None, 0, 1, "true", [], {}])
def test_invalid_state_precedes_reads(case: FlagCase, flag: str, enabled: Any) -> None:
    """Truthiness and coercion cannot accidentally enable a write."""
    with pytest.raises(ValueError, match="boolean"):
        change(case, flag, enabled)
    case.folders.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("message", [None, "id", {}, 7])
def test_unbound_message_precedes_reads(case: FlagCase, message: Any) -> None:
    """No bare IDs or search groups can bypass the explicit mailbox context."""
    with pytest.raises(ValueError, match="folder-bound"):
        case.service.set_read(message, read=True)
    case.folders.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("validity", [None, 0, -1, True])
def test_missing_namespace_precedes_reads(case: FlagCase, validity: Any) -> None:
    """An old UID must not silently bind to another mailbox incarnation."""
    case.message = case.message.model_copy(
        update={
            "folder": case.message.folder.model_copy(update={"uid_validity": validity})
        }
    )
    with pytest.raises(ValueError, match="UID validity"):
        change(case)
    case.folders.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("account", [None, "", "  ", True, 123])
def test_missing_account_fails_before_submission(case: FlagCase, account: Any) -> None:
    """The response cannot be checked against a guessed account identifier."""
    case.service.params["dsid"] = account
    with pytest.raises(ValueError, match="account identifier"):
        change(case)
    case.folders.assert_not_called()
    case.session.request_raw.assert_not_called()


def test_mismatched_message_context_never_writes(case: FlagCase) -> None:
    """A message from another folder is not retargeted using a coincident UID."""
    case.message = case.message.model_copy(
        update={"mailbox": case.message.mailbox.model_copy(update={"id": "other"})}
    )
    with pytest.raises(ValueError, match="identities"):
        change(case)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["missing", "other-id", "wrong-folder", "epoch", "read-error"]
)
def test_preflight_failures_never_submit(case: FlagCase, kind: str) -> None:
    """Stale or failed metadata reads must fail before the mutation endpoint."""
    records = case.session.post.return_value.json.return_value["domainObjects"]
    if kind == "missing":
        records.clear()
    elif kind == "other-id":
        records[0]["identifier"] = "other-message"
    elif kind == "wrong-folder":
        records[0]["mboxRef"]["id"] = "other-folder"
    elif kind == "epoch":
        case.folders.return_value = []
    else:
        case.session.post.side_effect = PyiCloudConnectionException("offline")
    with pytest.raises((MailError, PyiCloudConnectionException)):
        change(case)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("counter", [None, 0, -1, True, "101", 1.5])
def test_invalid_acknowledgement_counter_is_uncertain(
    case: FlagCase, counter: Any
) -> None:
    """No coerced, absent, or invalid counter can prove that a write succeeded."""
    case.response.json.return_value["updated"][case.message.id]["modseq"] = counter
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        change(case)
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-account",
        "missing-field",
        "empty-updated",
        "null-updated",
        "wrong-id",
        "extra-id",
        "created",
        "destroyed",
        "notCreated",
        "notDestroyed",
        "error",
        "conflicting-failure",
        "foreign-failure",
    ],
)
def test_ambiguous_outcomes_never_claim_success(case: FlagCase, kind: str) -> None:
    """HTTP 200 and unrelated successes do not establish this operation's outcome."""
    data = case.response.json.return_value
    if kind == "wrong-account":
        data["accountId"] = "foreign-account"
    elif kind == "missing-field":
        del data["notUpdated"]
    elif kind == "empty-updated":
        data["updated"] = {}
    elif kind == "null-updated":
        data["updated"] = None
    elif kind == "wrong-id":
        data["updated"] = {"other": {"modseq": 101}}
    elif kind == "extra-id":
        data["updated"]["other"] = {"modseq": 101}
    elif kind == "destroyed":
        data["destroyed"] = ["unexpected-message"]
    elif kind in ("conflicting-failure", "foreign-failure"):
        data["notUpdated"] = {
            case.message.id if kind == "conflicting-failure" else "other": {}
        }
    else:
        data[kind] = {"private-server-detail": "never print me"}
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        change(case)
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


def test_exact_rejection_is_distinct_from_uncertainty(case: FlagCase) -> None:
    """Only an unambiguous notUpdated entry for this target is a known rejection."""
    data = case.response.json.return_value
    data["updated"] = None
    data["notUpdated"] = {case.message.id: {"reason": "private-server-detail"}}
    with pytest.raises(MailWriteRejected, match="rejected") as caught:
        change(case)
    assert "private-server-detail" not in str(caught.value)
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "field",
    ["created", "destroyed", "notCreated", "notUpdated", "notDestroyed", "updated"],
)
@pytest.mark.parametrize("value", [False, "", 0])
def test_false_values_cannot_stand_in_for_outcome_maps(
    case: FlagCase, field: str, value: Any
) -> None:
    """Malformed false-like values are not evidence of empty failure maps."""
    case.response.json.return_value[field] = value
    with pytest.raises(MailWriteUncertain):
        change(case)
    case.response.close.assert_called_once()


@pytest.mark.parametrize("status", [302, 400, 401, 403, 429, 500])
def test_http_failures_are_uncertain_and_not_replayed(
    case: FlagCase, status: int
) -> None:
    """No redirect, reauthentication replay, or rate-limit retry follows submission."""
    case.response.status_code = status
    with pytest.raises(MailWriteUncertain):
        change(case)
    case.response.json.assert_not_called()
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


def test_real_session_does_not_normalize_or_replay_write_auth_errors(
    case: FlagCase,
) -> None:
    """Use the real raw-session pipeline while intercepting the actual HTTP sink."""
    session = object.__new__(PyiCloudSession)
    Session.__init__(session)
    case.response.status_code = 401
    with (
        patch.object(session, "_logger", MagicMock(), create=True),
        patch.object(
            MailService, "session", new_callable=PropertyMock, return_value=session
        ),
        patch.object(case.service, "message_by_uid", return_value=case.message),
        patch.object(session, "_update_session_data"),
        patch.object(session, "_save_session_data") as save,
        patch.object(session, "_handle_request_error") as authenticate,
        patch("requests.Session.request", return_value=case.response) as http,
        pytest.raises(MailWriteUncertain),
    ):
        change(case)
    http.assert_called_once()
    assert http.call_args.kwargs["method"] == "POST"
    assert http.call_args.kwargs["allow_redirects"] is False
    authenticate.assert_not_called()
    save.assert_called_once()
    case.response.close.assert_called_once()
    session.close()


@pytest.mark.parametrize("stage", ["request", "json", "close"])
def test_submission_failures_are_uncertain(case: FlagCase, stage: str) -> None:
    """Late failures cannot imply a safe retry or leak transport/server diagnostics."""
    if stage == "request":
        case.session.request_raw.side_effect = ReadTimeout("private-url")
    elif stage == "json":
        case.response.json.side_effect = ValueError("private-json")
    else:
        case.response.close.side_effect = OSError("private-path")
    with pytest.raises(MailWriteUncertain) as caught:
        change(case)
    assert "private-" not in str(caught.value)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "action,flag,enabled",
    [
        ("read", "seen", True),
        ("unread", "seen", False),
        ("flag", "flagged", True),
        ("unflag", "flagged", False),
    ],
)
@pytest.mark.parametrize("mode", ["json", "text"])
def test_cli_explicit_actions(
    case: FlagCase, action: str, flag: str, enabled: bool, mode: str
) -> None:
    """The real CLI writes only one target after exact folder/message resolution."""
    result = invoke(case, action, mode)
    assert result.exit_code == 0, result.exception
    case.session.request_raw.assert_called_once()
    if mode == "json":
        assert json.loads(result.stdout) == {
            "message_id": case.message.id,
            "flag": flag,
            "enabled": enabled,
            "modseq": 101,
        }
    else:
        assert "Acknowledgement" in result.stdout
    assert "Synthetic older message" not in result.stdout
    assert "synthetic-account" not in result.stdout


@pytest.mark.parametrize("kind", ["missing", "namespace", "uncertain", "rejected"])
def test_cli_errors_do_not_print_success(case: FlagCase, kind: str) -> None:
    """Writes never fall back to a newest-message scan or echo server failure maps."""
    if kind == "missing":
        case.session.post.return_value.json.return_value["domainObjects"] = []
    elif kind == "namespace":
        case.folders.return_value = [
            case.message.folder.model_copy(update={"uid_validity": None})
        ]
    elif kind == "uncertain":
        case.session.request_raw.side_effect = ReadTimeout("private-secret")
    else:
        data = case.response.json.return_value
        data["updated"] = None
        data["notUpdated"] = {case.message.id: {"reason": "private-secret"}}
    result = invoke(case, "read")
    assert result.exit_code != 0
    assert '"modseq"' not in result.stdout
    assert "private-secret" not in result.stdout
    assert "--limit" not in result.stdout
    if kind in ("missing", "namespace"):
        case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["id", "--folder-id", "10"],
        ["id", "--action", "read"],
        ["id", "--folder-id", "10", "--action", "delete"],
    ],
)
def test_cli_help_and_invalid_selectors_are_offline(args: list[str]) -> None:
    """There is no default mutation and no wildcard, folder-wide, or delete action."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", "mark", *args])
    assert result.exit_code == (0 if args == ["--help"] else 2)
    get_api.assert_not_called()
