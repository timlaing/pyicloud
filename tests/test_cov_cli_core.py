"""Coverage-focused tests for the core CLI helpers and auth commands.

These tests target the branches in ``pyicloud.cli.output``, ``pyicloud.cli.context``,
``pyicloud.cli.normalize``, ``pyicloud.cli.account_index``, ``pyicloud.cmdline`` and
``pyicloud.cli.commands.auth`` that the existing suite leaves uncovered.
"""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import click
import pytest
from rich.console import Console
import typer
from typer.testing import CliRunner

from pyicloud.cli import normalize
from pyicloud.cli import output as output_module
from pyicloud.cli.app import app as cli_app
from pyicloud.exceptions import (
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
    PyiCloudNoTrustedNumberAvailable,
)

account_index = importlib.import_module("pyicloud.cli.account_index")
context_module = importlib.import_module("pyicloud.cli.context")

TEST_BASE = Path(tempfile.gettempdir()) / "python-test-results"
TEST_BASE.mkdir(parents=True, exist_ok=True)


def _tmp_dir(label: str = "cov-cli") -> Path:
    """Create a throwaway directory allowed by the filesystem guard."""
    return Path(tempfile.mkdtemp(prefix=f"{label}-", dir=TEST_BASE))


def _ns(**kwargs: Any) -> Any:
    """Build a lightweight attribute namespace for fakes."""
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# pyicloud/cli/output.py
# ---------------------------------------------------------------------------


@dataclass
class _DataclassValue:
    """Simple dataclass used to exercise json_default."""

    value: int = 1


class _ModelDumpJson:
    """Value exposing a JSON-capable model_dump."""

    def model_dump(self, mode: str | None = None) -> dict[str, Any]:
        """Return the requested dump mode."""
        return {"mode": mode}


class _ModelDumpPlain:
    """Value whose model_dump rejects the JSON keyword."""

    def model_dump(self) -> dict[str, Any]:
        """Return a plain dump."""
        return {"mode": None}


class _RawDataValue:
    """Value exposing raw_data."""

    raw_data: dict[str, Any] = {"raw": 1}


class _DataValue:
    """Value exposing a data dict."""

    data: dict[str, Any] = {"data": 1}


class _NonDictData:
    """Value exposing a non-dict data attribute."""

    data: list[Any] = [1]


def test_json_default_serializes_supported_types() -> None:
    """json_default should serialize every supported CLI value shape."""
    assert (
        output_module.json_default(datetime(2026, 1, 1, tzinfo=timezone.utc))
        == "2026-01-01T00:00:00+00:00"
    )
    assert output_module.json_default(Path("/tmp/x")) == "/tmp/x"
    assert output_module.json_default(_DataclassValue()) == {"value": 1}
    assert isinstance(output_module.json_default(_DataclassValue), str)
    assert output_module.json_default(_ns(a=1)) == {"a": 1}
    assert output_module.json_default(_ModelDumpJson()) == {"mode": "json"}
    assert output_module.json_default(_ModelDumpPlain()) == {"mode": None}
    assert output_module.json_default(_RawDataValue()) == {"raw": 1}
    assert output_module.json_default(_DataValue()) == {"data": 1}
    assert isinstance(output_module.json_default(_NonDictData()), str)
    assert output_module.json_default({"b", "a"}) == ["a", "b"]
    assert output_module.json_default(b"hi") == "hi"
    assert isinstance(output_module.json_default(object()), str)


def test_to_json_string_sorts_when_indented() -> None:
    """Indented JSON output should sort keys for stable rendering."""
    text = output_module.to_json_string({"b": 1, "a": 2}, indent=2)
    assert text.index('"a"') < text.index('"b"')


