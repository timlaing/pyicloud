"""
Test the PyiCloudService and PyiCloudSession classes."""

# pylint: disable=protected-access

import json
import secrets
import tempfile
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests
from fido2.hid import CtapHidDevice
from requests import HTTPError, Response

from pyicloud import PyiCloudService
from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.exceptions import (
    PyiCloud2SARequiredException,
    PyiCloudAcceptTermsException,
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailable,
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)
from pyicloud.services.calendar import CalendarService
from pyicloud.services.contacts import ContactsService
from pyicloud.services.hidemyemail import HideMyEmailService
from pyicloud.services.notes import NotesService
from pyicloud.services.photos import PhotosService
from pyicloud.services.reminders import RemindersService
from pyicloud.services.ubiquity import UbiquityService
from pyicloud.session import PyiCloudSession
from pyicloud.utils import b64_encode
from tests.const import LOGIN_2FA


def test_authenticate_with_force_refresh(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with force_refresh=True."""
    with (
        patch("pyicloud.base.PyiCloudSession.post") as mock_post_response,
        patch("pyicloud.base.PyiCloudService._validate_token") as validate_token,
    ):
        pyicloud_service.session._data = {"session_token": "valid_token"}
        mock_post_response.json.return_value = {
            "apps": {"test_service": {"canLaunchWithOneFactor": True}},
            "status": "success",
        }
        pyicloud_service.data = {
            "apps": {"test_service": {"canLaunchWithOneFactor": True}}
        }
        validate_token = MagicMock(
            return_value={
                "status": "success",
                "dsInfo": {"hsaVersion": 1},
                "webservices": "TestWebservices",
            }
        )
        pyicloud_service._validate_token = validate_token
        pyicloud_service.authenticate(force_refresh=True, service="test_service")
        mock_post_response.assert_called_once()
        validate_token.assert_called_once()


def test_constructor_accepts_positional_refresh_interval() -> None:
    """refresh_interval stays positional-compatible with upstream."""
    with (
        patch("pyicloud.PyiCloudService.authenticate") as mock_authenticate,
        patch("pyicloud.PyiCloudService._setup_cookie_directory") as mock_setup_dir,
        patch("builtins.open", new_callable=mock_open),
    ):
        mock_authenticate.return_value = None
        mock_setup_dir.return_value = "/tmp/pyicloud/cookies"

        service = PyiCloudService(
            "test@example.com",
            secrets.token_hex(32),
            None,
            True,
            None,
            True,
            False,
            False,
            30.0,
        )

        assert service._refresh_interval == 30.0


def test_constructor_skips_authentication_when_requested() -> None:
    """authenticate=False should not trigger login during construction."""
    with (
        patch("pyicloud.PyiCloudService.authenticate") as mock_authenticate,
        patch("pyicloud.base.get_password_from_keyring") as get_from_keyring,
        patch("pyicloud.PyiCloudService._setup_cookie_directory") as mock_setup_dir,
        patch("builtins.open", new_callable=mock_open),
    ):
        mock_setup_dir.return_value = "/tmp/pyicloud/cookies"

        PyiCloudService(
            "test@example.com",
            secrets.token_hex(32),
            authenticate=False,
        )

        mock_authenticate.assert_not_called()
        get_from_keyring.assert_not_called()


def test_constructor_accepts_keyword_only_cloudkit_validation_extra() -> None:
    """cloudkit_validation_extra remains a keyword-only escape hatch."""
    with (
        patch("pyicloud.PyiCloudService.authenticate") as mock_authenticate,
        patch("pyicloud.PyiCloudService._setup_cookie_directory") as mock_setup_dir,
        patch("builtins.open", new_callable=mock_open),
    ):
        mock_authenticate.return_value = None
        mock_setup_dir.return_value = "/tmp/pyicloud/cookies"

        service = PyiCloudService(
            "test@example.com",
            secrets.token_hex(32),
            cloudkit_validation_extra="ignore",
        )

        assert service._cloudkit_validation_extra == "ignore"


def test_authenticate_with_missing_token(pyicloud_service: PyiCloudService) -> None:
    """Test the authenticate method with missing session_token."""
    with (
        patch("pyicloud.base.PyiCloudSession.get") as mock_get_response,
        patch("pyicloud.base.PyiCloudSession.post") as mock_post_response,
        patch.object(
            pyicloud_service,
            "_authenticate_with_token",
            side_effect=[PyiCloudFailedLoginException("a"), None],
        ) as mock_authenticate_with_token,
    ):
        mock_post_response.return_value.json.side_effect = [
            {
                "salt": "U29tZVNhbHQ=",
                "b": "U29tZUJ5dGVz",
                "c": "TestC",
                "protocol": "s2k",
                "iteration": 1000,
                "dsInfo": {"hsaVersion": 1},
                "hsaChallengeRequired": False,
                "webservices": "TestWebservices",
            },
            None,
        ]
        pyicloud_service.session.post = mock_post_response
        pyicloud_service.session._data = {}
        pyicloud_service.params = {}
        pyicloud_service.authenticate()
        assert mock_get_response.call_count == 1
        assert mock_post_response.call_count == 2
        assert mock_authenticate_with_token.call_count == 2


def test_get_auth_status_without_session_token(
    pyicloud_service: PyiCloudService,
) -> None:
    """Auth status should report unauthenticated when no token is present."""

    pyicloud_service.session._data = {}
    result = pyicloud_service.get_auth_status()

    assert result == {
        "authenticated": False,
        "trusted_session": False,
        "requires_2fa": False,
        "requires_2sa": False,
    }


def test_get_auth_status_with_valid_session(
    pyicloud_service: PyiCloudService,
) -> None:
    """Auth status should validate a persisted session token without logging in."""

    pyicloud_service.session._data = {"session_token": "token"}
    pyicloud_service.session.cookies = MagicMock()
    pyicloud_service.session.cookies.get.return_value = "cookie"

    with patch.object(
        pyicloud_service,
        "_validate_token",
        return_value={
            "dsInfo": {"dsid": "123", "hsaVersion": 2},
            "hsaTrustedBrowser": True,
            "webservices": {"findme": {"url": "https://example.com"}},
        },
    ):
        result = pyicloud_service.get_auth_status()

    assert result == {
        "authenticated": True,
        "trusted_session": True,
        "requires_2fa": False,
        "requires_2sa": False,
    }
    assert pyicloud_service.params["dsid"] == "123"


def test_get_auth_status_invalid_token_does_not_fallback_to_login(
    pyicloud_service: PyiCloudService,
) -> None:
    """Auth status should not attempt a password-based login on invalid tokens."""

    pyicloud_service.session._data = {"session_token": "token"}
    pyicloud_service.session.cookies = MagicMock()
    pyicloud_service.session.cookies.get.return_value = "cookie"
    pyicloud_service.data = {"hsaTrustedBrowser": True}
    pyicloud_service.params["dsid"] = "123"
    pyicloud_service._devices = MagicMock()

    with (
        patch.object(
            pyicloud_service,
            "_validate_token",
            side_effect=PyiCloudAPIResponseException("Invalid token"),
        ),
        patch.object(pyicloud_service, "_authenticate") as mock_authenticate,
    ):
        result = pyicloud_service.get_auth_status()

    assert result == {
        "authenticated": False,
        "trusted_session": False,
        "requires_2fa": False,
        "requires_2sa": False,
    }
    assert "dsid" not in pyicloud_service.params
    assert pyicloud_service._devices is None
    mock_authenticate.assert_not_called()


def test_validate_2fa_code(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with a valid code."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service._session = mock_session
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }

        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"success": True}
        mock_session.post.return_value = mock_post_response

        assert pyicloud_service.validate_2fa_code("123456")


def test_validate_2fa_code_uses_bridge_verifier_for_step2_state(
    pyicloud_service: PyiCloudService,
) -> None:
    """Bridge-backed trusted-device prompts should use the bridge verifier instead of the legacy endpoint."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}, "hsaChallengeRequired": False}
    pyicloud_service._two_factor_delivery_method = "trusted_device"
    bridge_state = MagicMock(uses_legacy_trusted_device_verifier=False)
    pyicloud_service._trusted_device_bridge_state = bridge_state
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._trusted_device_bridge.validate_code.return_value = True
    pyicloud_service.trust_session = MagicMock(
        side_effect=lambda: pyicloud_service.data.update({"hsaTrustedBrowser": True})
        or True
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }

    assert pyicloud_service.validate_2fa_code("123456") is True

    pyicloud_service._trusted_device_bridge.validate_code.assert_called_once()
    pyicloud_service.session.post.assert_not_called()
    pyicloud_service._trusted_device_bridge.close.assert_called_once_with(bridge_state)
    pyicloud_service.trust_session.assert_called_once_with()


def test_validate_2fa_code_keeps_legacy_endpoint_for_bridge_w_subtype(
    pyicloud_service: PyiCloudService,
) -> None:
    """Apple's `_W` bridge subtype should keep using the legacy trusted-device verifier."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}, "hsaChallengeRequired": False}
    pyicloud_service._two_factor_delivery_method = "trusted_device"
    bridge_state = MagicMock(uses_legacy_trusted_device_verifier=True)
    pyicloud_service._trusted_device_bridge_state = bridge_state
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service.trust_session = MagicMock(
        side_effect=lambda: pyicloud_service.data.update({"hsaTrustedBrowser": True})
        or True
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }
    pyicloud_service.session.post.return_value = MagicMock(status_code=200)

    assert pyicloud_service.validate_2fa_code("123456") is True

    pyicloud_service._trusted_device_bridge.validate_code.assert_not_called()
    args = pyicloud_service.session.post.call_args.args
    assert args[0] == (
        f"{pyicloud_service._auth_endpoint}/verify/trusteddevice/securitycode"
    )
    pyicloud_service._trusted_device_bridge.close.assert_called_once_with(bridge_state)


def test_validate_2fa_code_bridge_verification_exception_propagates(
    pyicloud_service: PyiCloudService,
) -> None:
    """Bridge verification failures should not be downgraded to generic invalid-code results."""

    pyicloud_service._two_factor_delivery_method = "trusted_device"
    bridge_state = MagicMock(uses_legacy_trusted_device_verifier=False)
    pyicloud_service._trusted_device_bridge_state = bridge_state
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._trusted_device_bridge.validate_code.side_effect = (
        PyiCloudTrustedDeviceVerificationException("bridge verification failed")
    )

    with pytest.raises(
        PyiCloudTrustedDeviceVerificationException,
        match="bridge verification failed",
    ):
        pyicloud_service.validate_2fa_code("123456")

    pyicloud_service._trusted_device_bridge.close.assert_called_once_with(bridge_state)


def test_request_2fa_code_requests_sms_delivery(
    pyicloud_service: PyiCloudService,
) -> None:
    """Nested phone verification data should trigger SMS delivery."""

    pyicloud_service._auth_data = {
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": "sms",
            }
        }
    }

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service._session = mock_session
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
        }

        assert pyicloud_service.request_2fa_code() is True

        args = mock_session.put.call_args.args
        kwargs = mock_session.put.call_args.kwargs
        assert args[0] == f"{pyicloud_service._auth_endpoint}/verify/phone"
        assert kwargs["json"] == {
            "phoneNumber": {"id": 3, "nonFTEU": False},
            "mode": "sms",
        }
        assert kwargs["headers"]["Accept"] == "application/json"


def test_get_mfa_auth_options_parses_hsa2_boot_html(
    pyicloud_service: PyiCloudService,
) -> None:
    """GET /appleauth/auth HTML should populate the HSA2 boot context."""

    response = MagicMock()
    response.json.side_effect = ValueError("not json")
    response.text = """
    <html>
      <script type="application/json" class="boot_args">
        {
          "direct": {
            "authInitialRoute": "auth/bridge/step",
            "hasTrustedDevices": true,
            "twoSV": {
              "authFactors": ["web_piggybacking", "sms"],
              "sourceAppId": 1159,
              "bridgeInitiateData": {
                "apnsTopic": "com.apple.idmsauthwidget",
                "apnsEnvironment": "prod",
                "webSocketUrl": "websocket.push.apple.com",
                "phoneNumberVerification": {
                  "trustedPhoneNumber": {
                    "id": 3,
                    "nonFTEU": false,
                    "pushMode": "sms"
                  }
                }
              }
            }
          }
        }
      </script>
    </html>
    """
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.get.return_value = response

    auth_options = pyicloud_service._get_mfa_auth_options()

    _, kwargs = pyicloud_service.session.get.call_args
    assert kwargs["headers"]["Accept"] == "text/html"
    assert auth_options["authInitialRoute"] == "auth/bridge/step"
    assert auth_options["hasTrustedDevices"] is True
    assert auth_options["authFactors"] == ["web_piggybacking", "sms"]
    assert auth_options["bridgeInitiateData"]["webSocketUrl"] == (
        "websocket.push.apple.com"
    )
    assert auth_options["phoneNumberVerification"]["trustedPhoneNumber"]["id"] == 3
    assert auth_options["sourceAppId"] == "1159"
    assert pyicloud_service._hsa2_boot_context is not None
    assert pyicloud_service._hsa2_boot_context.auth_initial_route == (
        "auth/bridge/step"
    )
    assert pyicloud_service._hsa2_boot_context.has_trusted_devices is True


def test_request_2fa_code_prefers_trusted_device_bridge(
    pyicloud_service: PyiCloudService,
) -> None:
    """Request-7 style HSA2 challenges should start the bridge before SMS."""

    pyicloud_service.data = {
        "dsInfo": {"hsaVersion": 2},
        "hsaChallengeRequired": True,
        "hsaTrustedBrowser": False,
    }
    pyicloud_service._auth_data = {
        "authInitialRoute": "auth/bridge/step",
        "hasTrustedDevices": True,
        "authFactors": ["web_piggybacking", "sms"],
        "bridgeInitiateData": {
            "apnsTopic": "com.apple.idmsauthwidget",
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": "sms",
            }
        },
    }

    bridge_state = MagicMock()
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._trusted_device_bridge.start.return_value = bridge_state
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.headers = {"User-Agent": "test-agent"}
    pyicloud_service.session.data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }

    assert pyicloud_service.request_2fa_code() is True

    pyicloud_service._trusted_device_bridge.start.assert_called_once()
    pyicloud_service.session.put.assert_not_called()
    assert pyicloud_service.two_factor_delivery_method == "trusted_device"
    assert pyicloud_service._trusted_device_bridge_state is bridge_state


def test_request_2fa_code_replaces_existing_bridge_state_before_restart(
    pyicloud_service: PyiCloudService,
) -> None:
    """Starting a new bridge prompt should close any previous in-memory bridge session."""

    pyicloud_service._auth_data = {
        "authInitialRoute": "auth/bridge/step",
        "hasTrustedDevices": True,
        "bridgeInitiateData": {
            "apnsTopic": "com.apple.idmsauthwidget",
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
    }

    previous_bridge_state = MagicMock()
    next_bridge_state = MagicMock()
    pyicloud_service._trusted_device_bridge_state = previous_bridge_state
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._trusted_device_bridge.start.return_value = next_bridge_state
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.headers = {"User-Agent": "test-agent"}
    pyicloud_service.session.data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }

    assert pyicloud_service.request_2fa_code() is True

    pyicloud_service._trusted_device_bridge.close.assert_called_once_with(
        previous_bridge_state
    )
    assert pyicloud_service._trusted_device_bridge_state is next_bridge_state


def test_request_2fa_code_falls_back_to_sms_when_bridge_fails(
    pyicloud_service: PyiCloudService,
) -> None:
    """Bridge bootstrap failures should fall back to SMS when Apple exposes it."""

    pyicloud_service._auth_data = {
        "authInitialRoute": "auth/bridge/step",
        "hasTrustedDevices": True,
        "bridgeInitiateData": {
            "apnsTopic": "com.apple.idmsauthwidget",
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": "sms",
            }
        },
    }

    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._trusted_device_bridge.start.side_effect = (
        PyiCloudTrustedDevicePromptException("bridge failed")
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.headers = {"User-Agent": "test-agent"}
    pyicloud_service.session.data = {
        "scnt": "test_scnt",
        "session_id": "test_session_id",
    }

    assert pyicloud_service.request_2fa_code() is True

    args = pyicloud_service.session.put.call_args.args
    kwargs = pyicloud_service.session.put.call_args.kwargs
    assert args[0] == f"{pyicloud_service._auth_endpoint}/verify/phone"
    assert kwargs["json"] == {
        "phoneNumber": {"id": 3, "nonFTEU": False},
        "mode": "sms",
    }
    assert pyicloud_service.two_factor_delivery_method == "sms"
    assert pyicloud_service.two_factor_delivery_notice == (
        "Trusted-device prompt failed; falling back to SMS."
    )


def test_request_2fa_code_keeps_security_key_path_separate(
    pyicloud_service: PyiCloudService,
) -> None:
    """Security-key challenges should not start the bridge or SMS flows."""

    pyicloud_service._auth_data = {
        "fsaChallenge": {"challenge": "abc"},
        "authInitialRoute": "auth/bridge/step",
        "hasTrustedDevices": True,
        "bridgeInitiateData": {
            "apnsTopic": "com.apple.idmsauthwidget",
            "apnsEnvironment": "prod",
            "webSocketUrl": "websocket.push.apple.com",
        },
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": "sms",
            }
        },
    }

    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.headers = {"User-Agent": "test-agent"}

    assert pyicloud_service.request_2fa_code() is False

    pyicloud_service._trusted_device_bridge.start.assert_not_called()
    pyicloud_service.session.put.assert_not_called()
    assert pyicloud_service.two_factor_delivery_method == "security_key"


def test_validate_2fa_code_uses_nested_sms_phone_number(
    pyicloud_service: PyiCloudService,
) -> None:
    """Nested phone verification data should validate via the SMS endpoint."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}
    pyicloud_service._auth_data = {
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": "sms",
            }
        }
    }
    pyicloud_service.trust_session = MagicMock(
        side_effect=lambda: pyicloud_service.data.update({"hsaTrustedBrowser": True})
        or True
    )

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service._session = mock_session
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }

        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"success": True}
        mock_session.post.return_value = mock_post_response

        assert pyicloud_service.validate_2fa_code("123456")

        args = mock_session.post.call_args.args
        kwargs = mock_session.post.call_args.kwargs
        assert args[0] == f"{pyicloud_service._auth_endpoint}/verify/phone/securitycode"
        assert kwargs["json"] == {
            "phoneNumber": {"id": 3, "nonFTEU": False},
            "securityCode": {"code": "123456"},
            "mode": "sms",
        }


