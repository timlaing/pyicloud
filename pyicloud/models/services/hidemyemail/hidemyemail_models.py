# Start of Selection
"""
Pydantic models for the HideMyEmail service.

Models for these operations:
    - Generate new email aliases
    - Reserve specific aliases
    - List all existing aliases
    - Get alias details by ID
    - Update alias metadata (label, note)
    - Delete aliases
    - Deactivate aliases
    - Reactivate aliases
"""

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from pyicloud.utils import underscore_to_camelcase


# ─── Base and Shared Config ──────────────────────────────────────────────────
class ConfigModel(BaseModel):
    """Base class providing camel-case aliases, population by name, and allowing extra fields."""

    model_config = ConfigDict(
        alias_generator=underscore_to_camelcase,
        populate_by_name=True,
        extra="allow",
        json_encoders={
            datetime: lambda dt: int(dt.replace(tzinfo=timezone.utc).timestamp())
        },
    )


# Example constants (anonymized)
EXAMPLE_FORWARD_EMAIL = "user@example.com"
EXAMPLE_ALIAS_ON_DEMAND = "alias-example-1a@icloud.com"
EXAMPLE_ALIAS_IN_APP = "alias-inapp-2b@icloud.com"
EXAMPLE_ALIAS_GENERATED = "alias-generated-3c@icloud.com"
EXAMPLE_ALIAS_RESERVE = "alias-reserve-4d@icloud.com"
EXAMPLE_LABEL = "Project Signup"
EXAMPLE_RESERVED_LABEL = "Reserved Label"


# ─── Shared building-blocks ─────────────────────────────────────────────────
class HideMyEmailByIdRequest(ConfigModel):
    """Request payload for single-alias operations by anonymousId."""

    anonymous_id: str = Field(..., alias="anonymousId")
    """Anonymous ID of the alias."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={"example": {"anonymousId": "abc123anonymous"}}
    )


class MessageResult(ConfigModel):
    """Generic result payload containing only a `message` field."""

    message: str = Field(...)
    """Result message, e.g., 'success'."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={"example": {"message": "success"}}
    )


# ─── Alias models ───────────────────────────────────────────────────────────
class HideMyEmailBase(ConfigModel):
    """Common fields for Hide My Email entries."""

    origin: Literal["ON_DEMAND", "IN_APP"]
    """The origin of the alias, either "ON_DEMAND" or "IN_APP"."""

    anonymous_id: str = Field(..., alias="anonymousId")
    """Anonymous ID of the alias."""

    domain: str
    """The domain associated with the alias."""

    forward_to_email: EmailStr = Field(..., alias="forwardToEmail")
    """The email address to which emails are forwarded."""

    hme: str
    """The Hide My Email address."""

    label: str
    """The label for the alias."""

    note: str
    """The note for the alias."""

    create_timestamp: datetime = Field(..., alias="createTimestamp")
    """Creation timestamp as a datetime object."""

    is_active: bool = Field(..., alias="isActive")
    """Whether the alias is active."""

    recipient_mail_id: str = Field(..., alias="recipientMailId")
    """The recipient mail ID."""

    @field_validator("create_timestamp", mode="before")
    @classmethod
    def _parse_create_timestamp(cls, v):
        # API returns milliseconds since epoch
        if isinstance(v, int):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "origin": "ON_DEMAND",
                "anonymousId": "xyz000anon",
                "domain": "",
                "forwardToEmail": EXAMPLE_FORWARD_EMAIL,
                "hme": EXAMPLE_ALIAS_ON_DEMAND,
                "label": EXAMPLE_LABEL,
                "note": "",
                "createTimestamp": 1700000000000,
                "isActive": True,
                "recipientMailId": "",
            }
        }
    )


class HideMyEmailOnDemand(HideMyEmailBase):
    """Alias created on demand via iCloud settings."""

    origin: Literal["ON_DEMAND"]
    """The origin of the alias, always "ON_DEMAND"."""

    model_config = ConfigModel.model_config