def test_write_json_file_creates_parents() -> None:
    """write_json_file should create parents and persist JSON."""
    target = _tmp_dir("json-file") / "nested" / "out.json"
    output_module.write_json_file(target, {"b": 2, "a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}


def test_print_json_text_renders_pretty_json() -> None:
    """print_json_text should emit the payload through the console."""
    buffer = io.StringIO()
    output_module.print_json_text(Console(file=buffer), {"a": 1})
    assert '"a"' in buffer.getvalue()


def test_format_color_value_handles_dict_payload_variants() -> None:
    """format_color_value should cover each dict and scalar branch."""
    assert output_module.format_color_value({"daHexString": "#fff"}) == "#fff"
    assert output_module.format_color_value({"ckSymbolicColorName": "red"}) == "red"
    assert output_module.format_color_value({"foo": "bar"}) == '{"foo": "bar"}'
    assert (
        output_module.format_color_value({
            "daHexString": "#fff",
            "ckSymbolicColorName": "custom",
        })
        == "#fff"
    )
    assert (
        output_module.format_color_value({"ckSymbolicColorName": "custom"}) == "custom"
    )
    assert output_module.format_color_value(42) == "42"
    assert output_module.format_color_value("   ") == ""


# ---------------------------------------------------------------------------
# pyicloud/cli/context.py
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> Any:
    """Build a CLIState with a filesystem-safe session directory."""
    params: dict[str, Any] = {
        "username": None,
        "password": None,
        "china_mainland": None,
        "interactive": True,
        "accept_terms": False,
        "with_family": False,
        "refresh_interval": None,
        "session_dir": str(_tmp_dir("state")),
        "http_proxy": None,
        "https_proxy": None,
        "no_verify_ssl": False,
        "log_level": context_module.LogLevel.WARNING,
        "output_format": output_module.OutputFormat.TEXT,
    }
    params.update(overrides)
    return context_module.CLIState(**params)


def test_log_level_logging_levels() -> None:
    """Every LogLevel should map to its stdlib logging level."""
    assert context_module.LogLevel.ERROR.logging_level() == logging.ERROR
    assert context_module.LogLevel.INFO.logging_level() == logging.INFO
    assert context_module.LogLevel.DEBUG.logging_level() == logging.DEBUG
    assert context_module.LogLevel.WARNING.logging_level() == logging.WARNING


def test_delete_keyring_password_when_absent() -> None:
    """Deleting an absent password should still prune and return False."""
    state = _make_state()
    with patch.object(
        context_module.utils, "password_exists_in_keyring", return_value=False
    ):
        assert state.delete_keyring_password("user@example.com") is False


def test_has_keyring_password_without_candidate() -> None:
    """An empty resolved username should report no keyring password."""
    state = _make_state(username="   ")
    assert state.has_keyring_password() is False


def test_resolve_username_raises_without_accounts() -> None:
    """No local accounts should abort with a bootstrap hint."""
    state = _make_state()
    with pytest.raises(context_module.CLIAbort):
        state._resolve_username()


def test_password_for_login_uses_keyring_then_prompt() -> None:
    """Passwords should resolve from the keyring before prompting."""
    state = _make_state(password=None, interactive=True)
    with patch.object(
        context_module.utils, "get_password_from_keyring", return_value="keyring-pw"
    ):
        assert state._password_for_login("u") == ("keyring-pw", "keyring")
    with (
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value=None
        ),
        patch.object(context_module.utils, "get_password", return_value="prompted-pw"),
    ):
        assert state._password_for_login("u") == ("prompted-pw", "prompt")


def test_stored_password_prefers_explicit_password() -> None:
    """A command-supplied password should win over the keyring."""
    state = _make_state(password="explicit")
    assert state._stored_password_for_session("u") == "explicit"


def test_prompt_index_variants() -> None:
    """_prompt_index should short-circuit, parse, and reject selections."""
    state = _make_state(interactive=True)
    assert state._prompt_index("pick", 1) == 0
    with patch.object(context_module.typer, "prompt", return_value="1"):
        assert state._prompt_index("pick", 3) == 1
    with (
        patch.object(context_module.typer, "prompt", return_value="not-a-number"),
        pytest.raises(context_module.CLIAbort),
    ):
        state._prompt_index("pick", 3)
    with (
        patch.object(context_module.typer, "prompt", return_value="9"),
        pytest.raises(context_module.CLIAbort),
    ):
        state._prompt_index("pick", 3)


def test_handle_2fa_unknown_delivery_requests_code() -> None:
    """An unknown delivery method should still prompt and trust the session."""
    api = _ns(
        fido2_devices=[],
        request_2fa_code=MagicMock(return_value=True),
        two_factor_delivery_notice=None,
        two_factor_delivery_method="unknown",
        validate_2fa_code=MagicMock(return_value=True),
        is_trusted_session=False,
        trust_session=MagicMock(),
    )
    state = _make_state(interactive=True)
    with patch.object(context_module.typer, "prompt", return_value="123456"):
        state._handle_2fa(api)
    api.trust_session.assert_called_once_with()


def test_handle_2fa_requires_interaction_without_security_key() -> None:
    """Non-interactive 2FA without a security key should abort."""
    api = _ns(fido2_devices=[], is_trusted_session=True)
    state = _make_state(interactive=False)
    with pytest.raises(context_module.CLIAbort):
        state._handle_2fa(api)


def test_handle_2fa_without_trusted_number_aborts() -> None:
    """A missing trusted number should surface a CLIAbort."""
    api = _ns(
        fido2_devices=[],
        request_2fa_code=MagicMock(
            side_effect=PyiCloudNoTrustedNumberAvailable("no number")
        ),
        is_trusted_session=True,
    )
    state = _make_state(interactive=True)
    with pytest.raises(context_module.CLIAbort):
        state._handle_2fa(api)


def test_handle_2sa_without_devices_aborts() -> None:
    """2SA without trusted devices should abort."""
    api = _ns(trusted_devices=[])
    state = _make_state()
    with pytest.raises(context_module.CLIAbort):
        state._handle_2sa(api)


def test_handle_2sa_requires_interaction() -> None:
    """2SA should abort when prompts are disabled."""
    api = _ns(trusted_devices=[{"phoneNumber": "+1"}])
    state = _make_state(interactive=False)
    with pytest.raises(context_module.CLIAbort):
        state._handle_2sa(api)


def test_handle_2sa_send_failure_aborts() -> None:
    """A failed 2SA code send should abort."""
    api = _ns(
        trusted_devices=[{"phoneNumber": "+1"}],
        send_verification_code=MagicMock(return_value=False),
    )
    state = _make_state(interactive=True)
    with pytest.raises(context_module.CLIAbort):
        state._handle_2sa(api)


def test_handle_2sa_verify_failure_aborts() -> None:
    """A failed 2SA code verification should abort."""
    api = _ns(
        trusted_devices=[{}],
        send_verification_code=MagicMock(return_value=True),
        validate_verification_code=MagicMock(return_value=False),
    )
    state = _make_state(interactive=True)
    with (
        patch.object(context_module.typer, "prompt", return_value="123"),
        pytest.raises(context_module.CLIAbort),
    ):
        state._handle_2sa(api)


def test_get_login_api_returns_cached_api() -> None:
    """A cached login API should be returned immediately."""
    state = _make_state()
    cached = MagicMock()
    state._api = cached
    assert state.get_login_api() is cached


def test_get_login_api_failed_login_clears_keyring() -> None:
    """A keyring password rejected on login should be removed."""
    state = _make_state(username="user@example.com", password=None, interactive=False)
    with (
        patch.object(
            context_module.utils, "get_password_from_keyring", return_value="stale"
        ),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=True
        ),
        patch.object(
            context_module.utils, "delete_password_in_keyring"
        ) as delete_password,
        patch.object(
            context_module,
            "PyiCloudService",
            side_effect=PyiCloudFailedLoginException("bad"),
        ),
        pytest.raises(context_module.CLIAbort),
    ):
        state.get_login_api()
    delete_password.assert_called_once_with("user@example.com")