def test_validate_2fa_code_defaults_sms_mode_when_push_mode_missing(
    pyicloud_service: PyiCloudService,
) -> None:
    """Missing SMS pushMode should still validate using the delivery mode used to trigger SMS."""

    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}, "hsaChallengeRequired": False}
    pyicloud_service._auth_data = {
        "phoneNumberVerification": {
            "trustedPhoneNumber": {
                "id": 3,
                "nonFTEU": False,
                "pushMode": None,
            }
        }
    }
    pyicloud_service._two_factor_delivery_method = "sms"
    pyicloud_service.trust_session = MagicMock(
        side_effect=lambda: pyicloud_service.data.update({"hsaTrustedBrowser": True})
        or True
    )

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service._session = mock_session
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }

        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"success": True}
        mock_session.post.return_value = mock_post_response

        assert pyicloud_service.validate_2fa_code("123456")

        kwargs = mock_session.post.call_args.kwargs
        assert kwargs["json"]["mode"] == "sms"


def test_validate_2fa_code_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the validate_2fa_code method with an invalid code."""
    exception = PyiCloudAPIResponseException("Invalid code")
    exception.code = -21669
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        mock_session.post.side_effect = exception
        pyicloud_service._session = mock_session
        assert not pyicloud_service.validate_2fa_code("000000")


@patch("pyicloud.base.CtapHidDevice.list_devices", return_value=[MagicMock()])
@patch("pyicloud.base.Fido2Client")
def test_confirm_security_key_success(
    mock_fido2_client_cls, mock_list_devices, pyicloud_service: PyiCloudService
) -> None:
    """Test that the FIDO2 WebAuthn flow works"""
    rp_id = "example.com"
    challenge = "ZmFrZV9jaGFsbGVuZ2U"

    # Arrange
    pyicloud_service._submit_webauthn_assertion_response = MagicMock()
    pyicloud_service.trust_session = MagicMock()

    # Simulated WebAuthn options returned from backend
    pyicloud_service._auth_data = {
        "fsaChallenge": {
            "challenge": challenge,  # base64url(fake_challenge)
            "keyHandles": ["a2V5MQ", "a2V5Mg"],  # base64url(fake_key_ids)
            "rpId": rp_id,
        }
    }

    # Simulated FIDO2 response
    mock_response = MagicMock()
    mock_response.response = MagicMock()
    mock_response.response.client_data = b"client_data"
    mock_response.response.signature = b"signature"
    mock_response.response.authenticator_data = b"auth_data"
    mock_response.response.user_handle = b"user_handle"
    mock_response.raw_id = b"cred_id"

    mock_fido2_client = MagicMock()
    mock_fido2_client.get_assertion.return_value.get_response.return_value = (
        mock_response
    )
    mock_fido2_client_cls.return_value = mock_fido2_client

    # Act
    pyicloud_service.confirm_security_key()

    # Assert
    mock_list_devices.assert_called_once()
    mock_fido2_client.get_assertion.assert_called_once()

    # Check if data was submitted correctly
    pyicloud_service._submit_webauthn_assertion_response.assert_called_once_with(
        {
            "challenge": challenge,
            "rpId": rp_id,
            "clientData": b64_encode(mock_response.response.client_data),
            "signatureData": b64_encode(mock_response.response.signature),
            "authenticatorData": b64_encode(mock_response.response.authenticator_data),
            "userHandle": b64_encode(mock_response.response.user_handle),
            "credentialID": b64_encode(mock_response.raw_id),
        }
    )

    pyicloud_service.trust_session.assert_called_once()


def test_get_webservice_url_success(pyicloud_service: PyiCloudService) -> None:
    """Test the get_webservice_url method with a valid key."""
    pyicloud_service._webservices = {"test_key": {"url": "https://example.com"}}
    url: str = pyicloud_service.get_webservice_url("test_key")
    assert url == "https://example.com"


def test_get_webservice_url_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the get_webservice_url method with an invalid key."""
    pyicloud_service._webservices = {}
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_service.get_webservice_url("invalid_key")


