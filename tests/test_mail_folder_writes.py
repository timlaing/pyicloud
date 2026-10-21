"""Fresh namespace checks, exact folder outcomes, and no replay after dispatch."""

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import (
    MailError,
    MailFolder,
    MailService,
    MailWriteRejected,
    MailWriteUncertain,
)
from pyicloud.services.mail._folder_names import encode_leaf, folder_path
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@dataclass
class FolderCase:
    """A synthetic mailbox server, with independent read/write responses."""

    service: MailService
    session: MagicMock
    response: MagicMock
    folder: MailFolder
    records: list[dict[str, Any]]
    messages: list[dict[str, Any]] = field(default_factory=list)
    override: dict[str, Any] | None = None
    after: list[dict[str, Any]] | None = None
    read_error: Exception | None = None
    marker: str | None = None
    did_write: bool = False


@pytest.fixture(name="case")
def folder_case() -> FolderCase:
    """Use real public methods, actual parsers, and only mocked HTTP I/O."""
    folder = MailFolder(identifier="20", name="Testing", uidValidity=5678)
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    service = MailService(
        "https://example.test", session, {"dsid": "synthetic-account"}
    )
    case = FolderCase(
        service,
        session,
        response,
        folder,
        [
            {"identifier": "10", "name": "INBOX", "uidValidity": 1234, "flags": 0},
            {"identifier": "20", "name": "Testing", "uidValidity": 5678, "flags": 0},
        ],
    )

    def read(*_args: Any, **kwargs: Any) -> MagicMock:
        if case.did_write and case.read_error is not None:
            raise case.read_error
        result = MagicMock(spec=Response)
        result.status_code = 200
        query = kwargs["json"]
        if query["domain"] == "mailbox":
            assert "limit" not in query
        records = (
            case.records if not case.did_write or case.after is None else case.after
        )
        result.json.return_value = {
            "domainObjects": deepcopy(
                records if query["domain"] == "mailbox" else case.messages
            ),
            "continuationMarker": case.marker,
        }
        return result

    def submit(*_args: Any, **kwargs: Any) -> MagicMock:
        case.did_write = True
        request = kwargs["json"]
        if case.override is not None:
            response.json.return_value = deepcopy(case.override)
        elif "create" in request:
            data = load_fixture("mail", "folder_create_response.json")
            created = data["created"]["mailbox"]
            created["name"] = request["create"]["mailbox"]["name"]
            case.records.append(deepcopy(created))
            response.json.return_value = data
        elif "update" in request:
            case.records[1]["name"] = request["update"]["20"]["name"]
            response.json.return_value = load_fixture(
                "mail", "folder_rename_response.json"
            )
        else:
            case.records.pop(1)
            response.json.return_value = load_fixture(
                "mail", "folder_delete_response.json"
            )
        return response

    session.post.side_effect = read
    session.request_raw.side_effect = submit
    return case


def operate(case: FolderCase, operation: str) -> Any:
    """Exercise all three public operations without private submission shortcuts."""
    if operation == "create":
        return case.service.create_folder("Created")
    if operation == "rename":
        return case.service.rename_folder(case.folder, "Renamed")
    return case.service.delete_folder(case.folder, confirm=True)


