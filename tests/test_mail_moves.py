"""Single-message moves preserve flags and never replay uncertain submissions."""

from collections.abc import Iterator
from dataclasses import dataclass
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
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
class MoveCase:
    """Independent metadata, capability lookups, and write acknowledgement."""

    service: MailService
    session: MagicMock
    response: MagicMock
    message: MailMessage
    destination: MailFolder
    capabilities: dict[str, dict[str, Any]]


@pytest.fixture(name="case")
def move_case() -> Iterator[MoveCase]:
    """Use real service validation and synthetic HTTP envelopes only."""
    source = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    destination = MailFolder(identifier="20", name="Testing", uidValidity=5678)
    records = load_fixture("mail", "message_lookup_response.json")
    message = MailMessage.model_validate({
        **records["domainObjects"][0],
        "folder": source,
    })
    capabilities = {
        folder.id: {
            "domainObjects": [
                {
                    "identifier": folder.id,
                    "uidValidity": folder.uid_validity,
                    "name": folder.name,
                    "flags": 0,
                }
            ]
        }
        for folder in (source, destination)
    }
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "move_response.json")
    session.request_raw.return_value = response

    def metadata(*_args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock(spec=Response)
        result.status_code = 200
        if kwargs["json"]["domain"] == "mailbox":
            result.json.return_value = capabilities[
                kwargs["json"]["predicate"]["value"]
            ]
        else:
            result.json.return_value = records
        return result

    session.post.side_effect = metadata
    service = MailService(
        "https://example.test", session, {"dsid": "synthetic-account"}
    )
    with patch.object(service, "folders", return_value=[source, destination]):
        yield MoveCase(service, session, response, message, destination, capabilities)


def test_move_has_one_mailbox_only_patch(case: MoveCase) -> None:
    """Acknowledge the old ID without guessing new IDs, counters, or numeric flags."""
    before = case.message.model_dump()
    result = case.service.move(case.message, case.destination)
    assert result.model_dump() == {
        "source_message_id": case.message.id,
        "source_folder_id": "10",
        "destination_folder_id": "20",
    }
    assert case.message.model_dump() == before
    case.session.request_raw.assert_called_once_with(
        "POST",
        "https://example.test/mailws2/v1/email/set",
        params={"dsid": "synthetic-account", "clientIntent": "moveMessagesGems"},
        json=load_fixture("mail", "move_request.json"),
        timeout=(10.0, 60.0),
        allow_redirects=False,
    )
    assert case.service.params == {"dsid": "synthetic-account"}
    queries = [c.kwargs["json"] for c in case.session.post.call_args_list]
    assert [q["domain"] for q in queries] == ["email", "mailbox", "mailbox"]
    assert [q["predicate"]["value"] for q in queries[1:]] == ["10", "20"]
    case.response.close.assert_called_once()


@pytest.mark.parametrize("destination", [None, "20", {}, 20])
def test_destination_type_fails_before_http(case: MoveCase, destination: Any) -> None:
    """Destination IDs cannot bypass a discovered folder context."""
    with pytest.raises(ValueError, match="destination MailFolder"):
        case.service.move(case.message, destination)
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("validity", [None, True, 0, -1, "5678"])
def test_destination_namespace_fails_before_http(case: MoveCase, validity: Any) -> None:
    """Never retarget a saved destination across namespace recreation."""
    destination = case.destination.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="destination UID validity"):
        case.service.move(case.message, destination)
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


def test_same_folder_is_not_a_write(case: MoveCase) -> None:
    """Do not submit a misleading no-op acknowledgement."""
    with pytest.raises(ValueError, match="must differ"):
        case.service.move(case.message, case.message.folder)
    case.session.post.assert_not_called()