def test_trust_session_success(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a successful response."""

    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        mock_session.data = {
            "scnt": "test_scnt",
            "session_id": "test_session_id",
            "session_token": "test_session_token",
        }
        mock_session.post.return_value.json.return_value = {
            "termsUpdateNeeded": False,
            "hsaTrustedBrowser": True,
        }
        pyicloud_service._session = mock_session
        assert pyicloud_service.trust_session()


def test_trust_session_failure(pyicloud_service: PyiCloudService) -> None:
    """Test the trust_session method with a failed response."""
    with patch("pyicloud.base.PyiCloudSession") as mock_session:
        pyicloud_service._session = mock_session
        mock_session.get.side_effect = PyiCloudAPIResponseException("Trust failed")
        assert not pyicloud_service.trust_session()


@pytest.mark.parametrize(
    ("keep_trusted", "all_sessions", "expected_payload"),
    [
        (False, False, {"trustBrowser": False, "allBrowsers": False}),
        (True, False, {"trustBrowser": True, "allBrowsers": False}),
        (False, True, {"trustBrowser": False, "allBrowsers": True}),
        (True, True, {"trustBrowser": True, "allBrowsers": True}),
    ],
)
def test_logout_payload_mappings(
    pyicloud_service: PyiCloudService,
    keep_trusted: bool,
    all_sessions: bool,
    expected_payload: dict[str, bool],
) -> None:
    """Logout should map CLI semantics to Apple's payload exactly."""

    pyicloud_service.params["dsid"] = "123"
    pyicloud_service.session.cookies = MagicMock()
    pyicloud_service.session.cookies.get.return_value = "cookie"
    pyicloud_service.session.clear_persistence = MagicMock()
    pyicloud_service.session.post = MagicMock(
        return_value=MagicMock(json=MagicMock(return_value={"success": True}))
    )

    result = pyicloud_service.logout(
        keep_trusted=keep_trusted,
        all_sessions=all_sessions,
    )

    kwargs = pyicloud_service.session.post.call_args.kwargs
    assert kwargs["params"]["dsid"] == "123"
    assert kwargs["headers"] == {"Content-Type": "text/plain;charset=UTF-8"}
    assert json.loads(kwargs["data"]) == expected_payload
    assert result["payload"] == expected_payload
    assert result["remote_logout_confirmed"] is True


def test_logout_clears_authenticated_state(
    pyicloud_service: PyiCloudService,
) -> None:
    """Logout should clear in-memory auth state and persisted session data."""

    pyicloud_service.data = {"dsInfo": {"dsid": "123"}}
    pyicloud_service.params["dsid"] = "123"
    pyicloud_service._devices = MagicMock()
    pyicloud_service.session.cookies = MagicMock()
    pyicloud_service.session.cookies.get.return_value = "cookie"
    pyicloud_service.session.post = MagicMock(
        side_effect=PyiCloudAPIResponseException("logout failed")
    )
    pyicloud_service.session.clear_persistence = MagicMock()

    result = pyicloud_service.logout()

    assert result["remote_logout_confirmed"] is False
    assert result["local_session_cleared"] is True
    pyicloud_service.session.clear_persistence.assert_called_once_with(
        remove_files=True
    )
    assert pyicloud_service.data == {}
    assert "dsid" not in pyicloud_service.params
    assert pyicloud_service._devices is None


def test_logout_closes_active_trusted_device_bridge_state(
    pyicloud_service: PyiCloudService,
) -> None:
    """Logout should close any active trusted-device bridge session before clearing state."""

    bridge_state = MagicMock()
    pyicloud_service._trusted_device_bridge_state = bridge_state
    pyicloud_service._trusted_device_bridge = MagicMock()
    pyicloud_service.session.cookies = MagicMock()
    pyicloud_service.session.cookies.get.return_value = None
    pyicloud_service.session.clear_persistence = MagicMock()

    pyicloud_service.logout()

    pyicloud_service._trusted_device_bridge.close.assert_called_once_with(bridge_state)
    assert pyicloud_service._trusted_device_bridge_state is None


def test_cookiejar_path_property(pyicloud_session: PyiCloudSession) -> None:
    """Test the cookiejar_path property."""
    path: str = pyicloud_session.cookiejar_path
    assert isinstance(path, str)


def test_session_path_property(pyicloud_session: PyiCloudSession) -> None:
    """Test the session_path property."""
    path: str = pyicloud_session.session_path
    assert isinstance(path, str)


def test_clear_persistence_removes_session_and_cookie_files(
    pyicloud_session: PyiCloudSession,
) -> None:
    """Session persistence cleanup should clear cookies and remove persisted files."""

    pyicloud_session._data = {"session_token": "token"}
    with patch("pyicloud.session.os.remove") as mock_remove:
        pyicloud_session.clear_persistence()

    pyicloud_session.cookies.clear.assert_called_once_with()
    assert pyicloud_session.data == {}
    assert mock_remove.call_count == 2
    removed_paths = {call.args[0] for call in mock_remove.call_args_list}
    assert removed_paths == {
        pyicloud_session.cookiejar_path,
        pyicloud_session.session_path,
    }


def test_clear_persistence_replaces_cookiejar_after_clear_failure(
    pyicloud_session: PyiCloudSession,
) -> None:
    """Cookie clear failures should reset the in-memory jar before cleanup continues."""

    broken_cookie_jar = MagicMock()
    broken_cookie_jar.clear.side_effect = RuntimeError("boom")
    pyicloud_session.cookies = broken_cookie_jar

    with patch("pyicloud.session.os.remove"):
        pyicloud_session.clear_persistence()

    broken_cookie_jar.clear.assert_called_once_with()
    assert isinstance(pyicloud_session.cookies, PyiCloudCookieJar)
    assert pyicloud_session.cookies.filename == pyicloud_session.cookiejar_path


def test_requires_2sa_property(pyicloud_service: PyiCloudService) -> None:
    """Test the requires_2sa property."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}}
    assert pyicloud_service.requires_2sa


def test_requires_2fa_property(pyicloud_service: PyiCloudService) -> None:
    """Test the requires_2fa property."""
    pyicloud_service.data = LOGIN_2FA
    assert pyicloud_service.requires_2fa


def test_is_trusted_session_property(pyicloud_service: PyiCloudService) -> None:
    """Test the is_trusted_session property."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 2}}
    assert not pyicloud_service.is_trusted_session


def test_request_success(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with a successful response."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("os.path.exists", return_value=True),
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
        patch("http.cookiejar.LWPCookieJar.load") as mock_load,
        patch("pyicloud.cookie_jar.PyiCloudCookieJar.copy") as mock_copy,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        mock_copy.return_value = MagicMock()

        pyicloud_session = PyiCloudSession(
            service=pyicloud_service_working,
            client_id="",
            cookie_directory="",
        )

        response: Response = pyicloud_session.request(
            "POST", "https://example.com", data={"key": "value"}
        )
        assert response.json() == {"success": True}
        assert response.headers.get("Content-Type") == "application/json"
        mock_request.assert_called_once_with(
            method="POST",
            url="https://example.com",
            data={"key": "value"},
            params=None,
            headers=None,
            cookies=None,
            files=None,
            auth=None,
            timeout=None,
            allow_redirects=True,
            proxies=None,
            hooks=None,
            stream=None,
            verify=None,
            cert=None,
            json=None,
        )
        mock_save.assert_called_once_with(
            mock_copy.return_value,
            filename="testexamplecom.cookiejar",
            ignore_discard=True,
            ignore_expires=False,
        )
        mock_load.assert_called_once_with(
            filename="testexamplecom.cookiejar",
            ignore_discard=True,
            ignore_expires=False,
        )


def test_session_persistence_excludes_trusted_device_bridge_state(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Bridge-only state should remain in memory and never be written to persisted session files."""

    test_base = Path(tempfile.gettempdir()) / "python-test-results"
    test_base.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bridge-auth-", dir=test_base))
    session = PyiCloudSession(
        service=pyicloud_service_working,
        client_id="",
        cookie_directory=str(temp_root),
    )
    pyicloud_service_working._session = session
    pyicloud_service_working._trusted_device_bridge_state = MagicMock(
        push_token="bridge-ptkn",
        session_uuid="bridge-session-uuid",
        idmsdata="bridge-idmsdata",
        encrypted_code="bridge-encrypted-code",
    )
    session._data = {
        "session_token": "valid-token",
        "session_id": "persisted-session-id",
    }

    session._save_session_data()

    persisted_session = Path(session.session_path).read_text(encoding="utf-8")
    for secret_value in (
        "bridge-ptkn",
        "bridge-session-uuid",
        "bridge-idmsdata",
        "bridge-encrypted-code",
    ):
        assert secret_value not in persisted_session

    cookiejar_path = Path(session.cookiejar_path)
    if cookiejar_path.exists():
        persisted_cookiejar = cookiejar_path.read_text(encoding="utf-8")
        for secret_value in (
            "bridge-ptkn",
            "bridge-session-uuid",
            "bridge-idmsdata",
            "bridge-encrypted-code",
        ):
            assert secret_value not in persisted_cookiejar


def test_request_failure(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with a failure response."""

    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open) as open_mock,
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.ok = False
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=""
        )
        with pytest.raises(PyiCloudAPIResponseException):
            pyicloud_session.request(
                "POST", "https://example.com", data={"key": "value"}
            )

        mock_request.assert_called_once_with(
            method="POST",
            url="https://example.com",
            data={"key": "value"},
            params=None,
            headers=None,
            cookies=None,
            files=None,
            auth=None,
            timeout=None,
            allow_redirects=True,
            proxies=None,
            hooks=None,
            stream=None,
            verify=None,
            cert=None,
            json=None,
        )
        mock_save.assert_called_once()
        assert open_mock.call_count == 2


def test_request_raw_normalizes_transport_failure(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Raw requests should keep the session's normalized transport failure contract."""

    with patch("requests.Session.request") as mock_request:
        mock_request.side_effect = requests.exceptions.Timeout("timed out")
        test_base = Path(tempfile.gettempdir()) / "python-test-results"
        test_base.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix="request-raw-", dir=test_base))
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=str(temp_root)
        )

        with pytest.raises(
            PyiCloudAPIResponseException, match="Request failed to iCloud"
        ):
            pyicloud_session.request_raw("GET", "https://example.com")


