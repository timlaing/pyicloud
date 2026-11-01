"""Auth request ordering and shared-state ownership before private helper extraction."""

# pylint: disable=protected-access

from typing import Any
from unittest.mock import patch

import pytest

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudConnectionException


@pytest.mark.parametrize("connected", [True, False])
def test_trust_uses_current_auth_state_and_preserves_request_order(
    pyicloud_service_working: PyiCloudService, connected: bool
) -> None:
    """Trust precedes token validation and a transport failure never validates."""
    api = pyicloud_service_working
    api.session.data.update({
        "scnt": "synthetic-count",
        "session_id": "synthetic-session",
        "auth_attributes": "synthetic-attributes",
    })
    original = dict(api.session.data)
    events: list[str] = []

    def trust(_url: str, *, headers: dict[str, Any]) -> None:
        events.append("trust")
        assert headers["scnt"] == original["scnt"]
        assert headers["X-Apple-ID-Session-Id"] == original["session_id"]
        assert headers["X-Apple-Auth-Attributes"] == original["auth_attributes"]
        assert headers["X-Apple-Frame-Id"] == api._client_id
        if not connected:
            raise PyiCloudConnectionException("offline")

    with (
        patch.object(api.session, "get", side_effect=trust),
        patch.object(
            api,
            "_authenticate_with_token",
            side_effect=lambda: events.append("validate"),
        ),
    ):
        if connected:
            assert api.trust_session() is True
        else:
            with pytest.raises(PyiCloudConnectionException, match="offline"):
                api.trust_session()
    assert events == (["trust", "validate"] if connected else ["trust"])
    assert api.session.data == original


def test_auth_header_overrides_do_not_mutate_state(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Overrides affect one request only, not session tokens or header templates."""
    api = pyicloud_service_working
    api.session.data["scnt"] = "original"
    first = api._get_auth_headers({"scnt": "override", "Synthetic": "extra"})
    second = api._get_auth_headers()
    assert first["scnt"] == "override"
    assert second["scnt"] == "original"
    assert "Synthetic" not in second