class HideMyEmailInApp(HideMyEmailBase):
    """Alias created within a third-party app supporting Hide My Email."""

    origin: Literal["IN_APP"]
    """The origin of the alias, always "IN_APP"."""

    origin_app_name: str = Field(..., alias="originAppName")
    """The name of the originating app."""

    app_bundle_id: str = Field(..., alias="appBundleId")
    """The bundle ID of the originating app."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "origin": "IN_APP",
                "anonymousId": "uvw111anon",
                "domain": "com.example.app",
                "forwardToEmail": EXAMPLE_FORWARD_EMAIL,
                "hme": EXAMPLE_ALIAS_IN_APP,
                "label": "App Feature",
                "note": "Generated by App",
                "createTimestamp": 1700000001234,
                "isActive": True,
                "recipientMailId": "",
                "originAppName": "ExampleApp",
                "appBundleId": "com.example.app",
            }
        }
    )


HideMyEmail = Annotated[
    Union[HideMyEmailOnDemand, HideMyEmailInApp],
    Field(discriminator="origin"),
]


# ─── List endpoint ──────────────────────────────────────────────────────────
class HideMyEmailListResult(ConfigModel):
    """Container for the result of a Hide My Email list operation."""

    forward_to_emails: list[EmailStr] = Field(
        default_factory=list, alias="forwardToEmails"
    )
    """List of email addresses to which emails are forwarded."""

    hme_emails: list[HideMyEmail] = Field(default_factory=list, alias="hmeEmails")
    """List of Hide My Email aliases."""

    selected_forward_to: EmailStr = Field(..., alias="selectedForwardTo")
    """The currently selected forward-to email address."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "forwardToEmails": [EXAMPLE_FORWARD_EMAIL],
                "hmeEmails": [
                    {
                        "origin": "ON_DEMAND",
                        "anonymousId": "xyz000anon",
                        "domain": "",
                        "forwardToEmail": EXAMPLE_FORWARD_EMAIL,
                        "hme": EXAMPLE_ALIAS_ON_DEMAND,
                        "label": EXAMPLE_LABEL,
                        "note": "",
                        "createTimestamp": 1700000000000,
                        "isActive": True,
                        "recipientMailId": "",
                    }
                ],
                "selectedForwardTo": EXAMPLE_FORWARD_EMAIL,
            }
        }
    )


class HideMyEmailListResponse(ConfigModel):
    """Full response model for the Hide My Email 'list' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: HideMyEmailListResult
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000000,
                "result": {
                    "forwardToEmails": [EXAMPLE_FORWARD_EMAIL],
                    "hmeEmails": [
                        {
                            "origin": "ON_DEMAND",
                            "anonymousId": "xyz000anon",
                            "domain": "",
                            "forwardToEmail": EXAMPLE_FORWARD_EMAIL,
                            "hme": EXAMPLE_ALIAS_ON_DEMAND,
                            "label": EXAMPLE_LABEL,
                            "note": "",
                            "createTimestamp": 1700000000000,
                            "isActive": True,
                            "recipientMailId": "",
                        }
                    ],
                    "selectedForwardTo": EXAMPLE_FORWARD_EMAIL,
                },
            }
        }
    )


# ─── Generate endpoint ─────────────────────────────────────────────────────
class HideMyEmailGenerateRequest(ConfigModel):
    """Request payload for generating a new Hide My Email address."""

    lang_code: str = Field(..., alias="langCode")
    """Language code for the request."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={"example": {"langCode": "en-us"}}
    )


class HideMyEmailGenerateResult(ConfigModel):
    """Result payload containing the newly generated Hide My Email address."""

    hme: str
    """The newly generated Hide My Email address."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={"example": {"hme": EXAMPLE_ALIAS_GENERATED}}
    )


class HideMyEmailGenerateResponse(ConfigModel):
    """Full response model for the Hide My Email 'generate' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: HideMyEmailGenerateResult = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000100,
                "result": {"hme": EXAMPLE_ALIAS_GENERATED},
            }
        }
    )


# ─── Reserve endpoint ────────────────────────────────────────────────────────


