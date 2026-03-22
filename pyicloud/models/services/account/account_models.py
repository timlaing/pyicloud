"""
Pydantic models for the Account service.

Models for these operations:
    - {self.service_root}/setup/web/device/getDevices
    - {self.service_root}/setup/web/family/getFamilyDetails
    - {self.service_root}/setup/ws/1/storageUsageInfo
    - {self._gateway_root}/v1/accounts/{dsid}/plans/icloud/pricing
    - {self._gateway_root}/v3/accounts/{dsid}/subscriptions/features/cloud.storage/plan-summary
    - {self._gateway_root}/v1/accounts/{dsid}/plans/next-larger-plan
    - {self._gateway_root}/v3/accounts/{dsid}/subscriptions/features
    - {self._gateway_root}/v4/accounts/{dsid}/subscriptions/features
    -
"""

from datetime import datetime
from typing import List, Literal, Optional

from dateutil.parser import isoparse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from pyicloud.utils import underscore_to_camelcase


# ─── Base and Shared Config ──────────────────────────────────────────────────
class ConfigModel(BaseModel):
    """Base class providing camel-case aliases, population by name, and allowing extra fields."""

    model_config = ConfigDict(
        alias_generator=underscore_to_camelcase, populate_by_name=True, extra="allow"
    )


# ─── Constants ───────────────────────────────────────────────────────────────

# Example constants (anonymized)
EXAMPLE_SERIAL_MAC = "●●●●●XXXXX"
EXAMPLE_DEVICE_NAME_MAC = "User's MacBook Pro"

# Device specification constants
EXAMPLE_MAC_OS_VERSION = "OSX;15.5"
EXAMPLE_MAC_MODEL = "MacBookPro18,4"
EXAMPLE_MAC_DISPLAY_NAME = 'MacBook Pro 14"'


# --- {self.service_root}/setup/web/device/getDevices ───────────────────────


class AccountDevice(ConfigModel):
    """Model for any account device."""

    # Fields that are ALWAYS present (from sample data)
    serial_number: str
    """Device serial number (privacy-masked)"""
    os_version: str
    """Operating system and version (format: 'OS;version')"""
    name: str
    """User-assigned device name"""
    model: str
    """Apple's internal model identifier"""
    udid: str
    """Universally unique device identifier"""
    model_display_name: str
    """Human-readable model name"""

    # Device images (always present) - Keep manual aliases because uses "URL" not "Url"
    model_large_photo_url1x: HttpUrl = Field(alias="modelLargePhotoURL1x")
    """URL of large photo (1x)"""
    model_large_photo_url2x: HttpUrl = Field(alias="modelLargePhotoURL2x")
    """URL of large photo (2x)"""
    model_small_photo_url1x: HttpUrl = Field(alias="modelSmallPhotoURL1x")
    """URL of small photo (1x)"""
    model_small_photo_url2x: HttpUrl = Field(alias="modelSmallPhotoURL2x")
    """URL of small photo (2x)"""

    # Fields that MIGHT be present (observed in sample)
    imei: Optional[str] = None
    """International Mobile Equipment Identity (privacy-masked)"""
    latest_backup: Optional[datetime] = None
    """ISO timestamp of most recent backup"""
    payment_methods: Optional[List[str]] = None
    """List of payment method IDs associated with this device"""

    @field_validator("latest_backup", mode="before")
    @classmethod
    def _parse_latest_backup(cls, v):
        """Parse ISO 8601 datetime string to datetime object."""
        if isinstance(v, str):
            # Use dateutil for proper ISO 8601 parsing (handles "Z" suffix in Python 3.10+)
            return isoparse(v)
        return v

    # extra="allow" handles any other device-specific fields automatically


class AccountPaymentMethod(ConfigModel):
    """Model for an account payment method."""

    last_four_digits: str
    """Last four digits of card/account number"""
    balance_status: Literal["UNAVAILABLE", "NOTAPPLICABLE", "AVAILABLE"]
    """Current balance status of the payment method"""
    suspension_reason: Literal["ACTIVE", "SUSPENDED", "INACTIVE"]
    """Current suspension status"""
    id: str
    """Unique payment method identifier"""
    type: str
    """Descriptive name of payment method"""
    is_car_key: bool
    """Whether this method can be used as a car key"""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "lastFourDigits": "XXXX",
                "balanceStatus": "UNAVAILABLE",
                "suspensionReason": "ACTIVE",
                "id": "redacted",
                "type": "Revolut Mastercard",
                "isCarKey": False,
            }
        }
    )