def test_invalid_source_never_writes(case: MoveCase) -> None:
    """The shared flag/move source validator still rejects unbound messages."""
    with pytest.raises(ValueError, match="folder-bound"):
        case.service.move(None, case.destination)  # type: ignore[arg-type]
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("folder_id", ["10", "20"])
@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "marker",
        "foreign",
        "epoch",
        "missing-capability",
        "flags",
        "junk",
        "drafts",
        "renamed",
        "coercion",
    ],
)
def test_fresh_capabilities_guard_both_folders(
    case: MoveCase, folder_id: str, kind: str
) -> None:
    """Unknown, unsafe, stale, partial, or ambiguous folder metadata fails closed."""
    envelope = case.capabilities[folder_id]
    record = envelope["domainObjects"][0]
    if kind == "missing":
        envelope["domainObjects"] = []
    elif kind == "duplicate":
        envelope["domainObjects"].append(record.copy())
    elif kind == "marker":
        envelope["continuationMarker"] = "more"
    elif kind == "foreign":
        record["identifier"] = "other"
    elif kind == "epoch":
        record["uidValidity"] += 1
    elif kind == "missing-capability":
        del record["flags"]
    elif kind == "coercion":
        record["flags"] = "0"
    elif kind in {"junk", "drafts"}:
        name = "Junk" if kind == "junk" else "Drafts"
        record["name"] = name
        if folder_id == "10":
            case.message = case.message.model_copy(
                update={"folder": case.message.folder.model_copy(update={"name": name})}
            )
        else:
            case.destination = case.destination.model_copy(update={"name": name})
    elif kind == "renamed":
        record["name"] = "new-name"
    else:
        record["flags"] = 4
    with pytest.raises(MailError):
        case.service.move(case.message, case.destination)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-account",
        "created",
        "destroyed",
        "notCreated",
        "notDestroyed",
        "missing-field",
        "missing-update",
        "empty-update",
        "foreign-id",
        "extra-id",
        "unexpected-counter",
        "conflicting",
        "foreign-failure",
        "malformed",
    ],
)
def test_uncertain_acknowledgements_never_retry(case: MoveCase, kind: str) -> None:
    """Include the contradictory success/failure shape actually captured live."""
    data = case.response.json.return_value
    if kind == "wrong-account":
        data["accountId"] = "other"
    elif kind in {"created", "notCreated", "notDestroyed"}:
        data[kind] = {"unexpected": {}}
    elif kind == "destroyed":
        data[kind] = [case.message.id]
    elif kind == "missing-field":
        del data["notUpdated"]
    elif kind == "missing-update":
        data["updated"] = None
    elif kind == "empty-update":
        data["updated"] = {}
    elif kind == "foreign-id":
        data["updated"] = {"other": {}}
    elif kind == "extra-id":
        data["updated"]["other"] = {}
    elif kind == "unexpected-counter":
        data["updated"][case.message.id] = {"modseq": 101}
    elif kind == "conflicting":
        case.response.json.return_value = load_fixture(
            "mail", "move_conflicting_response.json"
        )
    elif kind == "foreign-failure":
        data["notUpdated"] = {"other": {"type": "unknown"}}
    else:
        case.response.json.return_value = []
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        case.service.move(case.message, case.destination)
    case.session.request_raw.assert_called_once()
    case.response.close.assert_called_once()


def test_rejected_move_is_distinct(case: MoveCase) -> None:
    """Only an exact failure without successes is an unambiguous rejection."""
    case.response.json.return_value["updated"] = None
    case.response.json.return_value["notUpdated"] = {
        case.message.id: {"type": "denied"}
    }
    with pytest.raises(MailWriteRejected, match="rejected the move"):
        case.service.move(case.message, case.destination)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("kind", ["timeout", "http", "json", "close"])
def test_transport_failure_is_uncertain(case: MoveCase, kind: str) -> None:
    """Never retry a dispatched mutation even on an authentication HTTP status."""
    if kind == "timeout":
        case.session.request_raw.side_effect = ReadTimeout("private details")
    elif kind == "http":
        case.response.status_code = 421
    elif kind == "json":
        case.response.json.side_effect = ValueError("private details")
    else:
        case.response.close.side_effect = OSError("private details")
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        case.service.move(case.message, case.destination)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("mode", ["json", "text"])
@pytest.mark.parametrize("outcome", ["success", "missing", "uncertain"])
def test_mounted_move_cli(case: MoveCase, mode: str, outcome: str) -> None:
    """Exercise explicit CLI lookup, real move validation, and safe output paths."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = case.service
    if outcome == "missing":
        case.session.post.side_effect = None
        response = MagicMock(spec=Response)
        response.status_code = 200
        response.json.return_value = {"domainObjects": []}
        case.session.post.return_value = response
    if outcome == "uncertain":
        case.response.json.return_value = load_fixture(
            "mail", "move_conflicting_response.json"
        )
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "move",
                case.message.id,
                "--folder-id",
                "10",
                "--destination-id",
                "20",
                "--format",
                mode,
            ],
        )
    if outcome == "success":
        assert result.exit_code == 0, result.output
        assert case.message.id in result.output
        case.session.request_raw.assert_called_once()
    else:
        assert result.exit_code != 0
        assert isinstance(result.exception, CLIAbort)
        assert "source_message_id" not in result.output
        if outcome == "missing":
            assert "no move submitted" in str(result.exception)
            case.session.request_raw.assert_not_called()
        else:
            assert "uncertain" in str(result.exception)
            case.session.request_raw.assert_called_once()


def test_move_cli_requires_explicit_destination() -> None:
    """A missing destination cannot default to Trash or any localized name."""
    result = CliRunner().invoke(app, ["mail", "move", "id", "--folder-id", "10"])
    assert result.exit_code != 0
    # Rich styles the option name in separate runs, so ANSI escapes may split
    # "--destination-id"; strip them before asserting on the literal text.
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "destination-id" in plain_output