def test_request_with_custom_headers(pyicloud_service_working: PyiCloudService) -> None:
    """Test the request method with custom headers."""
    with (
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("http.cookiejar.LWPCookieJar.save") as mock_save,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "header test"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response
        pyicloud_session = PyiCloudSession(
            pyicloud_service_working, "", cookie_directory=""
        )

        response: Response = pyicloud_session.request(
            "GET",
            "https://example.com",
            headers={"Custom-Header": "Value"},
        )
        assert response.json() == {"data": "header test"}
        assert response.headers.get("Content-Type") == "application/json"
        mock_request.assert_called_once_with(
            method="GET",
            url="https://example.com",
            data=None,
            headers={"Custom-Header": "Value"},
            params=None,
            cookies=None,
            files=None,
            auth=None,
            timeout=None,
            allow_redirects=True,
            proxies=None,
            hooks=None,
            stream=None,
            verify=None,
            cert=None,
            json=None,
        )
        mock_save.assert_called_once()


def test_request_error_handling_for_response_conditions() -> None:
    """Mock the get_webservice_url to return a valid fmip_url."""
    pyicloud_service = MagicMock(spec=PyiCloudService)
    with (
        pytest.raises(PyiCloudAPIResponseException),
        patch("requests.Session.request") as mock_request,
        patch("builtins.open", new_callable=mock_open),
        patch("os.path.exists", return_value=False),
        patch("http.cookiejar.LWPCookieJar.save"),
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://fmip.example.com",
        ),
    ):
        # Mock the response with conditions that cause an error.
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.json.return_value = {"error": "Server Error"}
        mock_response.headers.get.return_value = "application/json"
        mock_request.return_value = mock_response

        pyicloud_session = PyiCloudSession(pyicloud_service, "", cookie_directory="")
        pyicloud_service.data = {"session_token": "valid_token"}

        # Use the mocked fmip_url in the request.
        pyicloud_session.request("GET", "https://fmip.example.com/path")