@pytest.mark.parametrize(
    "operation,intent,payload",
    [
        (
            "create",
            "gemsCreateMailbox",
            {"domain": "mailbox", "create": {"mailbox": {"name": "Created"}}},
        ),
        (
            "rename",
            "renameMailboxGems",
            {"domain": "mailbox", "update": {"20": {"name": "Renamed"}}},
        ),
        ("delete", "deleteMailboxGems", {"domain": "mailbox", "destroy": ["20"]}),
    ],
)
def test_folder_write_exact_wire_and_fresh_result(
    case: FolderCase, operation: str, intent: str, payload: dict[str, Any]
) -> None:
    """Folder acknowledgements are not message-flag or mailbox-move envelopes."""
    before = case.folder.model_dump()
    result = operate(case, operation)
    case.session.request_raw.assert_called_once_with(
        "POST",
        "https://example.test/mailws2/v1/mailbox/set",
        params={"dsid": "synthetic-account", "clientIntent": intent},
        json=payload,
        timeout=(10.0, 60.0),
        allow_redirects=False,
    )
    assert case.service.params == {"dsid": "synthetic-account"}
    assert case.folder.model_dump() == before
    if operation == "delete":
        assert result.model_dump() == {"folder_id": "20", "permanent": True}
        query = case.session.post.call_args_list[1].kwargs["json"]
        assert query["limit"] == 1
        assert query["predicate"] == {
            "expression": {"property": "mboxRef"},
            "type": "eq",
            "value": "20",
        }
    else:
        assert result.id == ("new-folder" if operation == "create" else "20")
        assert result.name == ("Created" if operation == "create" else "Renamed")
        assert result.uid_validity == (5679 if operation == "create" else 5678)
        assert result.message_count is None
    case.response.close.assert_called_once()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("A&B", "A&-B"),
        ("Caf\u00e9", "Caf&AOk-"),
        ("\u53f0\u5317", "&U,BTFw-"),
        ("\u65e5\u672c\u8a9e", "&ZeVnLIqe-"),
        ("\u00e9&\u00e9", "&AOk-&-&AOk-"),
        ("~test", "~test"),
    ],
)
def test_modified_utf7_name_encoding(name: str, expected: str) -> None:
    """Use fixed RFC/browser-compatible examples, not an encode/decode tautology."""
    assert encode_leaf(name) == expected


def test_new_child_and_rename_preserve_wire_parent(case: FolderCase) -> None:
    """Parent paths are discovered wire data, never encoded a second time."""
    case.records[1]["name"] = "Caf&AOk-"
    case.folder = case.folder.model_copy(update={"name": "Caf&AOk-"})
    child = case.service.create_folder("\u5b50", parent=case.folder)
    assert child.name == "Caf&AOk-/&W1A-"
    case.session.request_raw.assert_called_once()
    case.session.request_raw.reset_mock()
    case.did_write = False
    case.records[1]["name"] = "Caf&AOk-/Old"
    case.folder = case.folder.model_copy(update={"name": "Caf&AOk-/Old"})
    renamed = case.service.rename_folder(case.folder, "Renamed")
    assert renamed.name == "Caf&AOk-/Renamed"
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "name",
    [
        None,
        1,
        "",
        "  ",
        "#namespace",
        "$category$_test",
        "a/b",
        "?",
        "*",
        "%",
        "\n",
        "\x7f",
        "\x80",
        "\ud800",
        "a" * 513,
    ],
)
@pytest.mark.parametrize("operation", ["create", "rename"])
def test_invalid_leaf_precedes_network(
    case: FolderCase, name: Any, operation: str
) -> None:
    """Do not normalize, accept controls, or turn a name into a path/wildcard."""
    with pytest.raises(ValueError):
        if operation == "create":
            case.service.create_folder(name)
        else:
            case.service.rename_folder(case.folder, name)
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "name", ["INBOX", "drafts", "Junk", "Archive", "Sent Messages", "Deleted Messages"]
)
def test_reserved_root_names_precede_network(case: FolderCase, name: str) -> None:
    """Canonical server roles are not inferred from translated UI labels."""
    with pytest.raises(ValueError, match="system mailbox"):
        case.service.create_folder(name)
    case.session.post.assert_not_called()