def test_get_login_api_stores_confirmed_password() -> None:
    """An interactive login should store the password when confirmed."""
    session_dir = _tmp_dir("login-store")
    fake_api = _ns(
        requires_2fa=False,
        requires_2sa=False,
        account_name="user@example.com",
        is_china_mainland=False,
        session=_ns(
            session_path=str(session_dir / "u.session"),
            cookiejar_path=str(session_dir / "u.cookiejar"),
        ),
    )
    state = _make_state(
        username="user@example.com",
        password="secret",
        session_dir=str(session_dir),
        interactive=True,
    )
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module.utils, "password_exists_in_keyring", return_value=False
        ),
        patch.object(
            context_module.utils, "store_password_in_keyring"
        ) as store_password,
        patch.object(context_module, "confirm", return_value=True),
    ):
        api = state.get_login_api()
    assert api is fake_api
    store_password.assert_called_once_with("user@example.com", "secret")


def test_get_api_returns_cached_api() -> None:
    """A cached service API should be returned immediately."""
    state = _make_state()
    cached = MagicMock()
    state._api = cached
    assert state.get_api() is cached


def test_get_api_explicit_username_not_authenticated() -> None:
    """An explicit login without an authenticated session should abort."""
    probe = _ns(get_auth_status=MagicMock(return_value={"authenticated": False}))
    state = _make_state(username="user@example.com")
    with (
        patch.object(state, "build_probe_api", return_value=probe),
        pytest.raises(context_module.CLIAbort),
    ):
        state.get_api()


def test_get_api_explicit_hydration_failure_checks_session_status() -> None:
    """A failed probe hydration should fall back to the session auth status."""
    probe = _ns(get_auth_status=MagicMock(return_value={"authenticated": True}))
    session_api = _ns(get_auth_status=MagicMock(return_value={"authenticated": False}))
    state = _make_state(username="user@example.com")
    with (
        patch.object(state, "build_probe_api", return_value=probe),
        patch.object(state, "build_session_api", return_value=session_api),
        patch.object(state, "_hydrate_api_from_probe", return_value=False),
        pytest.raises(context_module.CLIAbort),
    ):
        state.get_api()


def test_get_api_explicit_hydration_success_skips_session_status() -> None:
    """A successful probe hydration should skip the session auth check."""
    session_dir = _tmp_dir("hydrate-success")
    probe = _ns(get_auth_status=MagicMock(return_value={"authenticated": True}))
    session_api = _ns(
        account_name="user@example.com",
        is_china_mainland=False,
        session=_ns(
            session_path=str(session_dir / "u.session"),
            cookiejar_path=str(session_dir / "u.cookiejar"),
        ),
        get_auth_status=MagicMock(
            side_effect=AssertionError("hydration should be sufficient")
        ),
    )
    state = _make_state(username="user@example.com", session_dir=str(session_dir))
    with (
        patch.object(state, "build_probe_api", return_value=probe),
        patch.object(state, "build_session_api", return_value=session_api),
        patch.object(state, "_hydrate_api_from_probe", return_value=True),
    ):
        api = state.get_api()
    assert api is session_api


def test_get_api_implicit_hydration_failure_checks_session_status() -> None:
    """An implicit session that cannot hydrate should abort when unauthenticated."""
    probe = _ns(account_name="user@example.com")
    session_api = _ns(get_auth_status=MagicMock(return_value={"authenticated": False}))
    state = _make_state(username=None)
    with (
        patch.object(
            state,
            "active_session_probes",
            return_value=[(probe, {"authenticated": True})],
        ),
        patch.object(state, "build_session_api", return_value=session_api),
        patch.object(state, "_hydrate_api_from_probe", return_value=False),
        pytest.raises(context_module.CLIAbort),
    ):
        state.get_api()


def test_hydrate_api_from_probe_rejects_missing_probe() -> None:
    """A missing probe should yield a False hydration result."""
    api = _ns()
    assert context_module.CLIState._hydrate_api_from_probe(api, None) is False


def test_hydrate_api_from_probe_rejects_empty_data() -> None:
    """An empty probe payload should yield a False hydration result."""
    api = _ns()
    assert context_module.CLIState._hydrate_api_from_probe(api, _ns(data={})) is False