def test_raise_error_2sa_required(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with a 2SA required exception."""
    with (
        pytest.raises(PyiCloud2SARequiredException),
        patch("pyicloud.base.PyiCloudService.requires_2sa", return_value=True),
    ):
        pyicloud_session._raise_error(
            code=401,
            reason="Missing X-APPLE-WEBAUTH-TOKEN cookie",
            response=MagicMock(),
        )


def test_raise_error_service_not_activated(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with a service not activated exception."""
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_session._raise_error(
            code="ZONE_NOT_FOUND", reason="ServiceNotActivated", response=MagicMock()
        )


def test_raise_error_access_denied(pyicloud_session: PyiCloudSession) -> None:
    """Test the _raise_error method with an access denied exception."""
    with pytest.raises(PyiCloudAPIResponseException):
        pyicloud_session._raise_error(
            code="ACCESS_DENIED", reason="ACCESS_DENIED", response=MagicMock()
        )


def test_request_pcs_for_service_icdrs_not_disabled(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when ICDRS is not disabled (should early return)."""
    mock_logger = MagicMock()
    pyicloud_service._session = MagicMock()
    pyicloud_service.session.post = MagicMock(
        return_value=MagicMock(json=MagicMock(return_value={"isICDRSDisabled": False}))
    )
    pyicloud_service.params = {}
    with patch("pyicloud.base.LOGGER", mock_logger):
        pyicloud_service._send_pcs_request = MagicMock()
        pyicloud_service._request_pcs_for_service("photos")
        mock_logger.debug.assert_any_call(
            "Skipping PCS request because Apple reports ICDRS is enabled"
        )
        pyicloud_service._send_pcs_request.assert_not_called()


def test_request_pcs_for_service_consent_needed_and_notification_sent(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when device consent is needed and notification is sent."""
    # First call: ICDRS disabled, device not consented
    # Second call: device consented (simulate after waiting)
    consent_states: List[dict[str, bool]] = [
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
    ]

    pyicloud_service._check_pcs_consent = MagicMock(side_effect=consent_states)
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._session.post.return_value.json.side_effect = [
        {"isDeviceConsentNotificationSent": True},
        {"status": "success", "message": "ok"},
    ]
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    pyicloud_service._session.post.assert_any_call(
        f"{pyicloud_service._setup_endpoint}/enableDeviceConsentForPCS",
        params=pyicloud_service.params,
    )
    # Should not raise


def test_request_pcs_for_service_consent_needed_and_notification_not_sent(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service when device consent notification is not sent (should raise)."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": False}
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._session.post.return_value.json.return_value = {
        "isDeviceConsentNotificationSent": False
    }
    with pytest.raises(
        PyiCloudAPIResponseException, match="Unable to request PCS access!"
    ):
        pyicloud_service._request_pcs_for_service("photos")


def test_request_pcs_for_service_pcs_consent_waits(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service waits for PCS consent and then proceeds."""
    # Simulate PCS consent not granted for first 2 tries, then granted
    consent_states: List[dict[str, bool]] = [
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": False},
        {"isICDRSDisabled": True, "isDeviceConsentedForPCS": True},
    ]
    pyicloud_service._check_pcs_consent = MagicMock(side_effect=consent_states)
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._session.post.return_value.json.return_value = {
        "isDeviceConsentNotificationSent": True
    }
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "success", "message": "ok"}
    )
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    assert pyicloud_service._send_pcs_request.called


def test_request_pcs_for_service_success_on_first_attempt(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service grants PCS access on first attempt."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "success", "message": "ok"}
    )
    pyicloud_service._request_pcs_for_service("photos")
    pyicloud_service._send_pcs_request.assert_called_once_with(
        "photos", derived_from_user_action=True
    )


def test_request_pcs_for_service_retries_on_cookie_messages(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service retries on known cookie messages and succeeds."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    responses: List[dict[str, str]] = [
        {"status": "error", "message": "Requested the device to upload cookies."},
        {"status": "error", "message": "Cookies not available yet on server."},
        {"status": "success", "message": "ok"},
    ]
    pyicloud_service._send_pcs_request = MagicMock(side_effect=responses)
    with patch("time.sleep"):
        pyicloud_service._request_pcs_for_service("photos")
    assert pyicloud_service._send_pcs_request.call_count == 3


def test_request_pcs_for_service_raises_on_unknown_message(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _request_pcs_for_service raises on unknown PCS state message."""
    pyicloud_service._check_pcs_consent = MagicMock(
        return_value={"isICDRSDisabled": True, "isDeviceConsentedForPCS": True}
    )
    pyicloud_service._session = MagicMock()
    pyicloud_service.params = {}
    pyicloud_service._send_pcs_request = MagicMock(
        return_value={"status": "error", "message": "Some unknown error"}
    )
    mock_logger = MagicMock()

    with (
        pytest.raises(
            PyiCloudAPIResponseException, match="Unable to request PCS access!"
        ),
        patch("pyicloud.base.LOGGER", mock_logger),
    ):
        pyicloud_service._request_pcs_for_service("photos")
    mock_logger.error.assert_called()


def test_handle_accept_terms_no_terms_update_needed(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _handle_accept_terms when no terms update is needed (should do nothing)."""
    pyicloud_service.data = {"termsUpdateNeeded": False}
    login_data: dict[str, str] = {"test": "data"}
    # Should not raise or call anything
    pyicloud_service._session = MagicMock()
    pyicloud_service._accept_terms = True
    pyicloud_service._handle_accept_terms(login_data)
    pyicloud_service._session.get.assert_not_called()
    pyicloud_service._session.post.assert_not_called()


def test_handle_accept_terms_terms_update_needed_accept_terms_false(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _handle_accept_terms when terms update is needed and accept_terms is
    False (should raise)."""
    pyicloud_service.data = {"termsUpdateNeeded": True}
    pyicloud_service._accept_terms = False
    login_data: dict[str, str] = {"test": "data"}
    with pytest.raises(
        PyiCloudAcceptTermsException,
        match="You must accept the updated terms of service",
    ):
        pyicloud_service._handle_accept_terms(login_data)


def test_handle_accept_terms_terms_update_needed_accept_terms_true_success(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _handle_accept_terms when terms update is needed and accept_terms is
    True (should accept terms)."""
    pyicloud_service.data = {
        "termsUpdateNeeded": True,
        "dsInfo": {"languageCode": "en_US"},
    }
    pyicloud_service._accept_terms = True
    login_data: dict[str, str] = {"test": "data"}

    # Mock session.get and session.post
    mock_get = MagicMock()
    mock_post = MagicMock()
    pyicloud_service.session.get = mock_get
    pyicloud_service.session.post = mock_post

    # Mock getTerms response
    get_terms_response = MagicMock()
    get_terms_response.raise_for_status = MagicMock()
    get_terms_response.json.return_value = {"iCloudTerms": {"version": 42}}
    mock_get.side_effect = [get_terms_response, get_terms_response]

    # Mock accountLogin response
    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"new": "data"}
    mock_post.return_value = post_response

    pyicloud_service._handle_accept_terms(login_data)

    # Check calls
    mock_get.assert_any_call(
        f"{pyicloud_service._setup_endpoint}/getTerms",
        params=pyicloud_service.params,
        json={"locale": "en_US"},
    )
    mock_get.assert_any_call(
        f"{pyicloud_service._setup_endpoint}/repairDone",
        params=pyicloud_service.params,
        json={"acceptedICloudTerms": 42},
    )
    mock_post.assert_called_once_with(
        f"{pyicloud_service._setup_endpoint}/accountLogin", json=login_data
    )
    assert pyicloud_service.data == {"new": "data"}


def test_handle_accept_terms_terms_update_needed_accept_terms_true_http_error(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _handle_accept_terms when terms update is needed and accept_terms is
    True but HTTP error occurs."""
    pyicloud_service.data = {
        "termsUpdateNeeded": True,
        "dsInfo": {"languageCode": "en_US"},
    }
    pyicloud_service._accept_terms = True
    login_data: dict[str, str] = {"test": "data"}

    # Mock session.get to raise HTTPError
    mock_get = MagicMock()
    pyicloud_service.session.get = mock_get
    mock_get.side_effect = HTTPError("HTTP error")

    with pytest.raises(HTTPError):
        pyicloud_service._handle_accept_terms(login_data)


def test_handle_accept_terms_terms_update_needed_accept_terms_true_post_error(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _handle_accept_terms when terms update is needed and accept_terms is
    True but POST raises HTTPError."""
    pyicloud_service.data = {
        "termsUpdateNeeded": True,
        "dsInfo": {"languageCode": "en_US"},
    }
    pyicloud_service._accept_terms = True
    login_data: dict[str, str] = {"test": "data"}

    # Mock session.get for getTerms and repairDone
    mock_get = MagicMock()
    pyicloud_service.session.get = mock_get
    get_terms_response = MagicMock()
    get_terms_response.raise_for_status = MagicMock()
    get_terms_response.json.return_value = {"iCloudTerms": {"version": 42}}
    mock_get.side_effect = [get_terms_response, get_terms_response]

    # Mock session.post to raise HTTPError
    mock_post = MagicMock()
    pyicloud_service.session.post = mock_post
    mock_post.side_effect = HTTPError("POST error")

    with pytest.raises(HTTPError):
        pyicloud_service._handle_accept_terms(login_data)


def test_validate_token_success(pyicloud_service: PyiCloudService) -> None:
    """Test _validate_token returns JSON when X-APPLE-WEBAUTH-TOKEN is present and
    request succeeds."""
    with (
        patch.object(pyicloud_service.session.cookies, "get", return_value="token"),
        patch.object(pyicloud_service.session, "post") as mock_post,
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "success"}
        mock_post.return_value = mock_response

        result = pyicloud_service._validate_token()
        assert result == {"status": "success"}
        mock_post.assert_called_once_with(
            f"{pyicloud_service._setup_endpoint}/validate", data="null"
        )


def test_validate_token_missing_cookie_raises(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _validate_token raises when X-APPLE-WEBAUTH-TOKEN cookie is missing."""
    with patch.object(pyicloud_service.session.cookies, "get", return_value=None):
        with pytest.raises(
            PyiCloudAPIResponseException, match="Missing X-APPLE-WEBAUTH-TOKEN cookie"
        ):
            pyicloud_service._validate_token()


def test_validate_token_post_raises_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _validate_token raises when session.post raises PyiCloudAPIResponseException."""
    with (
        patch.object(pyicloud_service.session.cookies, "get", return_value="token"),
        patch.object(
            pyicloud_service.session,
            "post",
            side_effect=PyiCloudAPIResponseException("Invalid token"),
        ),
    ):
        with pytest.raises(PyiCloudAPIResponseException, match="Invalid token"):
            pyicloud_service._validate_token()


def test_str_and_repr(pyicloud_service: PyiCloudService) -> None:
    """Test __str__ and __repr__ methods."""
    s = str(pyicloud_service)
    r: str = repr(pyicloud_service)
    assert s.startswith("iCloud API:")
    assert r.startswith("<iCloud API:")


def test_account_name_property(pyicloud_service: PyiCloudService) -> None:
    """Test account_name property returns the correct Apple ID."""
    assert pyicloud_service.account_name == pyicloud_service._apple_id


def test_requires_2sa_true(pyicloud_service: PyiCloudService) -> None:
    """Test requires_2sa returns True when hsaVersion >= 1 and not trusted."""
    pyicloud_service.data = {
        "dsInfo": {"hsaVersion": 1},
        "hsaChallengeRequired": True,
        "hsaTrustedBrowser": False,
    }
    assert pyicloud_service.requires_2sa


def test_requires_2sa_false(pyicloud_service: PyiCloudService) -> None:
    """Test requires_2sa returns False when hsaVersion < 1."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 0}}
    assert not pyicloud_service.requires_2sa


def test_requires_2fa_true(pyicloud_service: PyiCloudService) -> None:
    """Test requires_2fa returns True when hsaVersion == 2 and not trusted."""
    pyicloud_service.data = {
        "dsInfo": {"hsaVersion": 2},
        "hsaChallengeRequired": True,
        "hsaTrustedBrowser": False,
    }
    assert pyicloud_service.requires_2fa


def test_requires_2fa_false(pyicloud_service: PyiCloudService) -> None:
    """Test requires_2fa returns False when hsaVersion != 2."""
    pyicloud_service.data = {"dsInfo": {"hsaVersion": 1}}
    assert not pyicloud_service.requires_2fa


def test_is_trusted_session_true(pyicloud_service: PyiCloudService) -> None:
    """Test is_trusted_session returns True when hsaTrustedBrowser is True."""
    pyicloud_service.data = {"hsaTrustedBrowser": True}
    assert pyicloud_service.is_trusted_session


def test_is_trusted_session_false(pyicloud_service: PyiCloudService) -> None:
    """Test is_trusted_session returns False when hsaTrustedBrowser is False."""
    pyicloud_service.data = {"hsaTrustedBrowser": False}
    assert not pyicloud_service.is_trusted_session


def test_get_auth_headers_overrides(pyicloud_service: PyiCloudService) -> None:
    """Test _get_auth_headers applies overrides."""
    pyicloud_service.session.data["scnt"] = "test_scnt"
    pyicloud_service.session.data["session_id"] = "test_session_id"
    headers: dict[str, Any] = pyicloud_service._get_auth_headers(
        {"Extra-Header": "Value"}
    )
    assert headers["scnt"] == "test_scnt"
    assert headers["X-Apple-ID-Session-Id"] == "test_session_id"
    assert headers["Extra-Header"] == "Value"


def test_trusted_devices_calls_session_get(pyicloud_service: PyiCloudService) -> None:
    """Test trusted_devices property calls session.get and returns devices."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"devices": [{"id": "device1"}]}
    pyicloud_service.session.get = MagicMock(return_value=mock_response)
    devices: list[dict[str, Any]] = pyicloud_service.trusted_devices
    assert devices == [{"id": "device1"}]
    pyicloud_service.session.get.assert_called_once()


def test_send_verification_code_success(pyicloud_service: PyiCloudService) -> None:
    """Test send_verification_code returns True on success."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"success": True}
    pyicloud_service.session.post = MagicMock(return_value=mock_response)
    result = pyicloud_service.send_verification_code({"id": "device1"})
    assert result is True


def test_send_verification_code_failure(pyicloud_service: PyiCloudService) -> None:
    """Test send_verification_code returns False on failure."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"success": False}
    pyicloud_service.session.post = MagicMock(return_value=mock_response)
    result: bool = pyicloud_service.send_verification_code({"id": "device1"})
    assert result is False


def test_validate_verification_code_success(pyicloud_service: PyiCloudService) -> None:
    """Test validate_verification_code returns True when code is valid."""
    pyicloud_service.session.post = MagicMock()
    pyicloud_service.trust_session = MagicMock(return_value=True)
    result: bool = pyicloud_service.validate_verification_code(
        {"id": "device1"}, "123456"
    )
    assert result is True


def test_validate_verification_code_wrong_code(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test validate_verification_code returns False on wrong code."""
    exc = PyiCloudAPIResponseException("Invalid code")
    exc.code = -21669
    pyicloud_service.session.post = MagicMock(side_effect=exc)
    result: bool = pyicloud_service.validate_verification_code(
        {"id": "device1"}, "000000"
    )
    assert result is False


def test_validate_verification_code_raises_other(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test validate_verification_code raises on unknown error."""
    exc = PyiCloudAPIResponseException("Other error")
    exc.code = 12345
    pyicloud_service.session.post = MagicMock(side_effect=exc)
    with pytest.raises(PyiCloudAPIResponseException):
        pyicloud_service.validate_verification_code({"id": "device1"}, "000000")


def test_security_key_names_returns_key_names(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test security_key_names property returns keyNames from options."""
    pyicloud_service._auth_data = {"keyNames": ["key1", "key2"]}

    assert pyicloud_service.security_key_names == ["key1", "key2"]


def test_fido2_devices_lists_devices(pyicloud_service: PyiCloudService) -> None:
    """Test fido2_devices property lists devices."""
    with patch(
        "pyicloud.base.CtapHidDevice.list_devices", return_value=[MagicMock()]
    ) as mock_list:
        devices: List[CtapHidDevice] = pyicloud_service.fido2_devices
        assert isinstance(devices, list)
        mock_list.assert_called_once()


def test_confirm_security_key_no_devices_raises(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test confirm_security_key raises if no FIDO2 devices found."""
    pyicloud_service._auth_data = {
        "fsaChallenge": {"challenge": "c", "keyHandles": [], "rpId": "rp"}
    }

    with patch("pyicloud.base.CtapHidDevice.list_devices", return_value=[]):
        with pytest.raises(RuntimeError, match="No FIDO2 devices found"):
            pyicloud_service.confirm_security_key()


def test_get_webservice_url_raises_if_missing(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test get_webservice_url raises if key missing."""
    pyicloud_service._webservices = None
    with pytest.raises(PyiCloudServiceNotActivatedException):
        pyicloud_service.get_webservice_url("missing_key")


def test_get_webservice_url_returns_url(pyicloud_service: PyiCloudService) -> None:
    """Test get_webservice_url returns correct url."""
    pyicloud_service._webservices = {"foo": {"url": "https://foo.com"}}
    assert pyicloud_service.get_webservice_url("foo") == "https://foo.com"


def test_str_returns_expected_format(pyicloud_service: PyiCloudService) -> None:
    """Test __str__ method."""
    assert str(pyicloud_service).startswith("iCloud API:")


def test_repr_returns_expected_format(pyicloud_service: PyiCloudService) -> None:
    """Test __repr__ method."""
    assert repr(pyicloud_service).startswith("<iCloud API:")


def test_account_name_property_returns_apple_id(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test account_name property returns the correct Apple ID."""
    assert pyicloud_service.account_name == pyicloud_service._apple_id


def test_hidemyemail_returns_service(pyicloud_service: PyiCloudService) -> None:
    """Test hidemyemail property returns HideMyEmailService instance."""
    mock_hme_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://hme.example.com",
        ),
        patch(
            "pyicloud.base.HideMyEmailService", return_value=mock_hme_service
        ) as mock_hme_cls,
    ):
        pyicloud_service._hidemyemail = None
        result: HideMyEmailService = pyicloud_service.hidemyemail
        mock_hme_cls.assert_called_once_with(
            service_root="https://hme.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
        )
        assert result == mock_hme_service


def test_hidemyemail_returns_cached_instance(pyicloud_service: PyiCloudService) -> None:
    """Test hidemyemail property returns cached instance if already set."""
    mock_hme_service = MagicMock()
    pyicloud_service._hidemyemail = mock_hme_service
    result: HideMyEmailService = pyicloud_service.hidemyemail
    assert result == mock_hme_service


def test_hidemyemail_raises_on_api_exception(pyicloud_service: PyiCloudService) -> None:
    """Test hidemyemail property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://hme.example.com",
        ),
        patch(
            "pyicloud.base.HideMyEmailService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._hidemyemail = None
        with pytest.raises(
            PyiCloudServiceUnavailable, match="Hide My Email service not available"
        ):
            _: HideMyEmailService = pyicloud_service.hidemyemail


def test_files_returns_service(pyicloud_service: PyiCloudService) -> None:
    """Test files property returns UbiquityService instance."""
    mock_files_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://files.example.com",
        ),
        patch(
            "pyicloud.base.UbiquityService", return_value=mock_files_service
        ) as mock_files_cls,
    ):
        pyicloud_service._files = None
        result: UbiquityService = pyicloud_service.files
        mock_files_cls.assert_called_once_with(
            service_root="https://files.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
        )
        assert result == mock_files_service


def test_files_returns_cached_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test files property returns cached instance if already set."""
    mock_files_service = MagicMock()
    pyicloud_service._files = mock_files_service
    result: UbiquityService = pyicloud_service.files
    assert result == mock_files_service


def test_files_raises_on_api_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test files property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://files.example.com",
        ),
        patch(
            "pyicloud.base.UbiquityService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._files = None
        with pytest.raises(
            PyiCloudServiceUnavailable, match="Files service not available"
        ):
            _: UbiquityService = pyicloud_service.files


def test_files_raises_on_account_migrated(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test files property raises specific message if Account migrated."""
    exc = PyiCloudAPIResponseException("Account migrated")
    exc.reason = "Account migrated"
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://files.example.com",
        ),
        patch(
            "pyicloud.base.UbiquityService",
            side_effect=exc,
        ),
    ):
        pyicloud_service._files = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Files service not available use `api.drive` instead",
        ):
            _: UbiquityService = pyicloud_service.files


def test_photos_returns_service(pyicloud_service: PyiCloudService) -> None:
    """Test photos property returns PhotosService instance."""
    mock_photos_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            side_effect=[
                "https://photos.example.com",
                "https://upload.example.com",
                "https://shared.example.com",
            ],
        ),
        patch(
            "pyicloud.base.PhotosService", return_value=mock_photos_service
        ) as mock_photos_cls,
        patch.object(pyicloud_service, "_request_pcs_for_service"),
    ):
        pyicloud_service._photos = None
        pyicloud_service.data = {"dsInfo": {"dsid": "12345"}}
        result: PhotosService = pyicloud_service.photos
        mock_photos_cls.assert_called_once_with(
            service_root="https://photos.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
            upload_url="https://upload.example.com",
            shared_streams_url="https://shared.example.com",
        )
        assert pyicloud_service.params["dsid"] == "12345"
        assert result == mock_photos_service


def test_photos_returns_cached_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test photos property returns cached instance if already set."""
    mock_photos_service = MagicMock()
    pyicloud_service._photos = mock_photos_service
    with patch.object(pyicloud_service, "_request_pcs_for_service"):
        result: PhotosService = pyicloud_service.photos
        assert result == mock_photos_service


def test_photos_raises_on_api_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test photos property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            side_effect=[
                "https://photos.example.com",
                "https://upload.example.com",
                "https://shared.example.com",
            ],
        ),
        patch(
            "pyicloud.base.PhotosService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
        patch.object(pyicloud_service, "_request_pcs_for_service"),
    ):
        pyicloud_service._photos = None
        pyicloud_service.data = {"dsInfo": {"dsid": "12345"}}
        with pytest.raises(
            PyiCloudServiceUnavailable, match="Photos service not available"
        ):
            _: PhotosService = pyicloud_service.photos


def test_calendar_returns_service(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test calendar property returns CalendarService instance."""
    mock_calendar_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://calendar.example.com",
        ),
        patch(
            "pyicloud.base.CalendarService",
            return_value=mock_calendar_service,
        ) as mock_calendar_cls,
    ):
        pyicloud_service._calendar = None
        result: CalendarService = pyicloud_service.calendar
        mock_calendar_cls.assert_called_once_with(
            service_root="https://calendar.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
        )
        assert result == mock_calendar_service


