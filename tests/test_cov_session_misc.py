"""Branch-coverage tests for misc session-adjacent modules.

Covers previously-missed branches in:

- ``pyicloud.common.cloudkit.base`` (env-driven validation mode resolution)
- ``pyicloud.utils`` (keyring helpers and camelCase conversion)
- ``pyicloud.session`` (persistence, error normalization, JSON decoding)
- ``pyicloud.services.ubiquity`` (service/node edge cases)
"""

# pylint: disable=protected-access

from json import JSONDecodeError
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest
from requests import Response

from pyicloud.common.cloudkit.base import resolve_cloudkit_validation_extra
from pyicloud.const import AppleAuthError
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
)
from pyicloud.services.ubiquity import UbiquityNode, UbiquityService
from pyicloud.session import PyiCloudSession
from pyicloud.utils import (
    KEYRING_SYSTEM,
    camelcase_to_underscore,
    delete_password_in_keyring,
    get_password,
    get_password_from_keyring,
    password_exists_in_keyring,
    store_password_in_keyring,
    underscore_to_camelcase,
)

# ---------------------------------------------------------------------------
# pyicloud/common/cloudkit/base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "forbid"),
        ("true", "forbid"),
        ("yes", "forbid"),
        ("on", "forbid"),
        ("strict", "forbid"),
        ("0", "allow"),
        ("false", "allow"),
        ("no", "allow"),
        ("off", "allow"),
        ("lenient", "allow"),
    ],
)
def test_resolve_cloudkit_validation_extra_supports_boolean_env_values(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    """Legacy boolean-style PYICLOUD_CK_EXTRA values map to forbid/allow."""
    monkeypatch.setenv("PYICLOUD_CK_EXTRA", raw)
    assert resolve_cloudkit_validation_extra() == expected


def test_resolve_cloudkit_validation_extra_unknown_env_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised PYICLOUD_CK_EXTRA value falls back to ``default``."""
    monkeypatch.setenv("PYICLOUD_CK_EXTRA", "bogus")
    assert resolve_cloudkit_validation_extra() == "allow"
    assert resolve_cloudkit_validation_extra(default="forbid") == "forbid"


# ---------------------------------------------------------------------------
# pyicloud/utils
# ---------------------------------------------------------------------------


def test_get_password_returns_keyring_password() -> None:
    """get_password returns the keyring password without prompting."""
    with (
        patch("pyicloud.utils.keyring.get_password", return_value="secret"),
        patch("getpass.getpass") as mock_getpass,
    ):
        assert get_password("user", interactive=True) == "secret"
    mock_getpass.assert_not_called()


def test_get_password_prompts_when_no_keyring_password() -> None:
    """get_password prompts interactively when the keyring has no password."""
    with (
        patch("pyicloud.utils.keyring.get_password", return_value=None),
        patch("getpass.getpass", return_value="typed") as mock_getpass,
    ):
        assert get_password("user", interactive=True) == "typed"
    mock_getpass.assert_called_once_with("Enter iCloud password for user: ")


def test_get_password_non_interactive_returns_none() -> None:
    """get_password returns None when the keyring misses and not interactive."""
    with patch("pyicloud.utils.keyring.get_password", return_value=None):
        assert get_password("user", interactive=False) is None


def test_get_password_from_keyring_delegates_to_keyring() -> None:
    """get_password_from_keyring proxies to the keyring service."""
    with patch(
        "pyicloud.utils.keyring.get_password", return_value="secret"
    ) as mock_get:
        assert get_password_from_keyring("user") == "secret"
    mock_get.assert_called_once_with(KEYRING_SYSTEM, "user")


def test_password_exists_in_keyring() -> None:
    """password_exists_in_keyring reports whether the keyring has a password."""
    with patch("pyicloud.utils.keyring.get_password", return_value="secret"):
        assert password_exists_in_keyring("user") is True
    with patch("pyicloud.utils.keyring.get_password", return_value=None):
        assert password_exists_in_keyring("user") is False


def test_store_password_in_keyring_delegates_to_keyring() -> None:
    """store_password_in_keyring proxies to the keyring service."""
    with patch("pyicloud.utils.keyring.set_password") as mock_set:
        store_password_in_keyring("user", "secret")
    mock_set.assert_called_once_with(KEYRING_SYSTEM, "user", "secret")


def test_delete_password_in_keyring_delegates_to_keyring() -> None:
    """delete_password_in_keyring proxies to the keyring service."""
    with patch("pyicloud.utils.keyring.delete_password") as mock_delete:
        delete_password_in_keyring("user")
    mock_delete.assert_called_once_with(KEYRING_SYSTEM, "user")


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("start_date", "StartDate"),
        ("foo_bar_baz", "FooBarBaz"),
        ("foo__bar", "Foo_Bar"),
    ],
)
def test_underscore_to_camelcase_initial_capital(word: str, expected: str) -> None:
    """underscore_to_camelcase capitalizes the first word when requested."""
    assert underscore_to_camelcase(word, initial_capital=True) == expected


def test_camelcase_to_underscore_round_trips() -> None:
    """camelcase_to_underscore complements the camelCase conversion."""
    assert camelcase_to_underscore("startDate") == "start_date"


# ---------------------------------------------------------------------------
# pyicloud/session
# ---------------------------------------------------------------------------


def test_session_init_keeps_preloaded_client_id(
    pyicloud_service: Any,
) -> None:
    """A client_id already in the loaded session data is not overwritten."""
    with (
        patch("pyicloud.session.load", return_value={"client_id": "preloaded"}),
        patch("builtins.open", new_callable=mock_open),
    ):
        session = PyiCloudSession(pyicloud_service, "fresh-id", cookie_directory="")
    assert session.data["client_id"] == "preloaded"


def test_load_session_data_warns_and_clears_cookies_on_failure(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A corrupt cookie jar logs a warning and is cleared in memory."""
    pyicloud_session.cookies = MagicMock()
    pyicloud_session.cookies.load.side_effect = OSError("corrupt jar")
    with (
        patch("os.path.exists", return_value=True),
        patch("builtins.open", new_callable=mock_open),
    ):
        pyicloud_session._load_session_data()
    pyicloud_session.cookies.clear.assert_called_once_with()


def test_save_session_data_creates_missing_cookie_directory(
    pyicloud_service: Any,
) -> None:
    """_save_session_data creates the cookie directory when it is missing."""
    with (
        patch("os.path.isdir", return_value=False),
        patch("os.makedirs") as mock_makedirs,
        patch("builtins.open", new_callable=mock_open),
    ):
        session = PyiCloudSession(pyicloud_service, "", cookie_directory="/not/real")
        session.cookies = MagicMock()
        session._save_session_data()
    mock_makedirs.assert_called_once_with("/not/real", exist_ok=True)
    session.cookies.save.assert_called_once_with()


def test_save_session_data_warns_when_cookie_save_fails(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A failed cookie save logs a warning instead of raising."""
    pyicloud_session.cookies = MagicMock()
    pyicloud_session.cookies.save.side_effect = OSError("disk full")
    with patch("builtins.open", new_callable=mock_open):
        pyicloud_session._save_session_data()
    pyicloud_session.cookies.save.assert_called_once_with()


def test_clear_persistence_skips_missing_files(
    pyicloud_session: PyiCloudSession,
) -> None:
    """Files that no longer exist are skipped during persistence cleanup."""
    with (
        patch(
            "pyicloud.session.os.remove", side_effect=FileNotFoundError
        ) as mock_remove,
        patch("builtins.open", new_callable=mock_open),
    ):
        pyicloud_session.clear_persistence()
    assert pyicloud_session.data == {}
    assert mock_remove.call_count == 2


def test_clear_persistence_keeps_files_when_requested(
    pyicloud_session: PyiCloudSession,
) -> None:
    """clear_persistence(remove_files=False) re-saves instead of deleting."""
    with (
        patch.object(pyicloud_session, "_save_session_data") as mock_save,
        patch("pyicloud.session.os.remove") as mock_remove,
    ):
        pyicloud_session.clear_persistence(remove_files=False)
    mock_save.assert_called_once_with()
    mock_remove.assert_not_called()


def test_request_raw_success(pyicloud_service_working: Any) -> None:
    """request_raw returns the response and persists session state."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
    ):
        response = MagicMock(status_code=200)
        response.headers = {}
        response.json.return_value = {"ok": True}
        mock_request.return_value = response
        session = PyiCloudSession(pyicloud_service_working, "", cookie_directory="")
        session.cookies = MagicMock()
        assert session.request_raw("GET", "https://example.com") is response


def test_request_returns_non_json_response_without_decoding(
    pyicloud_service_working: Any,
) -> None:
    """A successful non-JSON response is returned without JSON decoding."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
    ):
        response = MagicMock(status_code=200, ok=True)
        response.headers = {"Content-Type": "text/html"}
        response.raise_for_status.return_value = None
        mock_request.return_value = response
        session = PyiCloudSession(pyicloud_service_working, "", cookie_directory="")
        session.cookies = MagicMock()
        assert session.request("GET", "https://example.com") is response


def test_handle_request_error_find_my_reauth_required(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A Find My reauth status raises the re-authentication exception."""
    response = MagicMock()
    response.reason = "Reauth required"
    with pytest.raises(PyiCloudAuthRequiredException):
        pyicloud_session._handle_request_error(
            status_code=AppleAuthError.FIND_MY_REAUTH_REQUIRED,
            response=response,
        )


def test_raise_if_account_locked_ignores_unrelated_service_errors(
    pyicloud_session: PyiCloudSession,
) -> None:
    """Service-error lists without an account lock are left alone."""
    response = MagicMock()
    response.json.return_value = {
        "serviceErrors": [
            "not-a-dict",
            {"code": "PCS_ERROR"},
            {"message": "no code field"},
        ]
    }
    pyicloud_session._raise_if_account_locked(response)
    response.json.assert_called_once_with()


def test_auth_type_from_hsa2_body_unknown_type_returns_none(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A body without an HSA2 marker returns None from the detector."""
    response = MagicMock()
    response.json.return_value = {"authType": "sms"}
    assert pyicloud_session._auth_type_from_hsa2_body(response) is None


@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_reason"),
    [
        ({"errorMessage": "Bad message", "errorCode": 500}, 500, "Bad message"),
        ({"reason": "Reason failed", "errorCode": 421}, 421, "Reason failed"),
        (
            {"errorReason": "Error reason", "serverErrorCode": "X"},
            "X",
            "Error reason",
        ),
        ({"error": "Generic"}, None, "Generic"),
    ],
)
def test_decode_json_response_extracts_reason_fields(
    pyicloud_session: PyiCloudSession,
    payload: dict[str, Any],
    expected_code: int | str | None,
    expected_reason: str,
) -> None:
    """_decode_json_response picks the first known reason field."""
    response = MagicMock()
    response.content = b"{}"
    response.json.return_value = payload
    with patch.object(pyicloud_session, "_raise_error") as mock_raise_error:
        pyicloud_session._decode_json_response(response)
    mock_raise_error.assert_called_once_with(response, expected_code, expected_reason)


def test_decode_json_response_coerces_non_string_reason(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A non-string reason is coerced to a generic message."""
    response = MagicMock()
    response.content = b"{}"
    response.json.return_value = {"errorMessage": 12345}
    with patch.object(pyicloud_session, "_raise_error") as mock_raise_error:
        pyicloud_session._decode_json_response(response)
    mock_raise_error.assert_called_once_with(response, None, "Unknown reason")


def test_decode_json_response_ignores_non_object_body(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A JSON array body is ignored by _decode_json_response."""
    response = MagicMock()
    response.content = b"[]"
    response.json.return_value = []
    with patch.object(pyicloud_session, "_raise_error") as mock_raise_error:
        pyicloud_session._decode_json_response(response)
    mock_raise_error.assert_not_called()


def test_decode_json_response_ignores_body_without_reason(
    pyicloud_session: PyiCloudSession,
) -> None:
    """A JSON object without a reason field is ignored."""
    response = MagicMock()
    response.content = b"{}"
    response.json.return_value = {"status": "ok"}
    with patch.object(pyicloud_session, "_raise_error") as mock_raise_error:
        pyicloud_session._decode_json_response(response)
    mock_raise_error.assert_not_called()


def test_decode_json_response_skips_empty_body(
    pyicloud_session: PyiCloudSession,
) -> None:
    """An empty body short-circuits _decode_json_response."""
    response = MagicMock()
    response.content = b""
    pyicloud_session._decode_json_response(response)
    response.json.assert_not_called()


def test_decode_json_response_handles_json_decode_error(
    pyicloud_session: PyiCloudSession,
) -> None:
    """An undecodable JSON body is logged and ignored."""
    response = MagicMock()
    response.content = b"not json"
    response.json.side_effect = JSONDecodeError("bad json", "not json", 0)
    pyicloud_session._decode_json_response(response)


# ---------------------------------------------------------------------------
# pyicloud/services/ubiquity
# ---------------------------------------------------------------------------


def test_ubiquity_service_init_reraises_non_503_error() -> None:
    """A non-503 API error during init is re-raised unchanged."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_session.get.side_effect = PyiCloudAPIResponseException(code=500, reason="boom")
    with pytest.raises(PyiCloudAPIResponseException):
        UbiquityService("https://example.com", mock_session, {"dsid": "12345"})


def test_ubiquity_service_get_file() -> None:
    """get_file streams the node file with the supplied kwargs."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_session.get.return_value = mock_response
    service = UbiquityService("https://example.com", mock_session, {"dsid": "12345"})
    assert service.get_file("node123", stream=True) is mock_response
    mock_session.get.assert_called_with(
        "https://example.com/ws/12345/file/node123", stream=True
    )


def test_ubiquity_service_getattr_delegates_to_root() -> None:
    """Unknown service attributes are resolved against the root node."""
    service = UbiquityService.__new__(UbiquityService)
    root = MagicMock()
    root.foo = "bar"
    service._root = root
    assert service.foo == "bar"


def test_ubiquity_service_getitem_delegates_to_root() -> None:
    """Indexing a service resolves the child on the root node."""
    service = UbiquityService.__new__(UbiquityService)
    root = MagicMock(spec=UbiquityNode)
    root.__getitem__.return_value = "item-abc"
    service._root = root
    item: Any = service["abc"]
    assert item == "item-abc"


def test_ubiquity_node_size_invalid_returns_none() -> None:
    """A non-numeric node size yields None."""
    node = UbiquityNode(MagicMock(), {"size": "not-a-number"})
    assert node.size is None


def test_ubiquity_node_open_without_item_id_raises() -> None:
    """open() raises KeyError when the node has no item id."""
    node = UbiquityNode(MagicMock(), {})
    with pytest.raises(KeyError, match="Node has no item id"):
        node.open()


def test_ubiquity_node_open_returns_file() -> None:
    """open() fetches the node file via the connection."""
    connection = MagicMock(spec=UbiquityService)
    connection.get_file.return_value = "file-response"
    node = UbiquityNode(connection, {"item_id": "42"})
    file_response: Any = node.open(stream=True)
    assert file_response == "file-response"
    connection.get_file.assert_called_once_with("42", stream=True)


def test_ubiquity_node_get_children_without_item_id_raises() -> None:
    """get_children() raises KeyError when the node has no item id."""
    node = UbiquityNode(MagicMock(), {})
    with pytest.raises(KeyError, match="Node has no item id"):
        node.get_children()


def test_ubiquity_node_get_children_caches_result() -> None:
    """get_children() fetches once and reuses the cached list."""
    connection = MagicMock(spec=UbiquityService)
    child = MagicMock(spec=UbiquityNode)
    connection.get_children.return_value = [child]
    node = UbiquityNode(connection, {"item_id": "42"})
    first = node.get_children()
    assert node.get_children() is first
    connection.get_children.assert_called_once_with("42")


def test_ubiquity_node_getitem_missing_child_raises_keyerror() -> None:
    """Indexing a node without a matching child raises KeyError."""
    connection = MagicMock(spec=UbiquityService)
    connection.get_children.return_value = []
    node = UbiquityNode(connection, {"item_id": "42"})
    with pytest.raises(KeyError, match="No child named missing named"):
        _ = node["missing named"]


def test_ubiquity_node_str_and_repr() -> None:
    """str() and repr() describe the node by name and type."""
    node = UbiquityNode(MagicMock(), {"name": "Docs", "type": "folder"})
    assert str(node) == "Docs"
    assert repr(node) == "<Folder: 'Docs'>"


def test_ubiquity_node_defaults() -> None:
    """Missing name/type/size metadata falls back to safe defaults."""
    node = UbiquityNode(MagicMock(), {})
    assert node.name == "<unknown>"
    assert node.type == "<unknown>"
    assert node.size == -1
    assert node.item_id is None