def test_hydrate_api_from_probe_copies_state() -> None:
    """Hydration should copy dsid params and present webservices."""
    api = _ns(data={}, params={}, webservices=None)
    probe = _ns(data={"dsInfo": {"dsid": "42"}, "webservices": {"a": 1}})
    assert context_module.CLIState._hydrate_api_from_probe(api, probe) is True
    assert api.data == {"dsInfo": {"dsid": "42"}, "webservices": {"a": 1}}
    assert api.params["dsid"] == "42"
    assert api.webservices == {"a": 1}


def test_hydrate_api_from_probe_without_webservices() -> None:
    """Hydration should succeed even without a webservices payload."""
    api = _ns(data={}, params={})
    probe = _ns(data={"dsInfo": {}})
    assert context_module.CLIState._hydrate_api_from_probe(api, probe) is True


def test_get_probe_api_returns_cached_api() -> None:
    """A cached probe API should be returned immediately."""
    state = _make_state()
    cached = MagicMock()
    state._probe_api = cached
    assert state.get_probe_api() is cached


def test_get_state_returns_existing_state() -> None:
    """get_state should return an already-resolved CLIState untouched."""
    state = _make_state()
    root_ctx = _ns(obj=state)
    ctx = cast(Any, _ns(find_root=lambda: root_ctx))
    assert context_module.get_state(ctx) is state


def test_service_call_without_account_name() -> None:
    """A re-auth failure without an account should use the generic hint."""
    error = PyiCloudAuthRequiredException("u", cast(Any, None))

    def _raise() -> Any:
        raise error

    with pytest.raises(context_module.CLIAbort) as exc:
        context_module.service_call("Drive", _raise)
    assert "icloud auth login" in str(exc.value)


def test_parse_datetime_variants() -> None:
    """parse_datetime should normalize Z suffixes and naive values."""
    assert context_module.parse_datetime(None) is None
    aware = context_module.parse_datetime("2026-01-01T00:00:00Z")
    assert aware is not None
    assert aware.tzinfo is not None
    naive = context_module.parse_datetime("2026-01-01T00:00:00")
    assert naive is not None
    assert naive.tzinfo == timezone.utc
    with pytest.raises(typer.BadParameter):
        context_module.parse_datetime("not-a-date")


def test_resolve_device_matches_id() -> None:
    """resolve_device should match a device by its id case-insensitively."""
    device = _ns(id="Device-1", name="Phone", deviceDisplayName="iPhone")
    other = _ns(id="device-2", name="Other", deviceDisplayName="iPad")
    api = _ns(devices=[other, device], account_name="u")
    assert context_module.resolve_device(api, " device-1 ") is device


def test_resolve_device_matches_names_and_dedupes() -> None:
    """resolve_device should match display names and drop duplicate ids."""
    device = _ns(id="d1", name="My Phone", deviceDisplayName="iPhone")
    duplicate = _ns(id="d1", name="My Phone", deviceDisplayName="iPhone")
    other = _ns(id="d2", name="Other", deviceDisplayName="MacBook")
    api = _ns(devices=[other, device, duplicate], account_name="u")
    assert context_module.resolve_device(api, "my phone") is device


def test_resolve_device_without_match_aborts() -> None:
    """resolve_device should abort when nothing matches."""
    api = _ns(devices=[], account_name="u")
    with pytest.raises(context_module.CLIAbort):
        context_module.resolve_device(api, "missing")


def test_resolve_device_require_unique_aborts_on_multiple() -> None:
    """require_unique should abort when several devices match."""
    first = _ns(id="a", name="Phone", deviceDisplayName="iPhone")
    second = _ns(id="b", name="Phone", deviceDisplayName="iPhone")
    api = _ns(devices=[first, second], account_name="u")
    with pytest.raises(context_module.CLIAbort) as exc:
        context_module.resolve_device(api, "phone", require_unique=True)
    assert "Multiple devices matched" in str(exc.value)


def test_resolve_drive_node_paths() -> None:
    """resolve_drive_node should resolve root, nested, and trash nodes."""
    drive = _ns(root={"a": {"b": "target"}}, trash="trash-node")
    assert context_module.resolve_drive_node(drive, "/") == drive.root
    assert context_module.resolve_drive_node(drive, "") == drive.root
    assert cast(Any, context_module.resolve_drive_node(drive, "a/b")) == "target"
    assert context_module.resolve_drive_node(drive, "/", trash=True) == "trash-node"


def test_write_to_file_streams_iter_content() -> None:
    """_write_to_file should prefer iter_content when available."""
    out = io.BytesIO()
    response = _ns(iter_content=lambda *, chunk_size: iter([b"a", b"", b"b"]))
    context_module._write_to_file(response, out)
    assert out.getvalue() == b"ab"


def test_write_to_file_streams_raw_read() -> None:
    """_write_to_file should fall back to raw.read streaming."""
    out = io.BytesIO()
    chunks = iter([b"x", b"y", b""])
    response = _ns(raw=_ns(read=lambda size: next(chunks)))
    context_module._write_to_file(response, out)
    assert out.getvalue() == b"xy"


def test_write_to_file_without_stream_returns() -> None:
    """_write_to_file should no-op quietly for non-streamable responses."""
    out = io.BytesIO()
    context_module._write_to_file(object(), out)
    assert out.getvalue() == b""


def test_write_response_to_path_rejects_unstreamable() -> None:
    """write_response_to_path should abort on unstreamable responses."""
    response = object()
    target = _tmp_dir("download") / "out.bin"
    with pytest.raises(context_module.CLIAbort):
        context_module.write_response_to_path(response, target)


