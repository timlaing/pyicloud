"""Find My iPhone service tests."""

from unittest import TestCase
from unittest.mock import mock_open, patch

from . import PyiCloudServiceMock
from .const_login import LOGIN_WORKING


class FindMyiPhoneServiceTest(TestCase):
    """Find My iPhone service tests."""

    def setUp(self) -> None:
        """Set up tests."""
        self.apple_id = "test@example.com"
        self.password = "password"
        self.service = self.create_service_with_mock_authenticate()

    @patch("builtins.open", new_callable=mock_open)
    def create_service_with_mock_authenticate(self, mock_open):
        with patch("pyicloud.base.PyiCloudService.authenticate") as mock_authenticate:
            # Mock the authenticate method during initialization
            mock_authenticate.return_value = None

            service = PyiCloudServiceMock(self.apple_id, self.password)
            service.data = LOGIN_WORKING
            service._webservices = service.data["webservices"]

        return service

    def test_devices(self):
        """Tests devices."""
        assert self.service.devices
        assert len(list(self.service.devices)) == 13

        for device in self.service.devices:
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
