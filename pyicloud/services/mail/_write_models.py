"""Strict, operation-specific Mail mutation envelopes."""

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Acknowledgement = TypeVar("Acknowledgement")


class WriteModel(BaseModel):
    """Fail closed on unrecognized mutation fields and coercions."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, strict=True
    )


class UpdateResponse(WriteModel, Generic[Acknowledgement]):
    """Require every observed outcome field, with a typed update acknowledgement."""

    account_id: Annotated[str, Field(strict=True, min_length=1)] = Field(
        alias="accountId"
    )
    created: dict[str, JsonValue] | None
    destroyed: list[str] | None
    new_state: JsonValue = Field(alias="newState")
    old_state: JsonValue = Field(alias="oldState")
    not_created: dict[str, JsonValue] | None = Field(alias="notCreated")
    not_destroyed: dict[str, JsonValue] | None = Field(alias="notDestroyed")
    not_updated: dict[str, JsonValue] | None = Field(alias="notUpdated")
    updated: dict[str, Acknowledgement] | None
