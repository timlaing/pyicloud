"""Account-scoped sender discovery must not infer permission from a login."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from requests import ReadTimeout, Response

from pyicloud.endpoints import webservice
from pyicloud.services.mail import MailError, MailService
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@pytest.fixture(name="case")
def sender_case() -> tuple[MailService, MagicMock, MagicMock, dict[str, Any]]:
    """Use real validation with only authenticated HTTP I/O mocked."""
    data: dict[str, Any] = load_fixture("mail", "sender_preferences_response.json")
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = data
    session.get.return_value = response
    return (
        MailService(
            "https://gateway.example.test",
            session,
            {"dsid": "12345"},
            settings_root="https://settings.example.test/",
        ),
        session,
        response,
        data,
    )


def test_enabled_sender_addresses_and_discovered_endpoint(case: Any) -> None:
    """Primary, active alias and permitted custom domains use account metadata."""
    service, session, response, _data = case
    result = service.senders()
    assert [(item.address, item.display_name) for item in result] == [
        ("primary@example.test", "Synthetic User"),
        ("alias@example.test", "Synthetic Alias"),
        ("custom@custom.example.test", "Synthetic User"),
    ]
    assert all("example.test" not in repr(item) for item in result)
    session.get.assert_called_once_with(
        "https://settings.example.test/cc/mail/v1/account/12345/preference/web/all",
        params={"userEntryPoint": "/mail/load"},
        timeout=(10.0, 60.0),
        allow_redirects=False,
    )
    response.close.assert_called_once()
    session.post.assert_not_called()
    session.request_raw.assert_not_called()


@pytest.mark.parametrize("identifier", [None, True, 12345, "", "../other", "\u0661"])
def test_invalid_account_is_rejected_before_settings_lookup(
    case: Any, identifier: Any
) -> None:
    """Account IDs cannot introduce path segments or select a different account."""
    service, session, _response, _data = case
    service.params["dsid"] = identifier
    with pytest.raises(ValueError, match="decimal account"):
        service.senders()
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "root",
    [
        "http://example.test",
        "https://user@example.test",
        "https://example.test/?token=secret",
    ],
)
def test_invalid_discovered_root_is_not_contacted(case: Any, root: str) -> None:
    """A malformed service root cannot redirect authenticated preference reads."""
    service, session, _response, _data = case
    service = MailService(
        service.service_root, session, service.params, settings_root=root
    )
    with pytest.raises(MailError, match="endpoint is unusable"):
        service.senders()
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "status",
        "json",
        "missing",
        "account",
        "bool-account",
        "alias-key",
        "bool-permission",
        "missing-permission",
    ],
)
def test_invalid_preferences_close_response(case: Any, kind: str) -> None:
    """Errors cannot enable identities or echo private preferences."""
    service, session, response, data = case
    if kind == "status":
        response.status_code = 421
    elif kind == "json":
        response.json.side_effect = ValueError("private response")
    elif kind == "missing":
        del data["account"]["aliases"]
    elif kind == "account":
        data["account"]["dsId"] = "99999"
    elif kind == "bool-account":
        data["account"]["dsId"] = True
    elif kind == "alias-key":
        data["account"]["account_id"] = data["account"].pop("dsId")
    elif kind == "bool-permission":
        data["account"]["supportedDomains"][0]["allowSendFrom"] = 1
    else:
        del data["account"]["supportedDomains"][0]["allowSendFrom"]
    with pytest.raises(MailError) as error:
        service.senders()
    assert "private response" not in str(error.value)
    response.close.assert_called_once()
    session.get.assert_called_once()
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("emailId", ""),
        ("emailId", "one@two"),
        ("emailId", "a\r\nBcc: b"),
        ("emailId", "bad local"),
        ("emailId", '"quoted"'),
        ("fullName", "Header\nInjection"),
        ("domain", ""),
        ("domain", "one@two"),
        ("domain", "bad domain"),
    ],
)
def test_invalid_sender_fields_never_become_headers(
    case: Any, field: str, value: str
) -> None:
    """Reject controls, malformed addresses and normalization ambiguity."""
    service, _session, response, data = case
    if field == "domain":
        data["account"]["supportedDomains"][0][field] = value
    else:
        data["account"][field] = value
    with pytest.raises(MailError):
        service.senders()
    response.close.assert_called_once()


def test_case_alias_collision_is_ambiguous(case: Any) -> None:
    """Do not silently choose between contradictory enabled sender records."""
    service, _session, _response, data = case
    data["account"]["aliases"][0]["emailId"] = "PRIMARY"
    with pytest.raises(MailError, match="ambiguous"):
        service.senders()


@pytest.mark.parametrize("key", ["error", "errors"])
def test_error_with_valid_account_data_is_not_success(case: Any, key: str) -> None:
    """Stale successful-looking fields cannot override a failure indication."""
    service, _session, response, data = case
    data[key] = "private failure details"
    with pytest.raises(MailError, match="returned an error") as error:
        service.senders()
    assert "private failure" not in str(error.value)
    response.close.assert_called_once()


def test_no_enabled_sender_is_not_silently_enabled(case: Any) -> None:
    """Neither a sole address nor the login grants an absent permission."""
    service, _session, _response, data = case
    data["account"]["aliases"] = []
    data["account"]["customDomains"] = []
    data["account"]["supportedDomains"][0]["allowSendFrom"] = False
    assert service.senders() == []


def test_missing_settings_endpoint_preserves_ordinary_reads(case: Any) -> None:
    """Only sender discovery requires the additional settings endpoint."""
    service, session, response, _data = case
    service = MailService(service.service_root, session, service.params)
    with pytest.raises(MailError, match="settings endpoint is unavailable"):
        service.senders()
    session.get.assert_not_called()
    response.json.return_value = {"domainObjects": []}
    session.post.return_value = response
    assert service.folders() == []


def test_sender_settings_inventory_is_optional() -> None:
    """Missing sender settings should not fail ordinary account diagnostics."""
    entry = webservice("mcc")
    assert entry is not None and entry.powers == ("mail",) and not entry.required


def test_numeric_account_and_alias_display_name_fallback(case: Any) -> None:
    """Support observed numeric IDs without boolean coercion or invented names."""
    service, _session, _response, data = case
    data["account"]["dsId"] = 12345
    del data["account"]["aliases"][0]["fullName"]
    assert service.senders()[1].display_name == "Synthetic User"


def test_settings_read_transport_failure_is_not_replayed(case: Any) -> None:
    """Existing read-side exception types remain visible; never call a write."""
    service, session, _response, _data = case
    session.get.side_effect = ReadTimeout("private")
    with pytest.raises(ReadTimeout):
        service.senders()
    session.get.assert_called_once()
    session.request_raw.assert_not_called()