def test_write_response_to_path_streams_to_disk() -> None:
    """write_response_to_path should stream a response into a nested path."""
    target = _tmp_dir("download-stream") / "nested" / "out.bin"
    response = _ns(iter_content=lambda *, chunk_size: iter([b"data"]))
    context_module.write_response_to_path(response, target)
    assert target.read_bytes() == b"data"


# ---------------------------------------------------------------------------
# pyicloud/cli/normalize.py
# ---------------------------------------------------------------------------


def test_normalize_simple_payloads() -> None:
    """The stateless normalizers should map their inputs verbatim."""
    assert normalize.normalize_account_device({
        "id": "i",
        "name": "n",
        "modelDisplayName": "m",
        "deviceClass": "c",
    }) == {"id": "i", "name": "n", "model_display_name": "m", "device_class": "c"}
    member = _ns(
        full_name="Jane",
        apple_id="jane@example.com",
        dsid="1",
        age_classification="adult",
        has_parental_privileges=True,
    )
    assert normalize.normalize_family_member(member)["full_name"] == "Jane"
    storage = _ns(
        usage=_ns(
            used_storage_in_bytes=1,
            available_storage_in_bytes=2,
            total_storage_in_bytes=3,
            used_storage_in_percent=4.0,
        ),
        usages_by_media={"photos": _ns(label="Photos", color="#fff", usage_in_bytes=1)},
    )
    assert normalize.normalize_storage(storage)["usages_by_media"]["photos"] == {
        "label": "Photos",
        "color": "#fff",
        "usage_in_bytes": 1,
    }
    device = _ns(
        id="d",
        name="Phone",
        deviceDisplayName="iPhone",
        deviceClass="iPhone",
        deviceModel="16,1",
        batteryLevel=0.5,
        batteryStatus="Charging",
        location={"lat": 1},
        data={"raw": True},
    )
    assert normalize.normalize_device_details(device, locate=True)["raw_data"] == {
        "raw": True
    }
    assert normalize.normalize_calendar({
        "guid": "g",
        "title": "t",
        "color": "#fff",
        "shareType": "owner",
    }) == {"guid": "g", "title": "t", "color": "#fff", "share_type": "owner"}
    assert normalize.normalize_event({
        "guid": "g",
        "pGuid": "c",
        "title": "t",
        "startDate": "s",
        "endDate": "e",
    }) == {"guid": "g", "calendar_guid": "c", "title": "t", "start": "s", "end": "e"}
    assert normalize.normalize_contact({
        "firstName": "John",
        "lastName": "Doe",
        "phones": [{"field": "+1"}],
        "emails": [{"field": "j@example.com"}],
    }) == {
        "first_name": "John",
        "last_name": "Doe",
        "phones": ["+1"],
        "emails": ["j@example.com"],
    }
    me = _ns(first_name="John", last_name="Doe", photo="p", raw_data={"c": 1})
    assert normalize.normalize_me(me)["first_name"] == "John"
    node = _ns(name="f", type="file", size=1, date_modified=datetime(2026, 1, 1))
    assert normalize.normalize_drive_node(node)["name"] == "f"
    assert normalize.normalize_alias({
        "hme": "a@x.com",
        "label": "l",
        "anonymousId": "id",
    }) == {"email": "a@x.com", "label": "l", "anonymous_id": "id"}


@dataclass
class _NoteRow:
    """Minimal note row with a deleted flag."""

    is_deleted: bool = False


class _RecentNotes:
    """Notes service keyed by the requested recent limit."""

    def __init__(self, pages: dict[int, list[Any]]) -> None:
        self._pages = pages

    def recents(self, *, limit: int) -> list[Any]:
        """Return the configured page for the requested limit."""
        return list(self._pages.get(limit, []))


def test_select_recent_notes_short_circuits() -> None:
    """A non-positive limit should return no notes."""
    assert normalize.select_recent_notes(object(), limit=0, include_deleted=False) == []


def test_select_recent_notes_include_deleted() -> None:
    """include_deleted should bypass filtering."""
    service = _ns(recents=lambda *, limit: [_NoteRow(), _NoteRow()])
    assert (
        len(normalize.select_recent_notes(service, limit=5, include_deleted=True)) == 2
    )


def test_select_recent_notes_widens_probe_limit() -> None:
    """The probe loop should widen the window until enough live notes appear."""
    service = _RecentNotes({
        2: [_NoteRow(True), _NoteRow(True)],
        4: [_NoteRow(True), _NoteRow(True), _NoteRow(), _NoteRow()],
    })
    result = normalize.select_recent_notes(service, limit=2, include_deleted=False)
    assert len(result) == 2


def test_select_recent_notes_returns_when_source_exhausted() -> None:
    """The probe loop should stop when the source returns fewer rows."""
    service = _RecentNotes({5: [_NoteRow()]})
    assert (
        len(normalize.select_recent_notes(service, limit=5, include_deleted=False)) == 1
    )


@dataclass
class _TitleNote:
    """Note row with an id, title, and optional modified time."""

    id: str
    title: str | None
    modified_at: Any = None