class GetDevicesResponse(ConfigModel):
    """Response model for the Get Devices operation."""

    devices: List[AccountDevice]
    """List of devices associated with the account"""
    payment_methods: List[AccountPaymentMethod]
    """List of payment methods associated with the account"""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "devices": [
                    {
                        "serialNumber": EXAMPLE_SERIAL_MAC,
                        "osVersion": EXAMPLE_MAC_OS_VERSION,
                        "name": EXAMPLE_DEVICE_NAME_MAC,
                        "model": EXAMPLE_MAC_MODEL,
                        "modelDisplayName": EXAMPLE_MAC_DISPLAY_NAME,
                    }
                ],
                "paymentMethods": [
                    {
                        "lastFourDigits": "XXXX",
                        "balanceStatus": "UNAVAILABLE",
                        "suspensionReason": "ACTIVE",
                        "id": "redacted",
                        "type": "Revolut Mastercard",
                        "isCarKey": False,
                    }
                ],
            }
        }
    )


# ─── {self.service_root}/setup/web/family/getFamilyDetails ──────────────────────────────────────────────────────


class FamilyMember(ConfigModel):
    """Model for a family member."""

    last_name: str
    """Family member's last name"""
    dsid: str
    """Apple ID Directory Services identifier"""
    original_invitation_email: str
    """Email address used for the original family invitation"""
    full_name: str
    """Complete name of the family member"""
    age_classification: Literal["ADULT", "CHILD", "TEEN"]
    """Age classification category"""
    apple_id_for_purchases: str
    """Apple ID used for purchases"""
    apple_id: str
    """Primary Apple ID"""
    family_id: str
    """Identifier of the family group"""
    first_name: str
    """Family member's first name"""
    has_parental_privileges: bool
    """Whether this member has parental control privileges"""
    has_screen_time_enabled: bool
    """Whether Screen Time is enabled for this member"""
    has_ask_to_buy_enabled: bool
    """Whether Ask to Buy is enabled for this member"""
    has_share_purchases_enabled: bool
    """Whether purchase sharing is enabled"""
    has_share_my_location_enabled: bool
    """Whether location sharing is enabled"""
    dsid_for_purchases: str
    """Directory Services ID used for purchases"""

    # Optional field - only appears for some family members
    share_my_location_enabled_family_members: Optional[List[str]] = None
    """List of family member DSIDs for whom location sharing is enabled"""


class Family(ConfigModel):
    """Model for family group information."""

    family_id: str
    """Unique identifier for the family group"""
    transfer_requests: List[str]
    """List of pending transfer requests"""
    invitations: List[str]
    """List of pending family invitations"""
    organizer: str
    """DSID of the family organizer"""
    members: List[str]
    """List of family member DSIDs"""
    outgoing_transfer_requests: List[str]
    """List of outgoing transfer requests"""
    etag: str
    """Entity tag for caching/versioning"""


class GetFamilyDetailsResponse(ConfigModel):
    """Response model for the Get Family Details operation."""

    status_message: str = Field(alias="status-message")
    """Human-readable status message"""
    family_invitations: List[str]
    """List of pending family invitations"""
    outgoing_transfer_requests: List[str]
    """List of outgoing transfer requests"""
    is_member_of_family: bool
    """Whether the current user is a family member"""
    family: Family
    """Family group information"""
    family_members: List[FamilyMember]
    """List of all family members"""
    status: int
    """Numeric status code"""
    show_add_member_button: bool
    """Whether to show the add member button in UI"""


# ─── {self.service_root}/setup/ws/1/storageUsageInfo ──────────────────────────────────────────────────


class StorageUsageByMedia(ConfigModel):
    """Model for storage usage by media type."""

    media_key: str
    """Media type identifier (e.g., 'photos', 'backup', 'docs')"""
    display_label: str
    """Human-readable label for the media type"""
    display_color: str
    """Hex color code for UI display (without #)"""
    usage_in_bytes: int
    """Storage used by this media type in bytes"""


class StorageUsageInfo(ConfigModel):
    """Model for overall storage usage information."""

    comp_storage_in_bytes: int
    """Complementary storage in bytes"""
    used_storage_in_bytes: int
    """Total used storage in bytes"""
    total_storage_in_bytes: int
    """Total available storage in bytes"""
    commerce_storage_in_bytes: int
    """Commercial storage allocation in bytes"""


class QuotaStatus(ConfigModel):
    """Model for storage quota status information."""

    over_quota: bool
    """Whether the user is over their storage quota"""
    have_max_quota_tier: bool
    """Whether the user has the maximum quota tier"""
    almost_full: bool = Field(alias="almost-full")
    """Whether the storage is almost full"""
    paid_quota: bool
    """Whether the user has a paid storage quota"""


