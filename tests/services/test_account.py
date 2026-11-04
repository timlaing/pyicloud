"""Account service tests."""

# pylint: disable=protected-access

from unittest.mock import MagicMock

import pytest

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.account import AccountService, AccountStorageUsage


def test_repr(pyicloud_service_working: PyiCloudService) -> None:
    """Tests representation."""
    assert (
        repr(pyicloud_service_working.account)
        == "<AccountService: {devices: 2, family: 3, storage: 3020076244 bytes free}>"
    )


def test_devices(pyicloud_service_working: PyiCloudService) -> None:
    """Tests devices."""
    assert pyicloud_service_working.account.devices
    assert len(pyicloud_service_working.account.devices) == 2

    for device in pyicloud_service_working.account.devices:
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
        assert (
            repr(device)
            == "<AccountDevice: {model: "
            + device.model_display_name
            + ", name: "
            + device.name
            + "}>"
        )


def test_family(pyicloud_service_working: PyiCloudService) -> None:
    """Tests family members."""
    assert pyicloud_service_working.account.family
    assert len(pyicloud_service_working.account.family) == 3

    for member in pyicloud_service_working.account.family:
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
        assert (
            repr(member)
            == "<FamilyMember: {name: "
            + member.full_name
            + ", age_classification: "
            + member.age_classification
            + "}>"
        )


def test_family_missing_key(mock_session: MagicMock) -> None:
    """family returns [] when familyMembers key is absent from response."""
    mock_session.get.return_value.json.return_value = {}
    service = AccountService(
        service_root="https://example.com",
        session=mock_session,
        china_mainland=False,
        params={},
    )
    assert not service.family


def test_storage(pyicloud_service_working: PyiCloudService) -> None:
    """Tests storage."""
    assert pyicloud_service_working.account.storage
    assert repr(pyicloud_service_working.account.storage) == (
        "<AccountStorage: {usage: 43.75% used of 5368709120 bytes, usages_by_media: "
        "{'photos': <AccountStorageUsageForMedia: {key: photos, usage: 0 bytes}>, "
        "'backup': <AccountStorageUsageForMedia: {key: backup, usage: "
        "799008186 bytes}>, "
        "'docs': <AccountStorageUsageForMedia: {key: docs, usage: 449092146 bytes}>, "
        "'mail': <AccountStorageUsageForMedia: {key: mail, usage: 1101522944 bytes}>}}>"
    )


def test_storage_usage(pyicloud_service_working: PyiCloudService) -> None:
    """Tests storage usage."""
    assert pyicloud_service_working.account.storage.usage
    usage: AccountStorageUsage = pyicloud_service_working.account.storage.usage
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
    assert (
        repr(usage)
        == "<AccountStorageUsage: "
        + str(usage.used_storage_in_percent)
        + "% used of "
        + str(usage.total_storage_in_bytes)
        + " bytes>"
    )


def test_storage_usages_by_media(pyicloud_service_working: PyiCloudService) -> None:
    """Tests storage usages by media."""
    assert pyicloud_service_working.account.storage.usages_by_media

    for (
        usage_media
    ) in pyicloud_service_working.account.storage.usages_by_media.values():
        assert usage_media.key
        assert usage_media.label
        assert usage_media.color
        assert usage_media.usage_in_bytes or usage_media.usage_in_bytes == 0
        assert (
            repr(usage_media)
            == "<AccountStorageUsageForMedia: {key: "
            + usage_media.key
            + ", usage: "
            + str(usage_media.usage_in_bytes)
            + " bytes}>"
        )


def test_summary_plan(
    pyicloud_service_working: PyiCloudService, mock_session: MagicMock
) -> None:
    """Tests the summary_plan property."""
    # Mock the response for the summary plan endpoint
    mock_response = {
        "planName": "iCloud+",
        "storageCapacity": "200GB",
        "price": "$2.99/month",
    }
    mock_session.get.return_value.json.return_value = mock_response
    pyicloud_service_working._session = mock_session

    # Access the summary_plan property
    summary_plan = pyicloud_service_working.account.summary_plan

    # Assertions
    assert summary_plan == mock_response
    mock_session.get.assert_called_once_with(
        pyicloud_service_working.account._gateway_summary_plan_url,
        params=pyicloud_service_working.account.params,
    )


@pytest.mark.parametrize("cached", [False, True])
def test_refresh_storage(mock_session: MagicMock, cached: bool) -> None:
    """Explicit refresh replaces the snapshot and subsequent reads stay cached."""
    service = AccountService(
        "https://example.com", mock_session, False, {"dsid": "123"}
    )
    mock_session.post.return_value.json.return_value = {
        "storageUsageInfo": {"usedStorageInBytes": 10, "totalStorageInBytes": 100},
        "storageUsageByMedia": [
            {"mediaKey": "photos", "usageInBytes": 10},
        ],
    }
    if cached:
        previous = service.storage
        assert service.storage is previous
        mock_session.post.assert_called_once()
    mock_session.post.reset_mock()
    mock_session.post.return_value.json.return_value = {
        "storageUsageInfo": {"usedStorageInBytes": 20, "totalStorageInBytes": 200},
        "storageUsageByMedia": [
            {"mediaKey": "docs", "usageInBytes": 20},
        ],
    }

    current = service.refresh_storage()

    assert current.usage.used_storage_in_bytes == 20
    assert current.usage.total_storage_in_bytes == 200
    assert set(current.usages_by_media) == {"docs"}
    assert service.storage is current
    mock_session.post.assert_called_once_with(
        "https://example.com/setup/ws/1/storageUsageInfo", params={"dsid": "123"}
    )


@pytest.mark.parametrize(
    "failure",
    ["request", "json", "storage"],
)
def test_refresh_storage_failure(mock_session: MagicMock, failure: str) -> None:
    """Failed refreshes propagate errors without discarding the cached snapshot."""
    service = AccountService("https://example.com", mock_session, False, {})
    mock_session.post.return_value.json.return_value = {
        "storageUsageInfo": {"usedStorageInBytes": 10, "totalStorageInBytes": 100},
    }
    previous = service.storage
    errors = {
        "request": PyiCloudAPIResponseException("Storage unavailable"),
        "json": ValueError("Invalid JSON"),
        "storage": KeyError("mediaKey"),
    }
    error = errors[failure]
    if failure == "request":
        mock_session.post.side_effect = error
    elif failure == "json":
        mock_session.post.return_value.json.side_effect = error
    else:
        mock_session.post.return_value.json.return_value = {"storageUsageByMedia": [{}]}

    with pytest.raises(type(error)):
        service.refresh_storage()

    assert service.storage is previous
    assert previous.usage.used_storage_in_bytes == 10
