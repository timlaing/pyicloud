"""Find My iPhone service tests."""

# pylint: disable=protected-access

from unittest.mock import MagicMock, call, patch

import pytest

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudAuthRequiredException,
    PyiCloudNoDevicesException,
    PyiCloudServiceUnavailable,
)
from pyicloud.services.findmyiphone import AppleDevice, FindMyiPhoneServiceManager


def test_devices(pyicloud_service_working: PyiCloudService) -> None:
    """Tests devices."""

    assert pyicloud_service_working.devices

    for device in pyicloud_service_working.devices:
        assert device["canWipeAfterLock"] is not None
        assert device["baUUID"] is not None
        assert device["wipeInProgress"] is not None
        assert device["lostModeEnabled"] is not None
        assert device["activationLocked"] is not None
        assert device["passcodeLength"] is not None
        assert device["deviceStatus"] is not None
        assert device["features"] is not None
        assert device["lowPowerMode"] is not None
        assert device["rawDeviceModel"] is not None
        assert device["id"] is not None
        assert device["isLocating"] is not None
        assert device["modelDisplayName"] is not None
        assert device["lostTimestamp"] is not None
        assert device["batteryLevel"] is not None
        assert device["locationEnabled"] is not None
        assert device["locFoundEnabled"] is not None
        assert device["fmlyShare"] is not None
        assert device["lostModeCapable"] is not None
        assert device["wipedTimestamp"] is None
        assert device["deviceDisplayName"] is not None
        assert device["audioChannels"] is not None
        assert device["locationCapable"] is not None
        assert device["batteryStatus"] is not None
        assert device["trackingInfo"] is None
        assert device["name"] is not None
        assert device["isMac"] is not None
        assert device["thisDevice"] is not None
        assert device["deviceClass"] is not None
        assert device["deviceModel"] is not None
        assert device["maxMsgChar"] is not None
        assert device["darkWake"] is not None
        assert device["remoteWipe"] is None

        assert device.data["canWipeAfterLock"] is not None
        assert device.data["baUUID"] is not None
        assert device.data["wipeInProgress"] is not None
        assert device.data["lostModeEnabled"] is not None
        assert device.data["activationLocked"] is not None
        assert device.data["passcodeLength"] is not None
        assert device.data["deviceStatus"] is not None
        assert device.data["features"] is not None
        assert device.data["lowPowerMode"] is not None
        assert device.data["rawDeviceModel"] is not None
        assert device.data["id"] is not None
        assert device.data["isLocating"] is not None
        assert device.data["modelDisplayName"] is not None
        assert device.data["lostTimestamp"] is not None
        assert device.data["batteryLevel"] is not None
        assert device.data["locationEnabled"] is not None
        assert device.data["locFoundEnabled"] is not None
        assert device.data["fmlyShare"] is not None
        assert device.data["lostModeCapable"] is not None
        assert device.data["wipedTimestamp"] is None
        assert device.data["deviceDisplayName"] is not None
        assert device.data["audioChannels"] is not None
        assert device.data["locationCapable"] is not None
        assert device.data["batteryStatus"] is not None
        assert device.data["trackingInfo"] is None
        assert device.data["name"] is not None
        assert device.data["isMac"] is not None
        assert device.data["thisDevice"] is not None
        assert device.data["deviceClass"] is not None
        assert device.data["deviceModel"] is not None
        assert device.data["maxMsgChar"] is not None
        assert device.data["darkWake"] is not None
        assert device.data["remoteWipe"] is None


def test_apple_device_properties(pyicloud_service_working: PyiCloudService) -> None:
    """Tests AppleDevice properties and methods."""
    device: AppleDevice = pyicloud_service_working.devices[0]

    # Test session property
    assert device.session is not None

    # Test location property
    location = device.location
    assert location is not None
    assert "latitude" in location
    assert "longitude" in location

    # Test status method
    status = device.status()
    assert "batteryLevel" in status
    assert "deviceDisplayName" in status
    assert "deviceStatus" in status
    assert "name" in status

    # Test status with additional fields
    additional_status = device.status(additional=["isMac", "deviceClass"])
    assert "isMac" in additional_status
    assert "deviceClass" in additional_status

    # Test data property
    assert device.data is not None
    assert "id" in device.data

    # Test __getitem__ method
    assert device["id"] == device.data["id"]

    # Test __getattr__ method
    assert device.deviceDisplayName == device.data["deviceDisplayName"]

    # Test __str__ method
    assert str(device) == f"{device['deviceDisplayName']}: {device['name']}"

    # Test __repr__ method
    assert repr(device) == f"<AppleDevice({device})>"