def test_path_length_and_non_wire_parent_are_explicit() -> None:
    """The browser's 512-character limit applies after encoding the full path."""
    with pytest.raises(ValueError, match="full encoded"):
        folder_path("\u00e9" * 200)
    with pytest.raises(ValueError, match="full encoded"):
        folder_path("child", "p" * 510)
    with pytest.raises(ValueError, match="parent namespace"):
        folder_path("child", "Caf\u00e9")


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize("account", [None, "", " ", True, 123])
def test_account_guard_precedes_reads(
    case: FolderCase, operation: str, account: Any
) -> None:
    """No account identifiers are guessed or copied from fixture credentials."""
    case.service.params["dsid"] = account
    with pytest.raises(ValueError, match="account identifier"):
        operate(case, operation)
    case.session.post.assert_not_called()
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("folder", [None, "20", {}, 20])
@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_folder_context_type_precedes_reads(
    case: FolderCase, folder: Any, operation: str
) -> None:
    """A bare identifier cannot be used to overwrite or destroy a namespace."""
    with pytest.raises(ValueError, match="MailFolder"):
        if operation == "create":
            invalid_parent: Any = folder if folder is not None else "20"
            case.service.create_folder("Child", parent=invalid_parent)
        elif operation == "rename":
            case.service.rename_folder(folder, "Renamed")
        else:
            case.service.delete_folder(folder, confirm=True)
    case.session.post.assert_not_called()


@pytest.mark.parametrize("validity", [None, True, 0, -1, "5678"])
def test_invalid_folder_namespace_precedes_reads(
    case: FolderCase, validity: Any
) -> None:
    """Saved folder IDs alone cannot identify an incarnation safely."""
    folder = case.folder.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="UID validity"):
        case.service.rename_folder(folder, "Renamed")
    case.session.post.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "epoch",
        "renamed",
        "system",
        "label",
        "virtual",
        "duplicate",
        "marker",
        "cap",
    ],
)
@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_fresh_inventory_guards(case: FolderCase, kind: str, operation: str) -> None:
    """Incomplete, ambiguous, stale, protected, or virtual namespaces fail closed."""
    if kind == "missing":
        case.records.pop()
    elif kind == "epoch":
        case.records[1]["uidValidity"] += 1
    elif kind == "renamed":
        case.records[1]["name"] = "Other"
    elif kind == "system":
        case.records[1]["name"] = "Archive"
        case.folder = case.folder.model_copy(update={"name": "Archive"})
    elif kind == "label":
        case.records[1]["flags"] = 4
    elif kind == "virtual":
        case.records[1]["name"] = "$category$_fake"
        case.folder = case.folder.model_copy(update={"name": "$category$_fake"})
    elif kind == "duplicate":
        case.records.append(case.records[1].copy())
    elif kind == "marker":
        case.marker = "more"
    else:
        case.records = [dict(case.records[1], identifier=str(i)) for i in range(1000)]
    if kind == "system" and operation == "create":
        result = case.service.create_folder("Child", parent=case.folder)
        assert result.name == "Archive/Child"
        return
    with pytest.raises(MailError):
        if operation == "create":
            case.service.create_folder("Child", parent=case.folder)
        else:
            operate(case, operation)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "rename"])
def test_existing_name_or_case_alias_never_overwrites(
    case: FolderCase, operation: str
) -> None:
    """Refuse collisions before a mutation rather than depend on server idempotency."""
    with pytest.raises(MailError, match="already exists"):
        if operation == "create":
            case.service.create_folder("testing")
        else:
            case.service.rename_folder(case.folder, "testing")
    case.session.request_raw.assert_not_called()


def test_unchanged_rename_is_not_a_write(case: FolderCase) -> None:
    """A no-op is not reported as a successful server mutation."""
    with pytest.raises(ValueError, match="unchanged"):
        case.service.rename_folder(case.folder, "Testing")
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("confirm", [False, None, 1, "true"])
def test_delete_confirmation_is_required_before_network(
    case: FolderCase, confirm: Any
) -> None:
    """Even an empty-folder deletion is permanent and preflight can race."""
    with pytest.raises(ValueError, match="confirm=True"):
        case.service.delete_folder(case.folder, confirm=confirm)
    case.session.post.assert_not_called()


