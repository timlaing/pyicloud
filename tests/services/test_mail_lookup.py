"""Offline identity and mailbox-epoch checks for exact Mail metadata lookup."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import Response

from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

LookupCase = tuple[MailService, MagicMock, MagicMock, MailFolder]


@pytest.fixture(name="lookup")
def lookup_fixture() -> Iterator[LookupCase]:
    """A synthetic older message in a stable mailbox; no network is available."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "message_lookup_response.json")
    session.post.return_value = response
    service = MailService("https://example.test", session, {})
    with patch.object(service, "folders", return_value=[folder]) as folders:
        yield service, session, folders, folder


def call_lookup(
    service: MailService, folder: MailFolder, method: str
) -> MailMessage | None:
    """Exercise both public identity forms with the same synthetic record."""
    if method == "message":
        return service.message(folder, "lookup-message-7")
    return service.message_by_uid(folder, 7)


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
def test_exact_lookup_never_scans_newest_messages(
    lookup: LookupCase, method: str
) -> None:
    """Exact queries request detailed metadata, exclude deleted mail, and close."""
    service, session, folders, folder = lookup
    result = call_lookup(service, folder, method)

    assert result is not None
    assert result.id == "lookup-message-7"
    assert result.uid == 7
    assert result.folder == folder
    assert result.parts[0].id == "1"
    assert folders.call_count == 2
    session.post.assert_called_once()
    session.post.return_value.close.assert_called_once()
    call = session.post.call_args
    assert call.args[0].endswith("/mailws2/v1/message/list")
    assert call.kwargs["timeout"] == (10, 60)
    assert call.kwargs["allow_redirects"] is False
    query = call.kwargs["json"]
    assert query["limit"] == 2
    assert "parts" in query["properties"]
    assert query["predicate"]["value"] == folder.id
    assert query["predicate"]["and"][0]["value"] is False
    expected = "identifier" if method == "message" else "uid"
    value = "lookup-message-7" if method == "message" else 7
    assert query["predicate"]["and"][1] == {
        "type": "eq",
        "expression": {"type": "property", "property": expected},
        "value": value,
    }
    assert "orderby" not in query
    assert "collapseOn" not in query


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
def test_missing_message_is_none(lookup: LookupCase, method: str) -> None:
    """An empty exact response is not confused with a failed request."""
    service, session, folders, folder = lookup
    session.post.return_value.json.return_value = {"domainObjects": []}
    assert call_lookup(service, folder, method) is None
    assert folders.call_count == 2
    session.post.return_value.close.assert_called_once()


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
@pytest.mark.parametrize("wrong_folder", [False, True])
def test_wrong_identity_is_never_returned(
    lookup: LookupCase, method: str, wrong_folder: bool
) -> None:
    """A server ignoring the exact predicate must not select unrelated mail."""
    service, session, _, folder = lookup
    record = session.post.return_value.json.return_value["domainObjects"][0]
    if wrong_folder:
        record["mboxRef"]["id"] = "other-folder"
    elif method == "message":
        record["identifier"] = "different-message"
    else:
        record["uid"] = 8
    with pytest.raises(MailError, match="different message or folder"):
        call_lookup(service, folder, method)
    session.post.return_value.close.assert_called_once()


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
@pytest.mark.parametrize(
    "count,marker", [(2, None), (3, None), (1, "opaque"), (0, "opaque")]
)
def test_ambiguous_or_incomplete_lookup_fails(
    lookup: LookupCase, method: str, count: int, marker: str | None
) -> None:
    """Never pick the first ambiguous match or claim an incomplete lookup is empty."""
    service, session, _, folder = lookup
    data = session.post.return_value.json.return_value
    data["domainObjects"] *= count
    data["continuationMarker"] = marker
    with pytest.raises(MailError, match="ambiguous or incomplete"):
        call_lookup(service, folder, method)


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
@pytest.mark.parametrize("during_request", [False, True])
@pytest.mark.parametrize(
    "replacement", [[], [MailFolder(identifier="10", name="INBOX", uidValidity=9999)]]
)
def test_mailbox_change_invalidates_lookup(
    lookup: LookupCase, method: str, during_request: bool, replacement: list[MailFolder]
) -> None:
    """Deletion and UID-namespace changes fail before returning data or None."""
    service, session, folders, folder = lookup
    folders.side_effect = [[folder], replacement] if during_request else [replacement]
    with pytest.raises(MailError, match="identity changed"):
        call_lookup(service, folder, method)
    assert session.post.call_count == int(during_request)


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
@pytest.mark.parametrize("validity", [None, 0, -1])
def test_missing_uid_namespace_fails_before_http(
    lookup: LookupCase, method: str, validity: int | None
) -> None:
    """A saved UID cannot be safely bound to an unknown mailbox incarnation."""
    service, session, folders, folder = lookup
    unknown = folder.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="UID validity"):
        call_lookup(service, unknown, method)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [None, True, 7, "", "   "])
def test_invalid_message_id_fails_before_http(lookup: LookupCase, value: Any) -> None:
    """Opaque IDs must be non-empty strings, never coerced numeric/thread values."""
    service, session, folders, folder = lookup
    with pytest.raises(ValueError, match="message_id"):
        service.message(folder, value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.5, "7"])
def test_invalid_uid_fails_before_http(lookup: LookupCase, value: Any) -> None:
    """Folder UIDs are positive integers, not truth values or coerced strings."""
    service, session, folders, folder = lookup
    with pytest.raises(ValueError, match="uid"):
        service.message_by_uid(folder, value)
    session.post.assert_not_called()
    folders.assert_not_called()


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
def test_lookup_preserves_transport_errors(lookup: LookupCase, method: str) -> None:
    """An HTTP failure must not masquerade as a missing message."""
    service, session, _, folder = lookup
    session.post.side_effect = PyiCloudConnectionException("offline")
    with pytest.raises(PyiCloudConnectionException):
        call_lookup(service, folder, method)


@pytest.mark.parametrize("method", ["message", "message_by_uid"])
def test_lookup_uses_refreshed_folder_name(lookup: LookupCase, method: str) -> None:
    """A rename within the same UID namespace returns current body-request context."""
    service, _, folders, folder = lookup
    renamed = folder.model_copy(update={"name": "Renamed"})
    folders.side_effect = [[folder], [renamed]]
    result = call_lookup(service, folder, method)
    assert result is not None
    assert result.folder.name == "Renamed"