def test_apple_device_actions(pyicloud_service_working: PyiCloudService) -> None:
    """Tests AppleDevice actions like play_sound, display_message, and lost_device."""
    device: AppleDevice = pyicloud_service_working.devices[0]

    # Mock session.post to avoid actual API calls
    with patch.object(device.session, "post") as mock_post:
        # Test play_sound
        device.play_sound(subject="Test Alert")
        mock_post.assert_called_with(
            device._sound_url,
            params=device._params,
            json={
                "device": device.data["id"],
                "subject": "Test Alert",
                "clientContext": {"fmly": True},
            },
        )

        # Test display_message
        device.display_message(subject="Test Message", message="Hello", sounds=True)
        mock_post.assert_called_with(
            device._message_url,
            params=device._params,
            json={
                "device": device.data["id"],
                "subject": "Test Message",
                "sound": True,
                "userText": True,
                "text": "Hello",
            },
        )

        # Test lost_device
        device.lost_device(
            number="1234567890", text="Lost device message", newpasscode="1234"
        )
        mock_post.assert_called_with(
            device._lost_url,
            params=device._params,
            json={
                "text": "Lost device message",
                "userText": True,
                "ownerNbr": "1234567890",
                "lostModeEnabled": True,
                "trackingEnabled": True,
                "device": device.data["id"],
                "passcode": "1234",
            },
        )