@pytest.mark.parametrize("value", [None, 1, "false"])
def test_nonempty_opt_in_is_not_truthiness(case: FolderCase, value: Any) -> None:
    """Never coerce a string or integer into permission to destroy messages."""
    with pytest.raises(ValueError, match="boolean"):
        case.service.delete_folder(case.folder, confirm=True, allow_nonempty=value)
    case.session.post.assert_not_called()


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_folder_write_refuses_children(case: FolderCase, operation: str) -> None:
    """No recursive cascade is silently authorized by one folder ID."""
    case.records.append({
        "identifier": "child",
        "name": "testing/Child",
        "uidValidity": 9,
        "flags": 0,
    })
    with pytest.raises(MailError, match="child folders"):
        if operation == "rename":
            case.service.rename_folder(case.folder, "Renamed")
        else:
            case.service.delete_folder(case.folder, confirm=True, allow_nonempty=True)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("allow", [False, True])
def test_delete_observed_nonempty_requires_opt_in(
    case: FolderCase, allow: bool
) -> None:
    """The emptiness probe includes deleted-flag records and requests no body."""
    case.messages = [{"identifier": "existing", "uid": 1, "mboxRef": {"id": "20"}}]
    if allow:
        assert (
            case.service.delete_folder(
                case.folder, confirm=True, allow_nonempty=True
            ).permanent
            is True
        )
        case.session.request_raw.assert_called_once()
    else:
        with pytest.raises(MailError, match="allow_nonempty=True"):
            case.service.delete_folder(case.folder, confirm=True)
        case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("kind", ["foreign", "over-limit", "marker"])
def test_unknown_emptiness_never_deletes(case: FolderCase, kind: str) -> None:
    """An empty page with continuation is not proof that a folder is empty."""
    case.messages = [
        {
            "identifier": "existing",
            "uid": 1,
            "mboxRef": {"id": "other" if kind == "foreign" else "20"},
        }
    ]
    if kind == "over-limit":
        case.messages *= 2
    elif kind == "marker":
        original_read = case.session.post.side_effect

        def marked(*args: Any, **kwargs: Any) -> MagicMock:
            result: MagicMock = original_read(*args, **kwargs)
            if kwargs["json"]["domain"] == "email":
                result.json.return_value = {
                    "domainObjects": [],
                    "continuationMarker": "more",
                }
            return result

        case.session.post.side_effect = marked
    with pytest.raises(MailError):
        case.service.delete_folder(case.folder, confirm=True, allow_nonempty=True)
    case.session.request_raw.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize(
    "kind",
    [
        "wrong-account",
        "no-outcome",
        "other-success",
        "other-failure",
        "foreign-key",
        "extra-key",
        "contradictory",
        "python-alias",
    ],
)
def test_uncertain_folder_outcomes_never_retry(
    case: FolderCase, operation: str, kind: str
) -> None:
    """HTTP 200 does not prove the intended folder mutation succeeded."""
    data = load_fixture("mail", f"folder_{operation}_response.json")
    success = {"create": "created", "rename": "updated", "delete": "destroyed"}[
        operation
    ]
    failure = {
        "create": "notCreated",
        "rename": "notUpdated",
        "delete": "notDestroyed",
    }[operation]
    key = "mailbox" if operation == "create" else "20"
    if kind == "wrong-account":
        data["accountId"] = "other"
    elif kind == "no-outcome":
        data[success] = None
    elif kind == "other-success":
        data["destroyed" if operation != "delete" else "updated"] = (
            ["other"] if operation != "delete" else {"other": None}
        )
    elif kind == "other-failure":
        data["notDestroyed" if operation != "delete" else "notUpdated"] = {
            "other": {"type": "denied"}
        }
    elif kind == "foreign-key":
        data[success] = (
            ["other"] if operation == "delete" else {"other": data[success][key]}
        )
    elif kind == "extra-key":
        if operation == "delete":
            data[success].append("other")
        else:
            data[success]["other"] = data[success][key]
    elif kind == "contradictory":
        data[failure] = {key: {"type": "unknown"}}
    else:
        data["account_id"] = data.pop("accountId")
    case.override = data
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize(
    "error", [{"type": "denied"}, {"reason": "denied"}, {"description": "denied"}]
)
def test_exact_structured_rejection_is_distinct(
    case: FolderCase, operation: str, error: dict[str, str]
) -> None:
    """Only the exact operation/key failure without successes establishes rejection."""
    data = load_fixture("mail", f"folder_{operation}_response.json")
    data[
        {"create": "created", "rename": "updated", "delete": "destroyed"}[operation]
    ] = None
    data[
        {"create": "notCreated", "rename": "notUpdated", "delete": "notDestroyed"}[
            operation
        ]
    ] = {"mailbox" if operation == "create" else "20": error}
    case.override = data
    with pytest.raises(MailWriteRejected, match="rejected"):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize("error", [None, False, 0, "denied", [], {}, {"reason": 1}])
