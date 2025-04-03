"""Account service."""

from typing import Any, Optional

from requests import Response

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession
from pyicloud.utils import underscore_to_camelcase

DEFAULT_DSID = "20288408776"


class AccountService(BaseService):
    """The 'Account' iCloud service."""

    def __init__(
        self,
        service_root: str,
        session: PyiCloudSession,
        china_mainland: bool,
        params: dict[str, Any],
    ) -> None:
        super().__init__(service_root, session, params)

        self._devices: list["AccountDevice"] = []
        self._family: list["FamilyMember"] = []
        self._storage: Optional[AccountStorage] = None

        self._acc_endpoint: str = f"{self.service_root}/setup/web"
        self._acc_devices_url: str = f"{self._acc_endpoint}/device/getDevices"
        self._acc_family_details_url: str = (
            f"{self._acc_endpoint}/family/getFamilyDetails"
        )
        self._acc_family_member_photo_url: str = (
            f"{self._acc_endpoint}/family/getMemberPhoto"
        )
        self._acc_storage_url: str = f"{self.service_root}/setup/ws/1/storageUsageInfo"

        self._gateway: str = (
            f"https://gatewayws.icloud.com{'' if not china_mainland else '.cn'}"
        )
        self._gateway_root: str = f"{self._gateway}/acsegateway"
        dsid: str = self.params.get("dsid", DEFAULT_DSID)
        self._gateway_pricing_url: str = (
            f"{self._gateway_root}/v1/accounts/{dsid}/plans/icloud/pricing"
        )
        self._gateway_summary_plan_url: str = (
            f"{self._gateway_root}/v3/accounts/{dsid}/subscriptions"
            "/features/cloud.storage/plan-summary"
        )

    @property
    def devices(self) -> list["AccountDevice"]:
        """Returns current paired devices."""
        if not self._devices:
            req: Response = self.session.get(self._acc_devices_url, params=self.params)
            response = req.json()

            for device_info in response["devices"]:
                self._devices.append(AccountDevice(device_info))

        return self._devices

    @property
    def family(self) -> list["FamilyMember"]:
        """Returns family members."""
        if not self._family:
            req: Response = self.session.get(
                self._acc_family_details_url, params=self.params
            )
            response = req.json()

            for member_info in response["familyMembers"]:
                self._family.append(
                    FamilyMember(
                        member_info,
                        self.session,
                        self.params,
                        self._acc_family_member_photo_url,
                    )
                )

        return self._family

    @property
    def storage(self) -> "AccountStorage":
        """Returns storage infos."""
        if not self._storage:
            req: Response = self.session.post(self._acc_storage_url, params=self.params)
            response = req.json()

            self._storage = AccountStorage(response)

        return self._storage

    @property
    def summary_plan(self):
        """Returns your subscription plan."""
        req: Response = self.session.get(
            self._gateway_summary_plan_url, params=self.params
        )
        response = req.json()
        return response

    def __str__(self) -> str:
        return "{{devices: {}, family: {}, storage: {} bytes free}}".format(
            len(self.devices),
            len(self.family),
            self.storage.usage.available_storage_in_bytes,
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"


class AccountDevice(dict):
    """Account device."""

    def __getattr__(self, key: str):
        return self[underscore_to_camelcase(key)]

    def __str__(self) -> str:
        return f"{{model: {self.model_display_name}, name: {self.name}}}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"


class FamilyMember:
    """A family member."""

    def __init__(
        self,
        member_info: dict[str, Any],
        session: PyiCloudSession,
        params: dict[str, Any],
        acc_family_member_photo_url: str,
    ) -> None:
        self._attrs: dict[str, Any] = member_info
        self._session: PyiCloudSession = session
        self._params: dict[str, Any] = params
        self._acc_family_member_photo_url: str = acc_family_member_photo_url

    @property
    def last_name(self) -> Optional[str]:
        """Gets the last name."""
        return self._attrs.get("lastName")

    @property
    def dsid(self) -> Optional[str]:
        """Gets the dsid."""
        return self._attrs.get("dsid")

    @property
    def original_invitation_email(self) -> Optional[str]:
        """Gets the original invitation."""
        return self._attrs.get("originalInvitationEmail")

    @property
    def full_name(self) -> Optional[str]:
        """Gets the full name."""
        return self._attrs.get("fullName")

    @property
    def age_classification(self):
        """Gets the age classification."""
        return self._attrs.get("ageClassification")

    @property
    def apple_id_for_purchases(self) -> Optional[str]:
        """Gets the apple id for purchases."""
        return self._attrs.get("appleIdForPurchases")

    @property
    def apple_id(self) -> Optional[str]:
        """Gets the apple id."""
        return self._attrs.get("appleId")

    @property
    def family_id(self):
        """Gets the family id."""
        return self._attrs.get("familyId")

    @property
    def first_name(self) -> Optional[str]:
        """Gets the first name."""
        return self._attrs.get("firstName")

    @property
    def has_parental_privileges(self):
        """Has parental privileges."""
        return self._attrs.get("hasParentalPrivileges")

    @property
    def has_screen_time_enabled(self):
        """Has screen time enabled."""
        return self._attrs.get("hasScreenTimeEnabled")

    @property
    def has_ask_to_buy_enabled(self):
        """Has to ask for buying."""
        return self._attrs.get("hasAskToBuyEnabled")

    @property
    def has_share_purchases_enabled(self):
        """Has share purshases."""
        return self._attrs.get("hasSharePurchasesEnabled")

    @property
    def share_my_location_enabled_family_members(self):
        """Has share my location with family."""
        return self._attrs.get("shareMyLocationEnabledFamilyMembers")

    @property
    def has_share_my_location_enabled(self):
        """Has share my location."""
        return self._attrs.get("hasShareMyLocationEnabled")

    @property
    def dsid_for_purchases(self):
        """Gets the dsid for purchases."""
        return self._attrs.get("dsidForPurchases")

    def get_photo(self) -> Response:
        """Returns the photo."""
        params_photo = dict(self._params)
        params_photo.update({"memberId": self.dsid})
        return self._session.get(
            self._acc_family_member_photo_url, params=params_photo, stream=True
        )

    def __getitem__(self, key):
        if self._attrs.get(key):
            return self._attrs[key]
        return getattr(self, key)

    def __str__(self) -> str:
        return "{{name: {}, age_classification: {}}}".format(
            self.full_name,
            self.age_classification,
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"


class AccountStorageUsageForMedia:
    """Storage used for a specific media type into the account."""

    def __init__(self, usage_data) -> None:
        self.usage_data: dict[str, Any] = usage_data

    @property
    def key(self):
        """Gets the key."""
        return self.usage_data["mediaKey"]

    @property
    def label(self):
        """Gets the label."""
        return self.usage_data["displayLabel"]

    @property
    def color(self):
        """Gets the HEX color."""
        return self.usage_data["displayColor"]

    @property
    def usage_in_bytes(self):
        """Gets the usage in bytes."""
        return self.usage_data["usageInBytes"]

    def __str__(self) -> str:
        return f"{{key: {self.key}, usage: {self.usage_in_bytes} bytes}}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"


class AccountStorageUsage:
    """Storage used for a specific media type into the account."""

    def __init__(self, usage_data, quota_data) -> None:
        self.usage_data: dict[str, Any] = usage_data
        self.quota_data: dict[str, Any] = quota_data

    @property
    def comp_storage_in_bytes(self):
        """Gets the comp storage in bytes."""
        return self.usage_data["compStorageInBytes"]

    @property
    def used_storage_in_bytes(self):
        """Gets the used storage in bytes."""
        return self.usage_data["usedStorageInBytes"]

    @property
    def used_storage_in_percent(self):
        """Gets the used storage in percent."""
        return round(self.used_storage_in_bytes * 100 / self.total_storage_in_bytes, 2)

    @property
    def available_storage_in_bytes(self):
        """Gets the available storage in bytes."""
        return self.total_storage_in_bytes - self.used_storage_in_bytes

    @property
    def available_storage_in_percent(self):
        """Gets the available storage in percent."""
        return round(
            self.available_storage_in_bytes * 100 / self.total_storage_in_bytes, 2
        )

    @property
    def total_storage_in_bytes(self):
        """Gets the total storage in bytes."""
        return self.usage_data["totalStorageInBytes"]

    @property
    def commerce_storage_in_bytes(self):
        """Gets the commerce storage in bytes."""
        return self.usage_data["commerceStorageInBytes"]

    @property
    def quota_over(self):
        """Gets the over quota."""
        return self.quota_data["overQuota"]

    @property
    def quota_tier_max(self):
        """Gets the max tier quota."""
        return self.quota_data["haveMaxQuotaTier"]

    @property
    def quota_almost_full(self):
        """Gets the almost full quota."""
        return self.quota_data["almost-full"]

    @property
    def quota_paid(self):
        """Gets the paid quota."""
        return self.quota_data["paidQuota"]

    def __str__(self) -> str:
        return "{}% used of {} bytes".format(
            self.used_storage_in_percent,
            self.total_storage_in_bytes,
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"


class AccountStorage:
    """Storage of the account."""

    def __init__(self, storage_data) -> None:
        self.usage = AccountStorageUsage(
            storage_data.get("storageUsageInfo"), storage_data.get("quotaStatus")
        )
        self.usages_by_media: dict[str, AccountStorageUsageForMedia] = {}

        for usage_media in storage_data.get("storageUsageByMedia"):
            self.usages_by_media[usage_media["mediaKey"]] = AccountStorageUsageForMedia(
                usage_media
            )

    def __str__(self) -> str:
        return f"{{usage: {self.usage}, usages_by_media: {self.usages_by_media}}}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self}>"