class _SearchNotes:
    """Notes service exposing recents and iter_all for search tests."""

    def __init__(self, recent: list[Any], all_rows: list[Any]) -> None:
        self._recent = recent
        self._all = all_rows

    def recents(self, *, limit: int) -> list[Any]:
        """Return the recent page truncated to the limit."""
        return list(self._recent[:limit])

    def iter_all(self) -> Iterator[Any]:
        """Iterate over every note row."""
        return iter(self._all)


def test_search_notes_by_title_short_circuits() -> None:
    """Search should return nothing for empty terms or a non-positive limit."""
    service = _SearchNotes([], [])
    assert not normalize.search_notes_by_title(service, limit=0)
    assert not normalize.search_notes_by_title(
        service, title="", title_contains="", limit=5
    )


def test_search_notes_by_title_recents_dedupe_and_limit() -> None:
    """Recents search should dedupe ids and stop once the limit is met."""
    service = _SearchNotes(
        [
            _TitleNote("1", "Alpha"),
            _TitleNote("1", "Alpha"),
            _TitleNote("2", "Alphabet"),
            _TitleNote("3", "Alpine"),
        ],
        [],
    )
    result = normalize.search_notes_by_title(
        service, title=None, title_contains="alp", limit=2
    )
    assert [note.id for note in result] == ["1", "2"]


def test_search_notes_by_title_exact_match() -> None:
    """An exact title should match through the exact branch."""
    service = _SearchNotes([_TitleNote("x", "Exact Title")], [])
    result = normalize.search_notes_by_title(service, title="Exact Title", limit=1)
    assert [note.id for note in result] == ["x"]


def test_search_notes_by_title_ignores_missing_titles() -> None:
    """Rows without titles should never match."""
    service = _SearchNotes([_TitleNote("n", None)], [_TitleNote("m", None)])
    assert not normalize.search_notes_by_title(service, title="anything", limit=1)


def test_search_notes_by_title_falls_back_to_full_scan() -> None:
    """A short recents page should trigger the full-scan fallback."""
    service = _SearchNotes([], [_TitleNote("a", "Alpha")])
    result = normalize.search_notes_by_title(
        service, title=None, title_contains="alpha", limit=1
    )
    assert [note.id for note in result] == ["a"]


def test_search_notes_by_title_fallback_skips_non_matches() -> None:
    """The fallback scan should skip non-matches while collecting matches."""
    service = _SearchNotes(
        [],
        [
            _TitleNote("a", "Alpha"),
            _TitleNote("z", "Zed"),
            _TitleNote("b", "Alpha Two"),
        ],
    )
    result = normalize.search_notes_by_title(
        service, title=None, title_contains="alpha", limit=2
    )
    assert [note.id for note in result] == ["a", "b"]