def test_malformed_failure_values_are_uncertain(
    case: FolderCase, operation: str, error: Any
) -> None:
    """A malformed per-key value must not imply that it is safe to retry."""
    data = load_fixture("mail", f"folder_{operation}_response.json")
    data[
        {"create": "created", "rename": "updated", "delete": "destroyed"}[operation]
    ] = None
    data[
        {"create": "notCreated", "rename": "notUpdated", "delete": "notDestroyed"}[
            operation
        ]
    ] = {"mailbox" if operation == "create" else "20": error}
    case.override = data
    with pytest.raises(MailWriteUncertain):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    [
        "missing-identifier",
        "mismatched-id",
        "wrong-name",
        "existing-id",
        "deleted",
        "flags",
        "uid-bool",
    ],
)
def test_invalid_created_identity_is_uncertain(case: FolderCase, kind: str) -> None:
    """A creation acknowledgement must identify a genuinely new expected namespace."""
    data = load_fixture("mail", "folder_create_response.json")
    record = data["created"]["mailbox"]
    if kind == "missing-identifier":
        del record["identifier"]
    elif kind == "mismatched-id":
        record["id"] = "other"
    elif kind == "wrong-name":
        record["name"] = "Other"
    elif kind == "existing-id":
        record["id"] = record["identifier"] = "20"
    elif kind == "deleted":
        record["isMboxDeleted"] = True
    elif kind == "flags":
        record["flags"] = 4
    else:
        record["uidValidity"] = True
    case.override = data
    with pytest.raises(MailWriteUncertain):
        case.service.create_folder("Created")
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize("kind", ["timeout", "http", "json", "close", "post-read"])
def test_write_or_verification_failure_is_uncertain(
    case: FolderCase, operation: str, kind: str
) -> None:
    """Failure after dispatch cannot authorize replay or destructive compensation."""
    if kind == "timeout":
        case.session.request_raw.side_effect = ReadTimeout("private detail")
    elif kind == "http":
        case.response.status_code = 421
    elif kind == "json":
        case.response.json.side_effect = ValueError("private detail")
    elif kind == "close":
        case.response.close.side_effect = OSError("private detail")
    else:
        case.read_error = PyiCloudConnectionException("private detail")
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("kind", ["missing", "name", "epoch", "flags"])
@pytest.mark.parametrize("operation", ["create", "rename"])
def test_post_write_namespace_mismatch_is_uncertain(
    case: FolderCase, kind: str, operation: str
) -> None:
    """Return no fabricated folder object if fresh verification disagrees."""
    identifier = "new-folder" if operation == "create" else "20"
    case.after = (
        []
        if kind == "missing"
        else [
            {
                "identifier": identifier,
                "name": "Created" if operation == "create" else "Renamed",
                "uidValidity": 5679 if operation == "create" else 5678,
                "flags": 0,
            }
        ]
    )
    if kind == "name":
        case.after[0]["name"] = "Other"
    elif kind == "epoch":
        case.after[0]["uidValidity"] += 1
    elif kind == "flags":
        case.after[0]["flags"] = 4
    with pytest.raises(MailWriteUncertain):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