def test_calendar_returns_cached_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test calendar property returns cached instance if already set."""
    mock_calendar_service = MagicMock()
    pyicloud_service._calendar = mock_calendar_service
    result: CalendarService = pyicloud_service.calendar
    assert result == mock_calendar_service


def test_calendar_raises_on_api_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test calendar property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://calendar.example.com",
        ),
        patch(
            "pyicloud.base.CalendarService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._calendar = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Calendar service not available",
        ):
            _: CalendarService = pyicloud_service.calendar


def test_contacts_returns_service(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test contacts property returns ContactsService instance."""
    mock_contacts_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://contacts.example.com",
        ),
        patch(
            "pyicloud.base.ContactsService",
            return_value=mock_contacts_service,
        ) as mock_contacts_cls,
    ):
        pyicloud_service._contacts = None
        result: ContactsService = pyicloud_service.contacts
        mock_contacts_cls.assert_called_once_with(
            service_root="https://contacts.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
        )
        assert result == mock_contacts_service


def test_contacts_returns_cached_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test contacts property returns cached instance if already set."""
    mock_contacts_service = MagicMock()
    pyicloud_service._contacts = mock_contacts_service
    result: ContactsService = pyicloud_service.contacts
    assert result == mock_contacts_service


def test_contacts_raises_on_api_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test contacts property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://contacts.example.com",
        ),
        patch(
            "pyicloud.base.ContactsService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._contacts = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Contacts service not available",
        ):
            _: ContactsService = pyicloud_service.contacts


def test_reminders_returns_service(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test reminders property returns RemindersService instance."""
    mock_reminders_service = MagicMock()
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://reminders.example.com",
        ),
        patch(
            "pyicloud.base.RemindersService",
            return_value=mock_reminders_service,
        ) as mock_reminders_cls,
    ):
        pyicloud_service._reminders = None
        result: RemindersService = pyicloud_service.reminders
        mock_reminders_cls.assert_called_once_with(
            service_root="https://reminders.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
            cloudkit_validation_extra=None,
        )
        assert result == mock_reminders_service