def test_search_notes_by_title_sorts_mixed_modified_at() -> None:
    """Sorting should treat missing and naive timestamps safely."""
    service = _SearchNotes(
        [
            _TitleNote("naive", "Alpha", datetime(2026, 1, 1)),
            _TitleNote("none", "Alpha", None),
            _TitleNote("aware", "Alpha", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ],
        [],
    )
    result = normalize.search_notes_by_title(
        service, title=None, title_contains="alpha", limit=3
    )
    assert [note.id for note in result] == ["aware", "naive", "none"]


# ---------------------------------------------------------------------------
# pyicloud/cli/account_index.py
# ---------------------------------------------------------------------------


def test_load_accounts_handles_invalid_payloads() -> None:
    """Malformed index payloads should degrade to an empty mapping."""
    root = _tmp_dir("index-load")
    path = root / "accounts.json"
    path.write_text("{not json", encoding="utf-8")
    assert account_index.load_accounts(root) == {}
    path.write_text('{"accounts": []}', encoding="utf-8")
    assert account_index.load_accounts(root) == {}
    path.write_text('{"accounts": {"user": "nope"}}', encoding="utf-8")
    assert account_index.load_accounts(root) == {}
    path.write_text(
        '{"accounts": {"user": {"session_path": 1, "cookiejar_path": 2}}}',
        encoding="utf-8",
    )
    assert account_index.load_accounts(root) == {}


def test_load_accounts_handles_oserror() -> None:
    """An unreadable index should degrade to an empty mapping."""
    with patch.object(Path, "read_text", side_effect=OSError("boom")):
        assert account_index._load_accounts_from_path(Path("/nonexistent")) == {}


def test_locked_index_ignores_missing_lock() -> None:
    """Cleanup should tolerate a missing lock file."""
    root = _tmp_dir("index-lock-missing")
    with patch.object(Path, "unlink", side_effect=FileNotFoundError):
        assert account_index.prune_accounts(root, lambda candidate: False) == []


def test_locked_index_ignores_unlink_oserror() -> None:
    """Cleanup should tolerate an unlink OSError."""
    root = _tmp_dir("index-lock-oserror")
    with patch.object(Path, "unlink", side_effect=OSError("boom")):
        assert account_index.prune_accounts(root, lambda candidate: False) == []


def test_save_accounts_cleans_temp_on_failure() -> None:
    """A failed atomic replace should surface the error after cleanup."""
    root = _tmp_dir("index-save-fail")
    accounts: dict[str, Any] = {
        "u": {
            "username": "u",
            "last_used_at": "",
            "session_path": "s",
            "cookiejar_path": "c",
        }
    }
    with (
        patch.object(account_index.os, "replace", side_effect=OSError("boom")),
        pytest.raises(OSError),
    ):
        account_index._save_accounts(root, accounts)


def test_save_accounts_raises_before_temp_created() -> None:
    """A failure before the temp file exists should still propagate."""
    root = _tmp_dir("index-save-early-fail")
    accounts: dict[str, Any] = {
        "u": {
            "username": "u",
            "last_used_at": "",
            "session_path": "s",
            "cookiejar_path": "c",
        }
    }
    with (
        patch.object(
            account_index.tempfile,
            "NamedTemporaryFile",
            side_effect=OSError("boom"),
        ),
        pytest.raises(OSError),
    ):
        account_index._save_accounts(root, accounts)


def test_remember_account_preserves_china_mainland() -> None:
    """A follow-up upsert should retain stored china_mainland metadata."""
    root = _tmp_dir("index-china")
    account_index.remember_account(
        root,
        username="u",
        session_path="s",
        cookiejar_path="c",
        china_mainland=True,
    )
    entry = account_index.remember_account(
        root,
        username="u",
        session_path="s",
        cookiejar_path="c",
        china_mainland=None,
    )
    assert entry["china_mainland"] is True


# ---------------------------------------------------------------------------
# pyicloud/cmdline.py
# ---------------------------------------------------------------------------


def test_cmdline_main_guard_runs_main() -> None:
    """Executing the module as __main__ should exit with main()'s code."""
    cmdline = importlib.import_module("pyicloud.cmdline")
    loader = cast(Any, cmdline.__loader__)
    source = loader.get_source("pyicloud.cmdline")
    filename = str(cmdline.__file__)
    fake_app = ModuleType("pyicloud.cli.app")
    fake_app.main = lambda: 5  # type: ignore[attr-defined]
    namespace: dict[str, Any] = {"__name__": "__main__"}
    code = compile(source, filename, "exec")
    with (
        patch.dict(sys.modules, {"pyicloud.cli.app": fake_app}),
        pytest.raises(SystemExit) as exc,
    ):
        exec(code, namespace)  # pylint: disable=exec-used
    assert exc.value.code == 5


# ---------------------------------------------------------------------------
# pyicloud/cli/commands/auth.py
# ---------------------------------------------------------------------------


class _FakeAPI:
    """Minimal authenticated API used by the auth command tests."""

    def __init__(self, username: str, session_dir: Path) -> None:
        stub = "".join(character for character in username if character.isalnum())
        self.account_name = username
        self.is_china_mainland = False
        self.session = SimpleNamespace(
            session_path=str(session_dir / f"{stub}.session"),
            cookiejar_path=str(session_dir / f"{stub}.cookiejar"),
        )
        self.get_auth_status = MagicMock(
            return_value={
                "authenticated": True,
                "trusted_session": True,
                "requires_2fa": False,
                "requires_2sa": False,
            }
        )
        self.logout = MagicMock(
            return_value={
                "remote_logout_confirmed": True,
                "local_session_cleared": True,
            }
        )


def _remember_account(
    session_dir: Path,
    username: str,
    *,
    has_session_file: bool = False,
    keyring: set[str] | None = None,
) -> _FakeAPI:
    """Seed the local account index with one account."""
    fake_api = _FakeAPI(username, session_dir)
    if has_session_file:
        Path(fake_api.session.session_path).write_text("", encoding="utf-8")
    account_index.remember_account(
        session_dir,
        username=username,
        session_path=fake_api.session.session_path,
        cookiejar_path=fake_api.session.cookiejar_path,
        china_mainland=None,
        keyring_has=lambda candidate: candidate in (keyring or set()),
    )
    return fake_api


@contextmanager
def _cli_patches(
    fake_api: Any, *, keyring: set[str] | None = None
) -> Generator[None, None, None]:
    """Patch the CLI context for an auth command invocation."""
    with (
        patch.object(context_module, "PyiCloudService", return_value=fake_api),
        patch.object(
            context_module,
            "configurable_ssl_verification",
            return_value=nullcontext(),
        ),
        patch.object(context_module, "confirm", return_value=False),
        patch.object(
            context_module.utils,
            "password_exists_in_keyring",
            side_effect=lambda candidate: candidate in (keyring or set()),
        ),
        patch.object(
            context_module.utils,
            "get_password_from_keyring",
            side_effect=lambda candidate: (
                "stored" if candidate in (keyring or set()) else None
            ),
        ),
    ):
        yield


def test_auth_keyring_group_without_command_shows_help() -> None:
    """The keyring subgroup should print help when invoked bare."""
    result = CliRunner().invoke(cli_app, ["auth", "keyring"])
    assert result.exit_code == 0
    assert "Usage:" in click.unstyle(result.output)


def test_auth_status_json_without_sessions() -> None:
    """JSON auth status without sessions should report an empty result."""
    session_dir = _tmp_dir("status-none")
    with _cli_patches(None):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "status", "--session-dir", str(session_dir), "--format", "json"],
        )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"authenticated": False, "accounts": []}


def test_auth_status_json_single_session() -> None:
    """JSON auth status with one session should emit that payload."""
    session_dir = _tmp_dir("status-single")
    fake_api = _remember_account(session_dir, "solo@example.com", has_session_file=True)
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "status", "--session-dir", str(session_dir), "--format", "json"],
        )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["authenticated"] is True
    assert payload["account_name"] == "solo@example.com"


