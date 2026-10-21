"""Strict, operation-specific Mail mutation envelopes."""

from collections.abc import Mapping
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Acknowledgement = TypeVar("Acknowledgement")
Created = TypeVar("Created")


def known_rejection(failures: Mapping[str, JsonValue], key: str) -> bool:
    """An exact structured failure is stronger evidence than a malformed map value."""
    if set(failures) != {key}:
        return False
    error = failures[key]
    if not isinstance(error, dict):
        return False
    return any(
        isinstance(error.get(field), str) and bool(str(error[field]).strip())
        for field in ("type", "reason", "description")
    )


class WriteModel(BaseModel):
    """Fail closed on unrecognized mutation fields and coercions."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, strict=True
    )


class MutationResponse(WriteModel, Generic[Acknowledgement, Created]):
    """Require every observed outcome field, with a typed update acknowledgement."""

    account_id: Annotated[str, Field(strict=True, min_length=1)] = Field(
        alias="accountId"
    )
    created: dict[str, Created] | None
    destroyed: list[str] | None
    new_state: JsonValue = Field(alias="newState")
    old_state: JsonValue = Field(alias="oldState")
    not_created: dict[str, JsonValue] | None = Field(alias="notCreated")
    not_destroyed: dict[str, JsonValue] | None = Field(alias="notDestroyed")
    not_updated: dict[str, JsonValue] | None = Field(alias="notUpdated")
    updated: dict[str, Acknowledgement] | None


class UpdateResponse(
    MutationResponse[Acknowledgement, JsonValue], Generic[Acknowledgement]
):
    """Message updates retain generic creation data only to reject side effects."""
