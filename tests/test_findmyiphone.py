"""Find My iPhone service tests."""

# pylint: disable=protected-access

from unittest.mock import patch

from pyicloud.base import PyiCloudService
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
    manager.refresh_client()
    assert len(manager) > 0

    # Test __getitem__
    device = manager[0]
    assert isinstance(device, AppleDevice)

    # Test __len__
    assert len(manager) == len(manager)

    # Test __iter__
    devices = list(iter(manager))
    assert len(devices) == len(manager)

    # Test __str__ and __repr__
    assert str(manager) == repr(manager)