def test_auth_status_json_multiple_sessions() -> None:
    """JSON auth status with several sessions should nest them."""
    session_dir = _tmp_dir("status-multi-json")
    _remember_account(session_dir, "alpha@example.com", has_session_file=True)
    _remember_account(session_dir, "beta@example.com", has_session_file=True)
    fake_api = _FakeAPI("alpha@example.com", session_dir)
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "status", "--session-dir", str(session_dir), "--format", "json"],
        )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["authenticated"] is True
    assert len(payload["accounts"]) == 2


def test_auth_status_text_multiple_sessions() -> None:
    """Text auth status with several sessions should render a table."""
    session_dir = _tmp_dir("status-multi-text")
    _remember_account(session_dir, "alpha@example.com", has_session_file=True)
    _remember_account(session_dir, "beta@example.com", has_session_file=True)
    fake_api = _FakeAPI("alpha@example.com", session_dir)
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "status", "--session-dir", str(session_dir)],
        )
    assert result.exit_code == 0
    assert "Active iCloud Sessions" in result.stdout


def test_auth_logout_json_without_sessions() -> None:
    """JSON logout without sessions should report an empty result."""
    session_dir = _tmp_dir("logout-none-json")
    with _cli_patches(None):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "logout", "--session-dir", str(session_dir), "--format", "json"],
        )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"authenticated": False, "accounts": []}


def test_auth_logout_text_without_sessions() -> None:
    """Text logout without sessions should explain how to log in."""
    session_dir = _tmp_dir("logout-none-text")
    with _cli_patches(None):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "logout", "--session-dir", str(session_dir)],
        )
    assert result.exit_code == 0
    assert "You are not logged into any iCloud accounts." in result.stdout


def test_auth_logout_multiple_sessions_aborts() -> None:
    """Implicit logout should abort when several sessions are active."""
    session_dir = _tmp_dir("logout-multi")
    _remember_account(session_dir, "alpha@example.com", has_session_file=True)
    _remember_account(session_dir, "beta@example.com", has_session_file=True)
    fake_api = _FakeAPI("alpha@example.com", session_dir)
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            ["auth", "logout", "--session-dir", str(session_dir)],
        )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "Multiple logged-in iCloud accounts" in result.exception.args[0]


def test_auth_logout_explicit_username_text() -> None:
    """Explicit logout should probe the account and clear the session."""
    session_dir = _tmp_dir("logout-explicit")
    fake_api = _FakeAPI("solo@example.com", session_dir)
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            [
                "auth",
                "logout",
                "--username",
                "solo@example.com",
                "--session-dir",
                str(session_dir),
            ],
        )
    assert result.exit_code == 0
    assert "Logged out and cleared local session." in result.stdout


def test_auth_logout_with_keyring_removal_text() -> None:
    """Confirmed logout with keyring removal should print the combined message."""
    session_dir = _tmp_dir("logout-keyring")
    fake_api = _FakeAPI("solo@example.com", session_dir)
    with (
        _cli_patches(fake_api, keyring={"solo@example.com"}),
        patch.object(context_module.utils, "delete_password_in_keyring"),
    ):
        result = CliRunner().invoke(
            cli_app,
            [
                "auth",
                "logout",
                "--username",
                "solo@example.com",
                "--remove-keyring",
                "--session-dir",
                str(session_dir),
            ],
        )
    assert result.exit_code == 0
    assert "removed stored password" in result.stdout


def test_auth_logout_unconfirmed_with_keyring_removal_text() -> None:
    """Unconfirmed logout with keyring removal should print the partial message."""
    session_dir = _tmp_dir("logout-keyring-unconfirmed")
    fake_api = _FakeAPI("solo@example.com", session_dir)
    fake_api.logout.return_value = {
        "remote_logout_confirmed": False,
        "local_session_cleared": True,
    }
    with (
        _cli_patches(fake_api, keyring={"solo@example.com"}),
        patch.object(context_module.utils, "delete_password_in_keyring"),
    ):
        result = CliRunner().invoke(
            cli_app,
            [
                "auth",
                "logout",
                "--username",
                "solo@example.com",
                "--remove-keyring",
                "--session-dir",
                str(session_dir),
            ],
        )
    assert result.exit_code == 0
    assert "remote logout was not confirmed" in result.stdout


def test_auth_logout_local_failure_aborts() -> None:
    """A local session clearing failure should surface a CLIAbort."""
    session_dir = _tmp_dir("logout-oserror")
    fake_api = _FakeAPI("solo@example.com", session_dir)
    fake_api.logout.side_effect = OSError("boom")
    with _cli_patches(fake_api):
        result = CliRunner().invoke(
            cli_app,
            [
                "auth",
                "logout",
                "--username",
                "solo@example.com",
                "--session-dir",
                str(session_dir),
            ],
        )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "Failed to clear local session state." in result.exception.args[0]


def test_auth_keyring_delete_json_output() -> None:
    """Keyring delete should emit a JSON payload when requested."""
    session_dir = _tmp_dir("keyring-delete-json")
    _remember_account(session_dir, "user@example.com")
    with (
        _cli_patches(None, keyring={"user@example.com"}),
        patch.object(context_module.utils, "delete_password_in_keyring"),
    ):
        result = CliRunner().invoke(
            cli_app,
            [
                "auth",
                "keyring",
                "delete",
                "--username",
                "user@example.com",
                "--session-dir",
                str(session_dir),
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "account_name": "user@example.com",
        "stored_password_removed": True,
    }