def test_acknowledged_delete_still_present_is_uncertain(case: FolderCase) -> None:
    """Do not claim a current-state postcondition solely from an acknowledgement."""
    case.after = deepcopy(case.records)
    with pytest.raises(MailWriteUncertain):
        case.service.delete_folder(case.folder, confirm=True)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_foreign_failure_key_is_uncertain(case: FolderCase, operation: str) -> None:
    """A different object's failure says nothing about the submitted mutation."""
    data = load_fixture("mail", f"folder_{operation}_response.json")
    data[
        {"create": "created", "rename": "updated", "delete": "destroyed"}[operation]
    ] = None
    data[
        {"create": "notCreated", "rename": "notUpdated", "delete": "notDestroyed"}[
            operation
        ]
    ] = {"foreign": {"type": "denied"}}
    case.override = data
    with pytest.raises(MailWriteUncertain):
        operate(case, operation)
    case.session.request_raw.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
@pytest.mark.parametrize("mode", ["json", "text"])
def test_mounted_folder_cli(case: FolderCase, operation: str, mode: str) -> None:
    """Exercise real CLI lookups, public methods, transport, and output."""
    args = {
        "create": ["create-folder", "Created"],
        "rename": ["rename-folder", "Renamed", "--folder-id", "20"],
        "delete": ["delete-folder", "--folder-id", "20", "--confirm-folder-id", "20"],
    }[operation]
    api = MagicMock()
    api.mail = case.service
    api.account_name = "user@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, ["mail", *args, "--format", mode])
    assert result.exit_code == 0, result.exception
    case.session.request_raw.assert_called_once()
    assert "synthetic-account" not in result.stdout
    if mode == "json":
        payload = json.loads(result.stdout)
        if operation == "delete":
            assert payload == {"folder_id": "20", "permanent": True}
        else:
            assert payload["name"] == (
                "Created" if operation == "create" else "Renamed"
            )
            assert payload["id"] == ("new-folder" if operation == "create" else "20")
    else:
        assert "Mail Folder" in result.stdout


def test_mounted_create_under_explicit_parent(case: FolderCase) -> None:
    """Parent selection uses an exact discovered ID, not a folder-name guess."""
    api = MagicMock()
    api.mail = case.service
    api.account_name = "user@example.test"
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(
            app,
            ["mail", "create-folder", "Child", "--parent-id", "20", "--format", "json"],
        )
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)["name"] == "Testing/Child"


@pytest.mark.parametrize("kind", ["missing", "uncertain", "nonempty", "allow-nonempty"])
def test_folder_cli_guards(case: FolderCase, kind: str) -> None:
    """No lookup fallback, raw failure echo, implicit nonempty opt-in, or retry."""
    api = MagicMock()
    api.mail = case.service
    api.account_name = "user@example.test"
    if kind == "missing":
        case.records.pop()
    elif kind == "uncertain":
        case.response.status_code = 503
    else:
        case.messages = [{"identifier": "existing", "uid": 1, "mboxRef": {"id": "20"}}]
    args = ["mail", "delete-folder", "--folder-id", "20", "--confirm-folder-id", "20"]
    if kind == "allow-nonempty":
        args.append("--allow-nonempty")
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
    ):
        result = CliRunner().invoke(app, args)
    if kind == "allow-nonempty":
        assert result.exit_code == 0, result.exception
        case.session.request_raw.assert_called_once()
    else:
        assert result.exit_code != 0
        assert isinstance(result.exception, CLIAbort)
        if kind == "uncertain":
            assert "uncertain" in str(result.exception)
            case.session.request_raw.assert_called_once()
        else:
            case.session.request_raw.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["create-folder"],
        ["rename-folder", "New"],
        ["delete-folder", "--folder-id", "20"],
        ["delete-folder", "--folder-id", "20", "--confirm-folder-id", "wrong"],
    ],
)
def test_invalid_folder_cli_selection_is_offline(args: list[str]) -> None:
    """Missing or mismatched confirmations cannot even acquire an account."""
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(app, ["mail", *args])
    assert result.exit_code != 0
    get_api.assert_not_called()