def test_reminders_returns_cached_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test reminders property returns cached instance if already set."""
    mock_reminders_service = MagicMock()
    pyicloud_service._reminders = mock_reminders_service
    result: RemindersService = pyicloud_service.reminders
    assert result == mock_reminders_service


def test_reminders_raises_on_api_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test reminders property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://reminders.example.com",
        ),
        patch(
            "pyicloud.base.RemindersService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._reminders = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Reminders service not available",
        ):
            _ = pyicloud_service.reminders


def test_reminders_raises_on_not_activated_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Reminders wraps missing ckdatabasews activation as service unavailable."""
    with patch.object(
        pyicloud_service,
        "get_webservice_url",
        side_effect=PyiCloudServiceNotActivatedException("error"),
    ):
        pyicloud_service._reminders = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Reminders service not available",
        ):
            _ = pyicloud_service.reminders


def test_notes_returns_new_notes_service_instance(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test notes property returns a new NotesService instance."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://notes.example.com",
        ),
        patch("pyicloud.base.NotesService") as mock_notes_service,
    ):
        mock_notes_instance = MagicMock(spec=NotesService)
        mock_notes_service.return_value = mock_notes_instance

        result = pyicloud_service.notes

        mock_notes_service.assert_called_once_with(
            service_root="https://notes.example.com",
            session=pyicloud_service.session,
            params=pyicloud_service.params,
            cloudkit_validation_extra=pyicloud_service._cloudkit_validation_extra,
        )
        assert result == mock_notes_instance


