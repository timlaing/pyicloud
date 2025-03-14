"""Account service tests."""

import sys
from unittest import TestCase, skipIf
from unittest.mock import mock_open, patch

from . import PyiCloudServiceMock
from .const_login import LOGIN_WORKING


class AccountServiceTest(TestCase):
    """Account service tests."""

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

        return service.account

    def test_repr(self):
        """Tests representation."""
        # fmt: off
        assert repr(self.service) == "<AccountService: {devices: 2, family: 3, storage: 3020076244 bytes free}>"
        # fmt: on

    def test_devices(self):
        """Tests devices."""
        assert self.service.devices
        assert len(self.service.devices) == 2

        for device in self.service.devices:
            assert device.name
            assert device.model
            assert device.udid
            assert device["serialNumber"]
            assert device["osVersion"]
            assert device["modelLargePhotoURL2x"]
            assert device["modelLargePhotoURL1x"]
            assert device["paymentMethods"]
            assert device["name"]
            assert device["model"]
            assert device["udid"]
            assert device["modelSmallPhotoURL2x"]
            assert device["modelSmallPhotoURL1x"]
            assert device["modelDisplayName"]
            # fmt: off
            assert repr(device) == "<AccountDevice: {model: "+device.model_display_name+", name: "+device.name+"}>"
            # fmt: on

    def test_family(self):
        """Tests family members."""
        assert self.service.family
        assert len(self.service.family) == 3

        for member in self.service.family:
            assert member.last_name
            assert member.dsid
            assert member.original_invitation_email
            assert member.full_name
            assert member.age_classification
            assert member.apple_id_for_purchases
            assert member.apple_id
            assert member.first_name
            assert not member.has_screen_time_enabled
            assert not member.has_ask_to_buy_enabled
            assert not member.share_my_location_enabled_family_members
            assert member.dsid_for_purchases
            # fmt: off
            assert repr(member) == "<FamilyMember: {name: "+member.full_name+", age_classification: "+member.age_classification+"}>"
            # fmt: on

    @skipIf(
        sys.version_info >= (3, 12),
        "OrderedDict repr format changes at python3.12 and higher",
    )
    def test_storage(self):
        """Tests storage."""
        assert self.service.storage
        # fmt: off
        assert repr(self.service.storage) == "<AccountStorage: {usage: 43.75% used of 5368709120 bytes, usages_by_media: OrderedDict([('photos', <AccountStorageUsageForMedia: {key: photos, usage: 0 bytes}>), ('backup', <AccountStorageUsageForMedia: {key: backup, usage: 799008186 bytes}>), ('docs', <AccountStorageUsageForMedia: {key: docs, usage: 449092146 bytes}>), ('mail', <AccountStorageUsageForMedia: {key: mail, usage: 1101522944 bytes}>)])}>"
        # fmt: on

    def test_storage_usage(self):
        """Tests storage usage."""
        assert self.service.storage.usage
        usage = self.service.storage.usage
        assert usage.comp_storage_in_bytes or usage.comp_storage_in_bytes == 0
        assert usage.used_storage_in_bytes
        assert usage.used_storage_in_percent
        assert usage.available_storage_in_bytes
        assert usage.available_storage_in_percent
        assert usage.total_storage_in_bytes
        assert usage.commerce_storage_in_bytes or usage.commerce_storage_in_bytes == 0
        assert not usage.quota_over
        assert not usage.quota_tier_max
        assert not usage.quota_almost_full
        assert not usage.quota_paid
        # fmt: off
        assert repr(usage) == "<AccountStorageUsage: "+str(usage.used_storage_in_percent)+"% used of "+str(usage.total_storage_in_bytes)+" bytes>"
        # fmt: on

    def test_storage_usages_by_media(self):
        """Tests storage usages by media."""
        assert self.service.storage.usages_by_media

        for usage_media in self.service.storage.usages_by_media.values():
            assert usage_media.key
            assert usage_media.label
            assert usage_media.color
            assert usage_media.usage_in_bytes or usage_media.usage_in_bytes == 0
            # fmt: off
            assert repr(usage_media) == "<AccountStorageUsageForMedia: {key: "+usage_media.key+", usage: "+str(usage_media.usage_in_bytes)+" bytes}>"
            # fmt: on