class StorageUsageInfoResponse(ConfigModel):
    """Response model for the Get Storage Usage Info operation."""

    storage_usage_by_media: List[StorageUsageByMedia]
    """Breakdown of storage usage by media type"""
    storage_usage_info: StorageUsageInfo
    """Overall storage usage statistics"""
    quota_status: QuotaStatus
    """Storage quota status information"""


# --- {self._gateway_root}/v1/accounts/{dsid}/plans/icloud/pricing ──────────────────────────────────────────────────────────


class PricingPlansResponse(ConfigModel):
    """Response model for the Get Pricing Plans operation."""

    paid_plan: bool
    """Whether this is a paid plan"""
    price_for_display: str
    """Formatted price string for display (e.g., '$9.99')"""
    renewal_period: Literal["MONTHLY", "YEARLY"]
    """Billing cycle frequency"""


# --- {self._gateway_root}/v3/accounts/{dsid}/subscriptions/features/cloud.storage/plan-summary ──────────────────────────────────────────────────────────


class PlanInclusion(ConfigModel):
    """Model for plan inclusion information."""

    included_in_plan: bool
    """Whether the feature is included in this plan"""
    limit: Optional[int] = None
    """Storage limit amount (if applicable)"""
    limit_units: Optional[str] = None
    """Storage limit units (e.g., 'TIB', 'GIB')"""


class PlanSummaryResponse(ConfigModel):
    """Response model for the Get Plan Summary operation."""

    feature_key: str
    """Feature identifier (e.g., 'cloud.storage')"""
    summary: PlanInclusion
    """Main plan summary information"""
    included_with_account_purchased_plan: PlanInclusion
    """Inclusion details for account purchased plan"""
    included_with_apple_one_plan: PlanInclusion
    """Inclusion details for Apple One plan"""
    included_with_shared_plan: PlanInclusion
    """Inclusion details for shared plan"""
    included_with_comped_plan: PlanInclusion
    """Inclusion details for complimentary plan"""
    included_with_managed_plan: PlanInclusion
    """Inclusion details for managed plan"""


# --- {self._gateway_root}/v1/accounts/{dsid}/plans/next-larger-plan ──────────────────────────────────────────────────────────


class NextLargerPlanResponse(ConfigModel):
    """Response model for the Get Next Larger Plan operation."""

    parameters: str
    """URL-encoded parameters for the plan purchase"""
    interrupted_buy_error_codes: str
    """JSON-encoded array of error codes as string"""
    price_for_display: str
    """Formatted price string for display (e.g., '$29.99')"""
    plan_size_in_bytes: int
    """Storage plan size in bytes"""
    plan_name: str
    """Human-readable plan name (e.g., '6 TB')"""
    highest_tier_plan_name: str
    """Name of the highest available tier plan"""
    user_eligible_for_offer: bool
    """Whether the user is eligible for this offer"""


# --- {self._gateway_root}/v3/accounts/{dsid}/subscriptions/features ──────────────────────────────────────────────────────────


class SubscriptionV3Feature(ConfigModel):
    """Model for an individual subscription feature."""

    feature_key: str
    """Feature identifier (e.g., 'cloud.storage', 'home.cameras')"""
    can_use: bool
    """Whether the user can use this feature"""
    cache_till: datetime
    """ISO timestamp when this feature data expires from cache"""
    limit: Optional[int] = None
    """Feature limit amount (if applicable)"""
    limit_units: Optional[str] = None
    """Feature limit units (e.g., 'TIB', 'GIB')"""

    @field_validator("cache_till", mode="before")
    @classmethod
    def _parse_cache_till(cls, v):
        """Parse ISO 8601 datetime string to datetime object."""
        if isinstance(v, str):
            return isoparse(v)
        return v


# Type alias for the subscription features response (array of features)
SubscriptionFeaturesResponse = List[SubscriptionV3Feature]


# --- {self._gateway_root}/v4/accounts/{dsid}/subscriptions/features ──────────────────────────────────────────────────────────


class SubscriptionV4Feature(ConfigModel):
    """Model for version 4 subscription features."""

    feature_key: str
    """Feature identifier (e.g., 'apps.rsvp.create-event')"""
    can_use: bool
    """Whether the user can use this feature"""
    cache_till: datetime
    """ISO timestamp when this feature data expires from cache"""
    access_token: str
    """JWT access token for this feature"""

    @field_validator("cache_till", mode="before")
    @classmethod
    def _parse_cache_till(cls, v):
        """Parse ISO 8601 datetime string to datetime object."""
        if isinstance(v, str):
            return isoparse(v)
        return v


# Type alias for the v4 subscription features response (array of features)
SubscriptionV4FeaturesResponse = List[SubscriptionV4Feature]