class HideMyEmailReserveRequest(ConfigModel):
    """Request payload for reserving an existing Hide My Email alias."""

    hme: str
    """The Hide My Email address to reserve."""

    label: str
    """The label for the reserved alias."""

    note: str
    """The note for the reserved alias."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "hme": EXAMPLE_ALIAS_RESERVE,
                "label": EXAMPLE_RESERVED_LABEL,
                "note": "",
            }
        }
    )


class HideMyEmailReserveOnly(ConfigModel):
    """Slim alias model for the 'reserve' operation (no forwardToEmail)."""

    origin: Literal["ON_DEMAND"]
    """The origin of the alias, always "ON_DEMAND"."""

    anonymous_id: str = Field(..., alias="anonymousId")
    """Anonymous ID of the alias."""

    domain: str
    """The domain associated with the alias."""

    hme: str
    """The Hide My Email address."""

    label: str
    """The label for the alias."""

    note: str
    """The note for the alias."""

    create_timestamp: datetime = Field(..., alias="createTimestamp")
    """Creation timestamp as a datetime object."""

    is_active: bool = Field(..., alias="isActive")
    """Whether the alias is active."""

    recipient_mail_id: str = Field(..., alias="recipientMailId")
    """The recipient mail ID."""

    @field_validator("create_timestamp", mode="before")
    @classmethod
    def _parse_create_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "hme": {
                    "origin": "ON_DEMAND",
                    "anonymousId": "xyz000anon",
                    "domain": "",
                    "hme": EXAMPLE_ALIAS_RESERVE,
                    "label": EXAMPLE_RESERVED_LABEL,
                    "note": "",
                    "createTimestamp": 1700000200,
                    "isActive": True,
                    "recipientMailId": "",
                }
            }
        }
    )


class HideMyEmailReserveResponse(ConfigModel):
    """Full response model for the Hide My Email 'reserve' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: HideMyEmailReserveOnly = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000200,
                "result": {
                    "hme": {
                        "origin": "ON_DEMAND",
                        "anonymousId": "xyz000anon",
                        "domain": "",
                        "hme": EXAMPLE_ALIAS_RESERVE,
                        "label": EXAMPLE_RESERVED_LABEL,
                        "note": "",
                        "createTimestamp": 1700000200,
                        "isActive": True,
                        "recipientMailId": "",
                    }
                },
            }
        }
    )


# ─── Get endpoint ──────────────────────────────────────────────────────────


class HideMyEmailGetRequest(HideMyEmailByIdRequest):
    """Request model for the Hide My Email 'get' API operation."""

    pass


class HideMyEmailGetResponse(ConfigModel):
    """Response model for the Hide My Email 'get' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: HideMyEmailBase = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000300,
                "result": {
                    "origin": "ON_DEMAND",
                    "anonymousId": "xyz000anon",
                    "domain": "",
                    "forwardToEmail": EXAMPLE_FORWARD_EMAIL,
                    "hme": EXAMPLE_ALIAS_ON_DEMAND,
                    "label": EXAMPLE_LABEL,
                    "note": "",
                    "createTimestamp": 1700000000000,
                    "isActive": True,
                    "recipientMailId": "",
                },
            }
        }
    )


# ─── Update endpoint ────────────────────────────────────────────────────────


class HideMyEmailUpdateRequest(HideMyEmailByIdRequest):
    """Request model for the Hide My Email 'update' API operation."""

    label: str
    """The new label for the alias."""

    note: str
    """The new note for the alias."""

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "anonymousId": "abc123anonymous",
                "label": EXAMPLE_LABEL,
                "note": "Updated note",
            }
        }
    )


class HideMyEmailUpdateResponse(ConfigModel):
    """Response model for the Hide My Email 'update' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: MessageResult = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000400,
                "result": {"message": "success"},
            }
        }
    )


# ─── Delete endpoint ─────────────────────────────────────────────────────────


class HideMyEmailDeleteRequest(HideMyEmailByIdRequest):
    """Request model for the Hide My Email 'delete' API operation."""

    pass


class HideMyEmailDeleteResponse(ConfigModel):
    """Response model for the Hide My Email 'delete' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: MessageResult = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000500,
                "result": {"message": "success"},
            }
        }
    )


# ─── Deactivate endpoint ─────────────────────────────────────────────────────


class HideMyEmailDeactivateRequest(HideMyEmailByIdRequest):
    """Request model for the Hide My Email 'deactivate' API operation."""

    pass


class HideMyEmailDeactivateResponse(ConfigModel):
    """Response model for the Hide My Email 'deactivate' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: MessageResult = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000600,
                "result": {"message": "success"},
            }
        }
    )


# ─── Reactivate endpoint ────────────────────────────────────────────────────


class HideMyEmailReactivateRequest(HideMyEmailByIdRequest):
    """Request model for the Hide My Email 'reactivate' API operation."""

    pass


class HideMyEmailReactivateResponse(ConfigModel):
    """Response model for the Hide My Email 'reactivate' API operation."""

    success: bool
    """Whether the API call was successful."""

    timestamp: datetime
    """Server timestamp as datetime object."""

    result: MessageResult = Field(...)
    """The result payload."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, int):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v

    model_config = ConfigModel.model_config | ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "timestamp": 1700000700,
                "result": {"message": "success"},
            }
        }
    )


# End of Selection