def test_findmyiphone_service_manager(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Tests FindMyiPhoneServiceManager methods."""
    manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices

    # Test refresh_client
    manager.refresh_client_with_reauth()
    assert len(manager) > 0

    # Test __getitem__
    device: AppleDevice = manager[0]
    assert isinstance(device, AppleDevice)

    # Test __len__
    assert len(manager) == len(manager)

    # Test __iter__
    devices: list[AppleDevice] = list(iter(manager))
    assert len(devices) == len(manager)

    # Test __str__ and __repr__
    assert str(manager) == repr(manager)


def test_refresh_no_content(pyicloud_service_working: PyiCloudService) -> None:
    with patch(
        "pyicloud.services.findmyiphone.FindMyiPhoneServiceManager.refresh_client_with_reauth",
        return_value=None,
    ):
        manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices
        manager._with_family = True

        with patch.object(manager.session, "post") as mock_post:
            mock_post.return_value.json.return_value = {}
            manager._refresh_client()
            assert mock_post.call_count == 1
            assert len(manager._devices) == 0
            mock_post.assert_called_with(
                url=manager._fmip_init_url,
                params=manager.params,
                json={
                    "clientContext": {
                        "appName": "iCloud Find (Web)",
                        "appVersion": "2.0",
                        "apiVersion": "3.0",
                        "deviceListVersion": 1,
                        "fmly": True,
                        "timezone": "US/Pacific",
                        "inactiveTime": 0,
                    }
                },
            )


def test_refresh_with_server_ctx(pyicloud_service_working: PyiCloudService) -> None:
    with patch(
        "pyicloud.services.findmyiphone.FindMyiPhoneServiceManager.refresh_client_with_reauth",
        return_value=None,
    ):
        manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices
        manager._with_family = True

        with patch.object(manager.session, "post") as mock_post:
            mock_post.return_value.json.return_value = {
                "serverContext": {
                    "theftLoss": {
                        "status": "OFF",
                    }
                },
                "content": [],
                "error": None,
            }
            manager._refresh_client()
            manager._refresh_client()
            assert mock_post.call_count == 2
            assert len(manager._devices) == 0
            mock_post.assert_has_calls(
                [
                    call(
                        url=manager._fmip_init_url,
                        params=manager.params,
                        json={
                            "clientContext": {
                                "appName": "iCloud Find (Web)",
                                "appVersion": "2.0",
                                "apiVersion": "3.0",
                                "deviceListVersion": 1,
                                "fmly": True,
                                "timezone": "US/Pacific",
                                "inactiveTime": 0,
                            }
                        },
                    ),
                    call().json(),
                    call(
                        url=manager._fmip_refresh_url,
                        params=manager.params,
                        json={
                            "clientContext": {
                                "appName": "iCloud Find (Web)",
                                "appVersion": "2.0",
                                "apiVersion": "3.0",
                                "deviceListVersion": 1,
                                "fmly": True,
                                "timezone": "US/Pacific",
                                "inactiveTime": 0,
                            },
                            "serverContext": {"theftLoss": None},
                        },
                    ),
                    call().json(),
                ]
            )


def test_get_erase_token_success(pyicloud_service_working: PyiCloudService) -> None:
    """Tests AppleDevice._get_erase_token returns token when available."""
    device: AppleDevice = pyicloud_service_working.devices[0]
    expected_token = "test_erase_token"

    with patch.object(device.session, "post") as mock_post:
        mock_post.return_value.json.return_value = {
            "tokens": {"mmeFMIPWebEraseDeviceToken": expected_token}
        }
        token: str = device._get_erase_token()
        assert token == expected_token
        mock_post.assert_called_with(
            url=device._erase_token_url,
            json={"dsWebAuthToken": device.session.data.get("session_token")},
        )


def test_get_erase_token_missing_tokens(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Tests AppleDevice._get_erase_token raises when tokens missing."""
    device: AppleDevice = pyicloud_service_working.devices[0]

    with patch.object(device.session, "post") as mock_post:
        mock_post.return_value.json.return_value = {}

        with pytest.raises(PyiCloudServiceUnavailable):
            device._get_erase_token()

        mock_post.assert_called_with(
            url=device._erase_token_url,
            json={"dsWebAuthToken": device.session.data.get("session_token")},
        )


def test_get_erase_token_missing_token_key(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Tests AppleDevice._get_erase_token raises when token key missing."""
    device: AppleDevice = pyicloud_service_working.devices[0]

    with (
        patch.object(device.session, "post") as mock_post,
        pytest.raises(PyiCloudServiceUnavailable),
    ):
        mock_post.return_value.json.return_value = {"tokens": {}}
        device._get_erase_token()


def test_erase_device_calls_post_with_correct_data(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Tests AppleDevice.erase_device calls session.post with correct data."""
    device: AppleDevice = pyicloud_service_working.devices[0]
    expected_token = "test_erase_token"

    with (
        patch.object(
            device, "_get_erase_token", return_value=expected_token
        ) as mock_get_token,
        patch.object(device.session, "post") as mock_post,
    ):
        device.erase_device(text="Erase this device", newpasscode="5678")
        mock_get_token.assert_called_once()
        mock_post.assert_called_with(
            device._erase_url,
            params=device._params,
            json={
                "authToken": expected_token,
                "text": "Erase this device",
                "device": device.data["id"],
                "passcode": "5678",
            },
        )


def test_erase_device_default_arguments(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Tests AppleDevice.erase_device with default arguments."""
    device: AppleDevice = pyicloud_service_working.devices[0]
    expected_token = "default_token"

    with (
        patch.object(
            device, "_get_erase_token", return_value=expected_token
        ) as mock_get_token,
        patch.object(device.session, "post") as mock_post,
    ):
        device.erase_device()
        mock_get_token.assert_called_once()
        mock_post.assert_called_with(
            device._erase_url,
            params=device._params,
            json={
                "authToken": expected_token,
                "text": "This device has been lost. Please call me.",
                "device": device.data["id"],
                "passcode": "",
            },
        )


def test_refresh_client_with_reauth_auth_required(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Test refresh_client_with_reauth handles PyiCloudAuthRequiredException and reauthenticates."""
    manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices

    # Patch _refresh_client to raise PyiCloudAuthRequiredException first, then succeed
    with (
        patch.object(
            manager,
            "_refresh_client",
            side_effect=[PyiCloudAuthRequiredException("", MagicMock()), None],
        ) as mock_refresh,
        patch.object(manager.session.service, "authenticate") as mock_authenticate,
        patch.object(manager, "_devices", {"dummy_id": "dummy_device"}),
        patch.object(manager, "_with_family", False),
    ):
        manager.refresh_client_with_reauth()
        mock_authenticate.assert_called_once_with(force_refresh=True)
        assert mock_refresh.call_count == 2
        mock_refresh.assert_has_calls([call(locate=True), call(locate=True)])


def test_refresh_client_with_reauth_failed(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Test refresh_client_with_reauth handles PyiCloudAuthRequiredException and reauthenticates."""
    manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices

    # Patch _refresh_client to raise PyiCloudAuthRequiredException first, then succeed
    with (
        patch.object(
            manager,
            "_refresh_client",
            side_effect=[
                PyiCloudAuthRequiredException("", MagicMock()),
                PyiCloudAuthRequiredException("", MagicMock()),
            ],
        ) as mock_refresh,
        patch.object(manager.session.service, "authenticate") as mock_authenticate,
        patch.object(manager, "_devices", {"dummy_id": "dummy_device"}),
        patch.object(manager, "_with_family", False),
    ):
        with pytest.raises(PyiCloudAuthRequiredException):
            manager.refresh_client_with_reauth()
        mock_authenticate.assert_called_once_with(force_refresh=True)
        assert mock_refresh.call_count == 2
        mock_refresh.assert_has_calls([call(locate=True), call(locate=True)])


def test_refresh_client_with_reauth_with_locate(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Test refresh_client_with_reauth calls _refresh_client with locate=True."""
    manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices
    manager._with_family = True

    with (
        patch.object(manager, "_refresh_client") as mock_refresh,
        patch.object(manager, "_devices", {"dummy_id": "dummy_device"}),
    ):
        manager.refresh_client_with_reauth()
        # Should call _refresh_client once: with locate=True
        assert mock_refresh.call_count == 1
        mock_refresh.assert_any_call(locate=True)


def test_refresh_client_with_reauth_with_loading_to_done(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Test refresh_client_with_reauth calls _refresh_client if the members are loading."""
    with patch(
        "pyicloud.services.findmyiphone.FindMyiPhoneServiceManager.refresh_client_with_reauth",
        return_value=None,
    ):
        manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices
    manager._with_family = True

    with (
        patch("time.sleep", return_value=None),
        patch.object(manager, "_refresh_client") as mock_refresh,
        patch.object(manager, "_user_info") as mock_user_info,
        patch.object(manager, "_devices", {"dummy_id": "dummy_device"}),
    ):
        mock_user_info.__getitem__.return_value = True

        mock_user_info.get.side_effect = [
            True,
            {
                "member1": {
                    "firstName": "Member1",
                    "lastName": "One",
                    "appleId": "member1@example.com",
                    "deviceFetchStatus": "LOADING",
                },
                "member2": {
                    "firstName": "Member2",
                    "lastName": "Two",
                    "appleId": "member2@example.com",
                    "deviceFetchStatus": "LOADING",
                },
            },
            True,
            {
                "member1": {
                    "firstName": "Member1",
                    "lastName": "One",
                    "appleId": "member1@example.com",
                    "deviceFetchStatus": "LOADING",
                },
                "member2": {
                    "firstName": "Member2",
                    "lastName": "Two",
                    "appleId": "member2@example.com",
                    "deviceFetchStatus": "DONE",
                },
            },
            True,
            {
                "member1": {
                    "firstName": "Member1",
                    "lastName": "One",
                    "appleId": "member1@example.com",
                    "deviceFetchStatus": "DONE",
                },
                "member2": {
                    "firstName": "Member2",
                    "lastName": "Two",
                    "appleId": "member2@example.com",
                    "deviceFetchStatus": "DONE",
                },
            },
        ]
        manager.refresh_client_with_reauth()
        assert mock_refresh.call_count == 3
        mock_refresh.assert_any_call(locate=True)


def test_refresh_client_with_reauth_no_devices_raises(
    pyicloud_service_working: PyiCloudService,
) -> None:
    """Test refresh_client_with_reauth raises PyiCloudNoDevicesException when no devices."""
    manager: FindMyiPhoneServiceManager = pyicloud_service_working.devices

    with (
        patch.object(manager, "_refresh_client"),
        patch.object(manager, "_devices", {}),
    ):
        with pytest.raises(PyiCloudNoDevicesException):
            manager.refresh_client_with_reauth()