def test_notes_returns_cached_instance(pyicloud_service: PyiCloudService) -> None:
    """Test notes property returns cached instance if already set."""
    mock_notes_service = MagicMock()
    pyicloud_service._notes = mock_notes_service
    result: NotesService = pyicloud_service.notes
    assert result == mock_notes_service


def test_notes_raises_on_api_exception(pyicloud_service: PyiCloudService) -> None:
    """Test notes property raises PyiCloudServiceUnavailable on API exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            return_value="https://notes.example.com",
        ),
        patch(
            "pyicloud.base.NotesService",
            side_effect=PyiCloudAPIResponseException("error"),
        ),
    ):
        pyicloud_service._notes = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Notes service not available",
        ):
            _ = pyicloud_service.notes


def test_notes_raises_on_not_activated_exception(
    pyicloud_service: PyiCloudService,
) -> None:
    """Notes wraps missing ckdatabasews activation as service unavailable."""
    with patch.object(
        pyicloud_service,
        "get_webservice_url",
        side_effect=PyiCloudServiceNotActivatedException("error"),
    ):
        pyicloud_service._notes = None
        with pytest.raises(
            PyiCloudServiceUnavailable,
            match="Notes service not available",
        ):
            _ = pyicloud_service.notes


def test_setup_cookie_directory_with_custom_path(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _setup_cookie_directory with a custom cookie directory path."""
    with (
        patch("pyicloud.base.path.expanduser") as mock_expanduser,
        patch("pyicloud.base.path.normpath") as mock_normpath,
        patch("pyicloud.base.makedirs") as mock_makedirs,
    ):
        mock_normpath.return_value = "/normalized/path"
        mock_expanduser.return_value = "/expanded/path"

        result: str = pyicloud_service._setup_cookie_directory("/custom/path")

        mock_expanduser.assert_called_once_with("/custom/path")
        mock_normpath.assert_called_once_with("/expanded/path")
        mock_makedirs.assert_called_once_with("/normalized/path", exist_ok=True)
        assert result == "/normalized/path"


def test_setup_cookie_directory_with_none_creates_default(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _setup_cookie_directory with None creates default directory structure."""
    with (
        patch("pyicloud.base.gettempdir") as mock_gettempdir,
        patch("pyicloud.base.getpass.getuser") as mock_getuser,
        patch("pyicloud.base.path.join") as mock_join,
        patch("pyicloud.base.makedirs") as mock_makedirs,
        patch("pyicloud.base.chmod") as mock_chmod,
    ):
        mock_gettempdir.return_value = "/tmp"
        mock_getuser.return_value = "testuser"
        mock_join.side_effect = ["/tmp/pyicloud", "/tmp/pyicloud/testuser"]

        result: str = pyicloud_service._setup_cookie_directory(None)

        mock_gettempdir.assert_called_once()
        mock_getuser.assert_called_once()
        assert mock_join.call_count == 2
        assert mock_makedirs.call_count == 2
        assert mock_chmod.call_count == 1
        mock_chmod.assert_called_once_with("/tmp/pyicloud", 0o1777)
        assert result == "/tmp/pyicloud/testuser"


def test_setup_cookie_directory_with_empty_string(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _setup_cookie_directory with empty string creates default directory structure."""
    with (
        patch("pyicloud.base.gettempdir") as mock_gettempdir,
        patch("pyicloud.base.getpass.getuser") as mock_getuser,
        patch("pyicloud.base.path.join") as mock_join,
        patch("pyicloud.base.makedirs"),
        patch("pyicloud.base.chmod"),
    ):
        mock_gettempdir.return_value = "/tmp"
        mock_getuser.return_value = "testuser"
        mock_join.side_effect = ["/tmp/pyicloud", "/tmp/pyicloud/testuser"]

        result: str = pyicloud_service._setup_cookie_directory("")

        mock_gettempdir.assert_called_once()
        mock_getuser.assert_called_once()
        assert result == "/tmp/pyicloud/testuser"


def test_setup_cookie_directory_with_tilde_expansion(
    pyicloud_service: PyiCloudService,
) -> None:
    """Test _setup_cookie_directory expands tilde in path."""
    with (
        patch("pyicloud.base.path.expanduser") as mock_expanduser,
        patch("pyicloud.base.path.normpath") as mock_normpath,
        patch("pyicloud.base.makedirs") as mock_makedirs,
        patch("pyicloud.base.umask") as mock_umask,
    ):
        mock_normpath.return_value = "/home/user/.pyicloud"
        mock_expanduser.return_value = "/home/user/.pyicloud"
        mock_umask.return_value = 0o700

        result: str = pyicloud_service._setup_cookie_directory("~/.pyicloud")

        mock_expanduser.assert_called_once_with("~/.pyicloud")
        mock_makedirs.assert_called_once_with("/home/user/.pyicloud", exist_ok=True)
        assert mock_umask.call_count == 2
        mock_umask.assert_called_with(0o700)
        mock_umask.assert_any_call(0o077)
        assert result == "/home/user/.pyicloud"
